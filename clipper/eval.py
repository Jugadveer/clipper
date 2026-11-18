"""Measure moment picking against what HUMAN editors chose from the same stream.

Ground truth = a human-made highlight video or shorts of the stream. Their audio is
transcribed and aligned to the stream transcript by word-trigram voting, which gives the
stream intervals the humans kept. Then:
  coverage   share of human-kept bits (merged intervals) that one of our clips overlaps
  precision  share of our clip time that lies inside human-kept intervals

  py -m clipper.eval map <stream work dir> <gt audio/video> [more...]   -> gt.json
  py -m clipper.eval score <stream work dir> [clips.json|verdicts.json|candidates.json]
"""
from __future__ import annotations

import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from .common import log, read_json, write_json

NORM = re.compile(r"[^a-z0-9']+")


def _words(doc: dict) -> list[dict]:
    out = []
    for seg in doc.get("segments", []):
        for w in seg["words"]:
            t = NORM.sub("", w["w"].lower())
            if t:
                out.append({"t": t, "s": w["s"], "e": w["e"]})
    return out


def align(stream_doc: dict, gt_doc: dict, chunk: int = 18, min_votes: int = 4) -> list[tuple[float, float]]:
    """Stream intervals that the GT recording contains."""
    sw = _words(stream_doc)
    gw = _words(gt_doc)
    tri = defaultdict(list)
    for i in range(len(sw) - 2):
        tri[(sw[i]["t"], sw[i + 1]["t"], sw[i + 2]["t"])].append(i)
    spans = []
    for c0 in range(0, len(gw) - 2, chunk // 2):
        votes = Counter()
        for j in range(c0, min(len(gw) - 2, c0 + chunk)):
            for i in tri.get((gw[j]["t"], gw[j + 1]["t"], gw[j + 2]["t"]), ()):
                votes[i - j] += 1
        if not votes:
            continue
        off, v = votes.most_common(1)[0]
        if v < min_votes:
            continue
        a = max(0, c0 + off)
        b = min(len(sw) - 1, c0 + chunk + off)
        spans.append((sw[a]["s"], sw[b]["e"]))
    spans.sort()
    merged: list[list[float]] = []
    for a, b in spans:
        if merged and a - merged[-1][1] < 6:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged if b - a >= 4]


def heat_gt(info: dict, top: int = 15, skip_head: int = 60) -> list[tuple[float, float]]:
    """The stream's own most-replayed bins (millions of viewers re-watching) as ground truth.
    Only fair when the run was made with --no-heatmap, so the tool never saw it."""
    hm = info.get("heatmap") or []
    dur = info.get("duration") or (hm[-1]["end_time"] if hm else 0)
    bins = [b for b in hm if b["start_time"] >= skip_head and b["end_time"] <= dur * 0.98]
    bins.sort(key=lambda b: -b["value"])
    return sorted((b["start_time"], b["end_time"]) for b in bins[:top])


def score(gt: list, ours: list, bit_gap: float = 15) -> dict:
    """gt/ours: lists of (start, end) in stream seconds."""
    bits: list[list[float]] = []
    for a, b in sorted(gt):
        if bits and a - bits[-1][1] < bit_gap:
            bits[-1][1] = max(bits[-1][1], b)
        else:
            bits.append([a, b])
    hit = sum(1 for a, b in bits if any(min(b, e) - max(a, s) > min(8, 0.3 * (b - a)) for s, e in ours))
    our_t = sum(e - s for s, e in ours) or 1
    inside = sum(max(0.0, min(e, b) - max(s, a)) for s, e in ours for a, b in gt)
    return {"gt_bits": len(bits), "gt_minutes": round(sum(b - a for a, b in bits) / 60, 1),
            "bits_hit": hit, "coverage": round(hit / max(1, len(bits)), 3),
            "our_clips": len(ours), "our_minutes": round(our_t / 60, 1),
            "precision": round(inside / our_t, 3)}


def main(argv):
    cmd, work = argv[0], Path(argv[1])
    stream_doc = read_json(work / "words.json")
    if cmd == "map":
        from .transcribe import transcribe
        allspans = []
        for src in argv[2:]:
            src = Path(src)
            wj = src.with_suffix(".words.json")
            if not wj.exists():
                wav = src.with_suffix(".wav")
                import subprocess
                from .common import FFMPEG
                subprocess.run([FFMPEG, "-v", "error", "-y", "-i", str(src), "-ac", "1", "-ar", "16000", str(wav)],
                               check=True)
                transcribe(wav, wj, "turbo")
                wav.unlink(missing_ok=True)
            spans = align(stream_doc, read_json(wj))
            log(f"{src.name}: {len(spans)} spans, {sum(b - a for a, b in spans) / 60:.1f} min of the stream")
            allspans += [{"src": src.name, "start": a, "end": b} for a, b in spans]
        write_json(work / "gt.json", allspans)
    else:
        if "--heat" in argv:
            argv = [a for a in argv if a != "--heat"]
            gt = heat_gt(read_json(work / "source.info.json"))
            print("GT = top YouTube most-replayed bins:", [(int(a), int(b)) for a, b in gt])
        else:
            gt = [(g["start"], g["end"]) for g in read_json(work / "gt.json")]
        f = Path(argv[2]) if len(argv) > 2 else work / "clips" / "clips.json"
        doc = read_json(f) or []
        rows = doc.get("verdicts", doc) if isinstance(doc, dict) else doc
        ours = []
        for r in rows:
            if "source_start" in r:
                ours.append((r["source_start"], r["source_end"]))
            elif r.get("keep", True) and "start" in r:
                ours.append((r["start"], r["end"]))
            elif "window" in r:
                ours.append(tuple(r["window"]))
        print(score(gt, ours))


if __name__ == "__main__":
    main(sys.argv[1:])
