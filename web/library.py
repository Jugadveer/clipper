"""Everything the studio can post, and the YouTube upload queue.

A library item is a finished short (work/<source>/clips/*.mp4) or a GTA long-form video
(gta/out/<name>/video.mp4). Its title, description, tags and review status ("skipped") can be
edited without touching the pipeline's files: edits live in state/meta_overrides.json.

Uploads go out as private with YouTube's own `publishAt`, so a scheduled video goes public on time
whether or not this PC is on. Slots come from the posting times in settings; a new video takes the
first free slot, never one already taken by another queued video.
"""
from __future__ import annotations

import datetime as dt
import json
import threading
import time
import uuid
from pathlib import Path

from . import settings, youtube

HERE = Path(__file__).resolve().parent
CLIPPER = HERE.parent
WORK = CLIPPER / "work"
GTA_OUT = CLIPPER.parent / "gta" / "out"
STATE = settings.STATE
OVERRIDES = STATE / "meta_overrides.json"
QUEUE = STATE / "uploads.json"
STATS = STATE / "stats.json"
LOCK = threading.RLock()


def load_json(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(p: Path, obj) -> None:
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


# ------------------------------------------------------------------ library

def transcript(work: str, a: float, b: float) -> str:
    wd = load_json(WORK / work / "words.json", {})
    return " ".join(w["w"] for s in wd.get("segments", []) for w in s["words"] if a <= w["s"] <= b)


def _status_map() -> dict[str, dict]:
    out = {}
    for x in load_json(QUEUE, []):
        if x["status"] != "removed":
            out[x["item"]] = x
    return out


def items() -> list[dict]:
    ov = load_json(OVERRIDES, {})
    posted = _status_map()
    out = []
    for cj in sorted(WORK.glob("*/clips/clips.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        work = cj.parent.parent
        info = load_json(work / "source.info.json", {})
        for r in load_json(cj, []):
            mp4 = cj.parent / r["file"]
            if not mp4.exists():
                continue
            iid = f"short:{work.name}:{r['file']}"
            podcast = r.get("layout") in ("speaker", "stack") or "podcast" in info.get("title", "").lower()
            it = {"id": iid, "kind": "short", "source": info.get("title", work.name), "work": work.name,
                  "streamer": info.get("uploader") or info.get("channel") or "",
                  "genre": "podcast" if podcast else "stream",
                  "video": f"/media/clips/{work.name}/clips/{r['file']}",
                  "thumb": f"/media/clips/{work.name}/clips/{r['thumb']}" if r.get("thumb") else "",
                  "path": str(mp4), "size": mp4.stat().st_size, "made": mp4.stat().st_mtime,
                  "duration": r.get("duration"), "virality": r.get("virality"), "style": r.get("style"),
                  "layout": r.get("layout"), "title": r.get("title", ""), "description": r.get("description", ""),
                  "tags": [t for t in (r.get("hashtags") or "").split(",") if t], "hook": r.get("hook", ""),
                  "why": r.get("why", ""), "end_caption": r.get("end_caption", ""),
                  "source_url": r.get("source_url", ""), "source_ts": r.get("source_ts", ""),
                  "source_start": r.get("source_start"), "source_end": r.get("source_end")}
            it.update(ov.get(iid, {}))
            out.append(it)
    if GTA_OUT.exists():
        for up in sorted(GTA_OUT.glob("*/upload.json")):
            mp4 = up.parent / "video.mp4"
            if not mp4.exists():
                continue
            d = load_json(up, {})
            iid = f"long:{up.parent.name}"
            it = {"id": iid, "kind": "long", "source": "GTA long-form", "work": up.parent.name, "streamer": "",
                  "genre": "gta", "video": f"/media/gta/{up.parent.name}/video.mp4",
                  "thumb": f"/media/gta/{up.parent.name}/thumbnail.jpg", "path": str(mp4),
                  "thumb_path": str(up.parent / "thumbnail.jpg"), "size": mp4.stat().st_size,
                  "made": mp4.stat().st_mtime, "duration": d.get("duration_s"), "title": d.get("title", ""),
                  "description": d.get("description", ""), "tags": d.get("tags", []), "style": "long-form"}
            it.update(ov.get(iid, {}))
            out.append(it)
    for it in out:
        q = posted.get(it["id"])
        if q:
            it["state"] = {"pending": "queued", "uploading": "uploading", "uploaded": "posted",
                           "failed": "failed"}.get(q["status"], q["status"])
            it["publish_at"], it["video_id"] = q.get("publish_at"), q.get("video_id")
        else:
            it["state"] = it.get("review") or "new"          # new | skipped
    return out


def item(iid: str) -> dict | None:
    return next((x for x in items() if x["id"] == iid), None)


def set_meta(iid: str, **fields) -> None:
    keep = {k: v for k, v in fields.items() if k in ("title", "description", "tags", "review")}
    with LOCK:
        ov = load_json(OVERRIDES, {})
        ov.setdefault(iid, {}).update(keep)
        save_json(OVERRIDES, ov)


# ------------------------------------------------------------------ schedule

def _local_tz():
    return dt.datetime.now().astimezone().tzinfo


def free_slots(n: int, start: dt.date | None = None, times: list[str] | None = None) -> list[str]:
    """The next n posting times (RFC3339 UTC) not already taken by a queued upload."""
    times = sorted(times or settings.load()["posting"]["times"])
    taken = {x.get("publish_at") for x in load_json(QUEUE, []) if x["status"] in ("pending", "uploading", "uploaded")}
    tz = _local_tz()
    day = start or dt.date.today()
    soon = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=20)
    out: list[str] = []
    for _ in range(3660):
        for t in times:
            hh, mm = map(int, t.split(":"))
            at = dt.datetime(day.year, day.month, day.day, hh, mm, tzinfo=tz).astimezone(dt.timezone.utc)
            iso = at.strftime("%Y-%m-%dT%H:%M:%SZ")
            if at > soon and iso not in taken:
                out.append(iso)
                if len(out) == n:
                    return out
        day += dt.timedelta(days=1)
    return out


def enqueue(ids: list[str], privacy: str = "scheduled", start: dt.date | None = None,
            times: list[str] | None = None, origin: str = "web") -> list[dict]:
    lib = {x["id"]: x for x in items()}
    ids = [i for i in ids if i in lib and lib[i]["state"] not in ("queued", "uploading", "posted")]
    slots = free_slots(len(ids), start, times) if privacy == "scheduled" else [None] * len(ids)
    tag = settings.load()["posting"]["shorts_tag"]
    added = []
    with LOCK:
        q = load_json(QUEUE, [])
        for iid, at in zip(ids, slots):
            it = lib[iid]
            title = it["title"]
            if it["kind"] == "short" and tag and "#shorts" not in title.lower():
                title = f"{title} #shorts"
            row = {"qid": uuid.uuid4().hex[:8], "item": iid, "path": it["path"], "thumb": it.get("thumb_path"),
                   "title": title[:100], "description": it["description"], "tags": it["tags"],
                   "publish_at": at, "privacy": privacy, "kind": it["kind"], "style": it.get("style"),
                   "duration": it.get("duration"), "status": "pending", "added": time.time(), "origin": origin}
            q.append(row)
            added.append(row)
        save_json(QUEUE, q)
        ov = load_json(OVERRIDES, {})
        for iid in ids:
            ov.get(iid, {}).pop("review", None)
        save_json(OVERRIDES, ov)
    return added


def queue() -> list[dict]:
    return load_json(QUEUE, [])


def unqueue(qid: str) -> bool:
    with LOCK:
        q = load_json(QUEUE, [])
        before = len(q)
        q = [x for x in q if not (x["qid"] == qid and x["status"] in ("pending", "failed"))]
        save_json(QUEUE, q)
    return len(q) < before


def unqueue_item(iid: str) -> bool:
    row = next((x for x in queue() if x["item"] == iid and x["status"] in ("pending", "failed")), None)
    return unqueue(row["qid"]) if row else False


# ------------------------------------------------------------------ uploader

_upload_hooks: list = []


def on_uploaded(fn) -> None:
    _upload_hooks.append(fn)


def uploader_loop() -> None:
    """One upload at a time. Quota exhausted (about 6 uploads a day on the default quota): wait an
    hour and try again; YouTube resets the quota at midnight Pacific."""
    while True:
        time.sleep(10)
        if not youtube.connected():
            continue
        with LOCK:
            q = load_json(QUEUE, [])
            job = next((x for x in q if x["status"] == "pending"), None)
            if job:
                job["status"] = "uploading"
                save_json(QUEUE, q)
        if not job:
            continue
        try:
            vid = youtube.upload(Path(job["path"]), job["title"], job["description"], job["tags"],
                                 privacy="public" if job["privacy"] == "public" else
                                 ("unlisted" if job["privacy"] == "unlisted" else "private"),
                                 publish_at=job.get("publish_at") if job["privacy"] == "scheduled" else None,
                                 category="20")
            upd = {"status": "uploaded", "video_id": vid, "uploaded": time.time(), "note": ""}
            if job.get("thumb") and Path(job["thumb"]).exists():
                try:
                    youtube.set_thumbnail(vid, Path(job["thumb"]))
                except Exception as e:
                    upd["thumb_error"] = str(e)[:200]
        except youtube.QuotaExceeded:
            upd = {"status": "pending", "note": "YouTube's daily upload quota is used up; trying again in an hour"}
        except Exception as e:
            upd = {"status": "failed", "error": str(e)[:300]}
        with LOCK:
            q = load_json(QUEUE, [])
            for x in q:
                if x["qid"] == job["qid"]:
                    x.update(upd)
                    job = x
            save_json(QUEUE, q)
        if upd["status"] == "uploaded":
            for fn in _upload_hooks:
                try:
                    fn(job)
                except Exception as e:
                    print(f"[uploader] hook failed: {e}", flush=True)
        if "quota" in upd.get("note", ""):
            time.sleep(3600)


def stats() -> dict:
    return load_json(STATS, {})


def refresh_stats() -> dict:
    ids = [x["video_id"] for x in queue() if x.get("video_id")]
    if not ids:
        return {}
    s = youtube.stats(ids)
    save_json(STATS, {"at": time.time(), "videos": s})
    return s


def performance() -> list[dict]:
    st = stats().get("videos", {})
    groups: dict = {}
    for x in queue():
        v = st.get(x.get("video_id") or "")
        if not v:
            continue
        d = float(x.get("duration") or 0)
        length = "under 20 s" if d < 20 else "20-35 s" if d < 35 else "35-60 s" if d < 60 else "over 60 s"
        for dim, val in (("Style", x.get("style") or "?"), ("Length", length),
                         ("Type", "Short" if x["kind"] == "short" else "Long-form")):
            g = groups.setdefault((dim, val), {"n": 0, "views": 0, "likes": 0})
            g["n"] += 1
            g["views"] += v["views"]
            g["likes"] += v["likes"]
    return sorted(({"dim": k[0], "value": k[1], "videos": g["n"], "avg_views": round(g["views"] / g["n"]),
                    "like_rate": round(g["likes"] / max(1, g["views"]) * 100, 2)} for k, g in groups.items()),
                  key=lambda r: (r["dim"], -r["avg_views"]))
