"""clipper CLI.

  py -m clipper run <url-or-file> [--clips 10] [--streamer "IShowSpeed"] [--layout auto]
  py -m clipper batch urls.txt     one URL per line, every source clipped in turn
  py -m clipper live <url>          clip a stream while it is still live (rolling windows)

Stages are cached in work/<id>/, so a re-run only redoes what changed
(delete verdicts.json to re-judge, clips/ to re-render).
"""
from __future__ import annotations

import argparse
import csv
import html
import re
import time
from pathlib import Path

import numpy as np

from . import candidates, ingest, judge, render, scan, signals, styles
from .common import fmt_ts, log, read_json, write_json


def slug(s: str, n: int = 48) -> str:
    s = re.sub(r"[^\w\s-]", "", s, flags=re.UNICODE).strip().lower()
    return re.sub(r"[\s_-]+", "-", s)[:n].strip("-") or "clip"


def local_judge(cands: list[dict], words_doc: dict) -> list[dict]:
    """Zero-cost judge: keep the strongest peaks, cut 22 s before to 10 s after, copy from text."""
    out = []
    for c in cands:
        s, e = max(c["window"][0], c["peak"] - 22), min(c["window"][1], c["peak"] + 10)
        said = " ".join(l.split("] ", 1)[1] for l in c["context"] if "<" not in l)[:60]
        out.append({"id": c["id"], "keep": True, "start": s, "end": e, "peak": c["peak"],
                    "title": said or f"Moment at {c['peak_ts']}", "hook": "", "description": said,
                    "hashtags": ["stream", "clips", "funny"], "emphasis": [],
                    "virality": int(min(99, 50 + 8 * c["excitement"])), "scores": {},
                    "excitement": c["excitement"], "drivers": c["drivers"], "why": "local heuristic"})
    return out


