"""Background music and sound effects for finished clips.

Sounds come from two places:

- AUDIO_LIBRARY_DIR/music and AUDIO_LIBRARY_DIR/sfx: audio files the user adds
  (for example Pixabay downloads, whose file names describe the sound).
- Freesound.org, when FREESOUND_API_KEY is set. Only CC0 sounds are used, so
  clips need no attribution. Previews are cached under AUDIO_LIBRARY_DIR/.cache.

The configured LLM reads the clip's words and picks a music style and a few
effect moments. ffmpeg then mixes them under the voice (music is ducked while
someone speaks) and copies the video stream untouched.
"""

import hashlib
import json
import logging
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from pydantic import BaseModel, Field

from .config import get_config
from .media.ffmpeg import ffprobe_duration, ffprobe_has_audio, run_ffmpeg_command
from .utils.async_helpers import run_in_thread

logger = logging.getLogger(__name__)

AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}
FREESOUND_SEARCH_URL = "https://freesound.org/apiv2/search/text/"
FREESOUND_CC0_FILTER = 'license:"Creative Commons 0"'
MAX_EFFECTS = 4
# Effects closer together than this sound cluttered, so later ones are dropped.
MIN_EFFECT_GAP_SECONDS = 4.0
# Gain limits for the loudness rule, so an odd measurement can never blast audio.
MAX_BOOST_DB = 6.0
MIN_GAIN_DB = -45.0
SILENT_LUFS = -60.0


class SoundCue(BaseModel):
    word_index: int = Field(ge=0, description="Index of the word the sound lands on")
    sound: str = Field(description="1-3 word sound search phrase, e.g. 'whoosh' or 'deep impact'")


class SoundPlan(BaseModel):
    music_keywords: str = Field(
        description="2-4 words describing instrumental background music, e.g. 'calm piano worship'"
    )
    effects: List[SoundCue] = Field(default_factory=list)


SOUND_PLAN_SYSTEM_PROMPT = f"""You are a sound designer for short vertical videos (Shorts, Reels, TikTok).
You receive the spoken words of one clip, each prefixed by its index.

Choose:
1. music_keywords: 2-4 plain words describing INSTRUMENTAL background music that fits the clip's mood
   and genre, the way a stock-music search would be phrased. Examples: "calm piano worship",
   "uplifting cinematic strings", "soft ambient pad", "upbeat corporate", "dark suspense",
   "emotional piano", "motivational epic". Never ask for vocals.
2. effects: at most {MAX_EFFECTS} sound effects, each on a word_index from the list.
   - Use them sparingly, only where they add punch: the opening hook (word 0, e.g. "whoosh"),
     a key reveal or punchline ("impact", "boom"), a list point ("ding", "pop"), or a topic turn ("swoosh").
   - Use common, searchable names: whoosh, swoosh, impact, boom, ding, pop, click, riser,
     shimmer, bell, camera shutter, cash register, applause.
   - Match the tone. For serious, emotional or religious speech prefer subtle sounds
     (shimmer, soft whoosh, bell, riser) and never comedic ones.
   - Keep effects at least a few seconds apart. An empty list is fine for a calm clip.

Return JSON only."""


def _tokens(text: str) -> set[str]:
    return {
        token
        for token in re.split(r"[^a-z]+", text.lower())
        if len(token) > 2 and token not in {"the", "and", "for", "with", "music", "sound"}
    }


def _audio_files(folder: Path) -> List[Path]:
    if not folder.is_dir():
        return []
    return sorted(
        path
        for path in folder.rglob("*")
        if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS and ".cache" not in path.parts
    )


