"""Logo and text-tag overlay burned into clips during the final render.

``branding`` is a dict saved with the task:
    {"logo_path": str | None, "text": str | None,
     "position": "top-left" | "top-right" | "bottom-left" | "bottom-right",
     "opacity": 0.0-1.0}

The overlay joins the final ffmpeg pass (no extra encode): the logo is added as
a second input and overlaid, and the tag is drawn with drawtext under it.
"""

import base64
import binascii
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..font_registry import FONTS_DIR
from .common import logger
from .ffmpeg import ffmpeg_escape_filter_path

POSITIONS = {"top-left", "top-right", "bottom-left", "bottom-right"}
LOGO_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
MAX_LOGO_BYTES = 2_000_000
MAX_TEXT_CHARS = 40
LOGO_WIDTH_FRACTION = 0.16
MARGIN_FRACTION = 0.045
# Bottom placements sit above the like/comment buttons and caption area of apps.
BOTTOM_CLEARANCE_FRACTION = 0.13
TAG_FONT = "TikTokSans-Bold.ttf"


def save_branding(task_dir: Path, task_id: str, raw: Any) -> Optional[Dict[str, Any]]:
    """Validate the branding a client sent and store its logo file.

    ``raw`` may hold ``logo_data_url`` (a data: URL), ``text`` and ``position``.
    Returns the dict to keep with the task, or None when nothing usable was sent.
    """
    if not isinstance(raw, dict):
        return None
    text = re.sub(r"\s+", " ", str(raw.get("text") or "")).strip()[:MAX_TEXT_CHARS] or None
    position = raw.get("position") if raw.get("position") in POSITIONS else "top-right"
    try:
        opacity = min(1.0, max(0.2, float(raw.get("opacity", 0.9))))
    except (TypeError, ValueError):
        opacity = 0.9

    logo_path = None
    data_url = raw.get("logo_data_url")
    if isinstance(data_url, str) and data_url.startswith("data:"):
        match = re.match(r"data:(image/[a-z]+);base64,(.+)", data_url, re.S)
        if match and match.group(1) in LOGO_TYPES:
            try:
                data = base64.b64decode(match.group(2), validate=True)
            except (binascii.Error, ValueError):
                data = b""
            if 0 < len(data) <= MAX_LOGO_BYTES:
                task_dir.mkdir(parents=True, exist_ok=True)
                logo_file = task_dir / f"{task_id}{LOGO_TYPES[match.group(1)]}"
                logo_file.write_bytes(data)
                if _image_size(logo_file):
                    logo_path = str(logo_file)
                else:
                    logo_file.unlink(missing_ok=True)

    if not logo_path and not text:
        return None
    return {"logo_path": logo_path, "text": text, "position": position, "opacity": opacity}


def _image_size(path: Path) -> Optional[tuple[int, int]]:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, timeout=30,
    )
    try:
        width, height = (int(value) for value in result.stdout.strip().split(",")[:2])
    except ValueError:
        return None
    return (width, height) if width > 0 and height > 0 else None


def _drawtext_escape(text: str) -> str:
    return (
        text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "’")
        .replace("%", "\\%").replace(",", "\\,")
    )


def _branding_chain(branding: Dict[str, Any], out_w: int, out_h: int, logo_input: Optional[int]) -> str:
    """Filter chain from [bbase] to [bout] that adds the logo and/or tag."""
    position = branding.get("position") if branding.get("position") in POSITIONS else "top-right"
    left = position.endswith("left")
    top = position.startswith("top")
    margin = round(out_w * MARGIN_FRACTION)
    bottom_edge = round(out_h * BOTTOM_CLEARANCE_FRACTION)
    opacity = float(branding.get("opacity") or 0.9)
    steps: List[str] = []
    current = "[bbase]"

    logo_h = 0
    if logo_input is not None:
        size = _image_size(Path(branding["logo_path"]))
        logo_w = round(out_w * LOGO_WIDTH_FRACTION)
        logo_h = round(logo_w * size[1] / size[0]) if size else logo_w
        x = str(margin) if left else f"main_w-overlay_w-{margin}"
        y = str(margin) if top else f"main_h-overlay_h-{bottom_edge}"
        steps.append(
            f"[{logo_input}:v]scale={logo_w}:{logo_h}:flags=lanczos,format=rgba,"
            f"colorchannelmixer=aa={opacity:.2f}[blogo]"
        )
        steps.append(f"{current}[blogo]overlay=x={x}:y={y}:shortest=1:format=auto[blogod]")
        current = "[blogod]"

    text = branding.get("text")
    if text:
        font_size = round(out_w * 0.034)
        gap = round(out_w * 0.012)
        x = str(margin) if left else f"w-tw-{margin}"
        if top:
            y = str(margin + (logo_h + gap if logo_h else 0))
        else:
            y = f"h-th-{bottom_edge + (logo_h + gap if logo_h else 0)}"
        font = ffmpeg_escape_filter_path(FONTS_DIR / TAG_FONT)
        steps.append(
            f"{current}drawtext=fontfile={font}:text='{_drawtext_escape(text)}':"
            f"fontsize={font_size}:fontcolor=white@{opacity:.2f}:"
            f"shadowcolor=black@0.55:shadowx=2:shadowy=2:x={x}:y={y}[btext]"
        )
        current = "[btext]"

    steps.append(f"{current}null[bout]")
    return ";".join(steps)


def apply_branding_to_command(
    command: List[str], branding: Optional[Dict[str, Any]], out_w: int, out_h: int
) -> List[str]:
    """Return the final-render ffmpeg command with the logo/tag overlay added."""
    if not branding or not (branding.get("logo_path") or branding.get("text")):
        return command
    cmd = list(command)
    try:
        first_input = cmd.index("-i")
    except ValueError:
        return command

    logo_path = branding.get("logo_path")
    logo_input = None
    if logo_path and Path(logo_path).is_file():
        cmd[first_input + 2:first_input + 2] = ["-loop", "1", "-i", str(logo_path)]
        logo_input = 1
    elif not branding.get("text"):
        return command

    chain = _branding_chain(branding, out_w, out_h, logo_input)
    if "-vf" in cmd:
        index = cmd.index("-vf")
        graph = f"[0:v]{cmd[index + 1]}[bbase];{chain}"
        cmd[index:index + 2] = ["-filter_complex", graph, "-map", "[bout]", "-map", "0:a?"]
        return cmd
    if "-filter_complex" in cmd:
        index = cmd.index("-filter_complex")
        video_label = next(
            (cmd[i + 1] for i in range(len(cmd) - 1) if cmd[i] == "-map" and cmd[i + 1].startswith("[")),
            None,
        )
        if not video_label:
            logger.warning("Branding skipped: no mapped video label in the final render")
            return command
        cmd[index + 1] = f"{cmd[index + 1]};{video_label}null[bbase];{chain}"
        map_index = next(i for i in range(len(cmd) - 1) if cmd[i] == "-map" and cmd[i + 1] == video_label)
        cmd[map_index + 1] = "[bout]"
        return cmd
    # A plain copy/encode without filters: give it a filter graph of its own.
    output_index = len(cmd) - 1
    cmd[output_index:output_index] = [
        "-filter_complex", f"[0:v]null[bbase];{chain}", "-map", "[bout]", "-map", "0:a?",
    ]
    return cmd
