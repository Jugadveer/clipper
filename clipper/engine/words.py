"""Spoken words -> caption groups: short lines broken where speech breaks, one speaker per line,
emphasis words marked. Pure functions, no model."""
from __future__ import annotations

import re

STOP = set("""a an the and or but so to of in on at for with by from as is are was were be been it its it's this that these
those i you he she we they me him her us them my your his our their do does did not no yes just like
really very then than if when what which who how all any some there here have has had will would can could
should about into up out over i'm you're don't can't that's""".split())


def clean(w: str) -> str:
    return w.strip()


def content(w: str) -> bool:
    """Not a stop word / question word: the only words allowed to be the big one."""
    k = re.sub(r"[^\w']", "", w).lower()
    return bool(k) and k not in STOP and k not in {"where", "why", "whose", "whom", "so", "even", "also", "only"}


def is_emph(w: str) -> bool:
    k = re.sub(r"[^\w']", "", w).lower()
    if not k or k in STOP:
        return False
    return bool(re.search(r"\d", k)) or len(k) >= 6 or w.endswith("!")


def groups(words: list[dict], max_words: int = 3, max_chars: int = 18, gap: float = 0.4,
           speakers: list[int] | None = None, emph: set[int] | None = None) -> list[dict]:
    """words: [{t, s, e}] (times in output seconds). speakers: speaker id per word (optional).
    emph: indices of emphasised words (optional; otherwise the longest content word per line)."""
    out, cur = [], []

    def flush():
        if not cur:
            return
        idx = [i for i, _ in cur]
        if emph is None:
            cand = [i for i in idx if is_emph(words[i]["t"])]
            em = {max(cand, key=lambda i: len(words[i]["t"]))} if cand else set()
        else:
            em = {i for i in idx if i in emph}
        out.append({"s": words[idx[0]]["s"], "e": words[idx[-1]]["e"],
                    "spk": (speakers[idx[0]] if speakers else 0),
                    "words": [{"t": words[i]["t"], "s": words[i]["s"], "e": words[i]["e"], "em": i in em} for i in idx]})
        cur.clear()

    for i, w in enumerate(words):
        if cur:
            prev = words[cur[-1][0]]
            chars = sum(len(words[j]["t"]) + 1 for j, _ in cur) + len(w["t"])
            spk_change = speakers is not None and speakers[i] != speakers[cur[-1][0]]
            if (len(cur) >= max_words or chars > max_chars or w["s"] - prev["e"] > gap or spk_change
                    or prev["t"].rstrip()[-1:] in ".?!,;:"):
                flush()
        cur.append((i, w))
    flush()
    # hold each line until the next starts (no flicker), but not more than 0.6 s past its last word
    for a, b in zip(out, out[1:]):
        a["e"] = min(b["s"], a["e"] + 0.6)
    if out:
        out[-1]["e"] += 0.5
    return out


UNITS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9}
TEENS = {"ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
         "seventeen": 17, "eighteen": 18, "nineteen": 19}
TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
DECADES = {k[:-1] + "ies": v for k, v in TENS.items()}          # sixties -> 60


def spoken_years(words: list[dict]) -> list[dict]:
    """Years said in a transcript: "1983", "1960s", "nineteen eighty three", "the nineteen sixties",
    "eighteen eighty nine", "twenty seventeen", "two thousand and eight". A phrase never runs across
    punctuation ("In nineteen sixty, two men" is 1960, not 1962) and a lone number word is never a
    year. Returns [{i, j, year, decade}] with i..j the word span."""
    toks = [re.sub(r"[^\w']", "", w["t"]).lower() for w in words]
    stop = [bool(re.search(r"[.,;:!?]$", w["t"].strip())) for w in words]
    out, i, n = [], 0, len(words)

    def two_digit(k):                     # the last two digits of a spoken year starting at token k
        if k >= n:
            return None
        t = toks[k]
        if t in TEENS:
            return TEENS[t], k, False
        if t in DECADES:
            return DECADES[t], k, True
        if t in TENS:
            if not stop[k] and k + 1 < n and toks[k + 1] in UNITS:
                return TENS[t] + UNITS[toks[k + 1]], k + 1, False
            return TENS[t], k, False
        if t == "oh" and not stop[k] and k + 1 < n and toks[k + 1] in UNITS:
            return UNITS[toks[k + 1]], k + 1, False
        if t == "hundred":
            return 0, k, False
        return None

    while i < n:
        t = toks[i]
        m = re.fullmatch(r"(1[0-9]\d\d|20\d\d|21\d\d)(s?)", t)
        if m:
            out.append({"i": i, "j": i, "year": int(m.group(1)), "decade": bool(m.group(2))})
            i += 1
            continue
        if t in ("eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen",
                 "twenty") and not stop[i]:
            r = two_digit(i + 1)
            if r and not (t == "twenty" and r[0] < 10 and toks[i + 1] != "oh"):
                cent = TEENS.get(t, 20)
                out.append({"i": i, "j": r[1], "year": cent * 100 + r[0], "decade": r[2]})
                i = r[1] + 1
                continue
        if t == "two" and not stop[i] and i + 1 < n and toks[i + 1] == "thousand":
            k = i + 1
            y = 2000
            if not stop[k] and k + 1 < n:
                k2 = k + 1 + (1 if toks[k + 1] == "and" and not stop[k + 1] else 0)
                r = (UNITS[toks[k2]], k2, False) if k2 < n and toks[k2] in UNITS else two_digit(k2)
                if r and not r[2]:
                    y, k = 2000 + r[0], r[1]
            if y > 2000 or (i and toks[i - 1] in ("in", "since", "by", "until", "of", "from", "year")):
                out.append({"i": i, "j": k, "year": y, "decade": False})   # "two thousand people" is a count
            i = k + 1
            continue
        i += 1
    return out