def find_local_audio(kind: str, query: str, seed: str) -> Tuple[Optional[Path], int]:
    """Best file in the user's music/ or sfx/ folder by file-name overlap with the query.

    Returns the file and how many query words its name matched (0 = no match).
    """
    files = _audio_files(Path(get_config().audio_library_dir) / kind)
    if not files:
        return None, 0
    wanted = _tokens(query)

    def rank(path: Path) -> Tuple[int, str]:
        overlap = len(wanted & _tokens(path.stem))
        # Stable tie-break per clip so equal matches still vary between clips.
        return overlap, hashlib.md5(f"{seed}{path.name}".encode()).hexdigest()

    best = max(files, key=rank)
    return best, rank(best)[0]


def search_freesound(kind: str, query: str) -> Optional[Dict[str, Any]]:
    """Most-downloaded CC0 match on Freesound, or None."""
    api_key = get_config().freesound_api_key
    if not api_key or not query.strip():
        return None
    duration = "duration:[30 TO 900]" if kind == "music" else "duration:[0.1 TO 4]"
    queries = [query, " ".join(query.split()[:2])] if kind == "music" else [query]
    for text in dict.fromkeys(queries):
        try:
            response = requests.get(
                FREESOUND_SEARCH_URL,
                params={
                    "query": text,
                    "filter": f"{FREESOUND_CC0_FILTER} {duration}",
                    "fields": "id,name,duration,previews,license,username,num_downloads",
                    "page_size": 12,
                    "token": api_key,
                },
                timeout=20,
            )
            response.raise_for_status()
            results = response.json().get("results") or []
        except Exception as exc:
            logger.warning("Freesound search failed for %r: %s", text, exc)
            return None
        if results:
            return max(results, key=lambda item: item.get("num_downloads") or 0)
    return None


def download_freesound_preview(sound: Dict[str, Any]) -> Optional[Path]:
    preview_url = (sound.get("previews") or {}).get("preview-hq-mp3")
    if not preview_url:
        return None
    cache_dir = Path(get_config().audio_library_dir) / ".cache" / "freesound"
    target = cache_dir / f"{int(sound['id'])}.mp3"
    if target.is_file() and target.stat().st_size > 0:
        return target
    cache_dir.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".part")
    try:
        with requests.get(preview_url, stream=True, timeout=60) as response:
            response.raise_for_status()
            with partial.open("wb") as handle:
                shutil.copyfileobj(response.raw, handle)
        partial.replace(target)
        return target
    except Exception as exc:
        logger.warning("Freesound download failed for %s: %s", sound.get("id"), exc)
        partial.unlink(missing_ok=True)
        return None


def resolve_sound(kind: str, query: str, seed: str) -> Optional[Dict[str, Any]]:
    """Pick a file: a matching local file first, then Freesound, then any local music."""
    local_path, overlap = find_local_audio(kind, query, seed)
    if local_path and overlap > 0:
        return {"path": local_path, "source": "local", "name": local_path.name}
    sound = search_freesound(kind, query)
    if sound:
        path = download_freesound_preview(sound)
        if path:
            return {
                "path": path,
                "source": "freesound",
                "name": sound.get("name"),
                "url": f"https://freesound.org/s/{sound['id']}/",
                "author": sound.get("username"),
                "license": "CC0",
            }
    # The user put music in the folder on purpose; use it even without a name match.
    if kind == "music" and local_path:
        return {"path": local_path, "source": "local", "name": local_path.name}
    return None


async def plan_sound(words: List[Dict[str, Any]], hook_title: Optional[str]) -> SoundPlan:
    from .ai import run_structured_prompt

    numbered = " ".join(f"{index}:{word.get('text', '')}" for index, word in enumerate(words))
    prompt = (
        (f"Clip headline: {hook_title}\n" if hook_title else "")
        + f"Clip words ({len(words)} words, indices 0-{max(0, len(words) - 1)}):\n{numbered}"
    )
    return await run_structured_prompt(prompt, SoundPlan, SOUND_PLAN_SYSTEM_PROMPT)


