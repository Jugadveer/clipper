"""Render one selected moment into a finished vertical short.

Python composes each 1080x1920 frame (layout + face tracking, then camera moves from fx.py) and pipes raw video
into ffmpeg, which burns the ASS captions, normalises loudness to -14 LUFS (the Shorts/TikTok
target), and encodes H.264 (NVENC when present) at 30 fps.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import numpy as np

from . import captions, reframe
from .common import FFMPEG, FONTS, log

_NVENC = None


def nvenc() -> bool:
    global _NVENC
    if _NVENC is None:
        try:
            subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.1",
                            "-c:v", "h264_nvenc", "-f", "null", "-"], check=True, capture_output=True)
            _NVENC = True
        except Exception:
            _NVENC = False
    return _NVENC


_HWDEC = None


def hwdec() -> list[str]:
    """['-hwaccel', 'cuda'] when NVDEC works: decoding 1080p60 on the CPU is the render's
    biggest cost, and the GPU decoder is otherwise idle."""
    global _HWDEC
    if _HWDEC is None:
        try:
            subprocess.run([FFMPEG, "-v", "error", "-hwaccel", "cuda", "-f", "lavfi", "-i",
                            "testsrc=s=320x240:d=0.2", "-f", "null", "-"], check=True, capture_output=True)
            _HWDEC = ["-hwaccel", "cuda"]
        except Exception:
            _HWDEC = []
    return _HWDEC


def snap(words_doc: dict, start: float, end: float) -> tuple[float, float]:
    """Move cut points onto speech boundaries: start just before a word that follows a pause
    (or a segment start), end just after a word, so no clip opens or closes mid-word."""
    ws = [w for seg in words_doc.get("segments", []) for w in seg["words"]]
    if not ws:
        return start, end
    near = [w for w in ws if start - 2.0 <= w["s"] <= start + 1.5]
    best_s = start
    if near:
        def gap_before(w):
            i = ws.index(w)
            return w["s"] - ws[i - 1]["e"] if i else 9
        cand = sorted(near, key=lambda w: (-(gap_before(w) > 0.25), abs(w["s"] - start)))
        best_s = cand[0]["s"] - 0.15
    tail = [w for w in ws if end - 2.0 <= w["e"] <= end + 1.5]
    best_e = end
    if tail:
        def gap_after(w):
            i = ws.index(w)
            return ws[i + 1]["s"] - w["e"] if i + 1 < len(ws) else 9
        cand = sorted(tail, key=lambda w: (-(gap_after(w) > 0.3), abs(w["e"] - end)))
        # never cut a word off; never cut short a reaction the judge chose to keep
        best_e = max(cand[0]["e"] + 0.45, end)
    return max(0.0, best_s), best_e


def punch_times(sig_parts: dict, start: float, end: float, k: int = 2) -> list[float]:
    """Source times of the biggest audio spikes (screams) for punch-in zooms."""
    keys = [k2 for k2 in ("scream", "loud") if k2 in sig_parts]
    if not keys:
        return []
    a, b = int(start), int(end)
    curve = np.sum([sig_parts[k2][a:b + 1] for k2 in keys], axis=0)
    out = []
    for t in np.argsort(-curve):
        if curve[t] < 3.0:
            break
        if all(abs(t - o) > 6 for o in out):
            out.append(int(t))
        if len(out) >= k:
            break
    return sorted(a + float(t) for t in out)


def dead_air(words_doc: dict, sig_parts: dict, start: float, end: float,
             min_gap: float = 1.3, pad: float = 0.3) -> list[tuple[float, float]]:
    """Gaps with no words AND nothing happening in the audio/picture: candidates to jump-cut.
    A silent stare at a jump scare is content, so a gap is only cut when the audio is quiet
    and no audio event or picture change is above its local baseline."""
    ws = [w for seg in words_doc.get("segments", []) for w in seg["words"]
          if start <= w["s"] and w["e"] <= end]
    cuts = []
    prev_e = start
    for w in ws + [{"s": end, "e": end}]:
        gs, ge = prev_e + pad, w["s"] - pad
        if ge - gs >= min_gap - 2 * pad and gs > start + 1.0 and ge < end - 1.0:
            a, b = int(gs), int(ge) + 1
            busy = any(k in sig_parts and float(sig_parts[k][a:b].max()) > 1.2
                       for k in ("loud", "scream", "laugh", "boom", "motion", "chat", "react"))
            if not busy:
                cuts.append((gs, ge))
        prev_e = max(prev_e, w["e"])
    return cuts


def build_segments(start: float, end: float, cold_open: tuple | None = None,
                   cuts: list | None = None) -> list[tuple[float, float]]:
    """Source ranges played in order: [cold-open punchline] + the clip minus dead air."""
    segs, cur = [], start
    for a, b in sorted(cuts or []):
        if a > cur:
            segs.append((cur, a))
        cur = max(cur, b)
    segs.append((cur, end))
    if cold_open:
        segs.insert(0, cold_open)
    return [(a, b) for a, b in segs if b - a > 0.2]


def _to_out(t: float, segments: list) -> float | None:
    """Source time -> output time (None if that moment was cut)."""
    off = 0.0
    for a, b in segments:
        if a <= t <= b:
            return off + t - a
        off += b - a
    return None


def clip_words_out(words_doc: dict, segments: list) -> list[dict]:
    """The clip's words on the OUTPUT timeline (segments played back to back)."""
    words, off = [], 0.0
    for a, b in segments:
        for w in captions.clip_words(words_doc, a, b):
            if w["s"] >= -0.05 and w["e"] <= b - a + 0.05:
                words.append({"t": w["w"], "s": round(w["s"] + off, 3), "e": round(w["e"] + off, 3)})
        off += b - a
    return words


