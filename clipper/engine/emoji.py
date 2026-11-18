"""Apple (iPhone) emoji as images, cached locally.

160 px originals from Emojipedia's CDN (Apple's artwork, the same as on an iPhone); if one is
missing, the 96 px bitmap from the Apple Color Emoji font build in assets/emoji. Apple's emoji are
Apple's artwork, used here the way every Shorts editor uses them; they are not openly licensed.
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "assets" / "emoji" / "cache"
FONT = ROOT / "assets" / "emoji" / "AppleColorEmoji-Linux.ttf"
VERSIONS = ("419", "391", "354")


def _codes(e: str) -> str:
    return "-".join(f"{ord(c):x}" for c in e)


def _slugs(e: str) -> list[str]:
    """Emojipedia names files by CLDR short name ("exploding-head", "red-heart", "flag-china")."""
    out = []
    try:
        import emoji as _E                        # CLDR names
        n = _E.demojize(e).strip(":")
        if n and n != e:
            if all(0x1F1E6 <= ord(c) <= 0x1F1FF for c in e):
                n = "flag " + n
            out.append(re.sub(r"[^a-z0-9]+", "-", n.lower()).strip("-"))
    except ImportError:
        pass
    names = [unicodedata.name(c, "") for c in e if c not in "‍️" and not 0x1F3FB <= ord(c) <= 0x1F3FF]
    out.append(re.sub(r"[^a-z0-9]+", "-", " ".join(names).lower()).strip("-"))
    return list(dict.fromkeys(s for s in out if s))


def _cdn(e: str, out: Path) -> bool:
    codes = [_codes(e), _codes(e.replace("️", "")), _codes(e.replace("️", "") + "️")]
    for v in VERSIONS:
        for slug, c in ((s, c) for s in _slugs(e) for c in dict.fromkeys(codes)):
            url = f"https://em-content.zobj.net/source/apple/{v}/{slug}_{c}.png"
            try:
                r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0 (shorts-engine)"})
            except requests.RequestException:
                continue
            if r.status_code == 200 and r.content[:4] == b"\x89PNG":
                out.write_bytes(r.content)
                return True
    return False


def _font(e: str, out: Path) -> bool:
    if not FONT.exists():
        return False
    from fontTools.ttLib import TTFont
    f = TTFont(str(FONT), lazy=True)
    cps = [ord(c) for c in e if c != "️"]
    if len(cps) != 1:
        return False
    g = f.getBestCmap().get(cps[0])
    if not g:
        return False
    strike = max(f["CBDT"].strikeData, key=lambda s: len(s))       # all strikes share glyph names
    for sd in reversed(f["CBDT"].strikeData):
        if g in sd:
            bmp = sd[g]
            bmp.decompile()
            out.write_bytes(bmp.imageData)
            return True
    return False


def png(e: str) -> Path | None:
    """Path to a cached PNG of emoji `e`, fetching it on first use; None if it cannot be found."""
    e = e.strip()
    if not e:
        return None
    CACHE.mkdir(parents=True, exist_ok=True)
    out = CACHE / f"{_codes(e)}.png"
    if out.exists() and out.stat().st_size > 100:
        return out
    if _cdn(e, out) or _font(e, out):
        return out
    return None


def is_emoji(s: str) -> bool:
    return bool(s) and all(ord(c) > 0x2000 or c in "‍️" for c in s.strip())
