"""clipper studio: make shorts and GTA videos, review them, and schedule them to YouTube.

  D:\\BOT\\.venv\\Scripts\\python.exe -m web.server        (from D:\\BOT\\clipper)  -> http://localhost:8765

One process runs the website, the job queue (one job at a time), the YouTube uploader, the
Telegram bot and, when switched on, the Cloudflare tunnel that lets a phone open the site.
On this PC no login is needed; through the tunnel every page and file needs the passcode.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import bot, jobs, library, meta, remote, settings, youtube
from .telegram import Bot, TelegramError

HERE = Path(__file__).resolve().parent
CLIPPER = HERE.parent
GTA = CLIPPER.parent / "gta"

app = FastAPI(title="clipper studio")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
library.WORK.mkdir(exist_ok=True)
app.mount("/media/clips", StaticFiles(directory=library.WORK), name="clips")
if library.GTA_OUT.exists():
    app.mount("/media/gta", StaticFiles(directory=library.GTA_OUT), name="gta")

OPEN = ("/login", "/api/login", "/static/", "/favicon")


@app.middleware("http")
async def gate(request: Request, call_next):
    path = request.url.path
    if remote.is_local(request) or path.startswith(OPEN) or remote.session_ok(request.cookies.get(remote.COOKIE)):
        request.state.local = remote.is_local(request)
        return await call_next(request)
    if path.startswith("/api/") or path.startswith("/media/"):
        return JSONResponse({"detail": "passcode needed"}, status_code=401)
    return RedirectResponse("/login")


def local_only(request: Request) -> None:
    if not remote.is_local(request):
        raise HTTPException(403, "Only from the PC itself")


# ------------------------------------------------------------------ pages + login

@app.get("/")
def index():
    return FileResponse(HERE / "static" / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/login")
def login_page():
    return FileResponse(HERE / "static" / "login.html", headers={"Cache-Control": "no-cache"})


_fails: dict[str, list[float]] = {}


@app.post("/api/login")
async def login(request: Request):
    who = request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "?")
    recent = [t for t in _fails.get(who, []) if time.time() - t < 600]
    if len(recent) >= 8:
        raise HTTPException(429, "Too many tries. Wait ten minutes.")
    body = await request.json()
    if not remote.check_passcode(str(body.get("passcode", ""))):
        _fails[who] = recent + [time.time()]
        raise HTTPException(401, "Wrong passcode")
    resp = JSONResponse({"ok": True})
    resp.set_cookie(remote.COOKIE, remote.make_session(), max_age=30 * 86400, httponly=True,
                    samesite="lax", secure=not remote.is_local(request))
    return resp


# ------------------------------------------------------------------ overview

@app.get("/api/overview")
def overview(request: Request):
    items = library.items()
    q = [x for x in library.queue() if x["status"] in ("pending", "uploading")]
    js = jobs.listing(12)
    counts = {s: sum(1 for x in items if x["state"] == s) for s in ("new", "queued", "posted", "skipped")}
    counts["shorts"] = sum(1 for x in items if x["kind"] == "short")
    counts["long"] = sum(1 for x in items if x["kind"] == "long")
    nxt = min((x for x in q if x.get("publish_at")), key=lambda x: x["publish_at"], default=None)
    return {"local": remote.is_local(request), "jobs": js, "counts": counts,
            "next_upload": {"title": nxt["title"], "at": nxt["publish_at"]} if nxt else None,
            "queued_uploads": len(q), "bot": bot.status(), "youtube": {"connected": youtube.connected()},
            "tunnel": remote.tunnel_status(), "inbox": [r for r in bot.inbox() if r["status"] == "waiting"],
            "spend": _spend()}


def _spend() -> dict:
    """AI picker spend from the clipper's own ledger: this month, and since the tool was built."""
    month, total = time.strftime("%Y-%m"), 0.0
    this = 0.0
    f = library.WORK / "llm_ledger.jsonl"
    if f.exists():
        for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            total += r.get("usd", 0)
            if str(r.get("t", "")).startswith(month):
                this += r.get("usd", 0)
    return {"month": round(this, 4), "total": round(total, 4)}


# ------------------------------------------------------------------ jobs

_probe_cache: dict[str, dict] = {}