def render_base(video: Path, src_w: int, src_h: int, segments: list, out_mp4: Path, layout: str = "auto",
                split_order: str = "game_top", genre: str = "stream", fps: int = 30) -> dict:
    """Only the vertical reframe (face tracking, layouts), with the original sound: no captions,
    camera moves or end card. The engine (clipper/engine) draws every edit on top of it."""
    video, out_mp4 = Path(video).resolve(), Path(out_mp4).resolve()
    span0, span1 = min(a for a, _ in segments), max(b for _, b in segments)
    plan = reframe.plan_clip(video, span0, span1 - span0, src_w, src_h, layout, split_order, genre)
    main = sum(b - a for a, b in segments)
    venc = (["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "19", "-b:v", "12M", "-profile:v", "high"]
            if nvenc() else ["-c:v", "libx264", "-preset", "medium", "-crf", "18"])
    trims = "".join(f"[1:a]atrim={a - span0:.3f}:{b - span0:.3f},asetpts=PTS-STARTPTS,"
                    f"afade=t=in:d=0.015,afade=t=out:st={max(0, b - a - 0.015):.3f}:d=0.015[a{i}];"
                    for i, (a, b) in enumerate(segments))
    fc = trims + "".join(f"[a{i}]" for i in range(len(segments))) + f"concat=n={len(segments)}:v=0:a=1[aout]"
    enc = subprocess.Popen(
        [FFMPEG, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{reframe.OUT_W}x{reframe.OUT_H}",
         "-r", str(fps), "-i", "-", "-ss", f"{span0:.3f}", "-t", f"{span1 - span0 + 0.5:.3f}", "-i", str(video),
         "-filter_complex", fc, "-map", "0:v", "-map", "[aout]", "-vf", "format=yuv420p", *venc,
         "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-t", f"{main:.3f}", str(out_mp4)],
        stdin=subprocess.PIPE)
    fsz = src_w * src_h * 3
    comp = np.zeros((reframe.OUT_H, reframe.OUT_W, 3), np.uint8)
    try:
        for a, b in segments:
            dec = subprocess.Popen([FFMPEG, "-v", "error", *hwdec(), "-ss", f"{a:.3f}", "-t", f"{b - a:.3f}",
                                    "-i", str(video), "-vf", f"fps={fps}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
                                   stdout=subprocess.PIPE)
            try:
                j = 0
                while True:
                    buf = dec.stdout.read(fsz)
                    if len(buf) < fsz:
                        break
                    reframe.compose(np.frombuffer(buf, np.uint8).reshape(src_h, src_w, 3), plan, a - span0 + j / fps, comp)
                    enc.stdin.write(comp.tobytes())
                    j += 1
            finally:
                if dec.poll() is None:
                    dec.kill()
                dec.wait()
    except BrokenPipeError:
        pass
    finally:
        try:
            enc.stdin.close()
        except OSError:
            pass
        enc.wait()
    if enc.returncode != 0:
        raise RuntimeError(f"base encode failed for {out_mp4}")
    return {"layout": plan.layout, "duration": round(main, 2), "plan": plan.to_dict()}


def render_clip(video: Path, src_w: int, src_h: int, segments: list, out_mp4: Path,
                words_doc: dict, verdict: dict, preset: dict, credit: str = "", layout: str = "auto",
                punches: list | None = None, split_order: str = "game_top", genre: str = "stream",
                fps: int = 30) -> dict:
    """`segments`: source (start, end) ranges played back to back. `verdict`: the judge's
    copy for this clip (hook, emphasis, notes, end_caption, end_emoji). `preset`: styles.pick()."""
    from . import fx
    video, out_mp4 = Path(video).resolve(), Path(out_mp4).resolve()
    span0 = min(a for a, _ in segments)
    span1 = max(b for _, b in segments)
    plan = reframe.plan_clip(video, span0, span1 - span0, src_w, src_h, layout, split_order, genre)

    # everything below is on the OUTPUT timeline
    words, off = [], 0.0
    for a, b in segments:
        for w in captions.clip_words(words_doc, a, b):
            if w["s"] >= -0.05 and w["e"] <= b - a + 0.05:
                words.append({"w": w["w"], "s": w["s"] + off, "e": w["e"] + off})
        off += b - a
    main = off
    end_len = 1.4 if (preset.get("end_card") and verdict.get("end_caption")) else 0.0
    total = main + end_len
    notes = []
    for n_ in (verdict.get("notes") or []) if preset.get("notes") else []:
        try:
            t = _to_out(float(n_.get("t")), segments)
        except (TypeError, ValueError, AttributeError):
            continue
        if t is not None:
            notes.append({"t": t, "text": n_.get("text", "")})
    pts = sorted(t for t in (_to_out(p, segments) for p in (punches or [])) if t is not None)
    starts = [w["s"] for i, w in enumerate(words)
              if i == 0 or words[i - 1]["w"][-1:] in ".?!" or w["s"] - words[i - 1]["e"] > 0.6]
    cam = fx.Camera(preset["camera"], main, pts, starts)
    ass = out_mp4.with_suffix(".ass")
    captions.build_ass(words, total, ass, plan.layout, preset["caption"],
                       verdict.get("hook", "") if preset.get("hook") else "",
                       verdict.get("emphasis", []), credit, notes, plan.seam_y, end_at=main)

    venc = (["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "21", "-b:v", "9M",
             "-maxrate", "12M", "-bufsize", "18M", "-profile:v", "high"] if nvenc() else
            ["-c:v", "libx264", "-preset", "medium", "-crf", "20"])
    # libass paths: run from the clip folder with relative names (Windows drive colons break filters)
    fonts_rel = os.path.relpath(FONTS, out_mp4.parent).replace("\\", "/")
    vf = f"unsharp=5:5:0.6:5:5:0.0,ass={ass.name}:fontsdir={fonts_rel},format=yuv420p"
    trims = "".join(f"[1:a]atrim={a - span0:.3f}:{b - span0:.3f},asetpts=PTS-STARTPTS,"
                    f"afade=t=in:d=0.015,afade=t=out:st={max(0, b - a - 0.015):.3f}:d=0.015[a{i}];"
                    for i, (a, b) in enumerate(segments))
    fc = (trims + "".join(f"[a{i}]" for i in range(len(segments)))
          + f"concat=n={len(segments)}:v=0:a=1,loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000,"
          f"afade=t=out:st={max(0, main - 0.25):.3f}:d=0.25")
    extra_in = []
    if end_len:
        fc += f",apad=pad_dur={end_len:.2f}[m];[2:a]adelay={int(main * 1000)}|{int(main * 1000)},volume=0.9[bm];"               f"[m][bm]amix=inputs=2:normalize=0:duration=first[aout]"
        extra_in = ["-i", str(fx.boom())]
    else:
        fc += "[aout]"
    enc = subprocess.Popen(
        [FFMPEG, "-v", "error", "-y",
         "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{reframe.OUT_W}x{reframe.OUT_H}", "-r", str(fps), "-i", "-",
         "-ss", f"{span0:.3f}", "-t", f"{span1 - span0 + 0.5:.3f}", "-i", str(video), *extra_in,
         "-filter_complex", fc, "-map", "0:v", "-map", "[aout]", "-vf", vf, *venc,
         "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-t", f"{total:.3f}", out_mp4.name],
        stdin=subprocess.PIPE, cwd=str(out_mp4.parent))
    fsz = src_w * src_h * 3
    comp = np.zeros((reframe.OUT_H, reframe.OUT_W, 3), np.uint8)
    shot = np.zeros_like(comp)
    i = 0
    try:
        for a, b in segments:
            dec = subprocess.Popen([FFMPEG, "-v", "error", *hwdec(), "-ss", f"{a:.3f}", "-t", f"{b - a:.3f}",
                                    "-i", str(video), "-vf", f"fps={fps}", "-f", "rawvideo",
                                    "-pix_fmt", "bgr24", "-"], stdout=subprocess.PIPE)
            try:
                j = 0
                while True:
                    buf = dec.stdout.read(fsz)
                    if len(buf) < fsz:
                        break
                    frame = np.frombuffer(buf, np.uint8).reshape(src_h, src_w, 3)
                    reframe.compose(frame, plan, a - span0 + j / fps, comp)
                    cam.apply(comp, i / fps, shot)
                    enc.stdin.write(shot.tobytes())
                    i += 1
                    j += 1
            finally:
                if dec.poll() is None:
                    dec.kill()                          # never block on a decoder nobody reads
                dec.wait()
        if end_len and i:
            ov = fx.end_card_overlay(verdict.get("end_caption", ""), verdict.get("end_emoji", ""))
            for f in fx.end_card_frames(shot.copy(), ov, int(end_len * fps)):
                enc.stdin.write(f.tobytes())
                i += 1
    except BrokenPipeError:
        pass                                            # encoder died; reported below
    finally:
        try:
            enc.stdin.close()
        except OSError:
            pass
        enc.wait()
    if enc.returncode != 0:
        raise RuntimeError(f"encode failed for {out_mp4}")
    return {"layout": plan.layout, "style": preset.get("name"), "frames": i, "duration": round(total, 2),
            "end_card": bool(end_len), "notes": len(notes), "punches": [round(p, 2) for p in pts],
            "segments": [[round(a, 2), round(b, 2)] for a, b in segments], "plan": plan.to_dict()}
