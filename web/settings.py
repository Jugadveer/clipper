"""One settings file for the studio, the bot and posting: web/state/settings.json (git-ignored).

Secrets (the bot token, mailbox secrets, the remote passcode hash) live here and never leave
this PC except the token and mailbox secrets, which the mailbox deploy copies into the
Cloudflare Worker's own secret store.
"""
from __future__ import annotations

import json
import os
import secrets
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE = Path(os.environ.get("STUDIO_STATE") or HERE / "state")   # tests point this elsewhere
STATE.mkdir(exist_ok=True)
FILE = STATE / "settings.json"
_LOCK = threading.Lock()

DEFAULTS = {
    "posting": {"times": ["12:00", "17:00", "20:00"], "shorts_tag": True,
                # a short the picker scored at least this high is queued without asking (0 = ask)
                "autopost_min_virality": 0},
    "clip_defaults": {"style": "mix", "judge": "llm", "clips": 0},
    "bot": {"token": "", "allowed": {}, "pair_code": "", "send_top": 8, "offset": 0},
    "mailbox": {"url": "", "pull_secret": "", "hook_secret": ""},
    "remote": {"passcode_hash": "", "session_key": ""},
}


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = _merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


def load() -> dict:
    try:
        cur = json.loads(FILE.read_text(encoding="utf-8"))
    except Exception:
        cur = {}
    s = _merge(DEFAULTS, cur)
    changed = False
    if not s["bot"]["pair_code"]:
        s["bot"]["pair_code"] = f"{secrets.randbelow(900000) + 100000}"
        changed = True
    if not s["remote"]["session_key"]:
        s["remote"]["session_key"] = secrets.token_hex(32)
        changed = True
    if changed:
        save(s)
    return s


def save(s: dict) -> None:
    with _LOCK:
        tmp = FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(s, indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(FILE)


def update(section: str, **values) -> dict:
    s = load()
    s[section].update(values)
    save(s)
    return s


def public(s: dict | None = None) -> dict:
    """What the website may show: no token, no secrets, no hashes."""
    s = s or load()
    tok = s["bot"]["token"]
    return {"posting": s["posting"], "clip_defaults": s["clip_defaults"],
            "bot": {"configured": bool(tok), "token_hint": (tok.split(":")[0] + ":…" + tok[-4:]) if tok else "",
                    "allowed": s["bot"]["allowed"], "pair_code": s["bot"]["pair_code"],
                    "send_top": s["bot"]["send_top"]},
            "mailbox": {"configured": bool(s["mailbox"]["url"]), "url": s["mailbox"]["url"]},
            "remote": {"passcode_set": bool(s["remote"]["passcode_hash"])}}
