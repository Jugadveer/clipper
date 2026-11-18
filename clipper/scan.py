"""Transcript scan: the recall pass.

Signal peaks only find moments that are LOUD (screams, chat spikes). Plenty of the best stream
clips are not: a deadpan line, a story, a roast, a wholesome moment. So one cheap text-only
pass reads the whole stream (12-minute windows, 1 minute overlap) with the non-verbal cues
inlined, and proposes every clip-worthy moment with rough bounds. Cost: a stream hour is ~9k
words of transcript, ~$0.001 on flash-lite.
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from .candidates import phrases
from .common import load_env, log
from .judge import call

SCAN = """You are scouting a livestream transcript for a streamer-clips channel that posts 15-25 Shorts per stream hour. List EVERY moment that could be a standalone Short (15-60 s): funny lines, rage, fails, jump scares, wins, chat trolling the streamer, roasts, stories, wild claims, wholesome moments, interactions with guests or chat, reactions to donations WITH a payoff, anything a viewer would send to a friend.

Lines look like "[812.4] text". Lines like "[815-822] <SCREAMING, VERY LOUD>" are non-verbal cues from audio/chat analysis (reliable). A moment can be quiet and still great; a loud moment can be boring.

Do NOT list: greetings, ads/sponsor reads, plain gameplay narration with no payoff, reading donations with no reaction, technical issues, repeated versions of the same bit (list the best one).

Reply JSON only: {"moments": [{"start": s, "end": s, "score": 0-99, "what": "<6-12 words>"}]} using the timestamps given. score: postable 50-70, strong 70-85, banger 85+. Aim for roughly one moment per 2-4 minutes of transcript when the content supports it; fewer if it is dead."""


SCAN_PODCAST = """You are scouting a podcast / interview transcript for a clips channel (the kind that posts Alex Hormozi, Joe Rogan, Raj Shamani clips) that posts 15-20 Shorts per hour of episode. List EVERY moment that works as a standalone 20-60 s Short: a contrarian or bold claim, a specific lesson or framework, a surprising number, a story with a payoff, a funny exchange, an emotional admission, a hot take, a quotable line, a clear answer to an interesting question. The clip must make sense to someone who never saw the episode.

Lines look like "[812.4] text". Lines like "[815-822] <LAUGHTER>" are audio cues (reliable).

Do NOT list: intros, sponsor reads, "subscribe", small talk, rambling with no point, a point that needs earlier context, the same idea twice (list the best telling).

