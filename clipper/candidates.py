"""Peaks of the excitement curve -> candidate moments with transcript context and a frame strip.

A candidate is NOT a clip yet: it is a ~2-minute window around a peak that the judge reads
and cuts down to the one self-contained moment inside it.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

from .common import FFMPEG, fmt_ts, log, write_json


def pick_peaks(curve: np.ndarray, k: int, min_gap: int = 35, skip_head: int = 60,
               floor: float = 0.3) -> list[int]:
    order = np.argsort(-curve)
    chosen: list[int] = []
    n = len(curve)
    for t in order:
        t = int(t)
        if curve[t] < floor and len(chosen) >= k // 2:
            break
        if t < skip_head or t > n - 5:
            continue                       # "starting soon" screens and the end card
        if any(abs(t - c) < min_gap for c in chosen):
            continue
        chosen.append(t)
        if len(chosen) >= k:
            break
    return sorted(chosen)


def phrases(words_doc: dict, max_s: float = 8.0, gap: float = 0.7) -> list[tuple[float, float, str]]:
    """Phrase lines rebuilt from word timings. Batched Whisper returns ~30 s segments, far too
    coarse for a judge to cut on; split at sentence ends, pauses and every ~8 s."""
    out, cur = [], []
    for w in (w for seg in words_doc.get("segments", []) for w in seg["words"]):
        if cur and (w["s"] - cur[-1]["e"] > gap or w["s"] - cur[0]["s"] > max_s):
            out.append(cur)
            cur = []
        cur.append(w)
        if w["w"].rstrip()[-1:] in ".!?":
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return [(p[0]["s"], p[-1]["e"], " ".join(x["w"] for x in p)) for p in out]


_PHRASES: dict[int, list] = {}


def transcript_lines(words_doc: dict, a: float, b: float) -> list[str]:
    key = id(words_doc)
    if key not in _PHRASES:
        _PHRASES[key] = phrases(words_doc)
    return [f"[{s:.1f}] {t}" for s, e, t in _PHRASES[key] if e >= a and s <= b]


def event_lines(parts: dict[str, np.ndarray], sig: dict[str, np.ndarray], a: int, b: int) -> list[str]:
    """Non-verbal cues the transcript cannot carry: screaming, laughter, chat spikes."""
    out = []
    labels = {"scream": "SCREAMING/SHOUTING", "laugh": "LAUGHTER", "cheer": "CHEERING",
              "boom": "EXPLOSION/BANG", "gasp": "GASP"}
    for s in range(max(0, a), min(len(sig["loud"]), b), 3):
        tags = []
        for k, name in labels.items():
            if k in sig and float(sig[k][s:s + 3].max()) >= 0.35:
                tags.append(name)
        if "music" in sig and float(sig["music"][s:s + 3].mean()) >= 0.6:
            tags.append("MUSIC")
        if "loud" in parts and float(parts["loud"][s:s + 3].max()) > 2.5:
            tags.append("VERY LOUD")
        if "react" in parts and float(parts["react"][s:s + 3].max()) > 2.5:
            tags.append("CHAT SPAMMING REACTIONS")
        elif "chat" in parts and float(parts["chat"][s:s + 3].max()) > 2.5:
            tags.append("CHAT SPIKE")
        if tags:
            out.append(f"[{s:.1f}] <{', '.join(tags)}>")
    return out


def frame_strip(video: Path, times: list[float], out: Path, w: int = 480) -> Path:
    """Frames at `times` side by side in one jpeg (one image per candidate keeps vision cheap)."""
    inputs = []
    for t in times:
        inputs += ["-ss", f"{max(0, t):.2f}", "-i", str(video)]
    n = len(times)
    fc = "".join(f"[{i}:v]scale={w}:-2,setsar=1[v{i}];" for i in range(n))
    fc += "".join(f"[v{i}]" for i in range(n)) + f"hstack=inputs={n}[out]"
    subprocess.run([FFMPEG, "-v", "error", "-y", *inputs, "-filter_complex", fc,
                    "-map", "[out]", "-frames:v", "1", "-q:v", "4", str(out)], check=True)
    return out


def build(work: Path, curve, parts, sig, words_doc: dict, k: int,
          before: int = 70, after: int = 40, centres: list | None = None) -> list[dict]:
    """`centres`: [(second, source, scan proposal or None)]; default = signal peaks only."""
    if centres is None:
        centres = [(t, "signal", None) for t in pick_peaks(curve, k)]
    log(f"{len(centres)} candidates ({sum(1 for c in centres if c[1] == 'scan')} from the scan, "
        f"{sum(1 for c in centres if c[1] == 'signal')} from signal peaks)")
    strips = work / "strips"
    strips.mkdir(exist_ok=True)
    n = len(curve)
    cands = []
    for i, (t, src, prop) in enumerate(sorted(centres, key=lambda c: c[0])):
        if prop:
            a = max(0, int(prop["start"]) - 30)
            b = min(n - 1, int(prop["end"]) + 25)
            shots = list(np.linspace(prop["start"], prop["end"], 4))
        else:
            a, b = max(0, t - before), min(n - 1, t + after)
            shots = [t - 25, t - 10, t, t + 10]
        shots = [min(max(0.0, float(x)), n - 1.5) for x in shots]   # a peak near either end asks past the file
        lines = transcript_lines(words_doc, a, b)
        ev = event_lines(parts, sig, a, b)
        merged = sorted(lines + ev, key=lambda s: float(s[1:s.index("]")]))
        if prop:
            merged.insert(0, f"(scout's note: \"{prop['what']}\", proposed {prop['start']:.0f}-{prop['end']:.0f}s)")
        strip = frame_strip(work / "source.mp4", shots, strips / f"c{i:02d}.jpg")
        cands.append({"id": i, "peak": t, "peak_ts": fmt_ts(t), "window": [a, b], "source": src,
                      "scan_score": prop["score"] if prop else None,
                      "excitement": round(float(curve[min(t, n - 1)]), 2),
                      "drivers": sorted(((k2, round(float(v[min(t, n - 1)]), 2)) for k2, v in parts.items()),
                                        key=lambda kv: -kv[1])[:4],
                      "context": merged, "strip": str(strip)})
    write_json(work / "candidates.json", cands)
    return cands