@app.get("/api/probe")
def probe(url: str):
    """Title, length and thumbnail of a link before clipping it (no download)."""
    if url in _probe_cache:
        return _probe_cache[url]
    if not re.match(r"https?://", url):
        p = Path(url)
        if p.exists():
            return {"title": p.name, "duration": None, "local": True}
        raise HTTPException(404, "No such file on this PC")
    try:
        out = subprocess.run([sys.executable, "-m", "yt_dlp", "-J", "--no-warnings", "--no-playlist",
                              "--skip-download", url], capture_output=True, text=True, timeout=45,
                             encoding="utf-8", errors="ignore")
        d = json.loads(out.stdout)
    except Exception:
        raise HTTPException(422, "Couldn't read that link. Check it opens in a browser.")
    dur = d.get("duration")
    r = {"title": d.get("title"), "duration": dur, "uploader": d.get("uploader") or d.get("channel"),
         "thumbnail": d.get("thumbnail"), "is_live": d.get("is_live"),
         "auto_clips": max(5, int(dur / 180)) if dur else None}
    _probe_cache[url] = r
    return r


@app.post("/api/jobs")
async def start_job(req: Request):
    o = await req.json()
    if not o.get("source"):
        raise HTTPException(400, "Paste a link or a file path")
    o.pop("kind", None)
    job = jobs.add("clip", o, title=o.get("title") or o["source"])
    return job


@app.post("/api/gta")
async def start_gta(script_text: str = Form(""), script: UploadFile | None = File(None),
                    voiceover: UploadFile | None = File(None), no_music: bool = Form(False)):
    if not (GTA / "gtavid").is_dir():
        raise HTTPException(501, "GTA videos need the separate gta project next to the clipper; it is not installed here")
    text = script_text
    if script is not None and script.filename:
        text = (await script.read()).decode("utf-8-sig", errors="ignore")
    if not text.strip():
        raise HTTPException(400, "Paste the script or choose a .txt file")
    from gtavid import script as gscript
    try:
        sc = gscript.parse(text)
    except Exception as e:
        raise HTTPException(422, f"The script didn't parse: {e}")
    title = sc.meta.get("title") or "untitled"
    stem = f"web_{time.strftime('%Y%m%d_%H%M')}_{re.sub(r'[^a-z0-9]+', '_', title.lower())[:40]}"
    path = GTA / "scripts" / f"{stem}.txt"
    path.write_text(text, encoding="utf-8")
    opts = {"script": str(path), "no_music": no_music}
    if voiceover is not None and voiceover.filename:
        vo = CLIPPER / "work" / "_uploads" / f"{stem}{Path(voiceover.filename).suffix or '.mp3'}"
        vo.parent.mkdir(parents=True, exist_ok=True)
        vo.write_bytes(await voiceover.read())
        opts["voiceover"] = str(vo)
    return jobs.add("gta", opts, title=title)


@app.get("/api/jobs")
def list_jobs():
    return jobs.listing(40)


@app.get("/api/jobs/{jid}/log")
def job_log(jid: str):
    return {"lines": jobs.log_lines(jid)[-300:]}


@app.post("/api/jobs/{jid}/cancel")
def cancel_job(jid: str):
    return {"ok": jobs.cancel(jid)}


@app.post("/api/jobs/{jid}/retry")
def retry_job(jid: str):
    j = jobs.retry(jid)
    if not j:
        raise HTTPException(400, "Can't retry a job that is still queued or running")
    return j


@app.delete("/api/jobs/{jid}")
def delete_job(jid: str):
    jobs.remove(jid)
    return {"ok": True}


@app.get("/api/options")
def options():
    return {"styles": ["mix", "viral", "pop", "karaoke", "hormozi", "clean"],
            "layouts": ["auto", "split", "single", "speaker", "stack", "fit"],
            "genres": ["auto", "stream", "podcast"], "split_orders": ["game_top", "cam_top"],
            "judges": ["llm", "llm-text-only", "local"], "defaults": settings.load()["clip_defaults"]}


# ------------------------------------------------------------------ library

@app.get("/api/library")
def api_library():
    return library.items()


@app.post("/api/meta/{iid:path}")
async def save_meta(iid: str, req: Request):
    body = await req.json()
    library.set_meta(iid, **body)
    return {"ok": True}


