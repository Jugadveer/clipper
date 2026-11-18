"""Edit styles: caption look + camera movement + extras, as one named preset.

  viral    streamer clips (Speedy Boykins look): glow captions that build word by word,
           slow push-in, punch-ins + shake + flash on the big beats, narrator notes,
           frozen end card with emoji + punchline + bass hit
  pop      bold yellow pop captions, punch-ins, end card
  karaoke  box under the spoken word, gentle push, end card
  hormozi  podcast clips: huge 2-word captions, jump-zoom on every new sentence, headline on top,
           no end card (talking-head channels end on the line, not on a meme)
  clean    podcast/professional: understated captions, slow push, headline on top
  speaker  (engine) each speaker owns a caption colour, glow, punch-ins, flashes, sticker/stat cards
  ui       (engine) small clean captions + glass cards that restate what is said (chat, pill, grid, checks)
  bigword  (engine) one huge word at a time under a header band
  tiered   (engine) small lead line + one huge keyword, documentary grade and grain
  story    (engine) one word at a time, key nouns on a blue box, a top tag for the year or money
  mix      a different preset AND accent per clip (seeded by clip index), so a bulk channel's
           uploads do not all look identical (also helps against reupload detection)
"""
from __future__ import annotations

import copy

PRESETS = {
    "viral": {
        "caption": {"caption": "glow", "font": "Montserrat ExtraBold", "size": 98, "y": 0.52, "accent": "lime"},
        "camera": {"push": 0.06, "punch": 0.16, "shake": 14, "flash": True, "jump_zoom": 0.0},
        "end_card": True, "notes": True, "hook": False,
    },
    "pop": {
        "caption": {"caption": "pop", "font": "Montserrat Black", "size": 100, "y": 0.55, "accent": "lime"},
        "camera": {"push": 0.04, "punch": 0.14, "shake": 8, "flash": False, "jump_zoom": 0.0},
        "end_card": True, "notes": True, "hook": False,
    },
    "karaoke": {
        "caption": {"caption": "box", "font": "Montserrat Black", "size": 92, "y": 0.56, "accent": "pink",
                    "outline": 0, "shadow": 0},
        "camera": {"push": 0.05, "punch": 0.12, "shake": 0, "flash": False, "jump_zoom": 0.0},
        "end_card": True, "notes": True, "hook": False,
    },
    "hormozi": {
        "caption": {"caption": "hormozi", "font": "Montserrat Black", "size": 128, "y": 0.60, "accent": "lime",
                    "outline": 9, "shadow": 4},
        "camera": {"push": 0.0, "punch": 0.0, "shake": 0, "flash": False, "jump_zoom": 0.12},
        "end_card": False, "notes": False, "hook": True,
    },
    "clean": {
        "caption": {"caption": "clean", "font": "Montserrat ExtraBold", "size": 84, "upper": False,
                    "accent": "yellow", "outline": 0, "shadow": 2},
        "camera": {"push": 0.05, "punch": 0.0, "shake": 0, "flash": False, "jump_zoom": 0.0},
        "end_card": False, "notes": False, "hook": True,
    },
}
# styles drawn by clipper/engine (HyperFrames): the reframe is the clipper's, every edit on top is the engine's
ENGINE = ("speaker", "ui", "bigword", "tiered", "story")
ACCENT_CYCLE = ["lime", "yellow", "red", "pink", "cyan"]
MIX = {"stream": ["viral", "bigword", "pop", "speaker", "karaoke"],
       "podcast": ["hormozi", "speaker", "ui", "clean", "tiered", "bigword", "story"]}
# engine looks render through a headless browser (minutes per clip); a live stream wants its clips
# soon after the moment, so live mode's "mix" rotates only the ffmpeg presets (about a minute each)
MIX_FAST = {g: [n for n in names if n not in ENGINE] for g, names in MIX.items()}
CHOICES = ["auto", "mix", *PRESETS, *ENGINE]


def pick(style: str, genre: str, index: int) -> tuple[str, dict]:
    """The preset for clip number `index` (0-based)."""
    if style in ("auto", ""):
        style = "viral" if genre == "stream" else "hormozi"
    name = style
    accent = None
    if style in ("mix", "mix-fast"):
        table = MIX_FAST if style == "mix-fast" else MIX
        names = table.get(genre, table["stream"])
        name = names[index % len(names)]
        accent = ACCENT_CYCLE[(index * 2 + 1) % len(ACCENT_CYCLE)]
    if name in ENGINE:
        return name, {"name": name, "engine": name}
    p = copy.deepcopy(PRESETS[name])
    if accent:
        p["caption"]["accent"] = accent
    p["name"] = name
    return name, p
