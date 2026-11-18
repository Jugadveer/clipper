"""Planners: turn words (and a few choices) into an edit decision list (EDL).

Two modes, one EDL format:
  footage(...)  given video (a clip from the clipper, or the user's own assets) + its words
  script(...)   a script + voiceover only: kinetic type, grounds and graphics change every ~1 s

One cheap model call per short (gemini-2.5-flash-lite through the clipper's own route) picks the
speakers, the emphasis words and which graphic goes where. Every answer is checked against the
template menu and the word times; anything the model invents is dropped. With no key (or
`llm=False`) a rule-based plan is used instead, so a render never depends on the model.
"""
from __future__ import annotations

import json
import re

from . import words as W
from .looks import LOOKS

MENU = """TEMPLATES you may place (props in braces; times are word indices from the transcript):
- chat {q, a}: glass chat card, a question typed in and its answer (use when a question/answer or "ask X" is said)
- pill {text}: white pill with ONE word in [brackets] coloured red, e.g. "AI is [not] smart" (a claim or correction)
- grid {items:[{label, value}]} 2-4 chips, numbers count up (a comparison of several things)
- checks {rows:[{label, sub, ok}]} full-screen dark card with check/cross rows (true vs false, do vs don't). Max 2.5 s.
- stat {value, prefix, suffix, label}: big number counting up (when a number is said)
- quote {text, by}: big quote (a memorable line, max 12 words)
- lower {name, role}: name tag when a person is introduced
- sticker {text}: 1-3 emoji or "???" popping on a reaction/joke beat
- slam {lead, word, ground}: full-screen keyword card, lead = 2-5 words, word = ONE word; ground one of cream, dark, grid, blue, news, red. Max 1.6 s.
- coins {items:[{icon, label}]}: 2-4 round icons (emoji) with labels (a list of things/brands)
- name {name, sub}: big name typed in + subtitle (opening a story about a person/brand)

TAGS (separate list, only when the look allows them): a label held at the top of the frame for a stretch of the story,
replaced by the next tag. {"from": i, "to": i, "kind": "money|text", "a": number, "b": number, "text": ""}
Years said in the words become tags on their own; do not add them.
- money: a value that grows or shrinks while it is talked about; it counts from a to b in dollars (2000000000 -> 4000000000000).
- text: 1-3 words such as "DAY 3" or a place, in the speaker's words.
Only values and years the words state; none when the words state none."""

FOOTAGE_SYS = """You are a top short-form editor (the Diary of a CEO / Iman Gadzhi / Ali Abdaal school). You get the transcript of ONE vertical clip, one word per line as "index time word". Plan the motion graphics.

Rules:
- speakers: list of [first_word_index, speaker_id] turns. Speaker 0 is whoever speaks first; a question from an interviewer is a different speaker.
- emphasis: indices of 1 word per 2-4 words that carry the meaning (numbers, the key noun, the punchline word). Never stop words.
- graphics: 3-7 for a 30-60 s clip, never two at once, each 1.5-4 s, starting on the word that triggers it. They must SAY what the words say (no invented facts, numbers or names). Leave at least 2 s between graphics.
- punches: 2-5 word indices for a zoom punch-in (punchlines, strong words).
- flashes: 0-2 word indices for a white flash cut (a big reveal).
- header: 3-7 word on-screen hook for the top of the video (curiosity or claim, grounded in the clip), or "" for none.

""" + MENU + """

JSON only: {"speakers": [[i, id]], "emphasis": [i], "graphics": [{"type": "", "from": i, "to": i, "p": {}}], "tags": [], "punches": [i], "flashes": [i], "header": ""}"""

