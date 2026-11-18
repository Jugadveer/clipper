"""Contact sheets of rendered clips, for reviewing output by eye before upload.

  py -m clipper.review work/<id>/clips            -> work/<id>/review/sheet_NN.jpg
Each row is one clip: frames at 0.5 s, 2 s, 25%, 50%, 75% and the last second.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

from .common import FFMPEG, ffprobe_json


def frame_at(mp4: Path, t: float, w: int = 216) -> np.ndarray:
    out = subprocess.run([FFMPEG, "-v", "error", "-ss", f"{t:.2f}", "-i", str(mp4), "-frames:v", "1",
                          "-vf", f"scale={w}:-2", "-f", "image2pipe", "-vcodec", "png", "-"],
                         capture_output=True, check=True).stdout
    return cv2.imdecode(np.frombuffer(out, np.uint8), cv2.IMREAD_COLOR)


def sheets(clips_dir: Path, per_sheet: int = 5) -> list[Path]:
    clips = sorted(p for p in clips_dir.glob("[0-9][0-9]_*.mp4"))
    out_dir = clips_dir.parent / "review"
    out_dir.mkdir(exist_ok=True)
    rows = []
    for mp4 in clips:
        d = float(ffprobe_json(mp4)["format"]["duration"])
        fr = [frame_at(mp4, t) for t in (0.5, 2.0, d * 0.25, d * 0.5, d * 0.75, max(0, d - 1))]
        row = np.hstack(fr)
        cv2.putText(row, f"{mp4.name[:40]}  {d:.0f}s", (6, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                    (0, 255, 255), 2, cv2.LINE_AA)
        rows.append(row)
    paths = []
    for i in range(0, len(rows), per_sheet):
        p = out_dir / f"sheet_{i // per_sheet + 1:02d}.jpg"
        cv2.imwrite(str(p), np.vstack(rows[i:i + per_sheet]), [cv2.IMWRITE_JPEG_QUALITY, 85])
        paths.append(p)
    return paths


def grid(clips_dir: Path, per_sheet: int = 10) -> list[Path]:
    """Compact sheets: 5 frames per clip (1 s, 30%, 55%, 80%, last), two clips per row."""
    import json
    clips = json.loads((clips_dir / "clips.json").read_text(encoding="utf-8"))
    rows = []
    for c in clips:
        f = clips_dir / c["file"]
        d = float(ffprobe_json(f)["format"]["duration"])
        row = np.hstack([cv2.resize(frame_at(f, t, 160), (128, 228)) for t in (1.0, d * .3, d * .55, d * .8, d - .5)])
        cv2.putText(row, f"{c['file'][:2]} {c.get('style')} {c.get('layout')} {d:.0f}s", (3, 14),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 255, 255), 1, cv2.LINE_AA)
        rows.append(row)
    out_dir = clips_dir.parent / "review"
    out_dir.mkdir(exist_ok=True)
    paths = []
    for k in range(0, len(rows), per_sheet):
        blk = rows[k:k + per_sheet]
        if len(blk) % 2:
            blk.append(np.zeros_like(rows[0]))
        sheet = np.vstack([np.hstack(blk[i:i + 2]) for i in range(0, len(blk), 2)])
        p = out_dir / f"grid_{k // per_sheet + 1}.jpg"
        cv2.imwrite(str(p), sheet, [cv2.IMWRITE_JPEG_QUALITY, 85])
        paths.append(p)
    return paths


if __name__ == "__main__":
    fn = grid if "--grid" in sys.argv else sheets
    for p in fn(Path([a for a in sys.argv[1:] if not a.startswith("--")][0])):
        print(p)