def _effect_times(plan: SoundPlan, words: List[Dict[str, Any]], duration: float) -> List[Tuple[float, str]]:
    cues: List[Tuple[float, str]] = []
    for cue in plan.effects:
        if cue.word_index >= len(words) or not cue.sound.strip():
            continue
        start = max(0.0, float(words[cue.word_index].get("start", 0.0)))
        # Land slightly before the word so the hit lines up with it.
        start = max(0.0, start - 0.08)
        if start >= duration - 0.5:
            continue
        if any(abs(start - existing) < MIN_EFFECT_GAP_SECONDS for existing, _ in cues):
            continue
        cues.append((start, cue.sound.strip()))
        if len(cues) >= MAX_EFFECTS:
            break
    return sorted(cues)


def measure_lufs(path: Path, seconds: Optional[float] = None) -> Optional[float]:
    """Integrated loudness (LUFS) of a file's audio, or None if it cannot be measured."""
    command = ["ffmpeg", "-nostats", "-hide_banner"]
    if seconds:
        command += ["-t", f"{seconds:.1f}"]
    command += ["-i", str(path), "-map", "0:a:0", "-af", "ebur128=framelog=quiet", "-f", "null", "-"]
    result = run_ffmpeg_command(command, timeout=300)
    matches = re.findall(r"\bI:\s+(-?\d+(?:\.\d+)?)\s+LUFS", result.stderr or "")
    if result.returncode != 0 or not matches:
        return None
    value = float(matches[-1])
    return value if value > SILENT_LUFS else None


def _gain_below_voice(source_lufs: Optional[float], voice_lufs: Optional[float], below_db: float) -> Optional[float]:
    """Linear gain that puts a sound ``below_db`` under the voice, or None if unmeasured."""
    if source_lufs is None or voice_lufs is None:
        return None
    gain_db = (voice_lufs - below_db) - source_lufs
    gain_db = max(MIN_GAIN_DB, min(MAX_BOOST_DB, gain_db))
    return round(10 ** (gain_db / 20), 5)


def mix_clip_audio(
    clip_path: Path,
    music_path: Optional[Path],
    effects: List[Tuple[float, Path]],
) -> bool:
    """Mix music (looped, ducked under speech) and effects into the clip in place.

    Loudness rule: the speaker's loudness is measured, then music is set
    MUSIC_BELOW_VOICE_DB under it and effects SFX_BELOW_VOICE_DB under it,
    whatever the source files' own levels. Music is ducked further while
    someone talks. Without a measurable voice the fixed fallback volumes apply.
    """
    config = get_config()
    duration = ffprobe_duration(clip_path)
    if duration <= 0 or (not music_path and not effects):
        return False
    has_voice = ffprobe_has_audio(clip_path)
    voice_lufs = measure_lufs(clip_path) if has_voice else None
    music_gain = config.music_volume
    if music_path:
        music_gain = (
            _gain_below_voice(measure_lufs(music_path, 120), voice_lufs, config.music_below_voice_db)
            or config.music_volume
        )
    logger.info(
        "Sound levels for %s: voice %s LUFS, music gain %.4f (%s dB under voice)",
        clip_path.name, voice_lufs, music_gain, config.music_below_voice_db,
    )
    fmt = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"

    inputs = ["-i", str(clip_path)]
    filters: List[str] = []
    mix_labels: List[str] = []
    next_input = 1

    if has_voice:
        if music_path:
            filters.append(f"[0:a]{fmt},asplit=2[voice][key]")
        else:
            filters.append(f"[0:a]{fmt}[voice]")
        mix_labels.append("[voice]")

    if music_path:
        inputs += ["-stream_loop", "-1", "-i", str(music_path)]
        fade_out = max(0.0, duration - 2.0)
        filters.append(
            f"[{next_input}:a]{fmt},atrim=0:{duration:.3f},asetpts=PTS-STARTPTS,"
            f"volume={music_gain},afade=t=in:d=1.5,afade=t=out:st={fade_out:.3f}:d=2[music]"
        )
        if has_voice:
            # Duck the music further while someone is speaking.
            filters.append(
                "[music][key]sidechaincompress=threshold=0.015:ratio=12:attack=20:release=600[ducked]"
            )
            mix_labels.append("[ducked]")
        else:
            mix_labels.append("[music]")
        next_input += 1

    for index, (start, path) in enumerate(effects):
        inputs += ["-i", str(path)]
        delay_ms = int(round(start * 1000))
        sfx_gain = (
            _gain_below_voice(measure_lufs(path), voice_lufs, config.sfx_below_voice_db)
            or config.sfx_volume
        )
        filters.append(
            f"[{next_input}:a]{fmt},atrim=0:4,afade=t=out:st=3.4:d=0.6,"
            f"volume={sfx_gain},adelay={delay_ms}|{delay_ms}[fx{index}]"
        )
        mix_labels.append(f"[fx{index}]")
        next_input += 1

    filters.append(
        f"{''.join(mix_labels)}amix=inputs={len(mix_labels)}:duration=first:normalize=0,"
        f"atrim=0:{duration:.3f},alimiter=limit=0.95[aout]"
        if has_voice
        else f"{''.join(mix_labels)}amix=inputs={len(mix_labels)}:duration=longest:normalize=0,"
        f"atrim=0:{duration:.3f},alimiter=limit=0.95[aout]"
    )

    mixed_path = clip_path.with_name(f"{clip_path.stem}.sound{clip_path.suffix}")
    command = [
        "ffmpeg", "-y", *inputs,
        "-filter_complex", ";".join(filters),
        "-map", "0:v", "-map", "[aout]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-movflags", "+faststart",
        str(mixed_path),
    ]
    result = run_ffmpeg_command(command, timeout=600)
    if result.returncode != 0 or not mixed_path.is_file():
        logger.warning("Sound mix failed for %s: %s", clip_path.name, (result.stderr or "")[-500:])
        mixed_path.unlink(missing_ok=True)
        return False
    mixed_path.replace(clip_path)
    return True


