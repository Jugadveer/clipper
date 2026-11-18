# clipper

Give it a long video or a livestream (YouTube, Twitch, Kick, or a file on your PC) and it makes
many ready-to-post vertical shorts: it finds the best moments, cuts them, follows the face,
adds captions and a headline, and writes a title, description and hashtags for each one. It also
works on a stream that is **still live**, and clips it as it goes.

It runs on **your own Windows PC**. The only paid part is a small AI call that picks the moments
(about $0.001 per finished short), and you can switch even that off.

---

## What runs where (and what Vercel is for)

| Part | Where it runs | Why |
|---|---|---|
| Downloading, transcribing (Whisper), finding moments, face tracking, rendering | **your PC** (an NVIDIA graphics card makes it fast) | It needs ffmpeg, a GPU and jobs that take 5-30 minutes. Vercel's servers stop any job after 60 seconds and have no disk, so this part *cannot* run on Vercel. |
| The AI judge that picks moments and writes titles | OpenRouter (an online service, pay as you go) | One small call per candidate moment. |
| The Studio website (paste a link, watch clips, schedule uploads) | **your PC**, at http://localhost:8765 | It shows files from your disk. |
| Using the Studio from your phone | a free Cloudflare tunnel from your PC | Gives a private `https://….trycloudflare.com` address, protected by your passcode. |
| A fixed address like `https://my-clips.vercel.app` (optional) | **Vercel**, free plan | Vercel only *forwards* to your PC. The tunnel address changes every restart; Vercel's doesn't. The studio updates it by itself. When your PC is off, the address shows an error. |

So: install it on the PC, run it there, and use Vercel only if you want one permanent link to
open from your phone.

---

## 1. Install (Windows, once, about 20 minutes)

You need:

1. **Python 3.11**: download from https://www.python.org/downloads/ . In the installer tick
   **"Add python.exe to PATH"**. Afterwards, `py --version` in a terminal should print 3.11.
