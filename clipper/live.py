"""Clip a stream WHILE it is live: no waiting for the VOD.

  py -m clipper live <url> [--step 90] [--floor 3] [--style mix] ...

Three things run at once:
  1. A recorder (ffmpeg on the stream's live HLS) writes the stream to disk continuously as ~30 s
     segments. It never pauses for clipping, so nothing is lost while a clip renders, and it
     re-resolves the stream URL when the connection drops (signed URLs expire after hours).
  2. A Whisper worker keeps one model loaded and transcribes only the audio that is new.
  3. The clipping loop wakes every `step` seconds of new stream. It builds a CONTEXT window (the
     last ~5 min plus what is new), computes the same signals as a VOD run (loudness, PANNs audio
     events, motion, speech), and looks for moments only in the RIPE zone: stream that has at
     least `lag` seconds of aftermath recorded, so a punchline is never cut off by the live edge.
     Candidates there are the signal peaks over an absolute bar (a VOD ranks the whole stream; a
     live stream has no whole yet) plus the transcript scan's proposals for that stretch. Only
     those few candidates are sent to the LLM judge, then the keepers render at once through the
     same `render_pick` a VOD run uses.

Every time is kept in STREAM seconds (since recording began). A moment is judged once and
clipped once: judged centres and rendered spans are remembered, so overlapping context windows
never produce the same clip twice.

Cost model is the VOD one: the LLM sees only candidate windows (one scan call over each new
stretch of transcript, one judge call per candidate). A quiet hour costs almost nothing.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np

from . import candidates, ingest, judge, scan, signals
from .common import FFMPEG, fmt_ts, log, read_json, write_json

SEG_S = 30            # recorder segment length
CTX_BEFORE = 300      # seconds of context kept before the ripe zone (signal baselines, setup)


# ----------------------------------------------------------------------------------- recorder
class Recorder:
    """ffmpeg writes the live stream to rec/run<r>_<n>.mkv as it arrives (Matroska holds any codec:
    MPEG-TS silently drops AV1 video); a dropped connection is
    re-resolved and recording continues in a new run. A local file is "streamed" at `readrate`x
    real time, which is how the loop is tested without a live stream."""

    def __init__(self, src: str, rec: Path, max_height: int = 1080, readrate: float = 1.0):
        self.src, self.rec, self.max_height, self.readrate = src, rec, max_height, readrate
        self.local = Path(src).exists()
        self.ended = False
        self.error = ""
        self.proc: subprocess.Popen | None = None
        rec.mkdir(parents=True, exist_ok=True)
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self.ended = True
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()

    def _resolve(self) -> str:
        if self.local:
            return str(Path(self.src).resolve())
        h = self.max_height
        r = subprocess.run(ingest.ytdlp("-g", "-f", f"best[height<={h}][protocol*=m3u8]/best[height<={h}]/best",
                                        self.src), capture_output=True, text=True, timeout=120)
        lines = [l for l in r.stdout.splitlines() if l.startswith("http")]
        if not lines:
            raise RuntimeError((r.stderr or "no stream url").strip().splitlines()[-1][:200])
        return lines[0]

    def _loop(self):
        run, fails = 0, 0
        while not self.ended:
            try:
                url = self._resolve()
            except Exception as e:
                fails += 1
                self.error = str(e)
                log(f"recorder: could not resolve the stream ({e}); attempt {fails}/3")
                if fails >= 3:
                    break
                time.sleep(15)
                continue
            fails = 0
            while (self.rec / f"run{run:02d}.csv").exists():
                run += 1
            inp = (["-readrate", str(self.readrate)] if self.local else
                   ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "10",
                    "-rw_timeout", "30000000"])
            cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", *inp, "-i", url,
                   "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy",
                   "-f", "segment", "-segment_time", str(SEG_S), "-reset_timestamps", "1",
                   "-segment_list", str(self.rec / f"run{run:02d}.csv"), "-segment_list_type", "csv",
                   "-segment_list_flags", "+live", str(self.rec / f"run{run:02d}_%05d.mkv")]
            log(f"recorder: run {run} started")
            self.proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL)
            self.proc.wait()
            if self.local or self.ended:
                break
            # the stream URL expired, the connection dropped, or the stream ended: ask again
            log(f"recorder: run {run} stopped (exit {self.proc.returncode}); checking the stream")
            if not self._still_live():
                break
        self.ended = True
        log("recorder: stream ended")

    def _still_live(self) -> bool:
        try:
            r = subprocess.run(ingest.ytdlp("--print", "is_live", self.src), capture_output=True,
                               text=True, timeout=120)
            return r.stdout.strip().splitlines()[-1:] == ["True"]
        except Exception:
            return False

    def segments(self) -> list[tuple[Path, float, float]]:
        """Finished segments in order: (path, stream start, duration)."""
        out, t = [], 0.0
        for csv in sorted(self.rec.glob("run*.csv")):
            for line in csv.read_text(encoding="utf-8", errors="replace").splitlines():
                parts = line.rsplit(",", 2)
                if len(parts) != 3:
                    continue
                try:
                    d = float(parts[2]) - float(parts[1])
                except ValueError:
                    continue
                if d <= 0.2:
                    continue
                p = self.rec / Path(parts[0]).name
                # stream time counts EVERY segment ever written: old ones are deleted to save disk,
                # and counting only the files still there ran the clock backwards and stalled the loop
                if p.exists():
                    out.append((p, t, d))
                t += d
        return out

    def recorded(self) -> float:
        """Seconds recorded so far, including segments already deleted."""
        t = 0.0
        for csv in sorted(self.rec.glob("run*.csv")):
            for line in csv.read_text(encoding="utf-8", errors="replace").splitlines():
                parts = line.rsplit(",", 2)
                try:
                    d = float(parts[2]) - float(parts[1])
                except (ValueError, IndexError):
                    continue
                if d > 0.2:
                    t += d
        return t


# ----------------------------------------------------------------------------------- whisper
class Whisper:
    """One faster-whisper model kept loaded in its own process (CTranslate2's cuDNN and torch's
    must not share a process; see cli.run)."""

    def __init__(self, model: str = "turbo"):
        root = Path(__file__).resolve().parent.parent
        self.p = subprocess.Popen([sys.executable, "-m", "clipper.transcribe", "--serve", model],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, cwd=str(root),
                                  encoding="utf-8")
        line = self._read()
        if line != "ready":
            raise RuntimeError(f"whisper worker did not start: {line}")

    def _read(self) -> str:
        while True:
            line = self.p.stdout.readline()
            if not line:
                return "eof"
            line = line.strip()
            if line == "ready" or line.startswith(("ok ", "err ")):
                return line

    def words(self, wav: Path, out: Path) -> list[dict]:
        self.p.stdin.write(f"{wav}\t{out}\n")
        self.p.stdin.flush()
        r = self._read()
        if not r.startswith("ok"):
            log(f"whisper: {r}")
            return []
        doc = read_json(out, {}) or {}
        return [w for seg in doc.get("segments", []) for w in seg["words"]]

    def close(self):
        try:
            self.p.stdin.close()
            self.p.wait(10)
        except Exception:
            self.p.kill()


def as_doc(words: list[dict], gap: float = 1.0, max_words: int = 40) -> dict:
    """Words back into the {segments: [{start, end, text, words}]} shape every stage reads."""
    segs, cur = [], []
    for w in words:
        if cur and (w["s"] - cur[-1]["e"] > gap or len(cur) >= max_words):
            segs.append(cur)
            cur = []
        cur.append(w)
    if cur:
        segs.append(cur)
    return {"segments": [{"start": s[0]["s"], "end": s[-1]["e"], "text": " ".join(w["w"] for w in s), "words": s}
                         for s in segs]}


# ----------------------------------------------------------------------------------- the loop
def _concat(segs: list[tuple[Path, float, float]], out: Path) -> None:
    lst = out.with_suffix(".txt")
    lst.write_text("".join(f"file '{p.as_posix()}'\n" for p, _, _ in segs), encoding="utf-8")
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy",
                    "-movflags", "+faststart", str(out)], check=True)


def _dur(p: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
                       capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def live(src: str, step: float = 90, lag: float = 25, floor: float = 3.0, max_judge: int = 3,
         style: str = "auto", genre: str = "auto", streamer: str = "", layout: str = "auto",
         judge_mode: str = "llm", model: str = "gemini-2.5-flash-lite", whisper: str = "turbo",
         min_virality: int = 50, prompt: str = "", text_only: bool = False, scan_pass: bool = True,
         cold_open: bool = True, trim_dead_air: bool = True, split_order: str = "game_top",
         max_clips: int = 0, max_minutes: float = 0, keep_source: bool = False, readrate: float = 1.0,
         jobs: int = 2, **_ignored) -> Path:
    from . import cli
    work = ingest.work_dir_for(src)
    base = work / "live"
    rec, out_dir = base / "rec", base / "clips"
    out_dir.mkdir(parents=True, exist_ok=True)
    meta_src = {"title": Path(src).stem, "uploader": ""} if Path(src).exists() else {}
    if not meta_src:
        r = subprocess.run(ingest.ytdlp("-J", "--no-download", "--no-playlist", src), capture_output=True,
                           text=True, timeout=180)
        try:
            meta_src = json.loads(r.stdout)
        except ValueError:
            meta_src = {}
        if meta_src and not meta_src.get("is_live"):
            log(f"warning: the source reports is_live={meta_src.get('is_live')}; recording it anyway")
    title = meta_src.get("title") or meta_src.get("fulltitle") or work.name
    streamer = streamer or meta_src.get("uploader") or meta_src.get("channel") or ""
    write_json(base / "source.info.json", {k: meta_src.get(k) for k in ("title", "uploader", "channel",
                                                                          "webpage_url", "is_live", "id")})
    if style == "mix":
        style = "mix-fast"                      # engine looks take minutes per clip; see styles.MIX_FAST
    log(f"LIVE: {title!r} by {streamer or '?'}  step={step:.0f}s lag={lag:.0f}s floor={floor} style={style}")

    state = read_json(base / "state.json", {}) or {}
    words: list[dict] = state.get("words", [])
    done_until = float(state.get("done_until", 0.0))
    heard_until = float(state.get("heard_until", 0.0))
    judged: list[float] = state.get("judged", [])
    retry: list[float] = state.get("retry", [])
    spans: list[list[float]] = state.get("spans", [])
    rows: list[dict] = read_json(out_dir / "clips.json", []) or []
    spend0 = judge.spent()

    from concurrent.futures import ThreadPoolExecutor
    pool = ThreadPoolExecutor(max(1, jobs))
    lock = threading.Lock()
    jobs_left: dict[Path, int] = {}
    reserved = [len(rows)]                     # clip numbers handed out, finished or not

    def _render(n, v, ctx, off, meta_ctx, words_doc, parts, genre_):
        name = f"{n:03d}_{cli.slug(v.get('title', ''))}"
        try:
            row = cli.render_pick(n, v, ctx / "source.mp4", meta_ctx, words_doc, parts, out_dir, style=style,
                                  genre=genre_, streamer=streamer, layout=layout, split_order=split_order,
                                  cold_open=cold_open, trim_dead_air=trim_dead_air,
                                  source_url=meta_src.get("webpage_url", src), name=name)
            row["stream_start"] = round(row["source_start"] + off, 2)
            row["stream_end"] = round(row["source_end"] + off, 2)
            row["stream_ts"] = f"{fmt_ts(row['stream_start'])}-{fmt_ts(row['stream_end'])}"
            row["rendered_at"] = time.strftime("%H:%M:%S")
            # seconds from the moment's end reaching our disk to its clip being ready: the segment
            # holding it was closed (mtime) when its own end was recorded
            end = row["stream_end"]
            seg = next(((sp, s0, d) for sp, s0, d in recorder.segments() if s0 <= end < s0 + d), None)
            row["latency_s"] = (round(time.time() - (seg[0].stat().st_mtime - (seg[1] + seg[2] - end) / readrate), 1)
                                if seg and seg[0].exists() else None)
            with lock:
                rows.append(row)
                rows.sort(key=lambda r: r["stream_start"])
                cli.write_outputs(out_dir, rows, title, streamer,
                                  f"live: LLM spend this session ${judge.spent() - spend0:.4f}")
            when = f", ready {row['latency_s']:.0f}s after it happened" if row["latency_s"] is not None else ""
            log(f"CLIP {n}: {row['title']!r} at {row['stream_ts']} (v={row['virality']}){when}")
        except Exception as e:                   # one failed render never stops the stream
            log(f"render {name} failed: {e}")
        finally:
            with lock:
                jobs_left[ctx] -= 1
                if jobs_left[ctx] <= 0 and ctx in done_ticks:
                    jobs_left.pop(ctx, None)
                    shutil.rmtree(ctx, ignore_errors=True)

    done_ticks: set[Path] = set()
    recorder = Recorder(src, rec, readrate=readrate).start()
    whisper_w = Whisper(whisper)
    tick = int(state.get("tick", 0))
    try:
        while True:
            segs = recorder.segments()
            total = recorder.recorded()
            ended = recorder.ended
            if max_minutes and total >= max_minutes * 60 and not ended:
                log(f"reached --max-minutes {max_minutes}; stopping the recorder")
                recorder.stop()
                time.sleep(2)
                continue
            ripe_to = total if ended else total - lag
            if not (ripe_to - done_until >= step or (ended and ripe_to - done_until > 5)):
                if ended:
                    break
                time.sleep(3)
                continue
            tick += 1
            t0 = time.time()
            # ---- context window: the segments covering [done_until - CTX_BEFORE, total]
            ctx_segs = [s for s in segs if s[1] + s[2] > max(0.0, done_until - CTX_BEFORE)]
            off = ctx_segs[0][1]
            ctx = base / f"tick_{tick:05d}"
            if ctx.exists():
                shutil.rmtree(ctx, ignore_errors=True)
            ctx.mkdir(parents=True)
            _concat(ctx_segs, ctx / "source.mp4")
            write_json(ctx / "source.info.json", {"title": title, "uploader": streamer})
            ingest.audio(ctx)
            dur = _dur(ctx / "source.mp4")
            end_stream = off + dur
            # ---- transcribe only what is new (from 1 s before the last kept word)
            a = max(off, heard_until - 1.0)
            if end_stream - a > 1.0:
                sl = ctx / "slice16k.wav"
                subprocess.run([FFMPEG, "-v", "error", "-y", "-ss", f"{a - off:.3f}", "-i", str(ctx / "audio16k.wav"),
                                "-t", f"{end_stream - a:.3f}", str(sl)], check=True)
                new = whisper_w.words(sl, ctx / "slice_words.json")
                keep_to = end_stream if ended else end_stream - 1.5   # a word at the live edge may be cut
                last = heard_until
                for w in new:
                    s_, e_ = w["s"] + a, w["e"] + a
                    if s_ >= heard_until - 0.05 and e_ <= keep_to:
                        words.append({**w, "s": round(s_, 3), "e": round(e_, 3)})
                        last = e_
                # a word crossing keep_to was dropped; it starts within a couple of seconds of the
                # edge, so the next slice (which begins 1 s before heard_until) hears it whole
                heard_until = max(heard_until, last, keep_to - 5)
            # ---- the context's own words doc, in context seconds
            ctx_words = [{**w, "s": round(w["s"] - off, 3), "e": round(w["e"] - off, 3)} for w in words
                         if w["s"] >= off]
            words_doc = as_doc(ctx_words)
            write_json(ctx / "words.json", words_doc)
            candidates._PHRASES.clear()        # it caches by id(); a new tick may reuse an old id
            sig = signals.compute(ctx, dur)
            sig["speech"] = signals.speech_rate(words_doc, len(sig["loud"]))
            curve, parts = signals.excitement(sig)
            if genre == "auto":
                genre = cli.detect_genre(title, words_doc, sig, dur)
                log(f"genre: {genre}")
            # ---- the ripe zone, in context seconds
            za, zb = int(max(0.0, done_until - off)), int(min(len(curve) - 1, ripe_to - off))
            masked = np.full_like(curve, -9.0)
            masked[za:zb + 1] = curve[za:zb + 1]
            far = [t for t in candidates.pick_peaks(masked, max_judge, min_gap=25, skip_head=0, floor=floor)
                   if masked[t] >= floor and all(abs(t + off - j) > 25 for j in judged)]
            # a moment whose judge call failed last tick (relay outage) gets one more go
            far += [int(r - off) for r in retry if 0 <= r - off < len(curve) - 5 and all(abs(r - off - t) > 25 for t in far)]
            retry = []
            props = []
            if judge_mode != "local" and scan_pass:
                zone_doc = as_doc([w for w in ctx_words if za - 20 <= w["s"] <= zb + lag])
                if zone_doc["segments"]:
                    try:
                        props = [p for p in scan.scan(zone_doc, sig, parts, title, streamer, model=model,
                                                      prompt=prompt, genre=genre)
                                 if za <= (p["start"] + p["end"]) / 2 <= zb
                                 and all(abs((p["start"] + p["end"]) / 2 + off - j) > 25 for j in judged)]
                    except judge.BudgetExhausted as e:
                        log(f"{e}; skipping the scan")
            centres = scan.merge_candidates(props, curve, far, max_judge,
                                            prefer="scan" if genre == "podcast" else "signal")
            picks = []
            if centres:
                cands = candidates.build(ctx, curve, parts, sig, words_doc, len(centres), centres=centres)
                try:
                    if judge_mode == "local":
                        verdicts = cli.local_judge(cands, words_doc)
                    else:
                        verdicts = judge.judge(cands, title, streamer, ctx, model=model, prompt=prompt,
                                               use_images=not text_only, genre=genre)
                except judge.BudgetExhausted as e:
                    log(f"{e}; using the free local picker")
                    verdicts = cli.local_judge(cands, words_doc)
                answered = {v.get("id") for v in verdicts}
                for c in cands:                  # judged once it has a verdict; a failed call does not use it up
                    (judged if c["id"] in answered else retry).append(c["peak"] + off)
                if retry:
                    log(f"no verdict for {len(retry)} candidates (LLM relay failing); trying them again next tick")
                for v in judge.select(verdicts, len(verdicts), min_len=15 if genre == "podcast" else 10,
                                      min_virality=min_virality):
                    s_, e_ = v["start"] + off, v["end"] + off
                    if v["end"] > dur - 0.5:
                        continue                                      # runs past what is recorded
                    if any(min(e_, b) - max(s_, a2) > 0.3 * (e_ - s_) for a2, b in spans):
                        continue                                      # already clipped
                    picks.append(v)
            # renders run in the background so the stream keeps being judged while a clip encodes;
            # the span is reserved now (dedupe), the row lands when the file is ready, and this
            # tick's context folder is deleted by whichever of its renders finishes last
            meta_ctx = cli.render_probe(ctx / "source.mp4") if picks else None
            jobs_left[ctx] = 0
            for v in picks:
                if max_clips and reserved[0] >= max_clips:
                    break
                reserved[0] += 1
                n = reserved[0]
                spans.append([v["start"] + off, v["end"] + off])
                jobs_left[ctx] += 1
                pool.submit(_render, n, v, ctx, off, meta_ctx, words_doc, parts, genre)
            done_until = float(ripe_to)
            log(f"tick {tick}: stream {fmt_ts(off)}-{fmt_ts(end_stream)} zone {fmt_ts(za + off)}-{fmt_ts(zb + off)} "
                f"| peaks {len(far)} scan {len(props)} judged {len(centres)} clipped {len(picks)} "
                f"| {time.time() - t0:.0f}s | clips {len(rows)} | LLM ${judge.spent() - spend0:.4f}")
            write_json(base / "state.json", {"tick": tick, "done_until": done_until, "heard_until": heard_until,
                                             "judged": judged, "retry": retry, "spans": spans, "words": words})
            with lock:
                done_ticks.add(ctx)
                if not jobs_left.get(ctx):
                    jobs_left.pop(ctx, None)
                    shutil.rmtree(ctx, ignore_errors=True)
            if not keep_source:                     # keep only what the next context needs
                for p, s0, d in segs:
                    if s0 + d < done_until - CTX_BEFORE - 60:
                        p.unlink(missing_ok=True)
            if max_clips and reserved[0] >= max_clips:
                log(f"reached --max-clips {max_clips}")
                break
            if ended and done_until >= total - 1:
                break
    finally:
        recorder.stop()
        whisper_w.close()
        pool.shutdown(wait=True)                  # let clips already picked finish
    log(f"live done: {len(rows)} clips -> {out_dir}  (LLM this session ${judge.spent() - spend0:.4f})")
    return out_dir
