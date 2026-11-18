"""The Telegram bot: a friend sends a link, a script or a voiceover from their phone; the studio on
this PC makes the shorts or the GTA video and sends them back with Post / Skip / Rename buttons.

Where messages come from:
  * direct  - this PC asks Telegram for new messages (getUpdates). Needs only the bot token, but
              Telegram keeps a message for 24 hours at most, so a weekend with the laptop shut
              loses what was sent on Friday.
  * mailbox - a free Cloudflare Worker (clipper/mailbox) receives every message the moment it is
              sent, answers "got it, the PC is off" at once, and keeps it until this PC collects
              it. Nothing is lost however long the laptop stays shut.

Nothing here calls a language model. Requests are read with `asks.parse`; a GTA video that
needs a script WRITTEN goes to the Claude inbox, which Claude Code picks up when it is next open.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import requests

from . import asks, jobs, library, remote, settings
from .telegram import SEND_LIMIT, Bot, TelegramError

HERE = Path(__file__).resolve().parent
STATE = settings.STATE
FILE = STATE / "bot_state.json"
INBOX = STATE / "claude_inbox.json"
UPLOADS = HERE.parent / "work" / "_telegram"
GTA = HERE.parent.parent / "gta"
if str(GTA) not in sys.path:
    sys.path.insert(0, str(GTA))          # gtavid.script reads the scripts friends send
API_BASE = os.environ.get("TELEGRAM_API", "https://api.telegram.org")
LOCK = threading.RLock()

HELP = """Send me a link to a stream, VOD or podcast (YouTube, Twitch, Kick) and I'll cut it into shorts and send them here.

Add words to change what you get:
• 20 shorts — how many
• podcast / stream — what kind of video
• hormozi, karaoke, pop, clean, viral, mix — caption style
• cam on top — streamer cam above the game
• free picker — $0 mode, no AI
• only when he rages at chat — clip only moments like that

Example: 15 shorts hormozi https://youtu.be/…

GTA videos: send a script as a .txt file, then a voiceover (mp3 or a voice note) or tap “free voice”.
/gta <topic> asks Claude to write one next time the PC is on.