def run(src: str, n_clips: int = 10, streamer: str = "", layout: str = "auto",
        judge_mode: str = "llm", model: str = "gemini-2.5-flash-lite", whisper: str = "turbo",
        min_virality: int = 50, cold_open: bool = True, trim_dead_air: bool = True,
        prompt: str = "", text_only: bool = False, scan_pass: bool = True,
        keep_source: bool = False, use_heatmap: bool = True, do_render: bool = True,
        genre: str = "auto", style: str = "auto", split_order: str = "game_top",
        verdicts_file: str = "", jobs: int = 3) -> Path:
    t0 = time.time()
    work = ingest.work_dir_for(src)
    video = ingest.fetch(src, work)
    info = ingest.info(work)
    streamer = streamer or info.get("uploader") or info.get("channel") or ""
    title = info.get("title", work.name)
    ingest.audio(work)
    meta = render_probe(video)
    dur = meta["duration"]
    log(f"source: {title!r} by {streamer} - {fmt_ts(dur)}, {meta['w']}x{meta['h']}")

    if not (work / "words.json").exists():
        # its own process: CTranslate2 loads a cuDNN that breaks torch's (PANNs) if they share one
        import subprocess
        import sys
        subprocess.run([sys.executable, "-m", "clipper.transcribe", str(work / "audio16k.wav"),
                        str(work / "words.json"), whisper], check=True, cwd=str(Path(__file__).parent.parent))
    words_doc = read_json(work / "words.json")

    sig = signals.compute(work, dur)
    if not use_heatmap:
        sig = {k: v for k, v in sig.items() if k != "heat"}
    sig["speech"] = signals.speech_rate(words_doc, len(sig["loud"]))
    curve, parts = signals.excitement(sig)
    if genre == "auto":
        genre = detect_genre(title, words_doc, sig, dur)
    log(f"genre: {genre}")
    if n_clips <= 0:                          # auto: up to one short per ~3 minutes of stream
        n_clips = max(5, int(dur / 180))
    k = max(int(n_clips * 2), 16)             # judged candidates: LLM cost scales with THIS
    cands = read_json(work / "candidates.json")
    if cands is None:
        centres = None
        if judge_mode != "local" and scan_pass:
            props = read_json(work / "scan.json")
            if props is None:
                try:
                    props = scan.scan(words_doc, sig, parts, title, streamer, model=model, prompt=prompt,
                                      genre=genre)
                    write_json(work / "scan.json", props)
                except judge.BudgetExhausted as e:
                    log(f"{e}; skipping the transcript scan")
                    props = []
            peaks = candidates.pick_peaks(curve, k, min_gap=25)
            # streams: audio/chat peaks first (they match what viewers re-watch), the scan
            # fills the gaps; podcasts: the transcript scan leads
            centres = scan.merge_candidates(props, curve, peaks, k,
                                            prefer="scan" if genre == "podcast" else "signal")
        cands = candidates.build(work, curve, parts, sig, words_doc, k, centres=centres)

    vfile = Path(verdicts_file) if verdicts_file else work / "verdicts.json"
    if vfile.exists():
        verdicts = read_json(vfile)["verdicts"]
    elif judge_mode == "local":
        verdicts = local_judge(cands, words_doc)
        write_json(vfile, {"model": "local", "usage": [], "verdicts": verdicts})
    else:
        try:
            verdicts = judge.judge(cands, title, streamer, work, model=model, prompt=prompt,
                                   use_images=not text_only, genre=genre)
            verdicts = judge.rerank(verdicts, words_doc, title, work, model=model)
        except judge.BudgetExhausted as e:
            # a cap reached mid-job costs quality, never the job: the free picker takes over
            log(f"{e}; using the free local picker")
            verdicts = local_judge(cands, words_doc)
            write_json(vfile, {"model": "local (budget reached)", "usage": [], "verdicts": verdicts})
    picks = judge.select(verdicts, n_clips, min_virality=min_virality,
                         min_len=15 if genre == "podcast" else 10)
    log(f"selected {len(picks)} clips")
    if not do_render:
        write_json(work / "picks.json", [{"source_start": v["start"], "source_end": v["end"],
                                          "title": v.get("title"), "virality": v.get("virality")}
                                         for v in picks])
        log(f"--no-render: picks.json written (LLM spend to date ${judge.spent():.4f})")
        return work

    out_dir = work / "clips"
    out_dir.mkdir(exist_ok=True)
    def one(i: int, v: dict) -> dict:
        return render_pick(i, v, video, meta, words_doc, parts, out_dir, style=style, genre=genre,
                           streamer=streamer, layout=layout, split_order=split_order, cold_open=cold_open,
                           trim_dead_air=trim_dead_air, source_url=info.get("webpage_url", src))

    # renders are ffmpeg pipes + numpy that release the GIL: a few in parallel use the cores
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max(1, jobs)) as ex:
        rows = list(ex.map(lambda iv: one(*iv), enumerate(picks, 1)))
    write_outputs(out_dir, rows, title, streamer, cost_line(work))
    if not keep_source:
        # the stream is gigabytes; the clips, transcript and signals are what we keep
        for f in ("source.mp4", "audio16k.wav", "audio32k.wav"):
            (work / f).unlink(missing_ok=True)
        for f in work.glob("source.*.part"):
            f.unlink(missing_ok=True)
        log("deleted the source stream (use --keep-source to keep it)")
    log(f"done in {(time.time() - t0) / 60:.1f} min -> {out_dir}  (LLM spend to date ${judge.spent():.4f})")
    return out_dir


