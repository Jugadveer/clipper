"""End-to-end test of the Telegram bot against a fake Telegram, in a throwaway state folder.

  D:\\BOT\\.venv\\Scripts\\python.exe -m web.tests.test_bot          (from D:\\BOT\\clipper)

Runs the real poll loop, parser, job queue (jobs are queued, not run), library, upload queue and
delivery. Uses finished shorts already in work/ as the "job output", so nothing is clipped and
no AI is called. Prints each step; exits non-zero on the first failure.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

STATE = Path(tempfile.mkdtemp(prefix="studio_test_"))
os.environ["STUDIO_STATE"] = str(STATE)
os.environ["TELEGRAM_API"] = "http://127.0.0.1:8799"

from web import bot, jobs, library, settings  # noqa: E402
from web.tests.fake_telegram import FakeTelegram  # noqa: E402

CHAT, STRANGER = 4242, 999
CLIPPER = Path(__file__).resolve().parents[2]


def check(name: str, cond) -> None:
    print(("PASS " if cond else "FAIL ") + name, flush=True)
    if not cond:
        sys.exit(1)


def main() -> None:
    tg = FakeTelegram(8799).start()
    settings.update("bot", token="123:FAKE", send_top=2)
    code = settings.load()["bot"]["pair_code"]
    bot.start()
    tg.wait_for(lambda: tg.calls("deleteWebhook"))
    check("direct mode clears any webhook", True)

    # ---- pairing
    tg.inject_text(STRANGER, "https://youtu.be/abc")
    tg.wait_for(lambda: any("private" in t for t in tg.texts()))
    check("a stranger is refused and nothing is queued", not jobs.listing())
    tg.inject_text(CHAT, f"/start {code}")
    tg.wait_for(lambda: any("You're in" in t for t in tg.texts()))
    check("the pairing code lets the friend in", str(CHAT) in settings.load()["bot"]["allowed"])

    # ---- a clip request read back in words
    tg.inject_text(CHAT, "12 shorts podcast hormozi https://youtu.be/xyz only when he talks about money")
    tg.wait_for(lambda: any("On it" in t for t in tg.texts()))
    j = jobs.listing()[0]
    o = j["options"]
    check("request parsed: count, genre, style, instruction",
          (o["source"], o["clips"], o["genre"], o["style"], o["prompt"]) ==
          ("https://youtu.be/xyz", 12, "podcast", "hormozi", "when he talks about money"))
    check("the job remembers which chat asked", j["origin"]["chat"] == CHAT)
    cmd = jobs.clip_cmd(o)
    check("command carries the options", all(x in cmd for x in ("--clips", "12", "--prompt", "--style", "hormozi")))

    # ---- delivery of a finished job (real shorts already in work/)
    work = next((p.parent.parent for p in sorted((CLIPPER / "work").glob("*/clips/clips.json"))), None)
    if work is None:
        print("SKIP delivery, Post/Skip/Rename: no finished clips in work/ yet (run one clip job first)")
    else:
        done = jobs._edit(j["id"], status="done", started=time.time() - 600, ended=time.time(),
                          out=str(work / "clips"))
        n0 = len(tg.calls("sendVideo"))
        bot.deliver_job(done)
        tg.wait_for(lambda: len(tg.calls("sendVideo")) >= n0 + 2, timeout=120)
        check("summary message sent", any("shorts from" in t for t in tg.texts()))
        vids = tg.calls("sendVideo")[-2:]
        check("best two shorts sent with Post/Skip buttons", all("post:" in v["reply_markup"] for v in vids))
        tg.wait_for(lambda: any("more in this batch" in t for t in tg.texts()))
        check("the rest are offered behind a button", True)

        # ---- Post, Skip, Rename
        import json as _j
        key1 = _j.loads(vids[0]["reply_markup"])["inline_keyboard"][0][0]["callback_data"]
        key2 = _j.loads(vids[1]["reply_markup"])["inline_keyboard"][0][1]["callback_data"]
        tg.press(CHAT, 777, key1)
        tg.wait_for(lambda: tg.calls("editMessageReplyMarkup"))
        q = library.queue()
        check("Post puts the short in the upload queue at a posting time",
              len(q) == 1 and q[0]["publish_at"] and q[0]["origin"] == f"tg:{CHAT}")
        check("the button now shows when it posts", "Posting" in tg.calls("editMessageReplyMarkup")[-1]["reply_markup"])
        tg.press(CHAT, 778, key2)
        iid2 = bot._iid(key2.split(":")[1])
        tg.wait_for(lambda: library.item(iid2)["state"] == "skipped")
        check("Skip marks the short skipped", True)
        tg.press(CHAT, 778, "ren:" + key2.split(":")[1])
        tg.wait_for(lambda: any("Send the new title" in t for t in tg.texts()))
        tg.inject_text(CHAT, "He Really Said That On Camera 😭")
        tg.wait_for(lambda: library.item(iid2)["title"] == "He Really Said That On Camera 😭")
        check("Rename takes the next message as the title", True)
        q1 = library.queue()[0]["publish_at"]
        rows = library.enqueue([iid2])
        check("a second short takes the next free slot, not the same one", rows and rows[0]["publish_at"] != q1)

    # ---- status and queue
    tg.inject_text(CHAT, "/queue")
    tg.wait_for(lambda: any(t.startswith(("Coming up", "Nothing scheduled")) for t in tg.texts()))
    tg.inject_text(CHAT, "/status")
    tg.wait_for(lambda: any("uploads scheduled" in t for t in tg.texts()))
    check("/queue and /status answer", True)

    # ---- GTA (only with the separate gta project beside the clipper)
    if not (CLIPPER.parent / "gta" / "scripts" / "pilot_leonida.txt").exists():
        print("SKIP GTA script, voiceover and /gta: the gta project is not installed here")
    else:
        tg.inject_file(CHAT, CLIPPER.parent / "gta" / "scripts" / "pilot_leonida.txt")
        tg.wait_for(lambda: any(t.startswith("📄 Script") for t in tg.texts()))
        check("a .txt script is read and costed in minutes", True)
        audio = STATE / "read.mp3"
        import subprocess
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=220:d=2", str(audio)], check=True)
        tg.inject_file(CHAT, audio, kind="voice", mime="audio/ogg")
        tg.wait_for(lambda: any(t.startswith("🎬 Making") for t in tg.texts()))
        g = jobs.listing()[0]
        check("the voiceover starts a GTA job with that recording",
              g["kind"] == "gta" and Path(g["options"]["voiceover"]).exists() and "--voiceover" in jobs.gta_cmd(g["options"]))

        # ---- a topic for Claude
        tg.inject_text(CHAT, "/gta every hidden detail in trailer 2")
        tg.wait_for(lambda: bot.inbox())
        row = bot.inbox()[0]
        check("/gta files a topic for Claude", row["topic"] == "every hidden detail in trailer 2" and row["chat"] == CHAT)
        job = bot.inbox_script_ready(row["id"], "scripts/pilot_leonida.txt")
        tg.wait_for(lambda: any(t.startswith("✍️ Script written") for t in tg.texts()))
        check("a written script becomes a render job and the friend is told", job["kind"] == "gta")

    # ---- cancel from the chat
    tg.inject_text(CHAT, "/cancel")
    tg.wait_for(lambda: any(t.startswith("Stopped") for t in tg.texts()))
    check("/cancel stops the friend's job", any(j["status"] == "cancelled" for j in jobs.listing()))
    print(f"\nall passed; {len(tg.sent)} Telegram calls; state in {STATE}")


if __name__ == "__main__":
    main()