/status — what's running   /queue — what's going up next
/more — send the rest of the last batch   /cancel — stop the current job
/site — link to the website (when the PC is on)"""


def _load() -> dict:
    try:
        return json.loads(FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _state() -> dict:
    s = _load()
    for k, v in {"keys": {}, "pending_rename": {}, "pending_script": {}, "requests": [], "rest": {}}.items():
        s.setdefault(k, v)
    return s


def _save(s: dict) -> None:
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(FILE)


def _key(iid: str) -> str:
    """Callback data is capped at 64 bytes; an item id is longer, so buttons carry a short key."""
    k = hashlib.sha1(iid.encode()).hexdigest()[:10]
    with LOCK:
        s = _state()
        s["keys"][k] = iid
        _save(s)
    return k


def _iid(key: str) -> str | None:
    return _state()["keys"].get(key)


def _log_request(chat, name: str, text: str, kind: str, job: str = "", status: str = "received") -> str:
    rid = uuid.uuid4().hex[:8]
    with LOCK:
        s = _state()
        s["requests"].insert(0, {"id": rid, "chat": chat, "name": name, "text": text[:500], "kind": kind,
                                 "job": job, "status": status, "at": time.time()})
        s["requests"] = s["requests"][:300]
        _save(s)
    return rid


def requests_log(limit: int = 100) -> list[dict]:
    return _state()["requests"][:limit]


def fmt_when(iso: str | None) -> str:
    if not iso:
        return "now"
    import datetime as dt
    t = dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
    today = dt.date.today()
    day = "today" if t.date() == today else "tomorrow" if t.date() == today + dt.timedelta(days=1) else t.strftime("%a %d %b")
    return f"{day} {t.strftime('%H:%M')}"


# ------------------------------------------------------------------ claude inbox

def inbox() -> list[dict]:
    return library.load_json(INBOX, [])


def inbox_add(topic: str, chat, name: str) -> dict:
    row = {"id": uuid.uuid4().hex[:8], "topic": topic, "chat": chat, "name": name,
           "status": "waiting", "created": time.time()}
    with LOCK:
        rows = inbox()
        rows.insert(0, row)
        library.save_json(INBOX, rows)
    return row


def inbox_update(rid: str, **fields) -> dict | None:
    with LOCK:
        rows = inbox()
        for r in rows:
            if r["id"] == rid:
                r.update(fields)
                library.save_json(INBOX, rows)
                return r
    return None


def inbox_script_ready(rid: str, script_path: str) -> dict:
    """Claude Code calls this (POST /api/claude-inbox/<id>/script) once it has written the script."""
    row = next((r for r in inbox() if r["id"] == rid), None)
    if not row:
        raise KeyError(rid)
    p = Path(script_path)
    if not p.is_absolute():
        p = GTA / p
    if not p.exists():
        raise FileNotFoundError(str(p))
    job = jobs.add("gta", {"script": str(p)}, origin={"from": "telegram", "chat": row.get("chat")},
                   title=_script_title(p))
    inbox_update(rid, status="rendering", script=str(p), job=job["id"])
    if row.get("chat"):
        say(row["chat"], f"✍️ Script written: {_script_title(p)}\nRendering the video now; I'll send it here.")
    return job


def _script_title(p: Path) -> str:
    for line in p.read_text(encoding="utf-8-sig", errors="ignore").splitlines()[:12]:
        m = re.match(r"\s*title\s*:\s*(.+)", line, re.I)
        if m:
            return m.group(1).strip()
    return p.stem


# ------------------------------------------------------------------ sending

_bot: Bot | None = None
_outbox: "list[tuple]" = []
_out_cv = threading.Condition()


def bot() -> Bot | None:
    global _bot
    tok = settings.load()["bot"]["token"]
    if not tok:
        return None
    if _bot is None or _bot.token != tok:
        _bot = Bot(tok, API_BASE)
    return _bot


def say(chat, text: str, buttons=None, force_reply: bool = False) -> dict | None:
    b = bot()
    if not b:
        return None
    try:
        return b.send(chat, text, buttons, force_reply=force_reply)
    except TelegramError as e:
        print(f"[bot] send to {chat} failed: {e}", flush=True)
        return None


def _later(fn, *args) -> None:
    """Slow sends (videos) run on the delivery thread, in order, so the bot keeps answering."""
    with _out_cv:
        _outbox.append((fn, args))
        _out_cv.notify()


def _delivery_loop() -> None:
    while True:
        with _out_cv:
            while not _outbox:
                _out_cv.wait()
            fn, args = _outbox.pop(0)
        try:
            fn(*args)
        except Exception as e:
            print(f"[bot] delivery failed: {e}", flush=True)


def _sendable(path: Path) -> Path:
    """Telegram takes files up to 50 MB from a bot. A bigger short gets a smaller copy for the chat
    only; the full file is what uploads to YouTube."""
    if path.stat().st_size <= SEND_LIMIT - (1 << 20):
        return path
    small = UPLOADS / "previews" / (path.stem + ".tg.mp4")
    if not small.exists():
        small.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(path), "-vf", "scale=720:-2",
                        "-c:v", "libx264", "-crf", "28", "-preset", "veryfast", "-c:a", "aac", "-b:a", "128k",
                        "-movflags", "+faststart", str(small)], check=True)
    return small


def _buttons(iid: str, state: str = "new", when: str = "") -> list:
    k = _key(iid)
    if state in ("queued", "uploading"):
        return [[(f"🗓 Posting {when} · undo", f"unpost:{k}")]]
    if state == "posted":
        return [[("✅ Posted", "noop")]]
    if state == "skipped":
        return [[("Skipped · undo", f"unskip:{k}")]]
    return [[("✅ Post", f"post:{k}"), ("⏭ Skip", f"skip:{k}")], [("✏️ Rename", f"ren:{k}")]]


def _caption(it: dict) -> str:
    bits = []
    if it.get("virality"):
        bits.append(f"🔥 {it['virality']}")
    if it.get("duration"):
        d = float(it["duration"])
        bits.append(f"{d / 60:.1f} min" if d >= 120 else f"{d:.0f} s")
    if it.get("style") and it["kind"] == "short":
        bits.append(it["style"])
    return f"{it['title']}\n{' · '.join(bits)}"


def _send_item(chat, it: dict) -> None:
    b = bot()
    if not b:
        return
    when = fmt_when(it.get("publish_at")) if it.get("state") in ("queued", "uploading") else ""
    if it["kind"] == "long":
        # a 10-minute video is far over 50 MB: thumbnail + a 45-second preview, the full file stays here
        b.send_photo(chat, Path(it["thumb_path"]), _caption(it), _buttons(it["id"], it["state"], when))
        prev = UPLOADS / "previews" / f"{it['work']}.preview.mp4"
        if not prev.exists():
            prev.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-t", "45", "-i", it["path"], "-vf", "scale=854:-2",
                            "-c:v", "libx264", "-crf", "27", "-preset", "veryfast", "-c:a", "aac",
                            "-b:a", "128k", "-movflags", "+faststart", str(prev)], check=True)
        b.send_video(chat, prev, "First 45 seconds. The full video is on the PC and uploads in full.")
        return
    b.send_video(chat, _sendable(Path(it["path"])), _caption(it), _buttons(it["id"], it["state"], when))


def deliver_job(job: dict) -> None:
    """Runs when a job finishes: report, then send the results (best first)."""
    o = job.get("origin") or {}
    chat = o.get("chat")
    if o.get("from") != "telegram" or not chat:
        return
    p = jobs.progress(job)
    title = p.get("title") or job.get("title", "")
    if job["status"] != "done":
        say(chat, f"❌ Couldn't finish “{title[:80]}”.\n{job.get('error', '')[:300]}",
            [[("↻ Try again", f"retry:{job['id']}")]])
        return
    out = Path(job.get("out") or "")
    if job["kind"] == "gta":
        it = library.item(f"long:{out.name}")
        if it:
            mins = (job.get("ended", 0) - job.get("started", 0)) / 60
            say(chat, f"🎬 GTA video ready: {it['title']}\nMade in {mins:.0f} min, $0. Tap ✅ Post to schedule it.")
            _later(_send_item, chat, it)
        return
    work = out.parent.name if out.name == "clips" else out.name
    made = sorted((x for x in library.items() if x["kind"] == "short" and x["work"] == work),
                  key=lambda x: -(x.get("virality") or 0))
    if not made:
        say(chat, f"Finished “{title[:80]}” but it found no moments worth a short.")
        return
    cfg = settings.load()
    auto = cfg["posting"]["autopost_min_virality"]
    if auto:
        hot = [x["id"] for x in made if (x.get("virality") or 0) >= auto and x["state"] == "new"]
        if hot:
            library.enqueue(hot, origin=f"tg:{chat}")
            made = sorted((x for x in library.items() if x["kind"] == "short" and x["work"] == work),
                          key=lambda x: -(x.get("virality") or 0))
    n = cfg["bot"]["send_top"]
    mins = (job.get("ended", 0) - job.get("started", 0)) / 60
    cost = p.get("llm_spend")
    lines = [f"✅ {len(made)} shorts from “{title[:80]}”", f"Made in {mins:.0f} min" +
             (f" · AI picker cost so far ${cost:.4f}" if cost is not None else "")]
    queued = [x for x in made if x["state"] in ("queued", "uploading")]
    if queued:
        lines.append(f"{len(queued)} scored {auto}+ and are already scheduled (autopost).")
    lines.append(f"Sending the best {min(n, len(made))}. ✅ Post schedules one at the next free time.")
    say(chat, "\n".join(lines))
    for it in made[:n]:
        _later(_send_item, chat, it)
    rest = [x["id"] for x in made[n:]]
    if rest:
        with LOCK:
            s = _state()
            s["rest"][str(chat)] = rest
            _save(s)
        _later(lambda: say(chat, f"{len(rest)} more in this batch.", [[(f"📦 Send the other {len(rest)}", "more")]]))


def notify_uploaded(row: dict) -> None:
    origin = row.get("origin") or ""
    if not origin.startswith("tg:"):
        return
    when = f"goes public {fmt_when(row['publish_at'])}" if row.get("publish_at") else row.get("privacy", "")
    say(origin[3:], f"📤 Uploaded to YouTube, {when}:\n{row['title']}\nhttps://youtu.be/{row['video_id']}")


# ------------------------------------------------------------------ receiving

def _allowed(chat) -> bool:
    return str(chat) in settings.load()["bot"]["allowed"]


def _status_text() -> str:
    js = [j for j in jobs.listing(30) if j["status"] in ("running", "queued")]
    q = [x for x in library.queue() if x["status"] in ("pending", "uploading")]
    lines = []
    for j in js:
        pr = j["progress"]
        if j["status"] == "running":
            extra = f" {pr.get('rendered', 0)}/{pr.get('total', '?')}" if pr["stage"] == "Rendering" and j["kind"] == "clip" else ""
            lines.append(f"▶ {pr['title'][:60]}\n   {pr['stage']}{extra} · {round(pr['fraction'] * 100)}%")
        else:
            lines.append(f"⏳ waiting: {pr['title'][:60]}")
    if not js:
        lines.append("Nothing running. Send a link to start.")
    lines.append(f"\n🗓 {len(q)} uploads scheduled" + (f", next {fmt_when(min(x['publish_at'] or '' for x in q) or None)}" if q else ""))
    return "\n".join(lines)


def _queue_text() -> str:
    q = sorted((x for x in library.queue() if x["status"] in ("pending", "uploading")),
               key=lambda x: x.get("publish_at") or "")
    if not q:
        return "Nothing scheduled. Tap ✅ Post under a short to schedule it."
    return "Coming up:\n" + "\n".join(f"• {fmt_when(x.get('publish_at'))} — {x['title'][:70]}" for x in q[:15]) + (
        f"\n…and {len(q) - 15} more" if len(q) > 15 else "")


def _start_clip(chat, name: str, text: str, source: str | None = None, msg_id: int | None = None) -> None:
    req = asks.parse(text)
    if source:
        req["source"] = source
    defaults = settings.load()["clip_defaults"]
    opts = {"style": defaults["style"], "judge": defaults["judge"], **req}
    ahead = [j for j in jobs.listing(50) if j["status"] in ("running", "queued")]
    job = jobs.add("clip", opts, origin={"from": "telegram", "chat": chat, "msg": msg_id})
    _log_request(chat, name, text, "clip", job["id"], "queued")
    wait = f"\n{len(ahead)} job(s) ahead of it." if ahead else "\nStarting now."
    say(chat, f"🎬 On it: {asks.describe(req)}{wait}\nI'll send the shorts here when they're done.",
        [[("✖ Cancel", f"cancel:{job['id']}")]])


def _save_upload(b: Bot, file_id: str, name: str) -> Path:
    safe = re.sub(r"[^\w.\-]+", "_", name)[-80:] or "file"
    return b.download(file_id, UPLOADS / f"{time.strftime('%Y%m%d-%H%M%S')}_{safe}")


def _got_script(chat, name: str, path: Path) -> None:
    from gtavid import script as gscript
    text = path.read_text(encoding="utf-8-sig", errors="ignore")
    try:
        sc = gscript.parse(text)
    except Exception as e:
        say(chat, f"That script didn't parse: {e}")
        return
    words = sum(len(p.split()) for p in sc.paragraphs)
    title = sc.meta.get("title") or path.stem
    dest = GTA / "scripts" / f"tg_{time.strftime('%Y%m%d_%H%M')}_{re.sub(r'[^a-z0-9]+', '_', title.lower())[:40]}.txt"
    dest.write_text(text, encoding="utf-8")
    with LOCK:
        s = _state()
        s["pending_script"][str(chat)] = {"script": str(dest), "title": title, "at": time.time()}
        _save(s)
    _log_request(chat, name, f"script: {title}", "gta-script", status="waiting for voice")
    say(chat, f"📄 Script: {title}\n{words} words, about {words / 186:.0f} min read.\n\n"
              "Send the voiceover now (mp3, m4a or a voice note), or use the free voice.",
        [[("▶ Make it with the free voice", "gta:tts")]])


def _start_gta(chat, voice: Path | None) -> None:
    with LOCK:
        s = _state()
        pend = s["pending_script"].pop(str(chat), None)
        _save(s)
    if not pend:
        say(chat, "Send the script (.txt) first, then the voiceover.")
        return
    opts = {"script": pend["script"]}
    if voice:
        opts["voiceover"] = str(voice)
    job = jobs.add("gta", opts, origin={"from": "telegram", "chat": chat}, title=pend["title"])
    say(chat, f"🎬 Making “{pend['title']}” with {'your voiceover' if voice else 'the free voice'}. "
              "About 15-25 minutes once it starts.", [[("✖ Cancel", f"cancel:{job['id']}")]])


def handle(u: dict) -> None:
    b = bot()
    if not b:
        return
    if "callback_query" in u:
        return _callback(b, u["callback_query"])
    m = u.get("message") or {}
    chat = m.get("chat", {}).get("id")
    if not chat:
        return
    name = " ".join(filter(None, [m.get("from", {}).get("first_name"), m.get("from", {}).get("last_name")])) or str(chat)
    text = (m.get("text") or m.get("caption") or "").strip()
    cfg = settings.load()

    code = cfg["bot"]["pair_code"]
    if not _allowed(chat):
        if re.fullmatch(rf"(/start\s+)?{code}", text):
            allowed = dict(cfg["bot"]["allowed"])
            allowed[str(chat)] = name
            settings.update("bot", allowed=allowed)
            _log_request(chat, name, "paired", "pair", status="done")
            say(chat, f"You're in, {name.split()[0]}. 👋\n\n{HELP}")
        else:
            say(chat, "This bot is private. Ask the owner for the 6-digit pairing code and send it here.")
        return

    st = _state()
    low = text.lower()
    # files first: a document or audio can carry a caption that is not a command
    doc = m.get("document")
    audio = m.get("audio") or m.get("voice")
    video = m.get("video")
    if doc and (doc.get("mime_type", "").startswith("text/") or doc.get("file_name", "").lower().endswith(".txt")):
        return _got_script(chat, name, _save_upload(b, doc["file_id"], doc.get("file_name", "script.txt")))
    if audio or (doc and doc.get("mime_type", "").startswith("audio/")):
        f = audio or doc
        if str(chat) not in st["pending_script"]:
            say(chat, "Got audio, but there's no script waiting for it. Send the script (.txt) first.")
            return
        try:
            path = _save_upload(b, f["file_id"], f.get("file_name") or "voiceover.ogg")
        except TelegramError as e:
            say(chat, f"Couldn't download that ({e}). Telegram lets bots fetch files up to 20 MB: "
                      "send it as mp3 or m4a, or as a voice note.")
            return
        return _start_gta(chat, path)
    if video or (doc and doc.get("mime_type", "").startswith("video/")):
        f = video or doc
        try:
            path = _save_upload(b, f["file_id"], f.get("file_name") or "video.mp4")
        except TelegramError:
            say(chat, "That video is over Telegram's 20 MB limit for bots. Send a link to it instead "
                      "(YouTube, Twitch, Kick, or a direct download link).")
            return
        return _start_clip(chat, name, text, source=str(path), msg_id=m.get("message_id"))

    if str(chat) in st["pending_rename"] and text and not text.startswith("/"):
        iid = st["pending_rename"].pop(str(chat))
        _save(st)
        library.set_meta(iid, title=text[:100])
        say(chat, f"Renamed to: {text[:100]}")
        return

    if low in ("/start", "/help", "help", "hi", "hello"):
        say(chat, HELP)
    elif low.startswith("/status"):
        say(chat, _status_text())
    elif low.startswith("/queue"):
        say(chat, _queue_text())
    elif low.startswith("/more"):
        _send_rest(chat)
    elif low.startswith("/cancel"):
        mine = [j for j in jobs.listing(50) if j["status"] in ("running", "queued")
                and (j.get("origin") or {}).get("chat") == chat]
        if mine:
            jobs.cancel(mine[-1]["id"] if mine[-1]["status"] == "running" else mine[0]["id"])
            say(chat, f"Stopped: {mine[0]['progress']['title'][:70]}")
        else:
            say(chat, "Nothing of yours is running.")
    elif low.startswith("/site"):
        url = remote.public_url()
        say(chat, f"Website: {url}\nUse the passcode the owner gave you." if url else
            "The website link isn't on right now (the PC is off or remote access is switched off).")
    elif low.startswith("/gta") or re.match(r"(make\s+)?(a\s+)?gta\s*6?\s+video\s+(about|on)\s+", low):
        topic = re.sub(r"^/gta\s*|^(make\s+)?(a\s+)?gta\s*6?\s+video\s+(about|on)\s+", "", text, flags=re.I).strip()
        if not topic:
            say(chat, "Tell me the topic: /gta every hidden detail in trailer 2")
            return
        row = inbox_add(topic, chat, name)
        _log_request(chat, name, text, "gta-topic", status="waiting for Claude")
        say(chat, f"📝 Noted: “{topic}”. A GTA video needs its script written and fact-checked; Claude does "
                  f"that the next time the PC and Claude Code are open, then the video renders and comes here. "
                  f"({len([r for r in inbox() if r['status'] == 'waiting'])} topic(s) waiting.)")
    elif asks.URL.search(text):
        _start_clip(chat, name, text, msg_id=m.get("message_id"))
    else:
        say(chat, "Send me a link to clip (YouTube, Twitch or Kick), a GTA script as a .txt file, or /help.")


def _send_rest(chat) -> None:
    with LOCK:
        s = _state()
        rest = s["rest"].pop(str(chat), [])
        _save(s)
    if not rest:
        say(chat, "Nothing left from the last batch.")
        return
    lib = {x["id"]: x for x in library.items()}
    for iid in rest:
        if iid in lib:
            _later(_send_item, chat, lib[iid])


def _callback(b: Bot, cq: dict) -> None:
    data = cq.get("data") or ""
    chat = cq.get("message", {}).get("chat", {}).get("id")
    mid = cq.get("message", {}).get("message_id")
    if not _allowed(chat):
        b.answer(cq["id"], "This bot is private.")
        return
    act, _, arg = data.partition(":")
    note = ""
    if act in ("post", "skip", "unpost", "unskip", "ren"):
        iid = _iid(arg)
        it = library.item(iid) if iid else None
        if not it:
            b.answer(cq["id"], "That video is gone from the PC.")
            return
        if act == "post":
            rows = library.enqueue([iid], origin=f"tg:{chat}")
            it = library.item(iid)
            when = fmt_when(rows[0]["publish_at"]) if rows else fmt_when(it.get("publish_at"))
            note = f"Scheduled for {when}" + ("" if __import__("web.youtube", fromlist=["x"]).connected()
                                              else ". YouTube isn't connected yet; it uploads once it is.")
            b.edit_buttons(chat, mid, _buttons(iid, it["state"], when))
        elif act == "unpost":
            library.unqueue_item(iid)
            b.edit_buttons(chat, mid, _buttons(iid, "new"))
            note = "Taken off the schedule"
        elif act == "skip":
            library.set_meta(iid, review="skipped")
            b.edit_buttons(chat, mid, _buttons(iid, "skipped"))
            note = "Skipped"
        elif act == "unskip":
            library.set_meta(iid, review="new")
            b.edit_buttons(chat, mid, _buttons(iid, "new"))
        elif act == "ren":
            with LOCK:
                s = _state()
                s["pending_rename"][str(chat)] = iid
                _save(s)
            say(chat, f"Send the new title for:\n{it['title']}", force_reply=True)
    elif act == "more":
        _send_rest(chat)
    elif act == "cancel":
        note = "Stopped" if jobs.cancel(arg) else "Already finished"
        b.edit_buttons(chat, mid, None)
    elif act == "retry":
        j = jobs.retry(arg)
        note = "Trying again" if j else "Can't retry that one"
        b.edit_buttons(chat, mid, None)
    elif act == "gta" and arg == "tts":
        b.edit_buttons(chat, mid, None)
        _start_gta(chat, None)
    b.answer(cq["id"], note)


# ------------------------------------------------------------------ the loop

_status = {"mode": "off", "error": "", "last_poll": 0.0, "username": ""}


def status() -> dict:
    return dict(_status)


def _pull_mailbox(mb: dict, acks: list[int], allowed: list[str]) -> list[dict]:
    r = requests.post(mb["url"].rstrip("/") + "/pull", timeout=30,
                      headers={"Authorization": f"Bearer {mb['pull_secret']}"},
                      json={"ack": acks, "allowed": allowed})
    r.raise_for_status()
    return r.json().get("updates", [])


def _loop() -> None:
    mode_set = None
    acks: list[int] = []
    while True:
        cfg = settings.load()
        b = bot()
        if not b:
            _status.update(mode="off", error="no bot token")
            time.sleep(5)
            continue
        mb = cfg["mailbox"]
        want = "mailbox" if mb["url"] and mb["pull_secret"] else "direct"
        try:
            if want != mode_set:
                if want == "mailbox":
                    b.set_webhook(mb["url"].rstrip("/") + "/telegram", mb["hook_secret"])
                else:
                    b.delete_webhook()
                _status["username"] = b.me().get("username", "")
                mode_set = want
            if want == "mailbox":
                ups = _pull_mailbox(mb, acks, list(cfg["bot"]["allowed"]))
                acks = []
            else:
                ups = b.updates(cfg["bot"]["offset"] or 0, wait=20)
            _status.update(mode=want, error="", last_poll=time.time())
        except Exception as e:
            _status.update(mode=want, error=str(e)[:200])
            time.sleep(10)
            continue
        for u in ups:
            try:
                handle(u)
            except Exception as e:
                print(f"[bot] update {u.get('update_id')} failed: {e}", flush=True)
                chat = ((u.get("message") or {}).get("chat") or {}).get("id")
                if chat and _allowed(chat):        # never leave the friend waiting on silence
                    say(chat, f"⚠️ Something went wrong with that: {str(e)[:200]}")
            if want == "mailbox":
                acks.append(u["update_id"])
            else:
                settings.update("bot", offset=u["update_id"] + 1)
        if want == "mailbox" and not ups:
            time.sleep(5)


_started = False


def start() -> None:
    global _started
    if _started:
        return
    _started = True
    jobs.on_done(deliver_job)
    library.on_uploaded(notify_uploaded)
    threading.Thread(target=_loop, daemon=True, name="bot").start()
    threading.Thread(target=_delivery_loop, daemon=True, name="bot-delivery").start()
