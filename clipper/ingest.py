"""Get a source into a work folder: source.mp4, source.info.json, chat (if any), audio wavs.

Accepts a YouTube / Twitch / Kick / any yt-dlp URL, or a local file. A stream that is still
live is recorded and clipped as it runs by clipper/live.py instead.
"""
from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

from .common import FFMPEG, WORK, log, read_json, write_json


def ytdlp(*args) -> list[str]:
    return [sys.executable, "-m", "yt_dlp", *args]


def work_dir_for(src: str) -> Path:
    if re.fullmatch(r"[\w-]{6,}", src) and (WORK / src).is_dir():
        return WORK / src                      # an existing work id: re-run its cached stages
    if Path(src).exists():
        key = Path(src).stem[:40] + "_" + hashlib.sha1(str(Path(src).resolve()).encode()).hexdigest()[:6]
    else:
        m = re.search(r"(?:v=|youtu\.be/|/videos/|/live/|/shorts/)([\w-]{6,})", src)
        key = m.group(1) if m else hashlib.sha1(src.encode()).hexdigest()[:10]
    d = WORK / re.sub(r"[^\w.-]", "_", key)
    d.mkdir(parents=True, exist_ok=True)
    return d


def fetch(src: str, work: Path, max_height: int = 1080, chat: bool = True) -> Path:
    video = work / "source.mp4"
    if video.exists():
        return video
    if Path(src).exists():
        log(f"local file -> {video}")
        shutil.copy2(src, video)
        write_json(work / "source.info.json", {"title": Path(src).stem, "uploader": "", "local": True})
        return video
    log(f"downloading {src}")
    # H.264 first: YouTube's default pick at 1080p60 is AV1, which decodes in software ~10x
    # slower and makes every signal and render pass crawl on a laptop.
    args = ["-f", f"bv*[vcodec^=avc1][height<={max_height}]+ba[ext=m4a]/"
                  f"bv*[height<={max_height}][ext=mp4]+ba[ext=m4a]/b[height<={max_height}]",
            "--merge-output-format", "mp4", "-o", str(work / "source.%(ext)s"),
            "--write-info-json", "--no-playlist"]
    if chat:
        args += ["--write-subs", "--sub-langs", "live_chat,rechat"]
    subprocess.run(ytdlp(*args, src), check=True)
    for f in work.glob("source.*.json"):
        if f.name.endswith(".live_chat.json") or f.name.endswith(".rechat.json"):
            f.rename(work / "chat.live_chat.json")
    return video


def audio(work: Path) -> None:
    for sr, name in ((16000, "audio16k.wav"), (32000, "audio32k.wav")):
        p = work / name
        if not p.exists():
            subprocess.run([FFMPEG, "-v", "error", "-y", "-i", str(work / "source.mp4"), "-vn",
                            "-ac", "1", "-ar", str(sr), str(p)], check=True)


def info(work: Path) -> dict:
    return read_json(work / "source.info.json", {}) or {}
