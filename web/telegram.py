"""The few Telegram Bot API calls the bot needs, over plain HTTPS. Free, no SDK.

Limits that shape the bot: a bot may DOWNLOAD files up to 20 MB and SEND files up to 50 MB, and
updates wait on Telegram's servers for 24 hours at most (the mailbox Worker removes that limit).
`base` is overridable so tests run against a fake Telegram on localhost.
"""
from __future__ import annotations

import json
from pathlib import Path

import requests

SEND_LIMIT = 50 * 1024 * 1024
GET_LIMIT = 20 * 1024 * 1024


class TelegramError(RuntimeError):
    pass


def keyboard(rows: list[list[tuple[str, str]]] | None) -> str | None:
    """[[("✅ Post", "post:abc"), ...], ...] -> reply_markup JSON (callback buttons)."""
    if rows is None:
        return None
    return json.dumps({"inline_keyboard": [[{"text": t, "callback_data": d[:64]} for t, d in r] for r in rows]})


class Bot:
    def __init__(self, token: str, base: str = "https://api.telegram.org"):
        self.token = token
        self.base = base.rstrip("/")

    def call(self, method: str, files: dict | None = None, http_timeout: float = 70, **params):
        params = {k: v for k, v in params.items() if v is not None}
        url = f"{self.base}/bot{self.token}/{method}"
        r = requests.post(url, data=params, files=files, timeout=http_timeout)
        try:
            d = r.json()
        except ValueError:
            raise TelegramError(f"{method}: HTTP {r.status_code}")
        if not d.get("ok"):
            raise TelegramError(f"{method}: {d.get('description', r.status_code)}")
        return d["result"]

    # ---------------------------------------------------------------- receive
    def me(self) -> dict:
        return self.call("getMe", http_timeout=15)

    def updates(self, offset: int, wait: int = 25) -> list[dict]:
        return self.call("getUpdates", offset=offset, timeout=wait, http_timeout=wait + 15,
                         allowed_updates=json.dumps(["message", "callback_query"]))

    def set_webhook(self, url: str, secret: str) -> None:
        self.call("setWebhook", url=url, secret_token=secret,
                  allowed_updates=json.dumps(["message", "callback_query"]), http_timeout=20)

    def delete_webhook(self) -> None:
        self.call("deleteWebhook", http_timeout=20)

    def download(self, file_id: str, dest: Path) -> Path:
        f = self.call("getFile", file_id=file_id, http_timeout=30)
        if f.get("file_size", 0) > GET_LIMIT:
            raise TelegramError("file over Telegram's 20 MB bot download limit")
        url = f"{self.base}/file/bot{self.token}/{f['file_path']}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        with requests.get(url, stream=True, timeout=120) as r:
            r.raise_for_status()
            with open(dest, "wb") as out:
                for chunk in r.iter_content(1 << 16):
                    out.write(chunk)
        return dest

    # ---------------------------------------------------------------- send
    def send(self, chat, text: str, buttons=None, reply_to: int | None = None, force_reply: bool = False) -> dict:
        markup = keyboard(buttons) if buttons is not None else (
            json.dumps({"force_reply": True}) if force_reply else None)
        return self.call("sendMessage", chat_id=chat, text=text[:4096], reply_markup=markup,
                         reply_to_message_id=reply_to, disable_web_page_preview="true")

    def send_video(self, chat, path: Path, caption: str = "", buttons=None) -> dict:
        with open(path, "rb") as f:
            return self.call("sendVideo", files={"video": (path.name, f, "video/mp4")}, http_timeout=600,
                             chat_id=chat, caption=caption[:1024], reply_markup=keyboard(buttons),
                             supports_streaming="true")

    def send_photo(self, chat, path: Path, caption: str = "", buttons=None) -> dict:
        with open(path, "rb") as f:
            return self.call("sendPhoto", files={"photo": (path.name, f, "image/jpeg")}, http_timeout=120,
                             chat_id=chat, caption=caption[:1024], reply_markup=keyboard(buttons))

    def edit_buttons(self, chat, message_id: int, buttons) -> None:
        try:
            self.call("editMessageReplyMarkup", chat_id=chat, message_id=message_id,
                      reply_markup=keyboard(buttons) or json.dumps({"inline_keyboard": []}), http_timeout=20)
        except TelegramError as e:
            if "not modified" not in str(e):
                raise

    def answer(self, callback_id: str, text: str = "") -> None:
        try:
            self.call("answerCallbackQuery", callback_query_id=callback_id, text=text[:200], http_timeout=15)
        except TelegramError:
            pass        # a callback older than ~15 minutes cannot be answered; the action still applies
