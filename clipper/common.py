"""Paths, subprocess helpers and small utilities shared by every stage."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

PKG = Path(__file__).resolve().parent
ROOT = PKG.parent                      # D:\BOT\clipper
ASSETS = ROOT / "assets"
FONTS = ASSETS / "fonts"
MODELS = ASSETS / "models"
WORK = ROOT / "work"
REPO = ROOT.parent                     # D:\BOT (for .env and the YOLO folder)

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or "ffprobe"


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, **kw)


def ffprobe_json(path: Path) -> dict:
    out = subprocess.run([FFPROBE, "-v", "error", "-print_format", "json",
                          "-show_format", "-show_streams", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def read_json(p: Path, default=None):
    p = Path(p)
    if not p.exists():
        return default
    return json.loads(p.read_text(encoding="utf-8"))


def write_json(p: Path, obj) -> None:
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def load_env() -> dict:
    """Read KEY=VALUE pairs from the repo's .env without touching os.environ."""
    env = {}
    for f in (ROOT / ".env", REPO / ".env"):
        if not f.exists():
            continue
        for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    for k, v in os.environ.items():
        env[k] = v
    return env


def fmt_ts(sec: float) -> str:
    sec = max(0, int(sec))
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def python_exe() -> str:
    return sys.executable
