"""Render a preview image for every caption template.

Uses the real caption renderer, so each preview shows exactly what a clip gets.
Run inside the worker container and copy the output into the frontend:

    docker compose exec -T worker sh -c 'cd /app && .venv/bin/python -' < backend/scripts/render_caption_previews.py
    docker cp supoclip-worker:/tmp/caption-previews/. frontend/public/caption-previews/
"""

import subprocess
from pathlib import Path

from src.caption_templates import CAPTION_TEMPLATES, get_template
from src.media.captions import (
    ass_fonts_dir,
    build_assemblyai_ass_subtitles,
    get_safe_vertical_position,
    get_scaled_font_size,
)
from src.media.ffmpeg import subtitles_filter_fragment

TEXT = "When you know your authority in Christ you stand in fullness not lack"
W, H, STEP = 1080, 1920, 0.35
SNAP = 1.62  # mid "authority" (1.40-1.72 s), after Blur Word sharpens at 1.58 s
BAND_W, BAND_H = 840, 360  # the frame area around the captions
OUT_W, OUT_H = 280, 120

out = Path("/tmp/caption-previews")
out.mkdir(parents=True, exist_ok=True)
words = [
    {"text": w, "start": round(i * STEP, 3), "end": round(i * STEP + STEP - 0.03, 3)}
    for i, w in enumerate(TEXT.split())
]
background = out / "background.mp4"
subprocess.run(
    ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
     f"gradients=s={W}x{H}:c0=0x2a3550:c1=0x6e4a36:c2=0x3b2f45:x0=0:y0=0:x1={W}:y1={H}:n=3:d=4:r=30",
     "-t", "4", "-pix_fmt", "yuv420p", str(background)],
    check=True,
)

for template_id in CAPTION_TEMPLATES:
    template = get_template(template_id)
    ass = out / f"{template_id}.ass"
    if not build_assemblyai_ass_subtitles(
        background, 0, 4, W, H, ass, caption_template=template_id, caption_words=words
    ):
        print("skipped", template_id)
        continue
    font_px = get_scaled_font_size(int(template["font_size"]), W)
    center_y = get_safe_vertical_position(H, int(font_px * 1.5), float(template.get("position_y", 0.8)))
    top = max(0, min(H - BAND_H, center_y - BAND_H // 2))
    vf = (
        subtitles_filter_fragment(ass, ass_fonts_dir(template["font_family"]))
        + f",crop={BAND_W}:{BAND_H}:{(W - BAND_W) // 2}:{top},scale={OUT_W}:{OUT_H}:flags=lanczos"
    )
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(background), "-ss", str(SNAP), "-vf", vf,
         "-frames:v", "1", "-q:v", "3", str(out / f"{template_id}.jpg")],
        check=True,
    )
    ass.unlink(missing_ok=True)
    print("rendered", template_id)
background.unlink(missing_ok=True)
