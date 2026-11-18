"""The studio's job queue: clip a stream, or render a GTA video from a script.

One job runs at a time. Two clip jobs at once would put two Whisper models and two render pools on
one 6 GB card, which is slower than running them in turn. Jobs survive a restart: a job that was
running when the studio stopped goes back to the front of the queue, and the pipeline's own caches
(transcript, signals, verdicts, finished clips) mean it picks up where it stopped.

Every job comes from somewhere (`origin`: the website, or a Telegram chat and message). When it
finishes, the callbacks registered with `on_done` run, which is how the bot delivers results.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
CLIPPER = HERE.parent
GTA = CLIPPER.parent / "gta"
from .settings import STATE  # noqa: E402
LOGS = STATE / "logs"
LOGS.mkdir(parents=True, exist_ok=True)
FILE = STATE / "jobs.json"
LOCK = threading.RLock()
_done_hooks: list = []
_procs: dict[str, subprocess.Popen] = {}


def _load() -> list[dict]:
    try:
        jobs = json.loads(FILE.read_text(encoding="utf-8"))
    except Exception:
        return []
    for j in jobs:                      # jobs written by the first studio version had no kind
        j.setdefault("kind", "clip")
        j.setdefault("created", j.get("started", 0))
    return jobs


def _save(jobs: list[dict]) -> None:
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(jobs, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(FILE)


def _edit(jid: str, **fields) -> dict | None:
    with LOCK:
        jobs = _load()
        for j in jobs:
            if j["id"] == jid:
                j.update(fields)
                _save(jobs)
                return j
    return None


def on_done(fn) -> None:
    _done_hooks.append(fn)


# ------------------------------------------------------------------ commands

CLIP_FLAGS = {"clips": "--clips", "streamer": "--streamer", "style": "--style", "layout": "--layout",
              "genre": "--genre", "split_order": "--split-order", "prompt": "--prompt",
              "min_virality": "--min-virality", "jobs": "--jobs"}


def clip_cmd(o: dict) -> list[str]:
    cmd = [sys.executable, "-u", "-m", "clipper", "run", o["source"]]
    for k, flag in CLIP_FLAGS.items():
        v = o.get(k)
        if v not in (None, "", "auto", 0, "0"):
            cmd += [flag, str(v)]
    if o.get("judge") == "local":
        cmd += ["--judge", "local"]
    elif o.get("judge") == "llm-text-only":
        cmd.append("--text-only")
    for k, flag in {"no_cold_open": "--no-cold-open", "keep_dead_air": "--keep-dead-air",
                    "keep_source": "--keep-source", "no_scan": "--no-scan"}.items():
        if o.get(k):
            cmd.append(flag)
    return cmd


def gta_cmd(o: dict) -> list[str]:
    script = Path(o["script"])
    cmd = [sys.executable, "-u", "-m", "gtavid.make", str(script), "--out", str(GTA / "out" / script.stem)]
    if o.get("voiceover"):
        cmd += ["--voiceover", str(o["voiceover"])]
    if o.get("no_music"):
        cmd.append("--no-music")
    return cmd


# ------------------------------------------------------------------ queue API

def add(kind: str, options: dict, origin: dict | None = None, title: str = "") -> dict:
    if kind not in ("clip", "gta"):
        raise ValueError(kind)
    job = {"id": uuid.uuid4().hex[:8], "kind": kind, "options": options, "origin": origin or {"from": "web"},
           "title": title or options.get("source") or Path(options.get("script", "")).stem,
           "status": "queued", "created": time.time()}
    with LOCK:
        jobs = _load()
        jobs.insert(0, job)
        _save(jobs[:200])
    return job


def get(jid: str) -> dict | None:
    return next((j for j in _load() if j["id"] == jid), None)


def cancel(jid: str) -> bool:
    j = get(jid)
    if not j or j["status"] not in ("queued", "running"):
        return False
    _edit(jid, status="cancelled", ended=time.time())
    p = _procs.get(jid)
    if p and p.poll() is None:
        # the clipper spawns ffmpeg and whisper children; kill the whole tree
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True)
        else:
            p.kill()
    return True


def retry(jid: str) -> dict | None:
    j = get(jid)
    if not j or j["status"] in ("queued", "running"):
        return None
    return add(j["kind"], j["options"], j.get("origin"), j.get("title", ""))


def remove(jid: str) -> None:
    with LOCK:
        _save([j for j in _load() if not (j["id"] == jid and j["status"] not in ("queued", "running"))])


# ------------------------------------------------------------------ progress

STAGES = {"clip": ["Downloading", "Transcribing", "Finding moments", "Rendering", "Done"],
          "gta": ["Voice", "Picking footage", "Rendering", "Done"]}


def log_lines(jid: str) -> list[str]:
    f = LOGS / f"{jid}.log"
    return f.read_text(encoding="utf-8", errors="ignore").splitlines() if f.exists() else []


def progress(job: dict) -> dict:
    """Stage, a 0-1 fraction and the latest line, read from the job's own log."""
    lines = log_lines(job["id"])
    text = "\n".join(lines)
    out = {"stage": STAGES[job["kind"]][0], "fraction": 0.0, "last": "", "title": job.get("title", "")}
    steps = [l for l in lines if l.startswith("[")]
    out["last"] = (steps[-1] if steps else (lines[-1] if lines else ""))[:200]
    if job["kind"] == "clip":
        m = re.search(r"source: '(.+?)' by (.*?) - ([\d:]+)", text) or re.search(r'source: "(.+?)" by (.*?) - ([\d:]+)', text)
        if m:
            out["title"], out["streamer"], out["length"] = m.group(1), m.group(2), m.group(3)
        sel = re.search(r"selected (\d+) clips", text)
        rendered = len(re.findall(r"\] render ", text))
        if job["status"] == "done":
            out.update(stage="Done", fraction=1.0)
        elif sel:
            n = int(sel.group(1)) or 1
            out.update(stage="Rendering", fraction=0.45 + 0.55 * min(1, rendered / n), rendered=rendered, total=n)
        elif "genre:" in text:
            out.update(stage="Finding moments", fraction=0.35)
        elif m:
            out.update(stage="Transcribing", fraction=0.15)
        else:
            dl = re.findall(r"\[download\]\s+([\d.]+)%", text)
            out.update(stage="Downloading", fraction=0.12 * (float(dl[-1]) / 100 if dl else 0.0))
        spend = re.findall(r"LLM spend to date \$([\d.]+)", text)
        if spend:
            out["llm_spend"] = float(spend[-1])
    else:
        if job["status"] == "done":
            out.update(stage="Done", fraction=1.0)
        elif "plan:" in text:
            out.update(stage="Rendering", fraction=0.4)
        elif "voice:" in text or "voiceover:" in text:
            out.update(stage="Picking footage", fraction=0.3)
        else:
            out.update(stage="Voice", fraction=0.05)
    return out


