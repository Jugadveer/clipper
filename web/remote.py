"""Using the studio from a phone: a passcode, and a free Cloudflare quick tunnel.

On this PC the studio needs no login. Anything arriving through the tunnel (Cloudflare adds
`CF-Connecting-IP` / `CF-Ray` to every request it forwards) must carry a session cookie, which
the login page issues for the right passcode. The tunnel never starts until a passcode is set,
so the studio is never on the internet without one.

`cloudflared tunnel --url` needs no Cloudflare account and costs nothing; its address
(https://<words>.trycloudflare.com) changes each time it starts, which is why the bot's /site
command reads it from here instead of anyone writing it down.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
from pathlib import Path

from . import settings

HERE = Path(__file__).resolve().parent
COOKIE = "studio_session"
_tunnel = {"url": "", "proc": None, "error": "", "fixed": "", "fixed_error": ""}


def hash_passcode(code: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(8)
    dk = hashlib.pbkdf2_hmac("sha256", code.encode(), salt.encode(), 200_000)
    return f"{salt}${dk.hex()}"


def check_passcode(code: str) -> bool:
    h = settings.load()["remote"]["passcode_hash"]
    if not h or "$" not in h:
        return False
    salt = h.split("$", 1)[0]
    return hmac.compare_digest(hash_passcode(code, salt), h)


def make_session(days: int = 30) -> str:
    exp = str(int(time.time() + days * 86400))
    key = settings.load()["remote"]["session_key"].encode()
    return exp + "." + hmac.new(key, exp.encode(), "sha256").hexdigest()


def session_ok(token: str | None) -> bool:
    if not token or "." not in token:
        return False
    exp, sig = token.split(".", 1)
    key = settings.load()["remote"]["session_key"].encode()
    good = hmac.new(key, exp.encode(), "sha256").hexdigest()
    return hmac.compare_digest(sig, good) and exp.isdigit() and int(exp) > time.time()


def is_local(request) -> bool:
    """Same machine, and not forwarded by the tunnel (which also connects from 127.0.0.1)."""
    host = request.client.host if request.client else ""
    forwarded = any(h in request.headers for h in ("cf-connecting-ip", "cf-ray", "x-forwarded-for"))
    return host in ("127.0.0.1", "::1", "localhost") and not forwarded


# ------------------------------------------------------------------ tunnel

def cloudflared() -> str | None:
    local = HERE / "bin" / ("cloudflared.exe" if os.name == "nt" else "cloudflared")
    return str(local) if local.exists() else shutil.which("cloudflared")


def public_url() -> str:
    """The address to hand out: the fixed Vercel one once it points at the live tunnel."""
    return _tunnel["fixed"] or _tunnel["url"]


def _point_vercel(url: str) -> None:
    from . import vercel_link
    try:
        fixed = vercel_link.deploy(url)
        if _tunnel["url"] == url:                # the tunnel may have restarted meanwhile
            _tunnel.update(fixed=fixed, fixed_error="")
    except Exception as e:                       # the trycloudflare address keeps working regardless
        _tunnel.update(fixed="", fixed_error=str(e)[:300])


def tunnel_status() -> dict:
    p = _tunnel["proc"]
    return {"available": bool(cloudflared()), "running": bool(p and p.poll() is None),
            "url": _tunnel["url"], "error": _tunnel["error"],
            "fixed": _tunnel["fixed"], "fixed_error": _tunnel["fixed_error"],
            "enabled": settings.load()["remote"].get("tunnel", False)}


def start_tunnel(port: int = 8765) -> dict:
    exe = cloudflared()
    if not exe:
        _tunnel["error"] = "cloudflared is not installed"
        return tunnel_status()
    if not settings.load()["remote"]["passcode_hash"]:
        _tunnel["error"] = "set a passcode first"
        return tunnel_status()
    if _tunnel["proc"] and _tunnel["proc"].poll() is None:
        return tunnel_status()
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    p = subprocess.Popen([exe, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, creationflags=flags)
    _tunnel.update(proc=p, url="", error="")

    def read():
        for line in p.stdout:
            m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
            if m and not _tunnel["url"]:
                _tunnel["url"] = m.group(0)
                from . import vercel_link
                if vercel_link.enabled():            # a fixed https://<alias>.vercel.app in front of it
                    threading.Thread(target=_point_vercel, args=(m.group(0),), daemon=True).start()
        _tunnel.update(url="", fixed="", error=_tunnel["error"] or "tunnel stopped")
    threading.Thread(target=read, daemon=True).start()
    for _ in range(40):                      # the address usually appears within 5-10 s
        if _tunnel["url"] or p.poll() is not None:
            break
        time.sleep(0.5)
    return tunnel_status()


def stop_tunnel() -> dict:
    p = _tunnel["proc"]
    if p and p.poll() is None:
        p.terminate()
    _tunnel.update(proc=None, url="", fixed="")
    return tunnel_status()
