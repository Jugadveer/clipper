"""Looks: one named bundle of caption style, camera, grade, accent and which graphics are allowed.
Footage looks (clips, talking heads) and script looks (voiceover-only motion graphics)."""

ALL_GRAPHICS = ["chat", "pill", "grid", "checks", "stat", "quote", "lower", "sticker", "slam", "coins", "name"]

LOOKS = {
    # ---- footage
    "speaker": {   # -rumn: each speaker owns a colour, glow, punch-ins, flashes, warm grade
        "caption": "speaker", "cap_y": 0.64, "max_words": 3, "max_chars": 16, "push": 0.05, "punch": 0.14, "shake": True,
        "theme": {"accent": "#d7ff3d", "grade": "warm", "vignette": 0.45, "grain": 0.0},
        "graphics": ["sticker", "pill", "stat", "slam"], "header": False},
    "ui": {        # zak: small clean captions + glass UI cards that say what is said
        "caption": "clean", "cap_y": 0.56, "max_words": 4, "max_chars": 22, "push": 0.04, "punch": 0.08, "shake": False,
        "theme": {"accent": "#3b6cff", "grade": "cool", "vignette": 0.3, "grain": 0.0},
        "graphics": ["chat", "pill", "grid", "checks", "stat", "quote", "lower"], "header": False},
    "bigword": {   # Z-gCH: one word at a time, header band, punchline in yellow
        "caption": "bigword", "cap_y": 0.62, "max_words": 1, "max_chars": 14, "push": 0.03, "punch": 0.12, "shake": True,
        "theme": {"accent": "#ffd400", "grade": "punchy", "vignette": 0.3, "grain": 0.0},
        "graphics": ["sticker", "stat"], "header": True, "header_band": "dark"},
    "tiered": {    # C-yZl / MoVlt: small lead line + one huge keyword, documentary grade + grain
        "caption": "tiered", "cap_y": 0.66, "max_words": 4, "max_chars": 24, "push": 0.05, "punch": 0.1, "shake": False,
        "theme": {"accent": "#ffd400", "grade": "fade", "vignette": 0.5, "grain": 0.07},
        "graphics": ["slam", "quote", "stat", "lower", "pill"], "header": False},
    "karaoke": {   # the baseline: uppercase, current word yellow
        "caption": "karaoke", "cap_y": 0.66, "max_words": 3, "max_chars": 16, "push": 0.04, "punch": 0.12, "shake": False,
        "theme": {"accent": "#ffd400", "grade": "punchy", "vignette": 0.3, "grain": 0.0},
        "graphics": ["sticker", "stat", "pill"], "header": True, "header_band": "none"},
    "story": {     # DfpnDOvh (Hi History): one word at a time, key nouns on a blue box, a top tag carries the
                   # year / the money / the day; in script mode an object sits on a cinematic set per sentence
        "caption": "boxed", "cap_y": 0.6, "max_words": 1, "max_chars": 14, "push": 0.05, "punch": 0.1, "shake": False,
        "theme": {"accent": "#1d2ce6", "grade": "punchy", "vignette": 0.55, "grain": 0.03},
        "graphics": ["tag", "sticker", "coins", "name"], "header": False,
        "grounds": ["stage", "night", "money", "wine", "blue", "dark"], "trans": ["whip", "zoom", "whip", "flash"]},
    # ---- script (voiceover only)
    "kinetic": {   # s5Zf / C-yZl: grounds change every beat, keyword huge, mixed fonts, grain
        "caption": None, "cap_y": 0.5, "max_words": 4, "max_chars": 24, "push": 0, "punch": 0, "shake": False,
        "theme": {"accent": "#ffd400", "grade": "none", "vignette": 0.35, "grain": 0.06},
        "graphics": ["stat", "chat", "pill", "grid", "checks", "quote", "coins", "name", "slam"], "serif_em": True},
    "neon": {      # s5Zf: green glow on near-black, 3D tilt, depth blur
        "caption": None, "cap_y": 0.5, "max_words": 4, "max_chars": 24, "push": 0, "punch": 0, "shake": False,
        "theme": {"accent": "#3dff8a", "grade": "none", "vignette": 0.6, "grain": 0.05},
        "graphics": ["stat", "coins", "pill", "quote", "slam"], "grounds": ["neon", "dark", "neon", "grid"],
        "tilt": True, "glow": True, "dof": True},
    "mono": {      # C-yZl: paper, monochrome, halftone grain, serif keyword
        "caption": None, "cap_y": 0.5, "max_words": 4, "max_chars": 24, "push": 0, "punch": 0, "shake": False,
        "theme": {"accent": "#111111", "grade": "mono", "vignette": 0.55, "grain": 0.1},
        "graphics": ["stat", "quote", "coins", "name", "slam", "checks"], "grounds": ["paper", "dark", "paper", "news"], "serif_em": True},
    "clean": {     # DLND: white explainer, dark type, green accents
        "caption": None, "cap_y": 0.5, "max_words": 4, "max_chars": 24, "push": 0, "punch": 0, "shake": False,
        "theme": {"accent": "#16a34a", "grade": "none", "vignette": 0.15, "grain": 0.0},
        "graphics": ["stat", "coins", "name", "chat", "pill", "grid", "checks"], "grounds": ["white", "white", "cream", "white"]},
}