SCRIPT_SYS = """You are a motion designer making a faceless explainer Short (the C-yZlQXN3rQ / s5Zfh26W6Vo / DLND-0I2FmM style: kinetic type, one idea per beat, a new visual every 1-2 s). You get the voiceover, one word per line as "index time word", already split into sentences.

For EVERY sentence choose one scene. Rules:
- kinetic {} is the default: the spoken words build on screen and the emphasis word is huge.
- Use the other templates when the sentence clearly fits them; props must restate what is said (no invented facts).
- emphasis: indices of 1 word per sentence (2 for long sentences) that carry the meaning: the key noun, name or verb. Never a question word (what, why, how), a pronoun, or a number word.
- ground for each scene: one of dark, grid, blue, neon, cream, paper, white, red, sunset, news. Change it often; dark-ink grounds are cream, paper, white, news.
- trans: the transition INTO the scene: flash, whip, zoom, iris, tear, glitch.
- obj: ONE emoji that best shows the beat's subject as an object or scene (🧱 for a wall, 🛰️ for a space station, 💸 for money). It is drawn huge as the hero object, so pick a thing, not a face, unless the beat is about a feeling.
- When a sentence states a number, use stat with value as a plain number (21000, not "twenty one thousand") and the unit in suffix or label.

""" + MENU + """

JSON only: {"emphasis": [i], "scenes": [{"type": "kinetic|stat|chat|pill|grid|checks|quote|coins|name|slam", "from": i, "to": i, "ground": "", "trans": "", "obj": "", "p": {}}], "tags": []}"""


def _n(w: str) -> str:
    return re.sub(r"[^\w]", "", str(w).lower())


def _num(x):
    try:
        return float(str(x).replace(",", ""))
    except (TypeError, ValueError):
        return None


def valid(typ: str, p: dict) -> dict | None:
    """The props a template needs, or None. Anything malformed is dropped, never repaired by guessing."""
    from .emoji import is_emoji
    p = dict(p or {})
    words = lambda x: len(str(x or "").split())
    if typ == "pill":
        return p if "[" in str(p.get("text", "")) and words(p.get("text")) >= 2 else None
    if typ == "chat":
        q, a = str(p.get("q", "")), str(p.get("a", ""))
        return p if words(q) >= 2 and len(a.strip("?! .")) >= 2 else None
    if typ == "stat":
        v = _num(p.get("value"))
        if v is None:
            return None
        p["value"] = v
        # words around the number go to the label; the counter keeps only short units
        pre, suf = str(p.get("prefix", "")), str(p.get("suffix", ""))
        lead = []
        if re.search(r"[A-Za-z]{2,}", pre):
            lead.append(pre.strip()); p["prefix"] = ""
        if len(suf.strip()) > 3 and suf.strip() not in ("km/h", "mph"):
            p["label"] = (suf.strip() + " " + str(p.get("label", ""))).strip(); p["suffix"] = ""
        if lead:
            p["lead"] = " ".join(lead)
        return p
    if typ == "grid":
        its = [it for it in p.get("items", []) if isinstance(it, dict) and it.get("label")]
        for it in its:
            it["value"] = _num(it.get("value"))
        return {**p, "items": its[:4]} if len(its) >= 2 else None
    if typ == "checks":
        rows = [r for r in p.get("rows", []) if isinstance(r, dict) and r.get("label")]
        return {**p, "rows": rows[:3]} if rows else None
    if typ == "coins":
        its = [it for it in p.get("items", []) if isinstance(it, dict) and is_emoji(str(it.get("icon", "")))]
        return {**p, "items": its[:4]} if len(its) >= 2 else None
    if typ == "quote":
        return p if words(p.get("text")) >= 3 else None
    if typ == "sticker":
        return p if p.get("text") else None
    if typ in ("slam",):                      # one keyword: a content word, never a number word
        w = str(p.get("word", "")).strip()
        return p if w and W.content(w) and _n(w) not in NUM_WORDS and not re.search(r"\d", w) else None
    if typ in ("name",):
        return p if p.get("name") else None
    if typ == "lower":
        return p if p.get("name") else None
    return p


NUM_WORDS = set("""zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen
eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety hundred thousand million billion trillion""".split())


def _states_number(words: list[dict], a: int, b: int) -> bool:
    return any(re.search(r"\d", w["t"]) or _n(w["t"]) in NUM_WORDS for w in words[a:b + 1])


