"""Turn a chat message into a clip request without a language model.

    "https://youtu.be/xyz"                                  -> defaults
    "20 shorts podcast hormozi https://..."                 -> 20 clips, podcast, hormozi captions
    "https://... only clip when he rages at chat"           -> --prompt "when he rages at chat"
    "cam on top, free picker https://twitch.tv/videos/..."  -> split_order cam_top, judge local

Everything not recognised is ignored rather than guessed at, except text after an explicit cue
("only", "just", "every time", "moments when ..."), which becomes the picker's instruction. Free
text without a cue never becomes an instruction: "make clips from this pls" must not narrow what
gets clipped.
"""
from __future__ import annotations

import re

URL = re.compile(r"https?://\S+", re.I)
STYLES = ["viral", "pop", "karaoke", "hormozi", "clean", "mix"]
COUNT = re.compile(r"(?<![\w/])(\d{1,3})\s*(?:shorts?|clips?|videos?)\b", re.I)
CUE = re.compile(r"(?:^|[\s,.;:])(only(?:\s+clip)?|just(?:\s+clip)?|focus\s+on|every\s*time|whenever|"
                 r"moments?\s+(?:when|where|like|of)|clip\s+(?:when|where|every))\b(.*)", re.I | re.S)
WORDS = {
    "genre": [(r"\b(podcasts?|interviews?|talk\s*show)\b", "podcast"),
              (r"\b(stream|streams|streamer|gaming|gameplay|vod|twitch|kick)\b", "stream")],
    "split_order": [(r"\b(cam|face|camera)\s+(on\s+)?top\b", "cam_top"),
                    (r"\b(game|gameplay)\s+(on\s+)?top\b", "game_top")],
    "judge": [(r"\b(free|local|no\s*ai|\$0)\s*(picker|judge|mode)?\b", "local")],
}


def parse(text: str) -> dict:
    text = (text or "").strip()
    urls = URL.findall(text)
    out: dict = {"source": urls[0].rstrip(").,>") if urls else ""}
    rest = URL.sub(" ", text)
    m = CUE.search(rest)
    if m:
        ask = re.sub(r"\s+", " ", m.group(2)).strip(" .,:;-")
        if len(ask.split()) >= 2 or (m.group(1).lower().startswith(("every", "whenever", "moment")) and ask):
            lead = m.group(1).lower()
            out["prompt"] = (lead + " " + ask) if lead.startswith(("every", "whenever", "moment")) else ask
        rest = rest[:m.start()]
    c = COUNT.search(rest)
    if c:
        out["clips"] = max(1, min(120, int(c.group(1))))
    for key, pats in WORDS.items():
        for pat, val in pats:
            if re.search(pat, rest, re.I):
                out[key] = val
                break
    for s in STYLES:
        if re.search(rf"\b{s}\b", rest, re.I):
            out["style"] = s
            break
    return out


def describe(req: dict) -> str:
    """The request read back in plain words, so a misread is visible before the job runs."""
    bits = [f"{req['clips']} shorts" if req.get("clips") else "as many shorts as it finds (about 1 per 3 min)"]
    if req.get("genre"):
        bits.append("podcast mode" if req["genre"] == "podcast" else "stream mode")
    if req.get("style"):
        bits.append(f"{req['style']} captions")
    if req.get("split_order") == "cam_top":
        bits.append("cam on top")
    if req.get("judge") == "local":
        bits.append("free picker ($0)")
    if req.get("prompt"):
        bits.append(f"only: “{req['prompt']}”")
    return " · ".join(bits)