def render_pick(i: int, v: dict, video: Path, meta: dict, words_doc: dict, parts: dict, out_dir: Path,
                style: str = "auto", genre: str = "stream", streamer: str = "", layout: str = "auto",
                split_order: str = "game_top", cold_open: bool = True, trim_dead_air: bool = True,
                source_url: str = "", name: str = "") -> dict:
    """Render one judged pick (times in `video`'s seconds) to out_dir/<name>.mp4 + thumb, and
    return its row for clips.json. Shared by `run` (a whole VOD) and `live` (a rolling window)."""
    s, e = render.snap(words_doc, v["start"], v["end"])
    name = name or f"{i:02d}_{slug(v.get('title', ''))}"
    mp4 = out_dir / f"{name}.mp4"
    punches = render.punch_times(parts, s, e)
    co = v.get("cold_open")
    cold = None
    if cold_open and isinstance(co, (list, tuple)) and len(co) == 2:
        try:
            a, b = float(co[0]), float(co[1])
            if s + 6 <= a < b <= e and 1.0 <= b - a <= 4.5:
                cold = (a, b)
        except (TypeError, ValueError):
            pass
    cuts = render.dead_air(words_doc, parts, s, e) if trim_dead_air else []
    segs = render.build_segments(s, e, cold, cuts)
    if not mp4.exists():
        sname, preset = styles.pick(style, genre, i - 1)
        log(f"render {name} [{fmt_ts(s)}-{fmt_ts(e)}] {e - s:.0f}s v={v.get('virality')} style={sname}"
            f" cold_open={bool(cold)} dead_air_cuts={len(cuts)}")
        if preset.get("engine"):
            r = engine_clip(video, meta, segs, mp4, words_doc, v, preset["engine"], layout, split_order, genre)
        else:
            r = render.render_clip(video, meta["w"], meta["h"], segs, mp4, words_doc, v, preset,
                                   f"@{streamer}" if streamer else "", layout, punches,
                                   split_order=split_order, genre=genre)
        write_json(mp4.with_suffix(".json"), r)
    else:
        r = read_json(mp4.with_suffix(".json"), {"layout": "cached"})
    thumb = out_dir / f"{name}.jpg"
    if not thumb.exists():
        import subprocess
        from .common import FFMPEG
        subprocess.run([FFMPEG, "-v", "error", "-y", "-ss", "1.5", "-i", str(mp4), "-frames:v", "1",
                        "-vf", "scale=360:-2", str(thumb)], check=True)
    tags = [t.lstrip("#").lower() for t in v.get("hashtags", [])]
    return {"file": mp4.name, "title": v.get("title", ""), "hook": v.get("hook", ""),
            "description": (v.get("description", "") + chr(10) * 2
                            + " ".join("#" + t for t in tags)).strip(),
            "hashtags": ",".join(tags), "virality": v.get("virality"),
            "scores": v.get("scores", {}), "why": v.get("why", ""),
            "source_start": round(s, 2), "source_end": round(e, 2),
            "source_ts": f"{fmt_ts(s)}-{fmt_ts(e)}", "duration": r.get("duration", round(e - s, 1)),
            "cold_open": bool(cold), "dead_air_cuts": len(cuts),
            "layout": r.get("layout"), "style": r.get("style"), "end_caption": v.get("end_caption"),
            "drivers": v.get("drivers"), "thumb": thumb.name, "source_url": source_url}


