"""The mailbox Worker against a fake Telegram: messages sent while the PC is off are kept,
answered at once, and handed to the studio when it comes back.

  1. cd <a copy of clipper/mailbox>; npx wrangler dev --port 8787   (TG_API var -> :8797,
     .dev.vars with BOT_TOKEN, HOOK_SECRET=hooksecret, PULL_SECRET=pullsecret; kv 'allowed' = ["4242"])
  2. D:\\BOT\\.venv\\Scripts\\python.exe -m web.tests.test_mailbox
"""
from __future__ import annotations

import os
import sys
import tempfile

import requests

os.environ["STUDIO_STATE"] = tempfile.mkdtemp(prefix="studio_mailbox_")
os.environ["TELEGRAM_API"] = "http://127.0.0.1:8797"

from web import bot, jobs, settings  # noqa: E402
from web.tests.fake_telegram import FakeTelegram  # noqa: E402

WORKER = os.environ.get("MAILBOX_URL", "http://127.0.0.1:8787")
CHAT = 4242


def check(name: str, cond) -> None:
    print(("PASS " if cond else "FAIL ") + name, flush=True)
    if not cond:
        sys.exit(1)


def main() -> None:
    tg = FakeTelegram(8797).start()
    tg.webhook = {"url": WORKER + "/telegram", "secret_token": "hooksecret"}   # as if already set

    # ---- the PC is off
    tg.inject_text(CHAT, "10 shorts https://youtu.be/offline1")
    tg.wait_for(lambda: any("PC is off" in t for t in tg.texts()), timeout=15)
    check("while the PC is off the Worker answers at once", True)
    tg.inject_text(CHAT, "https://youtu.be/offline2 every time he dies")
    tg.wait_for(lambda: sum("PC is off" in t for t in tg.texts()) >= 2, timeout=15)
    check("…and counts what is waiting", "2 message(s) waiting" in tg.texts()[-1])
    r = requests.post(WORKER + "/telegram", json={"update_id": 1}, headers={"X-Telegram-Bot-Api-Secret-Token": "x"})
    check("a webhook call without the secret is refused", r.status_code == 403)
    r = requests.post(WORKER + "/pull", json={}, headers={"Authorization": "Bearer wrong"})
    check("a pull without the secret is refused", r.status_code == 403)

    # ---- the PC comes back
    settings.update("bot", token="1:FAKE", allowed={str(CHAT): "Friend"})
    settings.update("mailbox", url=WORKER, pull_secret="pullsecret", hook_secret="hooksecret")
    bot.start()
    tg.wait_for(lambda: len([j for j in jobs.listing() if j["kind"] == "clip"]) >= 2, timeout=40)
    srcs = sorted(j["options"]["source"] for j in jobs.listing())
    check("both waiting messages become jobs, in order", srcs == ["https://youtu.be/offline1", "https://youtu.be/offline2"])
    check("the studio points the webhook at the Worker", tg.calls("setWebhook")[-1]["url"] == WORKER + "/telegram")
    tg.wait_for(lambda: sum(t.startswith("🎬 On it") for t in tg.texts()) >= 2, timeout=20)
    check("the PC answers each one once it has it", True)
    left = requests.post(WORKER + "/pull", json={}, headers={"Authorization": "Bearer pullsecret"}).json()
    tg.wait_for(lambda: not requests.post(WORKER + "/pull", json={}, headers={"Authorization": "Bearer pullsecret"}).json()["updates"], timeout=20)
    check("collected messages are deleted from the mailbox", True)

    # ---- while the PC is on, the Worker stays quiet and the PC answers
    n = sum("PC is off" in t for t in tg.texts())
    tg.inject_text(CHAT, "/status")
    tg.wait_for(lambda: any("uploads scheduled" in t for t in tg.texts()), timeout=30)
    check("with the PC on, no 'PC is off' reply", sum("PC is off" in t for t in tg.texts()) == n)
    print("\nall passed", left and "")


if __name__ == "__main__":
    main()