2. **ffmpeg**: open PowerShell and run `winget install Gyan.FFmpeg`. Close and reopen PowerShell,
   then `ffmpeg -version` should work. (No winget? Download the "essentials" zip from
   https://www.gyan.dev/ffmpeg/builds/ , unzip it, and add its `bin` folder to your PATH.)
3. **Git**: `winget install Git.Git` (or download the ZIP of this repo from GitHub instead).
4. **Node.js LTS** (only for the newer "engine" caption styles and the Vercel address):
   `winget install OpenJS.NodeJS.LTS`.
5. **An NVIDIA graphics card** is strongly recommended (6 GB or more). Without one everything
   still works on the CPU, just several times slower.

Then in PowerShell:

```powershell
git clone https://github.com/Jugadveer/clipper.git
cd clipper
py -m venv .venv
.venv\Scripts\activate
python -m pip install torch==2.5.1 torchaudio==2.5.1 --index-url https://download.pytorch.org/whl/cu121
python -m pip install -r requirements.txt
copy .env.example .env
notepad .env
```

In `.env`, paste your OpenRouter key (sign up at https://openrouter.ai, add a few dollars of
credit, then create a key at https://openrouter.ai/keys). Save and close.

Every time you open a new PowerShell window later: `cd clipper` then `.venv\Scripts\activate`.

The first run downloads two free AI models by itself: the Whisper speech model (1.6 GB) and the
audio-event model that hears screams and laughter (327 MB). After that it works offline except
for the downloads and the judge.

---

## 2. Make shorts

### From a finished video or VOD

```powershell
python -m clipper run "https://www.youtube.com/watch?v=..." --style mix
```

A 1-2 hour stream takes about 10-20 minutes with a GPU. The shorts land in `work\<id>\clips\`
together with `index.html` (open it to watch every clip), `upload.csv` (titles, descriptions,
hashtags for bulk schedulers) and `clips.json`. Copies also go to `SHORTS\<video title>\`.

### From a stream that is live right now

```powershell
python -m clipper live "https://www.twitch.tv/somestreamer" --style mix
```

It records the stream continuously and checks every 90 seconds of new stream for good moments.
A clip is ready a few minutes after the moment happens; nothing waits for the stream to end. Clips
appear in `work\<id>\live\clips\`. Stop it with Ctrl+C, or add `--max-minutes 60` or `--clips 20`.
If you restart it on the same link, it carries on where it stopped. In live mode `--style mix` rotates
only the fast looks (viral, pop, karaoke; hormozi and clean for podcasts), which render in about a
minute; the engine looks take several minutes per clip, so ask for one by name only if you can wait.

### Useful options

| Option | What it does |
|---|---|
| `--clips 15` | how many shorts (default: about one per 3 minutes of video) |
| `--style ...` | `viral`, `pop`, `karaoke`, `hormozi`, `clean`, the engine styles `speaker`, `ui`, `bigword`, `tiered`, `story`, or `mix` (rotates looks so a channel doesn't look copy-pasted) |
| `--genre stream` / `podcast` | auto-detected; stream = gaming/IRL, podcast = interviews and talk |
| `--prompt "every time he dies"` | only clip that kind of moment |
| `--judge local` | no AI at all: $0, weaker picks and generic titles |
| `--text-only` | the AI reads text only (no images): about half the cost |
| `--layout split` | gameplay on top, streamer below; also `single`, `speaker`, `stack`, `fit` |
| `--keep-source` | keep the downloaded video (deleted by default; it's gigabytes) |

Some YouTube videos need you to be signed in (age-restricted, members-only). yt-dlp can use
your browser's cookies for those; see https://github.com/yt-dlp/yt-dlp#usage-and-options .

---

## 3. The Studio (a website for all of the above)

```powershell
python -m web.server
```

Open http://localhost:8765 . Paste a link, pick how many shorts and a style, and watch the clips
when they're done; you can rename, download and schedule them to YouTube from there. The
Settings page connects YouTube uploads and a Telegram bot (send the bot a link from your phone and
the clips come back in the chat). Details: `web/README.md`.

The GTA long-form video maker in the Studio needs a separate project that is not in this repo,
so its button is hidden here.

### Use it from your phone

1. Install cloudflared: `winget install Cloudflare.cloudflared` (free, no account needed).
2. In the Studio, Settings: set a passcode, then tick **"Open a phone link"**. A
   `https://….trycloudflare.com` address appears. Open it on your phone and type the passcode.

### A fixed address on Vercel (optional)

The phone link changes every time it starts. For one that never changes:

1. Make a free account at https://vercel.com and create a token at
   https://vercel.com/account/tokens .
2. Put it in `.env`: `VERCEL_TOKEN=...` and choose a name, e.g. `VERCEL_ALIAS=my-clips`.
3. Restart the Studio and turn the phone link on. About 20 seconds later Settings shows
   `https://my-clips.vercel.app`, and every later restart re-points it by itself.

What happens behind the scenes: `web/vercel_link.py` deploys a project to your Vercel account that
contains no code, only one forwarding rule to your tunnel. You can also do it by hand:
`python -m web.vercel_link https://<your-tunnel>.trycloudflare.com`. Two limits: it only works while
your PC and the Studio are on, and very large uploads (a video file from your phone) are better
done on the PC itself. Vercel's free "Hobby" plan is meant for personal, non-commercial use.

---

## What it costs

| Thing | Cost |
|---|---|
| Everything on your PC (download, transcript, moment finding, rendering) | $0 |
| AI judge (gemini-2.5-flash-lite via OpenRouter) | about **$0.001 per finished short**: $0.0077 measured for a 72-minute stream that gave 9 shorts. A live stream costs about **$0.04 per hour** of stream (one scan call per 90 s of new stream plus one call per candidate moment). `--judge local` makes it $0. Set `CLIPPER_BUDGET_USD` in `.env` to cap total spend. |
| Phone link (Cloudflare quick tunnel) | $0 |
| Fixed Vercel address | $0 on the Hobby plan |
| Telegram bot + mailbox (Cloudflare Worker) | $0 on the free plans |

Why it stays cheap however long the stream is: the AI never reads the whole transcript. Free
local signals (loudness, screams and laughter, motion, speech) find the ~2-minute windows worth
looking at, and only those go to the AI. Cost scales with the number of shorts, not with hours.

A word on rights: clip streams whose owners allow clipping (many streamers run clipping
programmes), and follow each platform's rules.

---

## How it works (for the curious)

```
download ─> audio ─> transcript (words) ─┐
         └> signals: loudness, audio events (scream, laugh, cheer), chat, motion ─> excitement curve
                                                                              │ peaks
candidates: ~2-min windows with transcript + cue lines + a 4-frame strip
  ─> judge (cuts the clip, writes title / headline / hashtags / emphasis words, scores virality)
  ─> select (no overlaps) ─> snap cuts to word boundaries, jump-cut dead air
  ─> reframe (auto layout, face tracking) ─> captions + headline ─> loudness -14 LUFS ─> mp4
```

**Live mode** (`clipper/live.py`): a recorder writes the stream to disk as 30-second pieces and
never pauses; a Whisper worker transcribes only the new audio; every `--step` seconds the loop
looks at the last ~5 minutes plus the new part and judges only moments that already have 25 s of
aftermath recorded (so a punchline is never cut off); renders run in the background. Every moment
is judged once and clipped once.

**Layouts** (chosen per clip): `split` (gameplay top, streamer below), `single` (one crop following
the face, zooming per shot), `speaker` (two people, cuts to whoever talks), `stack`, `fit` (no
face: the whole frame over a blurred copy).

**Engine styles** (`speaker`, `ui`, `bigword`, `tiered`, `story`) are drawn by
`clipper/engine` with HyperFrames (headless Chrome, downloaded by `npx` on first use): speaker-
coloured captions, glass cards that restate what is said, one-word-at-a-time captions, top tags.
The same engine turns a script into a voiced motion-graphics short:
`python -m clipper.engine.make script my_script.txt --look kinetic -o out.mp4`
(`clipper/engine/README.md`).

| File | Does |
|---|---|
| `clipper/ingest.py` | yt-dlp download (video, info, chat replay), local files |
| `clipper/live.py` | clipping a live stream: recorder, Whisper worker, rolling judge, background renders |
| `clipper/transcribe.py` | faster-whisper word timestamps |
| `clipper/panns.py` | the audio-event model (scream, laugh, cheer, explosion) |
| `clipper/signals.py` | per-second signals fused into one excitement curve |
| `clipper/candidates.py` | peak picking, transcript + cue context, frame strips |
| `clipper/scan.py` | the transcript scan that proposes moments the signals miss |
| `clipper/judge.py` | the AI judge, rerank and selection |
| `clipper/reframe.py` | face tracking, layout choice, frame composition |
| `clipper/captions.py`, `clipper/render.py` | captions, cut snapping, dead air, encode |
| `clipper/engine/` | the HyperFrames editing engine (engine styles, script-to-short) |
| `clipper/cli.py` | `run` / `batch` / `live`, upload.csv, review page |
| `web/` | the Studio, Telegram bot, YouTube uploader, phone link, Vercel address |
| `mailbox/` | the free Cloudflare Worker that holds bot messages while the PC is off |

`python -m clipper.eval score <work dir> <picks json> --heat` scores picks against a YouTube video's
own "most replayed" graph, for a run made with `--no-heatmap`.