def write_outputs(out_dir: Path, rows: list[dict], title: str, streamer: str, cost: str) -> None:
    """clips.json, upload.csv, the review page and the SHORTS/ export for a set of rendered rows."""
    write_json(out_dir / "clips.json", rows)
    with open(out_dir / "upload.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["file", "title", "description", "hashtags", "virality", "duration"])
        w.writeheader()
        for r in rows:
            w.writerow({k: r[k] for k in w.fieldnames})
    write_review(out_dir, rows, title, streamer, cost)
    export_shorts(out_dir, title)


def engine_clip(video: Path, meta: dict, segs: list, mp4: Path, words_doc: dict, v: dict, look: str,
                layout: str, split_order: str, genre: str) -> dict:
    """An engine style: the clipper reframes (face tracking, layouts), clipper/engine edits on top
    (captions, cards, punch-ins, grade, sound). Its work folder is deleted once the mp4 exists."""
    import shutil
    from .engine import make
    work = mp4.parent / ".engine" / mp4.stem
    work.mkdir(parents=True, exist_ok=True)
    base = work / "base.mp4"
    r = render.render_base(video, meta["w"], meta["h"], segs, base, layout, split_order, genre) or {}
    make.edit(base, render.clip_words_out(words_doc, segs), mp4, look=look,
              title=v.get("hook") or v.get("title", ""), work=work)
    shutil.copy2(work / "edl.json", mp4.with_suffix(".edl.json"))
    shutil.rmtree(work, ignore_errors=True)
    return {**r, "style": look, "duration": round(sum(b - a for a, b in segs), 1)}


PODCAST_RE = re.compile(r"podcast|episode|\bep\.?\s?\d|interview|\bshow\b|#\d{2,}|conversation", re.I)


def detect_genre(title: str, words_doc: dict, sig: dict, dur: float) -> str:
    """podcast = dense continuous talk over a near-static picture; stream = everything else."""
    wps = sum(len(s["words"]) for s in words_doc.get("segments", [])) / max(1.0, dur)
    still = float(np.median(sig.get("motion", np.zeros(1)))) < 3.0
    if PODCAST_RE.search(title or "") and wps > 1.6:
        return "podcast"
    return "podcast" if (wps > 2.3 and still) else "stream"


def export_shorts(clips_dir: Path, title: str) -> Path:
    """Every finished short also lands in clipper/SHORTS/<source title>/ for viewing. Hard links,
    so the files take the disk space once (falls back to a copy across drives)."""
    import os
    import shutil
    from .common import ROOT
    dest = ROOT / "SHORTS" / slug(title, 60)
    dest.mkdir(parents=True, exist_ok=True)
    for f in list(clips_dir.glob("*.mp4")) + [clips_dir / "upload.csv"]:
        t = dest / f.name
        if f.exists() and not t.exists():
            try:
                os.link(f, t)
            except OSError:
                shutil.copy2(f, t)
    return dest


def render_probe(video: Path) -> dict:
    from .common import ffprobe_json
    d = ffprobe_json(video)
    v = next(s for s in d["streams"] if s["codec_type"] == "video")
    return {"w": int(v["width"]), "h": int(v["height"]), "duration": float(d["format"]["duration"])}


# $ per 1M tokens (input, output) for the judge models on the Quanzil relay / OpenRouter
PRICES = {"gemini-2.5-flash": (0.30, 2.50), "gemini-2.5-flash-lite": (0.10, 0.40)}


def cost_line(work: Path) -> str:
    v = read_json(work / "verdicts.json", {}) or {}
    use = v.get("usage", [])
    if not use:
        return "LLM cost: $0 (local judge)"
    pi, po = PRICES.get(v.get("model", ""), (0.30, 2.50))
    ti = sum(u.get("prompt_tokens", 0) for u in use)
    to = sum(u.get("completion_tokens", 0) for u in use)
    return (f"LLM judge: {len(use)} calls, {ti:,} in + {to:,} out tokens = "
            f"${ti / 1e6 * pi + to / 1e6 * po:.4f} at list price ({v.get('model')}); everything else ran locally.")


def write_review(out_dir: Path, rows: list[dict], title: str, streamer: str, cost: str) -> None:
    cards = []
    for r in rows:
        sc = r.get("scores") or {}
        cards.append(f"""<article><video src="{html.escape(r['file'])}" poster="{html.escape(r['thumb'])}" controls preload="none"></video>
<h3>{html.escape(r['title'])}</h3><p class=m>virality <b>{r['virality']}</b> · {r['duration']}s · {r['layout']} · source {r['source_ts']}
· hook {sc.get('hook','-')} flow {sc.get('flow','-')} value {sc.get('value','-')} trend {sc.get('trend','-')}</p>
<p>{html.escape(r['why'])}</p><pre>{html.escape(r['description'])}</pre></article>""")
    doc = f"""<!doctype html><meta charset=utf-8><title>Clips: {html.escape(title)}</title>
<style>body{{font:15px system-ui;background:#111;color:#eee;margin:24px}}main{{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:20px}}
article{{background:#1c1c1c;padding:12px;border-radius:10px}}video{{width:100%;aspect-ratio:9/16;background:#000;border-radius:6px}}
h3{{margin:10px 0 4px;font-size:16px}}.m{{color:#aaa;font-size:12px}}pre{{white-space:pre-wrap;font-size:12px;color:#bbb}}</style>
<h1>{html.escape(title)}</h1><p>{html.escape(streamer)} · {len(rows)} clips · {html.escape(cost)}</p><main>{''.join(cards)}</main>"""
    (out_dir / "index.html").write_text(doc, encoding="utf-8")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="clipper")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("run", "batch", "live"):
        p = sub.add_parser(name)
        p.add_argument("source")
        p.add_argument("--clips", type=int, default=0, help="0 = auto (about one per 3 minutes)")
        p.add_argument("--streamer", default="")
        p.add_argument("--layout", default="auto", choices=["auto", "split", "single", "speaker", "stack", "fit"])
        p.add_argument("--judge", default="llm", choices=["llm", "local"])
        p.add_argument("--model", default="gemini-2.5-flash-lite")
        p.add_argument("--whisper", default="turbo")
        p.add_argument("--min-virality", type=int, default=50)
        p.add_argument("--prompt", default="", help='only clip this, e.g. "every time he dies"')
        p.add_argument("--text-only", action="store_true",
                       help="judge without frame strips: ~half the LLM cost, weaker on purely visual moments")
        p.add_argument("--genre", default="auto", choices=["auto", "stream", "podcast"])
        p.add_argument("--style", default="auto",
                       choices=styles.CHOICES)
        p.add_argument("--split-order", default="game_top", choices=["game_top", "cam_top"])
        p.add_argument("--jobs", type=int, default=3, help="clips rendered in parallel")
        p.add_argument("--verdicts", default="", help="use this verdicts json instead of judging")
        p.add_argument("--no-heatmap", action="store_true", help="ignore YouTube most-replayed (for evaluation)")
        p.add_argument("--no-render", action="store_true", help="pick clips only (for evaluation)")
        p.add_argument("--no-scan", action="store_true", help="signal peaks only, skip the transcript scan")
        p.add_argument("--keep-source", action="store_true",
                       help="keep the downloaded stream (default: delete it once the clips are rendered)")
        p.add_argument("--no-cold-open", action="store_true", help="never replay the punchline first")
        p.add_argument("--keep-dead-air", action="store_true", help="do not jump-cut silent gaps")
        if name == "live":
            p.add_argument("--step", type=float, default=90, help="clip every N seconds of new stream")
            p.add_argument("--lag", type=float, default=25, help="seconds of aftermath a moment needs before it is judged")
            p.add_argument("--floor", type=float, default=3.0, help="excitement a signal peak needs to be judged")
            p.add_argument("--max-minutes", type=float, default=0, help="stop recording after N minutes (0 = until the stream ends)")
            p.add_argument("--readrate", type=float, default=1.0, help="local file only: play it at N x real time (testing)")
    a = ap.parse_args(argv)
    kw = dict(n_clips=a.clips, streamer=a.streamer, layout=a.layout, judge_mode=a.judge,
              model=a.model, whisper=a.whisper, min_virality=a.min_virality,
              cold_open=not a.no_cold_open, trim_dead_air=not a.keep_dead_air, prompt=a.prompt,
              text_only=a.text_only, scan_pass=not a.no_scan, keep_source=a.keep_source,
              use_heatmap=not a.no_heatmap, do_render=not a.no_render, genre=a.genre, style=a.style,
              split_order=a.split_order, verdicts_file=a.verdicts, jobs=a.jobs)
    if a.cmd == "run":
        run(a.source, **kw)
    elif a.cmd == "batch":
        for line in Path(a.source).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                try:
                    run(line, **kw)
                except Exception as e:                  # one bad source never stops the batch
                    log(f"FAILED {line}: {e}")
    elif a.cmd == "live":
        # clips while the stream is still running (clipper/live.py); --clips caps how many
        from . import live
        live.live(a.source, step=a.step, lag=a.lag, floor=a.floor, max_minutes=a.max_minutes,
                  readrate=a.readrate, max_clips=a.clips, **{k: v for k, v in kw.items() if k != "n_clips"})


if __name__ == "__main__":
    main()
