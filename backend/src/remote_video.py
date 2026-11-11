"""Facebook video links (posts, reels, past live streams) as clip sources.

A Facebook link is stored like an uploaded video (source type ``video_url``).
The first time its file is needed, yt-dlp downloads it into the uploads folder
under a name derived from the link, and later uses (regenerating clips, the
editor) reuse that file. Public videos work as-is; private or group videos need
a Netscape cookies file at FACEBOOK_COOKIES_FILE.
"""

import hashlib
import logging
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import yt_dlp

from .config import get_config

logger = logging.getLogger(__name__)

FACEBOOK_HOSTS = ("facebook.com", "fb.watch", "fb.com")
INFO_TIMEOUT_SECONDS = 30


def is_facebook_url(url: str) -> bool:
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    return parsed.scheme in {"http", "https"} and any(
        host == domain or host.endswith(f".{domain}") for domain in FACEBOOK_HOSTS
    )


def _base_options() -> Dict[str, Any]:
    opts: Dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": INFO_TIMEOUT_SECONDS,
    }
    cookies = get_config().facebook_cookies_file
    if cookies and Path(cookies).is_file():
        opts["cookiefile"] = cookies
    return opts


def get_remote_video_info(url: str) -> Dict[str, Any]:
    """Title and duration (seconds) of a Facebook video, without downloading it."""
    with yt_dlp.YoutubeDL({**_base_options(), "skip_download": True}) as ydl:
        info = ydl.extract_info(url, download=False) or {}
    if info.get("_type") == "playlist" and info.get("entries"):
        info = next((entry for entry in info["entries"] if entry), info)
    return {"title": info.get("title"), "duration": info.get("duration")}


def remote_video_path(url: str) -> Path:
    digest = hashlib.sha1(url.strip().encode("utf-8")).hexdigest()[:16]
    return Path(get_config().temp_dir) / "uploads" / f"facebook-{digest}.mp4"


def download_remote_video(url: str) -> Path:
    """Download (once) and return the local file for a Facebook video link."""
    target = remote_video_path(url)
    if target.is_file() and target.stat().st_size > 0:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    opts = {
        **_base_options(),
        "outtmpl": str(target.with_suffix(".%(ext)s")),
        "format": "bestvideo*+bestaudio/best",
        "merge_output_format": "mp4",
        "retries": 5,
        "fragment_retries": 10,
        "overwrites": True,
    }
    logger.info("Downloading Facebook video %s", url)
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    if not target.is_file():
        # Single-file formats can keep their own extension; normalise to .mp4.
        produced = next(
            (path for path in target.parent.glob(f"{target.stem}.*") if path.suffix != ".part"),
            None,
        )
        if produced is None:
            raise RuntimeError("Facebook download produced no video file")
        produced.replace(target)
    return target
