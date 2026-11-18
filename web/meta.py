"""Titles, descriptions and tags without an LLM: templates fed by the clip's own data.

Inputs per clip: the judge's title/hook/end caption (when the clip was judged), the words spoken in
it, the streamer and the genre. Output: several title variants in the patterns that the research
found on the biggest clip channels (reaction phrase + emoji pair; "Streamer did X"; quote of the
line), a description with credit + call to action + hashtags, and up to 25 tags.
"""
from __future__ import annotations

import random
import re
from collections import Counter

STOP = set("""a an the and or but if then so to of in on at for with from by is are was were be been it its this that
these those i you he she we they me him her us them my your his our their what who how why when where not no yes
just like really very got get go going gonna wanna yeah oh um uh okay ok bro chat dude man all can do did does
there here up out about into over one two three now know think want right let's lets yo it's that's
don't can't won't i'm he's she's they're we're you're actually literally even still never always too""".split())
EMOJI = {"stream": ["💀", "😭", "😂", "🤣", "🔥", "😱", "🥶"], "podcast": ["🤯", "💡", "👀", "🔥", ""]}
BASE_TAGS = {"stream": ["shorts", "funny", "streamer", "clips", "funny moments", "viral", "twitch", "live stream"],
             "podcast": ["shorts", "podcast", "podcast clips", "motivation", "business", "mindset", "interview"]}
GTA_TAGS = ["gta 6", "gta vi", "grand theft auto 6", "gta 6 gameplay", "gta 6 news", "vice city", "rockstar games"]


def keywords(text: str, k: int = 8) -> list[str]:
    ws = [w.lower() for w in re.findall(r"[A-Za-z][A-Za-z']{2,}", text)]
    c = Counter(w for w in ws if w not in STOP)
    return [w for w, _ in c.most_common(k)]


def quote(said: str, rnd: random.Random, max_words: int = 9) -> str:
    """A whole spoken sentence that reads as a title: 3-9 words, no web addresses or garbled
    transcript ("Petaoverright .com"), not the same phrase twice. Empty when none qualifies:
    a cut-off fragment in quotes reads as a mistake, not a hook."""
    good = []
    for sent in re.split(r"(?<=[.!?])\s+", said):
        sent = sent.strip().strip('"')
        words = sent.split()
        if not 3 <= len(words) <= max_words:
            continue
        if re.search(r"\.(com|net|org|tv|gg)|www|https?:|\d{3,}", sent, re.I):
            continue
        low = [w.lower().strip(".,!?") for w in words]
        if len(set(low)) < len(low) * 0.7:           # "bomb is on, bomb is on"
            continue
        good.append(sent.rstrip(".,"))
    if not good:
        return ""
    good.sort(key=lambda x: (not x.endswith(("!", "?")), abs(len(x.split()) - 6)))
    return rnd.choice(good[:3])


def titles(clip: dict, streamer: str, genre: str, seed: int = 0) -> list[str]:
    rnd = random.Random(seed)
    e = EMOJI.get(genre, EMOJI["stream"])
    em = rnd.choice(e)
    out = []
    if clip.get("title"):
        out.append(clip["title"])
    hook = (clip.get("hook") or "").strip()
    endc = (clip.get("end_caption") or "").strip().strip('"').title()
    said = (clip.get("transcript") or "").strip()
    who = streamer or "Bro"
    if genre == "podcast":
        if hook:
            out.append(f"{hook} {em}".strip())
        q = quote(said, rnd, 12)
        if q:
            out.append(f"\"{q}\"")
        if hook:
            out.append(f"{who} on {hook[0].lower() + hook[1:]}")
    else:
        if endc:
            out.append(f"{endc} {em}{em}")
            out.append(f"{who} was NOT ready for this {em}")
        q = quote(said, rnd)
        if q:
            out.append(f"\"{q}\" {em}")
        out.append(f"{who} did NOT expect this {em}{em}")
        if hook:
            out.append(f"{hook} {em}")
    from clipper.captions import censor          # titles follow the captions' ad-friendly rule
    seen, uniq = set(), []
    for t in out:
        t = censor(re.sub(r"\s+", " ", t).strip()[:95])
        if t and t.lower() not in seen:
            seen.add(t.lower())
            uniq.append(t)
    return uniq[:6]


def tags(clip: dict, streamer: str, genre: str, extra: list[str] | None = None) -> list[str]:
    t = []
    if streamer:
        t += [streamer.lower(), f"{streamer.lower()} clips", f"{streamer.lower()} funny moments"]
    t += [x.lower() for x in (clip.get("hashtags") or clip.get("tags") or []) if x]
    # title words only: transcript words are mostly filler ("bad", "start") and make weak tags
    t += [w for w in keywords(clip.get("title", ""), 6) if len(w) >= 4]
    t += BASE_TAGS.get(genre, BASE_TAGS["stream"])
    if re.search(r"\bgta\b|grand theft auto", " ".join([clip.get("title", ""), clip.get("transcript", "")]), re.I):
        t += GTA_TAGS
    t += extra or []
    out, seen = [], set()
    for x in t:
        x = x.strip().lstrip("#")
        if x and x not in seen and len(", ".join(out + [x])) < 480:
            seen.add(x)
            out.append(x)
    return out[:25]


def description(clip: dict, streamer: str, genre: str, channel_cta: str = "", source_url: str = "") -> str:
    line = (clip.get("description") or clip.get("hook") or clip.get("title") or "").split("\n")[0]
    tg = tags(clip, streamer, genre)[:6]
    parts = [line]
    if streamer:
        parts.append(f"🎥 Credit: {streamer}" + (f" | {source_url}" if source_url else ""))
    parts.append(channel_cta or ("Subscribe for daily clips 🔔" if genre == "stream" else "Follow for more 🔔"))
    parts.append(" ".join("#" + re.sub(r"\s+", "", x) for x in ["shorts"] + tg if x))
    return "\n\n".join(p for p in parts if p)


def generate(clip: dict, streamer: str = "", genre: str = "stream", seed: int = 0, cta: str = "") -> dict:
    return {"titles": titles(clip, streamer, genre, seed),
            "description": description(clip, streamer, genre, cta, clip.get("source_url", "")),
            "tags": tags(clip, streamer, genre)}