@app.post("/api/review")
async def review(req: Request):
    b = await req.json()
    for iid in b.get("ids", []):
        library.set_meta(iid, review="skipped" if b.get("skip") else "new")
    return {"ok": True}


@app.get("/api/generate/{iid:path}")
def generate_meta(iid: str, seed: int = 0):
    it = library.item(iid)
    if not it:
        raise HTTPException(404)
    clip = dict(it)
    if it["kind"] == "short":
        clip["transcript"] = library.transcript(it["work"], it["source_start"] or 0, it["source_end"] or 0)
    g = meta.generate(clip, it.get("streamer", ""), "podcast" if it["genre"] == "podcast" else "stream", seed)
    if it["kind"] == "long":
        g["titles"] = [it["title"]] + g["titles"][1:]
        g["description"] = it["description"]
        g["tags"] = list(dict.fromkeys(it["tags"] + meta.GTA_TAGS))[:30]
    return g


@app.get("/api/download/{iid:path}")
def download(iid: str):
    it = library.item(iid)
    if not it:
        raise HTTPException(404)
    name = re.sub(r'[<>:"/\\|?*]+', "", it["title"])[:80].strip() or "video"
    return FileResponse(it["path"], media_type="video/mp4", filename=f"{name}.mp4")


# ------------------------------------------------------------------ schedule + youtube

@app.get("/api/slots")
def slots(n: int = 6):
    return library.free_slots(max(1, min(n, 60)))


@app.post("/api/schedule")
async def schedule(req: Request):
    b = await req.json()
    start = dt.date.fromisoformat(b["start"]) if b.get("start") else None
    rows = library.enqueue(b.get("ids", []), b.get("privacy", "scheduled"), start, b.get("times") or None)
    return {"queued": len(rows), "rows": rows}


@app.get("/api/queue")
def api_queue():
    return library.queue()


@app.delete("/api/queue/{qid}")
def unqueue(qid: str):
    return {"ok": library.unqueue(qid)}


@app.post("/api/queue/{qid}/retry")
def requeue(qid: str):
    with library.LOCK:
        q = library.queue()
        for x in q:
            if x["qid"] == qid and x["status"] == "failed":
                x.update(status="pending", error="")
        library.save_json(library.QUEUE, q)
    return {"ok": True}


@app.get("/api/yt/status")
def yt_status():
    st = {"configured": youtube.configured(), "connected": youtube.connected(), "channel": None}
    if st["connected"]:
        try:
            st["channel"] = youtube.channel()
        except Exception as e:
            st["error"] = str(e)[:200]
    return st


@app.get("/api/yt/connect")
def yt_connect(request: Request):
    local_only(request)
    if not youtube.configured():
        raise HTTPException(400, "Save your OAuth client file as clipper/web/client_secret.json first")
    return RedirectResponse(youtube.auth_url())


@app.get("/oauth2callback")
def oauth_cb(request: Request, code: str = "", error: str = ""):
    local_only(request)
    if error or not code:
        return HTMLResponse(f"<p>Not connected: {error}</p><a href='/#settings'>back</a>")
    youtube.exchange(code)
    return RedirectResponse("/#settings")


@app.post("/api/yt/stats")
def refresh_stats():
    try:
        return library.refresh_stats()
    except Exception as e:
        raise HTTPException(502, str(e)[:200])


@app.get("/api/insights")
def insights():
    return {"rows": library.performance(), "stats_at": library.stats().get("at"), "research": RESEARCH}


RESEARCH = [
    ("Length", "Streamer clips that hit millions run 16-33 s; gaming moments run longer, 56-78 s."),
    ("Opening", "No title card. The first spoken word is the hook; captions sit mid-frame, word by word."),
    ("Ending", "Most viral clips end on a frozen frame with one emoji and the punchline in quotes."),
    ("Posting", "Clip channels post 2-4 Shorts a day per account. The default YouTube quota allows about 6 uploads."),
    ("Layout", "On the 12M-view Speed clip the game sits on top and the streamer underneath."),
    ("GTA titles", "BreezeGTA's 1M-view titles put the viewer in them: “You NEED…”, “You WON'T…”."),
    ("GTA footage", "MrBossFTW reuses 31% of source moments across videos; BreezeGTA 15% plus his own graphics."),
]


