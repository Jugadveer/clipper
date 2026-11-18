"""Edit decision list -> HyperFrames project -> finished mp4.

The EDL is one dict (see README.md in this folder). Video clips become timed <video> elements the
renderer seeks itself; everything else (captions, overlays, scenes, grounds, transitions, grade) is
drawn by web/runtime.js as a pure function of time. One premixed audio track carries voice, music
and sound effects, so the browser never mixes anything.
"""
from __future__ import annotations

import html
import json
import os
import shutil
import subprocess
import threading
from pathlib import Path

WEB = Path(__file__).resolve().parent / "web"
HF = [shutil.which("npx") or "npx", "--yes", "hyperframes@0.8.43"]
# one browser render at a time: HyperFrames already spreads a render over the cores, and the
# clipper renders several clips in parallel threads
_RENDER = threading.Semaphore(int(os.environ.get("ENGINE_RENDERS", "1")))


def _place(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    try:
        os.link(src, dst)                     # same drive: free and instant
    except OSError:
        shutil.copy2(src, dst)


def _emoji_assets(edl: dict, out_dir: Path) -> None:
    """Every emoji the plan uses becomes a local Apple PNG (stickers, coin icons, hero objects)."""
    from . import emoji as E

    def img(e: str) -> str | None:
        p = E.png(e) if E.is_emoji(e) else None
        if not p:
            return None
        rel = f"assets/emoji/{p.name}"
        _place(p, out_dir / rel)
        return rel

    for o in edl.get("overlays", []) + edl.get("scenes", []):
        p = o.get("p") or {}
        if o["type"] == "sticker" and p.get("text"):
            p["textImg"] = img(p["text"])
        if o["type"] == "object":
            p["img"] = img(p.get("emoji", ""))
        if o["type"] == "coins":
            for it in p.get("items", []):
                it["iconImg"] = img(it.get("icon", ""))
    edl["scenes"] = [o for o in edl.get("scenes", []) if o["type"] != "object" or o["p"].get("img")]
    edl["overlays"] = [o for o in edl.get("overlays", []) if o["type"] != "object" or o["p"].get("img")]


def build_project(edl: dict, out_dir: Path, assets: dict[str, Path]) -> Path:
    """Write out_dir/index.html (+ web files and assets). `assets` maps the EDL's src keys to files."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for f in WEB.rglob("*"):
        if f.is_file():
            _place(f, out_dir / f.relative_to(WEB))
    names = {}
    for key, p in assets.items():
        p = Path(p)
        names[key] = f"assets/{key}{p.suffix.lower()}"
        _place(p, out_dir / names[key])
    _emoji_assets(edl, out_dir)
    w, h, dur = edl["w"], edl["h"], round(edl["dur"], 3)
    vids = []
    for c in edl.get("clips", []):
        src = names[c["src"]]
        common = (f'src="{src}" data-start="{c["s"]:.3f}" data-duration="{c["e"] - c["s"]:.3f}" '
                  f'data-media-start="{c.get("in", 0):.3f}" muted playsinline')
        if c.get("fill") == "blur":            # a blurred copy of the clip fills the frame behind a band
            c["bg"] = c["id"] + "_bg"
            vids.append(f'<video id="{c["bg"]}" class="bgfill" {common} data-track-index="1"></video>')
        cls = "band" if c.get("fill") == "blur" else ""
        vids.append(f'<video id="{c["id"]}" class="{cls}" {common} data-track-index="2"></video>')
    audio = ""
    if edl.get("audio"):
        audio = (f'<audio id="mix" src="{names[edl["audio"]]}" data-start="0" data-duration="{dur}" '
                 f'data-track-index="10"></audio>')
    spec = json.dumps(edl, ensure_ascii=False).replace("</", "<\\/")
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width={w}, height={h}">
<title>short</title><link rel="stylesheet" href="base.css"></head>
<body>
<div id="root" data-composition-id="main" data-no-timeline data-start="0" data-width="{w}" data-height="{h}" data-duration="{dur}"
     style="--accent:{html.escape(edl.get('theme', {}).get('accent', '#ffd400'))}">
  <div id="stage"><div id="media">
    {chr(10).join(vids)}
  </div></div>
  {audio}
</div>
<script src="main.js"></script>
</body></html>
"""
    # spec + runtime in ONE generated script: the renderer does not run a separate inline
    # assignment before an external script reliably (snapshots did, renders came out black)
    main = out_dir / "main.js"
    if main.exists():
        main.unlink()
    main.write_text("window.__SPEC = " + spec + ";\n" + (WEB / "runtime.js").read_text(encoding="utf-8"), encoding="utf-8")
    (out_dir / "index.html").write_text(page, encoding="utf-8")
    (out_dir / "hyperframes.json").write_text(json.dumps({"name": out_dir.name}), encoding="utf-8")
    return out_dir


def render(project: Path, out_mp4: Path, quiet: bool = True) -> Path:
    project, out_mp4 = Path(project).resolve(), Path(out_mp4).resolve()
    raw = out_mp4.with_suffix(".raw.mp4")
    cmd = HF + ["render", str(project), "-o", str(raw)] + (["--quiet"] if quiet else [])
    with _RENDER:
        subprocess.run(cmd, check=True, cwd=str(project))
    # the renderer's AAC lands ~1 LU low; bring it to -14 LUFS with the picture untouched
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(raw), "-c:v", "copy", "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
                    "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", str(out_mp4)], check=True)
    raw.unlink(missing_ok=True)
    return out_mp4


def snapshot(project: Path, times: list[float]) -> Path:
    subprocess.run(HF + ["snapshot", str(project), "--at", ",".join(f"{t:.2f}" for t in times), "--no-end",
                         "--describe", "false", "--timeout", "30000"], check=True, cwd=str(project), capture_output=True)
    return Path(project) / "snapshots"