async def apply_sound_design(
    clip_path: Path,
    words: List[Dict[str, Any]],
    hook_title: Optional[str],
    music: bool,
    effects: bool,
) -> Optional[Dict[str, Any]]:
    """Choose, fetch and mix music/effects into one finished clip.

    Returns a summary of the sounds used (also saved next to the clip as
    ``<clip>.sound.json``), or None when nothing was added.
    """
    if not (music or effects) or not words:
        return None
    plan = await plan_sound(words, hook_title)
    duration = await run_in_thread(ffprobe_duration, clip_path)
    seed = clip_path.stem

    music_sound = (
        await run_in_thread(resolve_sound, "music", plan.music_keywords, seed) if music else None
    )
    effect_sounds: List[Tuple[float, Dict[str, Any]]] = []
    if effects:
        for start, query in _effect_times(plan, words, duration):
            sound = await run_in_thread(resolve_sound, "sfx", query, f"{seed}{start}")
            if sound:
                effect_sounds.append((start, sound))

    if not music_sound and not effect_sounds:
        logger.info(
            "No sounds found for %s (music %r); add files to %s or set FREESOUND_API_KEY",
            clip_path.name, plan.music_keywords, get_config().audio_library_dir,
        )
        return None

    mixed = await run_in_thread(
        mix_clip_audio,
        clip_path,
        music_sound["path"] if music_sound else None,
        [(start, sound["path"]) for start, sound in effect_sounds],
    )
    if not mixed:
        return None

    def describe(sound: Dict[str, Any]) -> Dict[str, Any]:
        return {key: str(value) for key, value in sound.items() if key != "path"}

    summary = {
        "music_keywords": plan.music_keywords,
        "music": describe(music_sound) if music_sound else None,
        "effects": [
            {"time": round(start, 2), **describe(sound)} for start, sound in effect_sounds
        ],
    }
    clip_path.with_suffix(".sound.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    logger.info(
        "Added sound to %s: music=%s, %d effects",
        clip_path.name,
        summary["music"]["name"] if summary["music"] else None,
        len(effect_sounds),
    )
    return summary
