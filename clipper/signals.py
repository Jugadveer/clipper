"""Per-second "something is happening" signals for a long video.

Every signal is a 1-D array indexed by second, normalised to a robust z-score against a
LOCAL baseline (rolling median / MAD over a few minutes): a stream that is loud for an hour is
not exciting for an hour, it is loud; what matters is louder than it was a minute ago.

  loud    audio loudness (95th-percentile RMS inside each second)
  laugh   PANNs laughter probability
  scream  PANNs screaming / shouting / yelling
  cheer   PANNs cheering / applause / crowd
  boom    PANNs explosion / gunshot / crash (gameplay events)
  heat    YouTube "most replayed" heatmap (only for VODs that have one)
  chat    chat messages per second (YouTube live_chat replay or a Twitch chat json)
  react   chat messages that are reactions (LMAO, W, 💀, OMEGALUL ...)
  motion  picture change (mean abs frame difference at 2 fps, 160x90)
  speech  words per second from the transcript (silence is never a clip)
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf

from .common import FFMPEG, log, read_json


def robust_z(x: np.ndarray, win: int = 240) -> np.ndarray:
    """z-score of x against a rolling median/MAD baseline of `win` seconds."""
    x = np.asarray(x, np.float32)
    n = len(x)
    if n == 0:
        return x
    step = max(1, win // 8)
    centers = np.arange(0, n, step)
    med = np.empty(len(centers), np.float32)
    mad = np.empty(len(centers), np.float32)
    for i, c in enumerate(centers):
        seg = x[max(0, c - win // 2): c + win // 2]
        m = np.median(seg)
        med[i] = m
        mad[i] = np.median(np.abs(seg - m)) * 1.4826 + 1e-6
    med_f = np.interp(np.arange(n), centers, med)
    mad_f = np.interp(np.arange(n), centers, mad)
    gmad = np.median(np.abs(x - np.median(x))) * 1.4826 + 1e-6
    return ((x - med_f) / np.maximum(mad_f, 0.5 * gmad)).astype(np.float32)


def smooth(x: np.ndarray, k: int) -> np.ndarray:
    if k <= 1:
        return x
    ker = np.ones(k, np.float32) / k
    return np.convolve(x, ker, mode="same").astype(np.float32)


def loudness(wav16: Path, n: int) -> np.ndarray:
    a, sr = sf.read(str(wav16), dtype="float32")
    if a.ndim > 1:
        a = a.mean(axis=1)
    hop = sr // 4                                   # 0.25 s frames
    m = len(a) // hop
    rms = np.sqrt(np.mean(a[: m * hop].reshape(m, hop) ** 2, axis=1) + 1e-9)
    db = 20 * np.log10(rms + 1e-9)
    per_s = np.full(n, db.min() if len(db) else -90, np.float32)
    for s in range(min(n, m // 4)):
        per_s[s] = np.percentile(db[s * 4:(s + 1) * 4], 95)
    return per_s


def heatmap(info: dict, n: int) -> np.ndarray | None:
    hm = info.get("heatmap") or []
    if not hm:
        return None
    out = np.zeros(n, np.float32)
    for b in hm:
        out[int(b["start_time"]):int(b["end_time"]) + 1] = b["value"]
    # the very end of a VOD is "most replayed" because players seek to it; damp the last 2%
    tail = int(n * 0.98)
    out[tail:] *= 0.3
    return out


REACT_RE = re.compile(
    r"(lmao+|lmfao|lol+|haha+|omegalul|kekw|💀|😂|🤣|😭|bro+|nah+|wtf|omg+|^w+$|^l+$|\bw\b|\bl\b|"
    r"pog|poggers|holy|clip (it|that)|clipped|speed+|ayo+|💯|🔥|😱|😳)", re.I)


def parse_chat(path: Path) -> list[tuple[float, str]]:
    """(seconds, text) from a yt-dlp live_chat.json (jsonl) or a Twitch/chat-downloader json."""
    msgs: list[tuple[float, str]] = []
    raw = path.read_text(encoding="utf-8", errors="ignore")
    try:                                   # whole-file json array (chat-downloader / TwitchDownloader)
        doc = json.loads(raw)
        items = doc.get("comments", doc) if isinstance(doc, dict) else doc
        for c in items:
            t = c.get("time_in_seconds", c.get("content_offset_seconds"))
            txt = c.get("message", "")
            if isinstance(txt, dict):
                txt = txt.get("body", "")
            if t is not None:
                msgs.append((float(t), str(txt)))
        return msgs
    except Exception:
        pass
    for line in raw.splitlines():          # yt-dlp youtube live_chat: one json per line
        try:
            d = json.loads(line)
        except Exception:
            continue
        act = d.get("replayChatItemAction", {})
        t = act.get("videoOffsetTimeMsec")
        for a in act.get("actions", []):
            item = a.get("addChatItemAction", {}).get("item", {})
            r = item.get("liveChatTextMessageRenderer") or item.get("liveChatPaidMessageRenderer")
            if not r or t is None:
                continue
            runs = r.get("message", {}).get("runs", [])
            txt = "".join(x.get("text") or x.get("emoji", {}).get("shortcuts", [""])[0] for x in runs)
            msgs.append((int(t) / 1000.0, txt))
    return msgs


def chat_rates(msgs, n: int) -> tuple[np.ndarray, np.ndarray]:
    rate = np.zeros(n, np.float32)
    react = np.zeros(n, np.float32)
    for t, txt in msgs:
        s = int(t)
        if 0 <= s < n:
            rate[s] += 1
            if REACT_RE.search(txt or ""):
                react[s] += 1
    return rate, react


def motion(video: Path, n: int) -> np.ndarray:
    """Mean abs difference of consecutive 160x90 grey frames, per second.

    Decodes KEYFRAMES only (-skip_frame nokey): a full decode of a 1080p60 stream is ~10x
    slower and this is a weak signal; keyframes every 1-4 s are enough to see the picture change."""
    cmd = [FFMPEG, "-v", "error", "-skip_frame", "nokey", "-i", str(video),
           "-vf", "scale=160:90,format=gray,showinfo", "-fps_mode", "passthrough",
           "-f", "rawvideo", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    import threading
    times: list[float] = []

    def read_err():                      # showinfo prints each frame's pts_time on stderr
        for line in p.stderr:
            m = re.search(rb"pts_time:([0-9.]+)", line)
            if m:
                times.append(float(m.group(1)))
    th = threading.Thread(target=read_err, daemon=True)
    th.start()
    fsz = 160 * 90
    prev = None
    vals = []
    while True:
        buf = p.stdout.read(fsz)
        if len(buf) < fsz:
            break
        f = np.frombuffer(buf, np.uint8).astype(np.int16)
        vals.append(0.0 if prev is None else float(np.abs(f - prev).mean()))
        prev = f
    p.wait()
    th.join(timeout=5)
    m = min(len(vals), len(times))
    if m < 2:
        return np.zeros(n, np.float32)
    return np.interp(np.arange(n), np.array(times[:m]), np.array(vals[:m])).astype(np.float32)


def speech_rate(words_doc: dict, n: int) -> np.ndarray:
    out = np.zeros(n, np.float32)
    for seg in words_doc.get("segments", []):
        for w in seg["words"]:
            s = int(w["s"])
            if 0 <= s < n:
                out[s] += 1
    return out


def compute(work: Path, duration: float) -> dict[str, np.ndarray]:
    """Compute (or load cached) raw per-second signals for one source in `work`."""
    cache = work / "signals.npz"
    if cache.exists():
        z = np.load(cache)
        return {k: z[k] for k in z.files}
    n = int(duration) + 1
    sig: dict[str, np.ndarray] = {}
    log("signal: loudness")
    sig["loud"] = loudness(work / "audio16k.wav", n)
    log("signal: audio events (PANNs)")
    from . import panns
    ev = panns.tag_events(work / "audio32k.wav")
    for k, v in ev.items():
        arr = np.zeros(n, np.float32)
        arr[: min(n, len(v))] = v[:n]
        sig[k] = arr
    info = read_json(work / "source.info.json", {}) or {}
    hm = heatmap(info, n)
    if hm is not None:
        sig["heat"] = hm
    chat_files = sorted(work.glob("*.live_chat.json")) + sorted(work.glob("chat*.json"))
    if chat_files:
        msgs = parse_chat(chat_files[0])
        log(f"signal: chat ({len(msgs)} messages)")
        if msgs:
            sig["chat"], sig["react"] = chat_rates(msgs, n)
    log("signal: motion")
    sig["motion"] = motion(work / "source.mp4", n)
    np.savez_compressed(cache, **sig)        # transcript-free, so it can run while Whisper does
    return sig


# How much each normalised signal contributes to the excitement curve. Chat and the
# heatmap are audience truth when present; audio events are the streamer's own reaction.
WEIGHTS = {"loud": 0.9, "scream": 1.0, "laugh": 1.0, "cheer": 0.5, "gasp": 0.3, "boom": 0.4,
           "heat": 1.3, "chat": 1.2, "react": 1.4, "motion": 0.3}


def excitement(sig: dict[str, np.ndarray]) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """Fuse signals into one per-second curve. Returns (curve, normalised parts)."""
    n = len(sig["loud"])
    parts: dict[str, np.ndarray] = {}
    for k, w in WEIGHTS.items():
        if k not in sig:
            continue
        x = sig[k].astype(np.float32)
        if k in ("laugh", "scream", "cheer", "gasp", "boom"):
            x = smooth(x, 3)
            z = robust_z(x, 300)
        elif k == "heat":
            z = (x - np.median(x)) / (np.std(x) + 1e-6)
        elif k in ("chat", "react"):
            z = robust_z(smooth(x, 5), 300)
        else:
            z = robust_z(smooth(x, 3), 240)
        parts[k] = np.clip(z, -2, 6) * w
    curve = np.sum(list(parts.values()), axis=0) / max(1.0, sum(WEIGHTS[k] for k in parts) / 3)
    # no speech and no events for a while = dead air; push it down
    if "speech" in sig:
        talk = smooth(sig["speech"], 9)
        curve = curve - 0.8 * (talk < 0.15)
    return smooth(curve.astype(np.float32), 5), parts