# ------------------------------------------------------------------ settings, bot, remote

@app.get("/api/settings")
def get_settings():
    s = settings.public()
    s["bot"]["status"] = bot.status()
    s["tunnel"] = remote.tunnel_status()
    s["youtube"] = {"configured": youtube.configured(), "connected": youtube.connected()}
    s["gta_available"] = (GTA / "gtavid").is_dir()     # the GTA long-form project sits beside the clipper, or not at all
    return s


@app.post("/api/settings/{section}")
async def set_settings(section: str, request: Request):
    body = await request.json()
    if section == "posting":
        times = [t.strip() for t in body.get("times", []) if re.fullmatch(r"\d{1,2}:\d{2}", t.strip())]
        vals = {"times": sorted({f"{int(t.split(':')[0]):02d}:{t.split(':')[1]}" for t in times}) or ["12:00"]}
        if "autopost_min_virality" in body:
            vals["autopost_min_virality"] = max(0, min(99, int(body["autopost_min_virality"] or 0)))
        if "shorts_tag" in body:
            vals["shorts_tag"] = bool(body["shorts_tag"])
        settings.update("posting", **vals)
    elif section == "clip_defaults":
        vals = {k: body[k] for k in ("style", "judge") if k in body}
        settings.update("clip_defaults", **vals)
    elif section == "bot":
        local_only(request)
        vals = {}
        if "send_top" in body:
            vals["send_top"] = max(1, min(40, int(body["send_top"])))
        if "token" in body:
            tok = body["token"].strip()
            if tok:
                try:
                    me = Bot(tok, bot.API_BASE).me()
                except (TelegramError, Exception) as e:
                    raise HTTPException(422, f"Telegram didn't accept that token: {e}")
                vals["token"] = tok
                vals["username"] = me.get("username", "")
            else:
                vals["token"] = ""
        if body.get("new_code"):
            import secrets
            vals["pair_code"] = f"{secrets.randbelow(900000) + 100000}"
        if "unpair" in body:
            allowed = dict(settings.load()["bot"]["allowed"])
            allowed.pop(str(body["unpair"]), None)
            vals["allowed"] = allowed
        settings.update("bot", **vals)
    elif section == "remote":
        local_only(request)
        if body.get("passcode"):
            if len(body["passcode"]) < 6:
                raise HTTPException(422, "Use at least 6 characters")
            import secrets
            settings.update("remote", passcode_hash=remote.hash_passcode(body["passcode"]),
                            session_key=secrets.token_hex(32))      # new passcode signs everyone out
        if "tunnel" in body:
            settings.update("remote", tunnel=bool(body["tunnel"]))
            return remote.start_tunnel() if body["tunnel"] else remote.stop_tunnel()
    else:
        raise HTTPException(404)
    return get_settings()


@app.get("/api/requests")
def requests_log():
    return {"requests": bot.requests_log(), "inbox": bot.inbox()}


@app.post("/api/claude-inbox")
async def inbox_add(req: Request):
    b = await req.json()
    if not (b.get("topic") or "").strip():
        raise HTTPException(400, "Write a topic")
    return bot.inbox_add(b["topic"].strip(), None, "website")


@app.post("/api/claude-inbox/{rid}/script")
async def inbox_script(rid: str, request: Request):
    local_only(request)
    b = await request.json()
    try:
        return bot.inbox_script_ready(rid, b["path"])
    except (KeyError, FileNotFoundError) as e:
        raise HTTPException(404, str(e))


@app.post("/api/claude-inbox/{rid}")
async def inbox_status(rid: str, request: Request):
    b = await request.json()
    keep = {k: b[k] for k in ("status", "note") if k in b}
    row = bot.inbox_update(rid, **keep)
    if not row:
        raise HTTPException(404)
    return row


# ------------------------------------------------------------------ start-up

@app.on_event("startup")
def _start():
    jobs.start()
    bot.start()
    threading.Thread(target=library.uploader_loop, daemon=True, name="uploader").start()
    if settings.load()["remote"].get("tunnel"):
        threading.Thread(target=remote.start_tunnel, daemon=True).start()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8765)
