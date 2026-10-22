from pathlib import Path
from typing import Any
from functools import lru_cache
import os
import re
import struct
import subprocess

SUPPORTED_FONT_EXTENSIONS = (".ttf", ".otf")
FONTS_DIR = Path(__file__).parent.parent / "fonts"
USER_FONTS_DIR = FONTS_DIR / "users"
# Extra read-only font folders, such as the host computer's installed fonts,
# separated by os.pathsep (":" in the containers).
LOCAL_FONT_DIRS = [
    Path(entry)
    for entry in os.getenv("LOCAL_FONT_DIRS", "").split(os.pathsep)
    if entry.strip()
]
TTF_NAME_ID_FONT_FAMILY = 1
TTF_NAME_ID_FULL_NAME = 4
ASS_UNSAFE_NAME_CHARS = frozenset("(){}\\")


def _display_name(font_stem: str) -> str:
    return font_stem.replace("-", " ").replace("_", " ").strip().title()


@lru_cache(maxsize=256)
def _query_font_languages(path: str, modified_ns: int, size: int) -> tuple[str, ...] | None:
    """Fontconfig compares this file's glyph coverage with language orthographies.

    The stat arguments invalidate cached results when an uploaded font changes.
    None means unavailable/invalid; an empty tuple means no covered orthographies.
    """
    try:
        result = subprocess.run(
            ["fc-query", "--format=%{lang}\\n", path],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    # Variable fonts can return several faces with the same coverage. Only report
    # languages covered by every face so the note is true for any selected weight.
    faces = [set(line.strip().split("|")) - {""} for line in result.stdout.splitlines()]
    if not faces:
        return None
    return tuple(sorted(set.intersection(*faces)))


def get_font_languages(font_path: Path) -> list[str] | None:
    try:
        stat = font_path.stat()
    except OSError:
        return None
    languages = _query_font_languages(str(font_path.resolve()), stat.st_mtime_ns, stat.st_size)
    return list(languages) if languages is not None else None


def sanitize_user_id_for_path(user_id: str) -> str:
    safe_value = re.sub(r"[^A-Za-z0-9_-]", "-", user_id).strip("-")
    return safe_value or "user"


def get_user_fonts_dir(user_id: str) -> Path:
    return USER_FONTS_DIR / sanitize_user_id_for_path(user_id)


def find_user_font_path(font_name: str, user_id: str) -> Path | None:
    """Resolve an exact font name inside one user's upload directory only."""
    requested = font_name.strip()
    if not requested or Path(requested).name != requested:
        return None

    user_fonts_dir = get_user_fonts_dir(user_id)
    exact_file = user_fonts_dir / requested
    if (
        exact_file.is_file()
        and exact_file.suffix.lower() in SUPPORTED_FONT_EXTENSIONS
    ):
        return exact_file

    if Path(requested).suffix:
        return None

    for extension in SUPPORTED_FONT_EXTENSIONS:
        candidate = user_fonts_dir / f"{requested}{extension}"
        if candidate.is_file():
            return candidate

    return None


def _collect_fonts_from_dir(font_dir: Path, scope: str) -> list[dict[str, Any]]:
    if not font_dir.exists():
        return []

    local = scope == "local"
    fonts: list[dict[str, Any]] = []
    # Linux keeps installed fonts in nested folders (/usr/share/fonts/truetype/...).
    for font_path in sorted(font_dir.rglob("*") if local else font_dir.iterdir()):
        extension = font_path.suffix.lower()
        if extension not in SUPPORTED_FONT_EXTENSIONS or not font_path.is_file():
            continue
        # Installed fonts have short file names (arialbd.ttf), so show the
        # font's own full name instead. Their coverage check is skipped
        # because running fc-query on hundreds of files makes the list slow.
        display_name = (
            (_cached_full_name(str(font_path), font_path.stat().st_mtime_ns) if local else None)
            or _display_name(font_path.stem)
        )
        fonts.append(
            {
                "name": font_path.stem,
                "display_name": display_name,
                "filename": font_path.name,
                "format": extension.lstrip("."),
                "file_path": str(font_path),
                "scope": scope,
                "supported_languages": None if local else get_font_languages(font_path),
            }
        )

    return fonts


def _dir_mtime(font_dir: Path) -> int:
    try:
        return font_dir.stat().st_mtime_ns
    except OSError:
        return 0


@lru_cache(maxsize=16)
def _collect_local_fonts(font_dir: str, modified_ns: int) -> tuple[dict[str, Any], ...]:
    """Host font folders are large and slow to scan; rescan only when they change."""
    return tuple(_collect_fonts_from_dir(Path(font_dir), scope="local"))


def get_available_fonts(user_id: str | None = None) -> list[dict[str, Any]]:
    fonts: list[dict[str, Any]] = _collect_fonts_from_dir(FONTS_DIR, scope="system")

    if user_id:
        fonts.extend(_collect_fonts_from_dir(get_user_fonts_dir(user_id), scope="user"))

    known = {font["name"].lower() for font in fonts}
    for font_dir in LOCAL_FONT_DIRS:
        for font in _collect_local_fonts(str(font_dir), _dir_mtime(font_dir)):
            if font["name"].lower() not in known:
                known.add(font["name"].lower())
                fonts.append(font)

    return sorted(fonts, key=lambda font: font["display_name"].lower())


def find_font_path(
    font_name: str,
    user_id: str | None = None,
    allow_all_user_fonts: bool = False,
) -> Path | None:
    requested = font_name.strip()
    if not requested:
        return None

    search_dirs = [FONTS_DIR, *LOCAL_FONT_DIRS]
    if user_id:
        search_dirs.insert(0, get_user_fonts_dir(user_id))

    for search_dir in search_dirs:
        exact_file = search_dir / requested
        if (
            exact_file.exists()
            and exact_file.suffix.lower() in SUPPORTED_FONT_EXTENSIONS
        ):
            return exact_file

        for extension in SUPPORTED_FONT_EXTENSIONS:
            candidate = search_dir / f"{requested}{extension}"
            if candidate.exists():
                return candidate

    normalized_requested = re.sub(r"[^a-z0-9]", "", requested.lower())
    for font in get_available_fonts(user_id):
        normalized_name = re.sub(r"[^a-z0-9]", "", font["name"].lower())
        if normalized_requested == normalized_name:
            return Path(font["file_path"])

    if allow_all_user_fonts:
        for font_path in USER_FONTS_DIR.glob(f"**/{requested}.*"):
            if font_path.suffix.lower() in SUPPORTED_FONT_EXTENSIONS:
                return font_path

    return None


def _decode_ttf_name(raw_value: bytes, platform_id: int) -> str:
    encodings = ["utf-16-be"] if platform_id in {0, 3} else ["mac_roman", "utf-8"]
    for encoding in encodings:
        try:
            decoded = raw_value.decode(encoding).strip("\x00").strip()
        except UnicodeDecodeError:
            continue
        if decoded:
            return decoded
    return ""


@lru_cache(maxsize=4096)
def _cached_full_name(path: str, modified_ns: int) -> str | None:
    names = _read_font_names(Path(path))
    if not names:
        return None
    full = names[TTF_NAME_ID_FULL_NAME] or names[TTF_NAME_ID_FONT_FAMILY]
    return full[0] if full else None


def get_font_family_name(font_path: Path) -> str | None:
    """Read a font's internal family name for renderers that match by metadata."""
    candidates = _read_font_names(font_path)
    if not candidates:
        return None
    family_names = list(dict.fromkeys(candidates[TTF_NAME_ID_FONT_FAMILY]))
    full_names = [name for name in candidates[TTF_NAME_ID_FULL_NAME] if name]
    # ASS override tags (\fn<name>) end at "(", "{" or "\", so a family like
    # "THE BOLD FONT (FREE VERSION)" silently falls back to another font.
    # libass also matches full names, so prefer any tag-safe candidate.
    for names in (family_names, full_names):
        safe_names = [name for name in names if not ASS_UNSAFE_NAME_CHARS & set(name)]
        if safe_names:
            return min(safe_names, key=len)

    if family_names:
        return min(family_names, key=len)
    if full_names:
        return full_names[0]
    return None


def _read_font_names(font_path: Path) -> dict[int, list[str]] | None:
    """Family and full-name records from a font's name table, English first."""
    try:
        data = Path(font_path).read_bytes()
        if len(data) < 12:
            return None

        num_tables = struct.unpack_from(">H", data, 4)[0]
        name_table_offset = None
        name_table_length = None
        table_record_offset = 12
        for table_index in range(num_tables):
            record_offset = table_record_offset + table_index * 16
            if record_offset + 16 > len(data):
                return None
            tag, _checksum, table_offset, table_length = struct.unpack_from(
                ">4sIII", data, record_offset
            )
            if tag == b"name":
                name_table_offset = table_offset
                name_table_length = table_length
                break

        if name_table_offset is None or name_table_length is None:
            return None
        if name_table_offset + min(name_table_length, 6) > len(data):
            return None

        _format_selector, record_count, string_offset = struct.unpack_from(
            ">HHH", data, name_table_offset
        )
        storage_start = name_table_offset + string_offset
        candidates: dict[int, list[str]] = {
            TTF_NAME_ID_FONT_FAMILY: [],
            TTF_NAME_ID_FULL_NAME: [],
        }

        for record_index in range(record_count):
            record_offset = name_table_offset + 6 + record_index * 12
            if record_offset + 12 > len(data):
                break
            (
                platform_id,
                _encoding_id,
                language_id,
                name_id,
                value_length,
                value_offset,
            ) = struct.unpack_from(">HHHHHH", data, record_offset)
            if name_id not in candidates:
                continue
            value_start = storage_start + value_offset
            value_end = value_start + value_length
            if value_start < storage_start or value_end > len(data):
                continue
            decoded = _decode_ttf_name(data[value_start:value_end], platform_id)
            if not decoded:
                continue
            # Prefer English records when present, but keep every valid name as fallback.
            if language_id in {0x0409, 0x0000}:
                candidates[name_id].insert(0, decoded)
            else:
                candidates[name_id].append(decoded)

        return candidates
    except Exception:
        return None


def sanitize_font_stem(file_name: str) -> str:
    raw_stem = Path(file_name).stem
    safe_stem = re.sub(r"[^A-Za-z0-9_-]", "-", raw_stem).strip("-")
    if not safe_stem:
        raise ValueError("Invalid font file name")
    return safe_stem


def build_user_font_stem(user_id: str, original_stem: str) -> str:
    safe_stem = sanitize_font_stem(original_stem)
    safe_user = sanitize_user_id_for_path(user_id)
    return f"usr-{safe_user}-{safe_stem}".lower()


def is_font_accessible(font_name: str, user_id: str) -> bool:
    return find_font_path(font_name, user_id=user_id) is not None
