"""Put the bot's mailbox on Cloudflare (free plan): one command, run on this PC.

  D:\\BOT\\.venv\\Scripts\\python.exe mailbox\\deploy.py        (from D:\\BOT\\clipper)

It uses wrangler (Cloudflare's CLI, through npx) to: sign in (a browser window, once), create a
D1 database, create the tables, store the bot token and two fresh random secrets as Worker
secrets, deploy worker.js, and save the Worker's address in the studio's settings. The studio
then points the bot's Telegram webhook at the Worker by itself within a few seconds.
Run it again any time; it reuses the database and replaces the secrets.
"""
from __future__ import annotations

import json
import re
import secrets
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from web import settings  # noqa: E402

NAME = "clipper-mailbox"
NPX = "npx.cmd" if sys.platform == "win32" else "npx"
WRANGLER = [NPX, "-y", "wrangler@4"]


def w(*args, stdin: str | None = None, check: bool = True, capture: bool = True) -> str:
    r = subprocess.run(WRANGLER + list(args), cwd=str(HERE), input=stdin, text=True,
                       capture_output=capture, encoding="utf-8", errors="ignore")
    out = (r.stdout or "") + (r.stderr or "")
    if check and r.returncode:
        raise SystemExit(f"wrangler {' '.join(args)} failed:\n{out[-2000:]}")
    return out


def main() -> None:
    s = settings.load()
    token = s["bot"]["token"]
    if not token:
        raise SystemExit("Save the bot token in the studio first (Settings -> Telegram bot).")
    if "not authenticated" in w("whoami", check=False):
        print("Signing in to Cloudflare (a browser window opens)...")
        w("login", capture=False)
    dbs = json.loads(re.search(r"\[.*\]", w("d1", "list", "--json"), re.S).group(0))
    db = next((d for d in dbs if d.get("name") == NAME), None)
    if not db:
        print("Creating the D1 database...")
        w("d1", "create", NAME)
        dbs = json.loads(re.search(r"\[.*\]", w("d1", "list", "--json"), re.S).group(0))
        db = next(d for d in dbs if d.get("name") == NAME)
    (HERE / "wrangler.toml").write_text(
        f'name = "{NAME}"\nmain = "worker.js"\ncompatibility_date = "2025-06-01"\n\n'
        f'[[d1_databases]]\nbinding = "DB"\ndatabase_name = "{NAME}"\ndatabase_id = "{db["uuid"]}"\n',
        encoding="utf-8")
    print("Creating the tables...")
    w("d1", "execute", NAME, "--remote", "--file", "schema.sql", "-y")
    hook, pull = secrets.token_urlsafe(24), secrets.token_urlsafe(32)
    print("Deploying the Worker...")
    out = w("deploy")
    url = re.search(r"https://[\w.-]+\.workers\.dev", out)
    if not url:
        raise SystemExit(f"Deployed, but no workers.dev address in the output:\n{out[-1500:]}")
    for k, v in (("BOT_TOKEN", token), ("HOOK_SECRET", hook), ("PULL_SECRET", pull)):
        w("secret", "put", k, stdin=v)
    settings.update("mailbox", url=url.group(0), hook_secret=hook, pull_secret=pull)
    print(f"Mailbox live at {url.group(0)}. The studio switches the bot over within a few seconds.")


if __name__ == "__main__":
    main()