def result_dir(job: dict) -> Path | None:
    if job["kind"] == "gta":
        d = GTA / "out" / Path(job["options"]["script"]).stem
        return d if (d / "video.mp4").exists() else None
    m = re.findall(r"done in [\d.]+ min -> (.+?)\s+\(LLM", "\n".join(log_lines(job["id"])))
    return Path(m[-1].strip()) if m else None


# ------------------------------------------------------------------ runner

def _run(job: dict) -> None:
    jid = job["id"]
    cmd = clip_cmd(job["options"]) if job["kind"] == "clip" else gta_cmd(job["options"])
    cwd = CLIPPER if job["kind"] == "clip" else GTA
    log = open(LOGS / f"{jid}.log", "a", encoding="utf-8")
    log.write(f"$ {' '.join(cmd)}\n")
    log.flush()
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    p = subprocess.Popen(cmd, cwd=str(cwd), stdout=log, stderr=subprocess.STDOUT, creationflags=flags, env=env)
    _procs[jid] = p
    _edit(jid, status="running", started=time.time(), pid=p.pid)
    rc = p.wait()
    log.close()
    _procs.pop(jid, None)
    cur = get(jid) or job
    if cur["status"] == "cancelled":
        return
    status = "done" if rc == 0 else "failed"
    err = ""
    if rc:
        tail = [l for l in log_lines(jid)[-40:] if l.strip()]
        err = next((l for l in reversed(tail) if "Error" in l or "error" in l), tail[-1] if tail else f"exit {rc}")
    res = result_dir(cur) if status == "done" else None
    final = _edit(jid, status=status, ended=time.time(), error=err[:300], out=str(res) if res else "")
    for fn in _done_hooks:
        try:
            fn(final)
        except Exception as e:          # a delivery failure must never stop the queue
            print(f"[jobs] done-hook failed for {jid}: {e}", flush=True)


def _loop() -> None:
    with LOCK:                          # whatever was running when the studio stopped runs again first
        jobs = _load()
        for j in jobs:
            if j["status"] == "running":
                j["status"], j["note"] = "queued", "resumed after a restart"
        _save(jobs)
    while True:
        time.sleep(2)
        with LOCK:
            jobs = _load()
            if any(j["status"] == "running" for j in jobs):
                continue
            nxt = next((j for j in reversed(jobs) if j["status"] == "queued"), None)
        if nxt:
            try:
                _run(nxt)
            except Exception as e:
                _edit(nxt["id"], status="failed", error=str(e)[:300], ended=time.time())


_started = False


def start() -> None:
    global _started
    if not _started:
        _started = True
        threading.Thread(target=_loop, daemon=True, name="jobs").start()


def listing(limit: int = 40) -> list[dict]:
    out = []
    for j in _load()[:limit]:
        out.append({**j, "progress": progress(j)})
    return out