Reply JSON only: {"moments": [{"start": s, "end": s, "score": 0-99, "what": "<6-12 words>"}]} using the timestamps given. score: postable 50-70, strong 70-85, banger 85+. Start at the sentence that sets up the point; end when the point lands."""


def cue_ranges(sig: dict, parts: dict, a: int, b: int) -> list[tuple[int, int, str]]:
    """Collapse per-second cues into ranges like (815, 822, 'SCREAMING, VERY LOUD')."""
    labels = {"scream": "SCREAMING", "laugh": "LAUGHTER", "cheer": "CHEERING", "boom": "BANG"}
    per = []
    for s in range(a, b):
        t = [name for k, name in labels.items() if k in sig and sig[k][s] >= 0.4]
        if "loud" in parts and parts["loud"][s] > 2.5:
            t.append("VERY LOUD")
        if "react" in parts and parts["react"][s] > 2.5:
            t.append("CHAT SPAMMING")
        elif "chat" in parts and parts["chat"][s] > 2.5:
            t.append("CHAT SPIKE")
        per.append(", ".join(t))
    out, i = [], 0
    while i < len(per):
        if not per[i]:
            i += 1
            continue
        j = i
        while j + 1 < len(per) and (per[j + 1] or (j + 2 < len(per) and per[j + 2])):
            j += 1
        tags = sorted({x for p in per[i:j + 1] for x in p.split(", ") if x})
        out.append((a + i, a + j + 1, ", ".join(tags)))
        i = j + 1
    return out


def scan(words_doc: dict, sig: dict, parts: dict, title: str, streamer: str,
         model: str = "gemini-2.5-flash-lite", window: int = 720, overlap: int = 60,
         prompt: str = "", genre: str = "stream") -> list[dict]:
    n = len(sig["loud"])
    ph = phrases(words_doc)
    env = load_env()
    usage: list = []
    wins = []
    t = 0
    while t < n:
        wins.append((t, min(n, t + window)))
        t += window - overlap

    def run(w):
        a, b = w
        lines = [(s, f"[{s:.1f}] {txt}") for s, e, txt in ph if a <= s < b]
        lines += [(s, f"[{s}-{e}] <{tags}>") for s, e, tags in cue_ranges(sig, parts, a, b)]
        if not lines:
            return []
        lines.sort()
        ask = f"\nONLY list moments that match: {prompt}\n" if prompt else ""
        msgs = [{"role": "system", "content": SCAN_PODCAST if genre == "podcast" else SCAN},
                {"role": "user", "content": f"Stream: {title}\nStreamer: {streamer}{ask}\n"
                                            f"Transcript {a}-{b} s:\n" + "\n".join(x for _, x in lines)}]
        try:
            text = call(msgs, env, model, usage, tag="scan")
            text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
            doc = json.loads(text)
        except Exception as e:
            log(f"  scan window {a}-{b} failed: {e}")
            return []
        out = []
        for m in (doc.get("moments", []) if isinstance(doc, dict) else doc):
            try:
                s, e = float(m["start"]), float(m["end"])
            except (KeyError, TypeError, ValueError):
                continue
            if a - 5 <= s < e <= b + 30 and e - s >= 5:
                out.append({"start": s, "end": e, "score": int(m.get("score", 50)),
                            "what": str(m.get("what", ""))[:120]})
        return out

    props: list[dict] = []
    with ThreadPoolExecutor(4) as ex:
        for res in ex.map(run, wins):
            props.extend(res)
    # windows overlap: merge proposals that are the same moment
    props.sort(key=lambda p: p["start"])
    merged: list[dict] = []
    for p in props:
        if merged and min(p["end"], merged[-1]["end"]) - max(p["start"], merged[-1]["start"]) > \
                0.5 * min(p["end"] - p["start"], merged[-1]["end"] - merged[-1]["start"]):
            if p["score"] > merged[-1]["score"]:
                merged[-1] = p
            continue
        merged.append(p)
    tin = sum(u.get("prompt_tokens", 0) for u in usage)
    tout = sum(u.get("completion_tokens", 0) for u in usage)
    log(f"scan: {len(merged)} moments from {len(wins)} windows ({tin} in / {tout} out tokens)")
    return merged


def merge_candidates(props: list[dict], curve: np.ndarray, peaks: list[int], k: int,
                     min_score: int = 50, prefer: str = "signal") -> list[tuple[int, str, dict | None]]:
    """Union of scan proposals and signal peaks, as (centre second, source, proposal).
    Proposals come first (ranked by score); a signal peak is added only if no proposal
    already covers it, so a loud moment the scan missed is still judged."""
    sc = [(int((p["start"] + p["end"]) / 2), "scan", p) for p in sorted(props, key=lambda p: -p["score"])
          if p["score"] >= min_score]
    sg = [(int(t), "signal", None) for t in sorted(peaks, key=lambda t: -curve[t])]
    first, second = (sg, sc) if prefer == "signal" else (sc, sg)
    n_first = int(k * 0.65)                  # the preferred source gets ~2/3 of the places
    out: list[tuple[int, str, dict | None]] = []

    def covered(c):
        t, _, pr = c
        for t2, _, pr2 in out:
            lo, hi = (pr2["start"] - 5, pr2["end"] + 5) if pr2 else (t2 - 25, t2 + 25)
            if lo <= t <= hi or (pr and pr["start"] - 5 <= t2 <= pr["end"] + 5):
                return True
        return False
    for c in first[:n_first]:
        if not covered(c):
            out.append(c)
    for c in second + first[n_first:]:
        if len(out) >= k:
            break
        if not covered(c):
            out.append(c)
    return out[:k]
