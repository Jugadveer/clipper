"""Animated captions as an ASS file (burned in by ffmpeg/libass), in several styles.

Caption styles (chosen by the style preset, see styles.py):
  glow     viral streamer-clip look (Speedy Boykins): mid-frame, words appear AS SPOKEN and
           build up to 4 per line, white with a coloured glow, punchline words coloured
  pop      heavy uppercase 1-3 words, spoken word pops and turns yellow
  hormozi  podcast look: huge Montserrat Black, 2 words, spoken word in the accent colour
  box      karaoke: a coloured box under the spoken word (word positions measured from the
           font so the box lands exactly on the word)
  clean    understated sentence-case lower third with a soft shadow

Also: narrator notes ("*TYPES*", "WAIT FOR IT"), the hook headline, the credit line.
Profanity is starred in captions only (audio untouched).
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from .common import FONTS

EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍]")
CENSOR = re.compile(r"(?<![A-Za-z])(fuck|fucking|fucked|fucker|motherfucker|shit|bitch|bitches|nigga|niggas|"
                    r"nigger|dick|pussy|cunt|faggot|asshole|bastard)(?![A-Za-z])", re.I)

# ASS colours are &HAABBGGRR
WHITE, BLACK = "&H00FFFFFF", "&H00000000"
ACCENTS = {  # name: (fill colour for keywords, glow colour)
    "lime": ("&H0000FF9E", "&H0000C878"),
    "yellow": ("&H0000E5FF", "&H0000A8E0"),
    "red": ("&H003C3CFF", "&H002020D0"),
    "pink": ("&H00C860FF", "&H009030E0"),
    "cyan": ("&H00FFE040", "&H00D0A000"),
}
FONT_FILES = {"Montserrat Black": "Montserrat-Black.ttf", "Montserrat ExtraBold": "Montserrat-ExtraBold.ttf",
              "Montserrat SemiBold": "Montserrat-SemiBold.ttf", "Anton": "Anton.ttf"}


def censor(word: str) -> str:
    """Star out the vowels of profanity in CAPTIONS only: keeps Shorts ad-friendly."""
    def star(m):
        w = m.group(0)
        return w[0] + re.sub(r"[aeiouAEIOU]", "*", w[1:])
    return CENSOR.sub(star, word)


def _t(sec: float) -> str:
    sec = max(0.0, sec)
    return f"{int(sec // 3600)}:{int(sec % 3600 // 60):02d}:{sec % 60:05.2f}"


def _clean(w: str) -> str:
    return EMOJI_RE.sub("", w).replace("{", "").replace("}", "").replace("\\", "")


def clip_words(words_doc: dict, start: float, end: float, clean: bool = True) -> list[dict]:
    out = []
    for seg in words_doc.get("segments", []):
        if seg["end"] < start or seg["start"] > end:
            continue
        for w in seg["words"]:
            if w["s"] >= start - 0.05 and w["e"] <= end + 0.3:
                txt = _clean(w["w"]).strip()
                if not txt:
                    continue
                if clean:
                    txt = censor(txt)
                # Whisper splits contractions ("y" + "'all"): glue them back onto the word
                if out and txt[0] in "'’-" and w["s"] - start - out[-1]["e"] < 0.3:
                    out[-1]["w"] += txt
                    out[-1]["e"] = w["e"] - start
                    continue
                out.append({"w": txt, "s": w["s"] - start, "e": w["e"] - start})
    return out


def chunk(words: list[dict], max_words: int = 3, max_chars: int = 16, gap: float = 0.45) -> list[list[dict]]:
    groups, cur = [], []
    for w in words:
        if cur:
            prev = cur[-1]
            chars = sum(len(x["w"]) + 1 for x in cur) + len(w["w"])
            if (len(cur) >= max_words or chars > max_chars or w["s"] - prev["e"] > gap
                    or re.search(r"[.!?,]$", prev["w"])):
                groups.append(cur)
                cur = []
        cur.append(w)
    if cur:
        groups.append(cur)
    return groups


@lru_cache(None)
def _measure_font(name: str, size: int):
    from fontTools.ttLib import TTFont
    from PIL import ImageFont
    path = FONTS / FONT_FILES[name]
    t = TTFont(str(path))
    os2, upem = t["OS/2"], t["head"].unitsPerEm
    # libass sizes a font so that winAscent + winDescent == the ASS font size
    em_px = size * upem / (os2.usWinAscent + os2.usWinDescent)
    return ImageFont.truetype(str(path), max(1, round(em_px)))


def text_width(text: str, font: str, size: int) -> float:
    return _measure_font(font, size).getlength(text)


def _key(w: str) -> str:
    return re.sub(r"\W", "", w).lower()


def _group_end(groups, gi, g, stop, tail=0.5, cap=0.9):
    e = groups[gi + 1][0]["s"] if gi + 1 < len(groups) else min(stop, g[-1]["e"] + tail)
    return min(e, g[-1]["e"] + cap, stop)


def build_ass(words: list[dict], clip_len: float, out: Path, layout: str, style: dict,
              hook: str = "", emphasis: list[str] | None = None, credit: str = "",
              notes: list[dict] | None = None, seam_y: int = 960, end_at: float | None = None) -> Path:
    """`style`: a caption section from styles.py. `end_at`: captions stop there (end card)."""
    emph = {_key(e) for e in (emphasis or []) if e}
    kind = style["caption"]
    font, size = style["font"], style["size"]
    accent_fill, accent_glow = ACCENTS[style.get("accent", "lime")]
    upper = style.get("upper", True)
    stop = clip_len if end_at is None else end_at
    if kind == "clean":
        cap_y = int(1920 * 0.72)
    elif layout in ("split", "stack"):
        cap_y = seam_y                          # on the seam between the two panels
    else:
        cap_y = int(1920 * style.get("y", 0.55))
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{font},{size},{WHITE},{WHITE},{BLACK},&H64000000,0,0,0,0,100,100,0,0,1,{style.get('outline', 7)},{style.get('shadow', 3)},5,40,40,0,1
Style: Glow,{font},{size},&HFF000000,&HFF000000,{accent_glow},&HFF000000,0,0,0,0,100,100,0,0,1,12,0,5,40,40,0,1
Style: Box,{font},{size},{WHITE},{WHITE},{accent_glow},{accent_glow},0,0,0,0,100,100,0,0,3,14,0,5,0,0,0,1
Style: Hook,Montserrat ExtraBold,86,&H00101010,&H00101010,&H00FFFFFF,&H00FFFFFF,0,0,0,0,100,100,0,0,3,20,0,8,90,90,140,1
Style: Note,Montserrat ExtraBold Italic,78,{accent_fill},{accent_fill},{BLACK},&H80000000,0,0,0,0,100,100,0,0,1,5,2,5,60,60,0,1
Style: Credit,Montserrat ExtraBold,34,&H50FFFFFF,&H50FFFFFF,&H90000000,&H00000000,0,0,0,0,100,100,1,0,1,2,0,5,40,40,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev: list[str] = []
    words = [w for w in words if w["s"] < stop - 0.1]
    fmt = (lambda s: s.upper()) if upper else (lambda s: s)
    pos = r"{\an5\pos(540," + str(cap_y) + r")}"

    if kind == "glow":
        # words appear AS SPOKEN and build the line up (max 4); the line clears on a pause
        groups = chunk(words, max_words=4, max_chars=20, gap=0.5)
        for gi, g in enumerate(groups):
            g_end = _group_end(groups, gi, g, stop, 0.6, 0.8)
            for wi, w in enumerate(g):
                s = w["s"]
                e = g[wi + 1]["s"] if wi + 1 < len(g) else g_end
                if e - s < 0.04:
                    continue
                shown = []
                for x in g[:wi + 1]:
                    word = fmt(x["w"])
                    if _key(x["w"]) in emph:
                        word = r"{\c" + accent_fill + "}" + word + r"{\c" + WHITE + "}"
                    shown.append(word)
                plain = " ".join(fmt(x["w"]) for x in g[:wi + 1])
                pop = r"{\fscx108\fscy108\t(0,80,\fscx100\fscy100)}" if wi == 0 else ""
                ev.append(f"Dialogue: 0,{_t(s)},{_t(e)},Glow,,0,0,0,,{pos}{{\\blur9}}{pop}{plain}")
                ev.append(f"Dialogue: 1,{_t(s)},{_t(e)},Cap,,0,0,0,,{pos}{{\\bord3\\shad0}}{pop}{' '.join(shown)}")

    elif kind in ("pop", "hormozi"):
        groups = chunk(words, max_words=2 if kind == "hormozi" else 3,
                       max_chars=14 if kind == "hormozi" else 16)
        active = accent_fill if kind == "hormozi" else ACCENTS["yellow"][0]
        for gi, g in enumerate(groups):
            g_end = _group_end(groups, gi, g, stop)
            for wi, w in enumerate(g):
                s = w["s"] if wi else max(0.0, g[0]["s"])
                e = g[wi + 1]["s"] if wi + 1 < len(g) else g_end
                if e - s < 0.04:
                    continue
                parts = []
                for wj, x in enumerate(g):
                    word = fmt(x["w"])
                    if wj == wi:
                        col = accent_fill if _key(x["w"]) in emph else active
                        parts.append(r"{\c" + col + r"\fscx114\fscy114\t(0,90,\fscx100\fscy100)}" + word
                                     + r"{\c" + WHITE + r"}")
                    else:
                        parts.append(word)
                fade = r"{\fad(40,0)}" if wi == 0 else ""
                ev.append(f"Dialogue: 1,{_t(s)},{_t(e)},Cap,,0,0,0,,{pos}{fade}{' '.join(parts)}")

    elif kind == "box":
        groups = chunk(words, max_words=3, max_chars=16)
        space = text_width(" ", font, size)
        for gi, g in enumerate(groups):
            g_end = _group_end(groups, gi, g, stop)
            ws = [fmt(x["w"]) for x in g]
            widths = [text_width(w, font, size) for w in ws]
            x = 540 - (sum(widths) + space * (len(ws) - 1)) / 2
            centers = []
            for wd in widths:
                centers.append(x + wd / 2)
                x += wd + space
            for wi, w in enumerate(g):            # the box under the spoken word
                s = w["s"]
                e = g[wi + 1]["s"] if wi + 1 < len(g) else g_end
                if e - s >= 0.04:
                    ev.append(f"Dialogue: 0,{_t(s)},{_t(e)},Box,,0,0,0,,"
                              r"{\an5\pos(" + f"{centers[wi]:.0f},{cap_y}" + r")}" + ws[wi])
            for wi, word in enumerate(ws):       # every word, white, on top of the box
                ev.append(f"Dialogue: 1,{_t(g[0]['s'])},{_t(g_end)},Cap,,0,0,0,,"
                          r"{\an5\pos(" + f"{centers[wi]:.0f},{cap_y}" + r")}" + word)

    else:  # clean
        groups = chunk(words, max_words=4, max_chars=22, gap=0.6)
        for gi, g in enumerate(groups):
            e = _group_end(groups, gi, g, stop, 0.6, 1.0)
            ev.append(f"Dialogue: 1,{_t(g[0]['s'])},{_t(e)},Cap,,0,0,0,,{pos}"
                      r"{\fad(80,80)\bord3\shad4}" + " ".join(x["w"] for x in g))

    for n in notes or []:
        try:
            t = float(n.get("t", 0))
        except (TypeError, ValueError):
            continue
        text = _clean(str(n.get("text", ""))).strip().upper()
        if text and 0 <= t < stop - 0.5:
            ny = int(1920 * 0.30) if layout not in ("split", "stack") else seam_y - 220
            ev.append(f"Dialogue: 2,{_t(t)},{_t(min(stop, t + 1.7))},Note,,0,0,0,,"
                      r"{\an5\pos(540," + str(ny) + r")\fad(60,150)\fscx80\fscy80\t(0,100,\fscx100\fscy100)\frz-3}"
                      + text)
    hook = _clean(hook).strip()
    if hook:
        ev.append(f"Dialogue: 3,{_t(0)},{_t(min(2.6, stop))},Hook,,0,0,0,,"
                  r"{\fad(0,200)\fscx92\fscy92\t(0,120,\fscx100\fscy100)}" + hook)
    if credit:
        cy = int(1920 * 0.62) if layout not in ("split", "stack") else seam_y + 330
        ev.append(f"Dialogue: 0,{_t(0)},{_t(stop)},Credit,,0,0,0,,"
                  r"{\an5\pos(540," + str(min(cy, 1700)) + r")}" + _clean(credit))
    out.write_text(head + "\n".join(ev) + "\n", encoding="utf-8")
    return out