def tags(raw, words: list[dict], dur: float) -> list[dict]:
    """The model's top tags, kept only when the words near them state them; each holds until the
    next one starts (the last one to the end)."""
    n, out = len(words), []
    for g in raw or []:
        if not isinstance(g, dict):
            continue
        a = _idx(g.get("from"), n)
        kind = g.get("kind")
        if a is None or kind not in ("money", "text"):      # years are read from the words, below
            continue
        lo, hi = max(0, a - 3), min(n - 1, a + 12)
        p = {"kind": kind}
        if kind in ("year", "money"):
            x, y = _num(g.get("a")), _num(g.get("b", g.get("a")))
            if x is None or y is None or not _states_number(words, lo, hi):
                continue
            if kind == "year" and not (1000 <= x <= 2200 and 1000 <= y <= 2200):
                continue
            if kind == "money" and (x < 0 or y <= 0):
                continue
            p.update(a=x, b=y)
        else:
            text = str(g.get("text", "")).strip()
            spoken = {_n(w["t"]) for w in words[lo:hi + 1]}
            toks = [_n(t) for t in text.split()]
            if not (1 <= len(toks) <= 3) or not all(t in spoken or t.isdigit() for t in toks):
                continue
            p["text"] = text.upper()
        out.append({"s": round(words[a]["s"], 3), "e": dur, "type": "tag", "p": p})
    prev = None
    for y in W.spoken_years(words):                  # every year said becomes the tag, rolling from the last one
        if prev is not None and y["year"] == prev:
            continue
        p = {"kind": "year", "a": prev if prev is not None else y["year"], "b": y["year"]}
        if y["decade"]:
            p["suffix"] = "s"
        out.append({"s": round(words[y["i"]]["s"], 3), "e": dur, "type": "tag", "p": p})
        prev = y["year"]
    out.sort(key=lambda o: o["s"])
    for o, nxt in zip(out, out[1:]):
        o["e"] = nxt["s"]
    return [o for o in out if o["e"] - o["s"] >= 0.8]


def _lines(words: list[dict]) -> str:
    return "\n".join(f"{i} {w['s']:.2f} {w['t']}" for i, w in enumerate(words))


def _ask(system: str, user: str, tag: str) -> dict | None:
    try:
        from ..common import load_env
        from ..judge import call
        env = load_env()
        if not (env.get("QUANZIL_API_KEY") or env.get("OPENROUTER_API_KEY")):
            return None
        txt = call([{"role": "system", "content": system}, {"role": "user", "content": user}], env,
                   "gemini-2.5-flash-lite", [], tag=tag)
        txt = re.sub(r"^```(?:json)?|```$", "", txt.strip(), flags=re.M).strip()
        return json.loads(txt)
    except Exception as e:                                        # the rule plan takes over
        print(f"  planner model call failed ({e}); using rules")
        return None


_NUMVAL = {w: str(i) for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen "
                                            "fourteen fifteen sixteen seventeen eighteen nineteen".split())}
_NUMVAL.update({w: str(v) for w, v in zip("twenty thirty forty fifty sixty seventy eighty ninety".split(), range(20, 100, 10))})


def _key(t: str) -> str:
    """A word for matching card copy to speech: "five" and "5" are the same word."""
    k = _n(t)
    return _NUMVAL.get(k, k)


def _spoken(label: str, words: list[dict], a: int, b: int | None, cap: int = 6) -> str:
    """A card label in the speaker's own words: the shortest spoken span (near the graphic) holding
    every content word of the model's label that was actually said. Words nobody said are dropped;
    a label mostly made of them is dropped whole ("rules General Advice" -> "general advice")."""
    want = [_key(t) for t in str(label).split() if W.content(t)]
    lo, hi = max(0, a - 4), min(len(words), (b if b is not None else a) + 14)
    if not want:                                    # only small words ("over"): kept when they were said there
        near = {_key(words[i]["t"]) for i in range(lo, hi)}
        return label if all(_key(t) in near for t in str(label).split()) else ""
    said = {_key(words[i]["t"]) for i in range(lo, hi)}
    kept = [x for x in want if x in said]
    if len(kept) * 2 < len(want):
        return ""
    want = kept
    pos = [i for i in range(lo, hi) if _key(words[i]["t"]) in want]
    best = None
    for i in pos:
        got, j = set(), i
        while j < hi and len(got) < len(set(want)):
            if _key(words[j]["t"]) in want:
                got.add(_key(words[j]["t"]))
            j += 1
        if len(got) == len(set(want)) and (best is None or j - i < best[1] - best[0]):
            best = (i, j)
    if not best or best[1] - best[0] > cap:
        return " ".join(t for t in str(label).split() if _key(t) in said or not W.content(t))
    i, j = best
    toks = [_key(t) for t in str(label).split()]
    tail = toks[len(toks) - next((k for k, t in enumerate(reversed(toks)) if W.content(t)), 0):]
    for t in tail:                                  # small words the label ends on, when they follow in speech ("five to")
        if j < len(words) and _key(words[j]["t"]) == t:
            j += 1
        else:
            break
    return " ".join(words[k]["t"] for k in range(i, j)).strip(" ,.;:!?")


