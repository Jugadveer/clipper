"""YouTube Data API v3 over plain HTTPS (requests): OAuth, resumable upload, thumbnails, stats.

Setup (once, by the channel owner):
  1. console.cloud.google.com -> new project -> enable "YouTube Data API v3"
  2. OAuth consent screen (External, add yourself as a test user)
  3. Credentials -> OAuth client ID -> "Web application", authorised redirect URI
     http://localhost:8765/oauth2callback  -> download JSON -> save as clipper/web/client_secret.json
  4. In the app: YouTube tab -> Connect -> pick the channel.

Scheduling uses YouTube's own `publishAt`: a video is uploaded as private with a publish time and
YouTube makes it public on schedule, whether or not this computer is on.
Quota: an upload costs ~1,600 of the default 10,000 units/day (about 6 uploads a day); the upload
queue waits for the next day when YouTube says the quota is used up.
"""
from __future__ import annotations

import json
import time
import urllib.parse
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
SECRET = HERE / "client_secret.json"
TOKEN = HERE / "youtube_token.json"
REDIRECT = "http://localhost:8765/oauth2callback"
SCOPES = "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly"
API = "https://www.googleapis.com/youtube/v3"


class QuotaExceeded(RuntimeError):
    pass


def _client() -> dict:
    d = json.loads(SECRET.read_text(encoding="utf-8"))
    return d.get("web") or d.get("installed") or d


def configured() -> bool:
    return SECRET.exists()


def connected() -> bool:
    return TOKEN.exists()


def auth_url(state: str = "clipper") -> str:
    c = _client()
    q = {"client_id": c["client_id"], "redirect_uri": REDIRECT, "response_type": "code", "scope": SCOPES,
         "access_type": "offline", "prompt": "consent", "include_granted_scopes": "true", "state": state}
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(q)


def exchange(code: str) -> None:
    c = _client()
    r = requests.post("https://oauth2.googleapis.com/token", timeout=30, data={
        "code": code, "client_id": c["client_id"], "client_secret": c["client_secret"],
        "redirect_uri": REDIRECT, "grant_type": "authorization_code"})
    r.raise_for_status()
    tok = r.json()
    tok["expires_at"] = time.time() + tok.get("expires_in", 3600) - 60
    TOKEN.write_text(json.dumps(tok), encoding="utf-8")


def _token() -> str:
    tok = json.loads(TOKEN.read_text(encoding="utf-8"))
    if time.time() < tok.get("expires_at", 0):
        return tok["access_token"]
    c = _client()
    r = requests.post("https://oauth2.googleapis.com/token", timeout=30, data={
        "client_id": c["client_id"], "client_secret": c["client_secret"],
        "refresh_token": tok["refresh_token"], "grant_type": "refresh_token"})
    r.raise_for_status()
    new = r.json()
    tok["access_token"] = new["access_token"]
    tok["expires_at"] = time.time() + new.get("expires_in", 3600) - 60
    TOKEN.write_text(json.dumps(tok), encoding="utf-8")
    return tok["access_token"]


def _h() -> dict:
    return {"Authorization": f"Bearer {_token()}"}


def _check(r: requests.Response) -> requests.Response:
    if r.status_code == 403 and "quota" in r.text.lower():
        raise QuotaExceeded(r.text[:300])
    r.raise_for_status()
    return r


def channel() -> dict:
    r = _check(requests.get(f"{API}/channels", headers=_h(), timeout=30,
                            params={"part": "snippet,statistics", "mine": "true"}))
    items = r.json().get("items", [])
    if not items:
        return {}
    it = items[0]
    return {"id": it["id"], "title": it["snippet"]["title"],
            "thumb": it["snippet"]["thumbnails"].get("default", {}).get("url"),
            "subscribers": it["statistics"].get("subscriberCount"), "videos": it["statistics"].get("videoCount")}


def upload(path: Path, title: str, description: str, tags: list[str], privacy: str = "private",
           publish_at: str | None = None, category: str = "20", made_for_kids: bool = False,
           progress=None) -> str:
    """Resumable upload; returns the video id. `publish_at` = RFC3339 UTC, forces privacy=private."""
    status = {"privacyStatus": "private" if publish_at else privacy, "selfDeclaredMadeForKids": made_for_kids}
    if publish_at:
        status["publishAt"] = publish_at
    body = {"snippet": {"title": title[:100], "description": description[:5000], "tags": tags[:30],
                        "categoryId": category}, "status": status}
    size = Path(path).stat().st_size
    r = _check(requests.post("https://www.googleapis.com/upload/youtube/v3/videos", timeout=60,
                             params={"uploadType": "resumable", "part": "snippet,status"},
                             headers={**_h(), "X-Upload-Content-Type": "video/mp4",
                                      "X-Upload-Content-Length": str(size),
                                      "Content-Type": "application/json; charset=UTF-8"},
                             data=json.dumps(body)))
    url = r.headers["Location"]
    chunk = 16 * 1024 * 1024
    sent = 0
    with open(path, "rb") as f:
        while sent < size:
            data = f.read(chunk)
            end = sent + len(data) - 1
            for attempt in range(5):
                try:
                    rr = requests.put(url, data=data, timeout=300, headers={
                        **_h(), "Content-Length": str(len(data)), "Content-Range": f"bytes {sent}-{end}/{size}"})
                    break
                except requests.RequestException:
                    if attempt == 4:
                        raise
                    time.sleep(3 * (attempt + 1))
            if rr.status_code in (200, 201):
                return rr.json()["id"]
            if rr.status_code != 308:
                _check(rr)
            sent = end + 1
            if progress:
                progress(sent / size)
    raise RuntimeError("upload did not complete")


def set_thumbnail(video_id: str, jpg: Path) -> None:
    """Custom thumbnails need a verified channel (phone verification)."""
    _check(requests.post("https://www.googleapis.com/upload/youtube/v3/thumbnails/set", timeout=120,
                         params={"videoId": video_id}, headers={**_h(), "Content-Type": "image/jpeg"},
                         data=Path(jpg).read_bytes()))


def stats(ids: list[str]) -> dict:
    out = {}
    for i in range(0, len(ids), 50):
        r = _check(requests.get(f"{API}/videos", headers=_h(), timeout=30,
                                params={"part": "statistics,status,snippet", "id": ",".join(ids[i:i + 50])}))
        for it in r.json().get("items", []):
            s = it.get("statistics", {})
            out[it["id"]] = {"views": int(s.get("viewCount", 0)), "likes": int(s.get("likeCount", 0)),
                             "comments": int(s.get("commentCount", 0)),
                             "status": it["status"].get("privacyStatus"),
                             "publishAt": it["status"].get("publishAt"),
                             "published": it["snippet"].get("publishedAt")}
    return out
