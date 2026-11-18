# Clipper Studio

One program on this PC runs the website, the job queue, the YouTube uploader and the Telegram bot.

```powershell
python -m web.server        # from the clipper folder, with .venv activated
```

Open http://localhost:8765.

| Page | What it's for |
|---|---|
| Make | Paste a link (YouTube, Twitch, Kick or a file on this PC). The page reads the title and length, then you pick the kind of video, how many shorts, the caption style, and optionally "only clip moments like…". Jobs run one at a time. A job that was running when the PC shut down starts again from where it stopped. The second tab turns a GTA script, with or without a recorded voiceover, into a long-form video. |
| Clips | Every finished short and GTA video, grouped by source and sorted by score. Tap one to watch it, rename it, generate free title ideas, download it, schedule it or skip it. Select several to schedule or skip them together. |
| Posting | Uploads coming up, by day. Set the daily posting times, and optionally schedule high-scoring shorts from Telegram automatically. A "What's working" table compares styles and lengths on your own channel. |
| Requests | What people sent the bot, plus GTA topics waiting for Claude to write. |
| Settings | The Telegram bot, the always-on mailbox, the phone link, YouTube, and defaults. |

## Telegram bot (for a friend without a laptop)

1. In Telegram, message **@BotFather** with `/newbot` and copy the token it gives you.
2. Paste the token in Settings → Telegram bot. The page then shows a 6-digit pairing code.
3. Your friend opens the bot and sends the code once. After that, they can send any of these:

| They send | What happens |
|---|---|
| a link, optionally with words: `15 shorts hormozi https://…`, `https://… only when he rages` | A clip job starts. When it finishes, the best shorts arrive in the chat, each with **✅ Post**, **⏭ Skip** and **✏️ Rename** buttons. Post schedules the short at the next free posting time. |
| a `.txt` GTA script, then an mp3/m4a or a voice note | A GTA video made with their voice. The recording is transcribed and lined up with the script word by word. |
| a `.txt` script, then the "free voice" button | A GTA video read by the free TTS voice. |
| `/gta <topic>` | The topic goes to the Claude inbox (see below). |
| `/status`, `/queue`, `/more`, `/cancel`, `/site` | What's running, what's posting next, the rest of a batch, stop their current job, and the phone link. |

Nothing in the bot calls an AI model: requests are read with plain rules (`asks.py`). Shorts over Telegram's 50 MB limit are sent as a smaller copy; the full file is what goes to YouTube. A GTA video gets its thumbnail and a 45-second preview.

### Always-on mailbox (so nothing is lost while the laptop is shut)

On its own, Telegram keeps a bot's messages for 24 hours. The mailbox is a free Cloudflare Worker with a D1 database. It receives every message the moment it's sent and replies straight away ("Got it, the PC is off, I'll start when it's back"). It holds the messages until the studio collects them. Set it up once, after the bot token is saved:

```powershell
python mailbox\deploy.py        # from the clipper folder
```

The script signs in to Cloudflare in your browser and creates everything. The studio then moves the bot over by itself. Cloudflare's free plan gives 100,000 requests a day; the studio uses about 14,000 while the PC is on.

## Phone link

Set a passcode in Settings, then switch on the phone link. It needs `cloudflared`, a free program from Cloudflare, in `web/bin/` or on the PATH. The studio gets a random `https://….trycloudflare.com` address, and the bot's `/site` command always has the current one. On this PC no passcode is needed. Through the link, every page, file and API call needs it, and the bot token, passcode and YouTube connection can only be changed on the PC itself.

## Claude inbox (only with the separate GTA project, not in this repo)

A GTA video needs a researched, fact-checked script, which is the one step that needs Claude. `/gta <topic>` (or the form on Requests) adds the topic to `state/claude_inbox.json`. When Claude Code is open, a scheduled task writes the script with the `gta-daily` skill and saves it under `gta/scripts/`. It then calls:

```
POST http://127.0.0.1:8765/api/claude-inbox/<id>/script   {"path": "scripts/<file>.txt"}
```

That queues the render and tells whoever asked in Telegram. Everything after the script costs nothing.

## YouTube (one-time setup, needs your Google account)

1. https://console.cloud.google.com → create a project → APIs & Services → enable **YouTube Data API v3**.
2. OAuth consent screen → External → add your own Google account under Test users.
3. Credentials → Create credentials → OAuth client ID → **Web application**. Add the authorised redirect URI `http://localhost:8765/oauth2callback`.
4. Download the JSON and save it as `web\client_secret.json` in the clipper folder.
5. Settings → YouTube → **Connect your channel** (on this PC).

The default API quota allows about 6 uploads a day. When it runs out, the queue waits and carries on the next day. Custom thumbnails need a phone-verified channel.

## Tests

```powershell
python -m web.tests.test_bot        # the whole bot against a fake Telegram
python -m web.tests.test_mailbox    # needs `npx wrangler dev` on the Worker, see the file
```

Files: `state/` holds settings, jobs, edits, the upload queue and the bot's state; it is git-ignored, as are `youtube_token.json` and `client_secret.json`.