def _idx(x, n):
    try:
        i = int(x)
    except (TypeError, ValueError):
        return None
    return i if 0 <= i < n else None


def _speakers_rule(words):
    """Rule fallback: a sentence ending in '?' is the other speaker."""
    spk, cur, out, start = [], 0, [], 0
    sent = []
    for i, w in enumerate(words):
        sent.append(i)
        if w["t"].rstrip()[-1:] in ".?!" or i == len(words) - 1:
            q = words[sent[-1]]["t"].rstrip().endswith("?")
            out += [(1 if q else 0)] * len(sent)
            sent = []
    return out


def footage(words: list[dict], dur: float, clip_src: str, look: str = "speaker", title: str = "",
            llm: bool = True, fill: str = "", focus=(0.5, 0.45)) -> dict:
    """EDL for one clip: words are in clip seconds [{t, s, e}]."""
    L = LOOKS[look]
    n = len(words)
    plan = _ask(FOOTAGE_SYS, f"Clip title: {title}\n\n{_lines(words)}", "engine-footage") if (llm and n) else None
    plan = plan or {}
    # speakers
    spk = [0] * n
    turns = sorted((_idx(i, n), int(s)) for i, s in plan.get("speakers", []) if _idx(i, n) is not None) if plan.get("speakers") else []
    if turns:
        cur, ti = 0, 0
        for i in range(n):
            while ti < len(turns) and turns[ti][0] <= i:
                cur = turns[ti][1]
                ti += 1
            spk[i] = cur
    else:
        spk = _speakers_rule(words)
    emph = {e for e in (_idx(i, n) for i in plan.get("emphasis", [])) if e is not None and W.content(words[e]["t"])} or None
    caps = W.groups(words, max_words=L["max_words"], max_chars=L["max_chars"], speakers=spk, emph=emph)
    # graphics, validated and spaced
    ovs, last_end = [], -9.0
    for g in plan.get("graphics", []) or []:
        a, b = _idx(g.get("from"), n), _idx(g.get("to"), n)
        if a is None or g.get("type") not in L["graphics"]:
            continue
        g["p"] = valid(g["type"], g.get("p") or {})
        if g["p"] is None:
            continue
        if g["type"] == "stat":                    # every word on the card is a word the speaker said
            for k in ("label", "lead"):
                if g["p"].get(k):
                    g["p"][k] = _spoken(g["p"][k], words, a, b)
        if g["type"] == "stat":                    # over footage: below the face, on a plate
            g["p"].update(y=0.70, plate=True)
        s = words[a]["s"]
        e = words[b]["e"] + 0.6 if b is not None and b >= a else s + 2.5
        e = min(max(e, s + 1.5), s + (2.5 if g["type"] in ("checks", "slam") else 4.0), dur)
        if s < last_end + 1.2:
            continue
        ovs.append({"s": round(s, 3), "e": round(e, 3), "type": g["type"], "p": g.get("p") or {}})
        last_end = e
    if "tag" in L["graphics"]:
        ovs += tags(plan.get("tags"), words, dur)
    if plan.get("header") and L.get("header"):
        ovs.insert(0, {"s": 0, "e": dur, "type": "header", "p": {"title": plan["header"], "band": L.get("header_band", "dark")}})
    punches = [words[i]["s"] for i in (_idx(x, n) for x in plan.get("punches", [])) if i is not None]
    if not punches:                                                          # rules: strongest words
        punches = [g["words"][-1]["s"] for g in caps if any(w["em"] for w in g["words"])][::4][:4]
    flashes = [words[i]["s"] for i in (_idx(x, n) for x in plan.get("flashes", [])) if i is not None][:2]
    # graphics that cover the frame hide captions underneath: mute captions over full-screen cards
    full = [(o["s"], o["e"]) for o in ovs if o["type"] in ("checks", "slam")]
    caps = [g for g in caps if not any(a <= g["s"] < b for a, b in full)]
    clip = {"id": "v0", "src": clip_src, "s": 0, "e": dur, "in": 0, "push": L["push"], "punch": L["punch"],
            "punches": punches, "shakes": punches[:2] if L.get("shake") else [], "fx": focus[0], "fy": focus[1]}
    if fill:
        clip["fill"] = fill
    return {"w": 1080, "h": 1920, "dur": round(dur, 3), "theme": L["theme"],
            "clips": [clip], "captions": {"style": L["caption"], "y": L["cap_y"], "groups": caps},
            "overlays": ovs, "trans": [{"t": t, "type": "flash"} for t in flashes], "audio": "mix"}


