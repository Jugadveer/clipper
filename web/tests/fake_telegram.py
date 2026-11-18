"""A fake Telegram Bot API on localhost, for testing the bot without a real token or phone.

  from web.tests.fake_telegram import FakeTelegram
  tg = FakeTelegram(port=8799).start()      # then TELEGRAM_API=http://127.0.0.1:8799
  tg.inject_text(chat=42, text="https://youtu.be/x")
  tg.sent            # every call the bot made, in order: [(method, params), ...]

Implements what the bot uses: getMe, getUpdates, setWebhook/deleteWebhook, getFile + file
download, sendMessage/sendVideo/sendPhoto, editMessageReplyMarkup, answerCallbackQuery.
`webhook` mode posts injected updates to the webhook URL instead of queueing them, exactly as
Telegram does once a webhook is set (used to test the mailbox Worker).
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path

import requests
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse


class FakeTelegram:
    def __init__(self, port: int = 8799):
        self.port = port
        self.sent: list[tuple[str, dict]] = []
        self.queue: list[dict] = []
        self.files: dict[str, Path] = {}
        self.webhook: dict | None = None
        self._uid = 1000
        self._mid = 500
        self.app = self._build()

    # ------------------------------------------------------------------ test side
    def start(self) -> "FakeTelegram":
        cfg = uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="warning")
        self.server = uvicorn.Server(cfg)
        threading.Thread(target=self.server.run, daemon=True).start()
        for _ in range(50):
            try:
                requests.get(f"http://127.0.0.1:{self.port}/", timeout=1)
                break
            except requests.RequestException:
                time.sleep(0.1)
        return self

    def _push(self, update: dict) -> None:
        self._uid += 1
        update["update_id"] = self._uid
        if self.webhook:
            requests.post(self.webhook["url"], json=update, timeout=30,
                          headers={"X-Telegram-Bot-Api-Secret-Token": self.webhook.get("secret_token", "")})
        else:
            self.queue.append(update)

    def _msg(self, chat: int, **fields) -> dict:
        self._mid += 1
        return {"message_id": self._mid, "date": int(time.time()), "chat": {"id": chat, "type": "private"},
                "from": {"id": chat, "first_name": "Friend", "last_name": "Test"}, **fields}

    def inject_text(self, chat: int, text: str) -> None:
        self._push({"message": self._msg(chat, text=text)})

    def inject_file(self, chat: int, path: Path, kind: str = "document", mime: str = "text/plain",
                    caption: str = "") -> None:
        fid = f"file{len(self.files) + 1}"
        self.files[fid] = Path(path)
        obj = {"file_id": fid, "file_name": Path(path).name, "mime_type": mime, "file_size": Path(path).stat().st_size}
        self._push({"message": self._msg(chat, **{kind: obj}, caption=caption)})

    def press(self, chat: int, message_id: int, data: str) -> None:
        self._push({"callback_query": {"id": f"cb{self._uid}", "data": data, "from": {"id": chat},
                                       "message": {"message_id": message_id, "chat": {"id": chat}}}})

    def calls(self, method: str) -> list[dict]:
        return [p for m, p in self.sent if m == method]

    def texts(self) -> list[str]:
        return [p.get("text", "") for m, p in self.sent if m == "sendMessage"]

    def wait_for(self, pred, timeout: float = 30.0):
        end = time.time() + timeout
        while time.time() < end:
            r = pred()
            if r:
                return r
            time.sleep(0.2)
        raise TimeoutError("condition not met; last calls: " + json.dumps(self.sent[-5:], default=str)[:1500])

    # ------------------------------------------------------------------ API side
    def _build(self) -> FastAPI:
        app = FastAPI()

        @app.get("/")
        def root():
            return {"ok": True}

        @app.get("/file/bot{token}/{path:path}")
        def file(token: str, path: str):
            return FileResponse(self.files[path])

        @app.post("/bot{token}/{method}")
        async def call(token: str, method: str, request: Request):
            if request.headers.get("content-type", "").startswith("application/json"):
                params = {k: (v if isinstance(v, str) else json.dumps(v)) for k, v in (await request.json()).items()}
            else:                                   # Telegram takes JSON or form posts; the bot sends forms
                form = await request.form()
                params = {k: (v if isinstance(v, str) else f"<file {getattr(v, 'filename', '')}>")
                          for k, v in form.items()}
            self.sent.append((method, params))
            ok = lambda result: JSONResponse({"ok": True, "result": result})
            if method == "getMe":
                return ok({"id": 1, "is_bot": True, "username": "fake_clipper_bot"})
            if method == "getUpdates":
                off = int(params.get("offset") or 0)
                wait = float(params.get("timeout") or 0)
                end = time.time() + min(wait, 2)
                while time.time() < end and not [u for u in self.queue if u["update_id"] >= off]:
                    await asyncio.sleep(0.1)          # never block the server: uploads share its loop
                return ok([u for u in self.queue if u["update_id"] >= off])
            if method == "setWebhook":
                self.webhook = {"url": params["url"], "secret_token": params.get("secret_token", "")}
                return ok(True)
            if method == "deleteWebhook":
                self.webhook = None
                return ok(True)
            if method == "getFile":
                fid = params["file_id"]
                return ok({"file_id": fid, "file_path": fid, "file_size": self.files[fid].stat().st_size})
            if method in ("sendMessage", "sendVideo", "sendPhoto"):
                self._mid += 1
                return ok({"message_id": self._mid, "chat": {"id": int(params["chat_id"])}})
            return ok(True)

        return app