MIN_CARD_S = {"chat": 2.2, "grid": 2.0, "coins": 1.6, "checks": 1.5, "quote": 1.8}   # time a card needs to build and be read
GROUND_CYCLE = ["dark", "cream", "blue", "grid", "paper", "neon", "news", "red", "white", "sunset"]
TRANS_CYCLE = ["whip", "flash", "zoom", "tear", "iris", "glitch"]
DARK_INK = {"cream", "paper", "white", "news"}


def _sentences(words):
    out, cur = [], []
    for i, w in enumerate(words):
        cur.append(i)
        if w["t"].rstrip()[-1:] in ".?!" or i == len(words) - 1:
            out.append(cur)
            cur = []
    return out


def script(words: list[dict], dur: float, look: str = "kinetic", llm: bool = True, images: list | None = None) -> dict:
    """EDL for a voiceover-only short. words: [{t, s, e}] from the voiceover."""
    L = LOOKS[look]
    n = len(words)
    sents = _sentences(words)
    user = "\n".join(f"SENTENCE {k}: " + " ".join(f"{i}:{words[i]['t']}" for i in s) for k, s in enumerate(sents))
    plan = (_ask(SCRIPT_SYS, user + "\n\n" + _lines(words), "engine-script") if (llm and n) else None) or {}
    emph = {e for e in (_idx(i, n) for i in plan.get("emphasis", [])) if e is not None and W.content(words[e]["t"])}
    if not emph:
        for s in sents:
            c = [i for i in s if W.is_emph(words[i]["t"])]
            if c:
                emph.add(max(c, key=lambda i: len(words[i]["t"])))
    # one scene per sentence; long sentences split in two so something changes every ~2.5 s
    beats, sent_of = [], []
    for si, s in enumerate(sents):
        if words[s[-1]]["e"] - words[s[0]]["s"] > 3.2 and len(s) > 6:
            commas = [k + 1 for k in range(2, len(s) - 3) if words[s[k]]["t"].rstrip()[-1:] in ",;:"]
            h = min(commas, key=lambda k: abs(k - len(s) / 2)) if commas else len(s) // 2
            beats += [s[:h], s[h:]]; sent_of += [si, si]
        else:
            beats.append(s); sent_of.append(si)
    asked = {}
    for sc in plan.get("scenes", []) or []:
        a = _idx(sc.get("from"), n)
        if a is not None:
            asked[a] = sc
    scenes, grounds, trans = [], [], []
    last_obj = ""
    for k, b in enumerate(beats):
        s = 0.0 if k == 0 else words[b[0]]["s"] - 0.08
        e = dur if k == len(beats) - 1 else words[beats[k + 1][0]]["s"] - 0.08
        sent = sents[sent_of[k]]
        sc = next((asked[i] for i in b if i in asked), None) or {}
        first_of_sent = k == 0 or sent_of[k - 1] != sent_of[k]
        if not sc and first_of_sent:                  # the object may come from anywhere in the sentence
            sc = {"obj": next((asked[i].get("obj") for i in sent if i in asked), "")}
        last_k = max(j for j in range(len(beats)) if sent_of[j] == sent_of[k])
        sent_e = dur if last_k == len(beats) - 1 else words[beats[last_k + 1][0]]["s"] - 0.08
        typ = sc.get("type") if sc.get("type") in L["graphics"] + ["kinetic"] else "kinetic"
        gp = valid(typ, sc.get("p") or {}) if typ != "kinetic" else {}
        said = {_n(words[i]["t"]) for i in b}
        if gp and typ == "slam" and _n(gp.get("word", "")) not in said:          # never print a word nobody says
            gp = None
        if gp and typ == "quote" and len({_n(x) for x in str(gp.get("text", "")).split()} & said) < 0.6 * len(str(gp.get("text", "")).split()):
            gp = None
        if gp and e - s < MIN_CARD_S.get(typ, 0):      # a card that cannot finish building is an empty box
            gp = None
        if gp is None:
            typ, gp = "kinetic", {}
        if not any(i in emph for i in b):                      # every beat gets one big word
            c = [i for i in b if W.is_emph(words[i]["t"])] or [i for i in b if W.content(words[i]["t"])]
            if c:                                              # no content word -> no big word ("So what can you see?")
                emph.add(max(c, key=lambda i: len(words[i]["t"])))
        g = sc.get("ground") if sc.get("ground") in GROUND_CYCLE else GROUND_CYCLE[k % len(GROUND_CYCLE)]
        if L.get("grounds"):
            g = L["grounds"][k % len(L["grounds"])]
        ink = "#111" if g in DARK_INK else "#fff"
        grounds.append({"s": round(s, 3), "e": round(e, 3), "type": g})
        bw = [{"t": words[i]["t"], "s": words[i]["s"], "e": words[i]["e"], "em": i in emph} for i in b]
        # the spoken words are always on screen; a graphic sits beside them when the beat earns one
        kin = {"s": round(s, 3), "e": round(e, 3), "type": "kinetic",
               "p": {"words": bw, "ink": ink, "accent": L["theme"]["accent"], "tilt": L.get("tilt", False), "glow": L.get("glow", False),
                     "dof": L.get("dof", False), "emFont": "PF" if (L.get("serif_em") and k % 3 == 1) else "MB",
                     "y": {"kinetic": 0.50, "name": 0.58}.get(typ, 0.30)}}
        if typ != "kinetic":
            kin["p"]["big"] = 76                      # the graphic carries the number/claim; no second big word
        if not L.get("caption"):                       # a captioned look puts the words in the captions instead
            scenes.append(kin)
        obj = str(sc.get("obj") or "").strip()
        if L.get("caption"):
            # the words are only captions here, so every beat without a card needs its hero object:
            # its own, else its sentence's, else the one before held over
            obj = obj or next((str(asked[i].get("obj") or "") for i in sent if i in asked and asked[i].get("obj")), "") or last_obj
            prev = next((o for o in reversed(scenes) if o["type"] == "object"), None)
            if typ == "kinetic" and obj and prev and prev["p"]["emoji"] == obj and abs(prev["e"] - s) < 0.05:
                prev["e"] = round(e, 3)                                  # the same object carries on
            elif typ == "kinetic" and obj:
                scenes.append({"s": round(s, 3), "e": round(e, 3), "type": "object",
                               "p": {"emoji": obj, "y": 0.36, "size": 520, "spot": True}})
            last_obj = obj or last_obj
        elif typ == "kinetic" and obj and first_of_sent:
            scenes.append({"s": round(s, 3), "e": round(sent_e, 3), "type": "object",
                           "p": {"emoji": obj, "y": 0.27, "spot": g not in DARK_INK}})
        if typ != "kinetic":
            p = dict(gp)
            p.setdefault("ink", ink)
            if typ == "stat":
                p.setdefault("color", L["theme"]["accent"] if ink == "#fff" else ink)
            if typ == "name":
                p.setdefault("color", ink); p.setdefault("subColor", ink)
            if typ in ("stat", "coins", "grid", "chat", "pill", "quote", "name"):
                p.setdefault("y", (0.30 if L.get("caption") else 0.58) if typ != "name" else 0.30)   # clear of captions
            if typ in ("slam", "checks") and not L.get("caption"):   # full-frame cards replace the type for that beat
                scenes.pop()
                p.setdefault("ground", g)
            scenes.append({"s": round(s + (0.0 if typ in ("slam", "checks") else 0.25), 3), "e": round(e, 3), "type": typ, "p": p})
        if k:
            trans.append({"t": round(s + 0.08, 3), "type": sc.get("trans") if sc.get("trans") in TRANS_CYCLE else (L.get("trans") or TRANS_CYCLE)[k % len(L.get("trans") or TRANS_CYCLE)]})
    ovs = tags(plan.get("tags"), words, dur) if "tag" in L["graphics"] else []   # above the moving stage
    caps = None
    if L.get("caption"):
        full = [(o["s"], o["e"]) for o in scenes if o["type"] in ("slam", "checks")]
        groups = [g for g in W.groups(words, max_words=L["max_words"], max_chars=L["max_chars"], emph=emph)
                  if not any(a <= g["s"] < b for a, b in full)]
        caps = {"style": L["caption"], "y": L["cap_y"], "groups": groups}
    return {"w": 1080, "h": 1920, "dur": round(dur, 3), "theme": L["theme"], "grounds": grounds, "scenes": scenes, "overlays": ovs,
            "trans": trans, "camera": {"push": 0.06, "drift": True}, "captions": caps, "audio": "mix"}
