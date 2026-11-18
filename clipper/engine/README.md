# clipper/engine: the shorts editing engine

One engine for both jobs:

- **footage**: edits a clip (the clipper's picks, or any video you give it) with captions, cards, punch-ins, flashes, a grade and sound.
- **script**: turns a script into a voiced motion-graphics short. Edge TTS reads it unless you give a voice file, and the type, grounds, cards and transitions change about once a second.

Rendering is HyperFrames (`npx hyperframes@0.8.43`). The page is redrawn as a pure function of time, so a render matches its preview frame for frame.

## Commands

```
# script + voiceover -> motion graphics
py -m clipper.engine.make script script.txt --look kinetic -o out/short.mp4

# edit footage; a landscape source is reframed to vertical with the clipper's face tracking first
py -m clipper.engine.make footage clip.mp4 --look speaker --start 1.4 --end 39.6 \
    --title "what the clip is" -o out/clip.mp4 [--words words.json] [--no-llm]

# inside the clipper: the engine looks are clipper styles
py -m clipper run <url> --style speaker|ui|bigword|tiered|story|mix
```

`--style mix` now rotates the engine looks in with the old presets: podcast gets hormozi, speaker, ui, clean, tiered, bigword, story; stream gets viral, bigword, pop, speaker, karaoke.

Use the venv (`D:/BOT/.venv/Scripts/python.exe`). It has soundfile, emoji and the clipper's other dependencies.

## Cost

One `gemini-2.5-flash-lite` call per short plans it. A 35-second script short measured 2,065 tokens in and 563 out, which is **$0.00043** through OpenRouter. Everything else runs locally for free: the voice (Edge TTS), the music bed and every sound effect (synthesized), and the render. `--no-llm` plans with rules only.

## Looks (`looks.py`)

| look | mode | from the study | what it does |
|---|---|---|---|
| speaker | footage | -rumnXioCL8 | each speaker has a caption colour; glow, punch-ins, shakes, flashes; sticker, pill, stat and slam cards |
| ui | footage | ZAK | small clean captions; glass cards that restate what is said (chat, pill, grid, checks, stat, quote) |
| bigword | footage | Z-gCH8zplBw | one huge word at a time under a header band |
| tiered | footage | C-yZl, MoVlt | small lead line and one huge keyword; faded grade, grain |
| karaoke | footage | the old baseline | uppercase, current word yellow |
| story | both | DfpnDOvh (Instagram) | one word at a time with key nouns on a blue box; a top tag holds the year (rolls on a time jump), money (counts up through BILLION to TRILLION) or a day; in script mode an Apple emoji hero object stands on a cinematic set per sentence, with whip and zoom between scenes |
| kinetic | script | s5Zf, C-yZl | ground changes every beat, keyword huge in a mixed serif, Apple emoji hero objects |
| neon | script | s5Zf | green glow on near-black, 3D tilt, depth blur |
| mono | script | C-yZl | paper, monochrome, halftone grain, serif keyword |
| clean | script | DLND | white explainer, dark type, green accents |

`research/SHORTS_STUDY.md` has the measurements behind each look, such as shot length, caption sizes and card timing.

## The plan (`plan.py`)

The model returns indices into the transcript and template props. It never returns times or layout. Each answer is checked before it is used:

- `valid()` drops malformed props and never repairs them by guessing. A pill needs a `[bracketed]` word; a chat needs a real question and answer; a stat needs a number.
- Every word on a stat card (label and the lead line above the number) is a word spoken near it (`_spoken`; "5" matches "five"). The model wrote "pieces General Advice" and "Top 10"; the card now reads "five to / 10 / pieces of general advice".
- The big word must be a content word (`words.content`), never "what" or "so".
- A slam word must be spoken, and a quote must overlap the speech by 60% or more.
- Cards are spaced at least 1.2 s apart. Full-screen cards hide the captions under them.
- A footage stat sits on a plate below the face, not over it.
- A slam keyword is a content word, never a number word.
- A card gets a beat long enough to build and be read (`MIN_CARD_S`: chat 2.2 s, grid 2 s, coins 1.6 s), otherwise the beat stays type.
- Years come from the words (`words.spoken_years`), never from the model; a phrase never crosses punctuation.
- A top tag is kept only when the words near it state a number or a year (or, for a text tag, say its words).

## EDL (one dict, written as `edl.json` next to each render)

```
w, h, dur            1080, 1920, seconds
theme                {accent, grade: warm|mono|punchy|cool|fade|none, vignette, grain}
clips                [{id, src, s, e, in, push, punch, punches[], shakes[], fx, fy, fill?}]   footage only
grounds              [{s, e, type: paper|cream|white|dark|grid|blue|neon|red|sunset|news|stage|night|money|wine}]    script only
scenes / overlays    [{s, e, type, p}]  type is a template in web/runtime.js TPL:
                     header rank chat pill grid checks object sticker name stat slam lower coins quote image kinetic tag
captions             {style: speaker|karaoke|bigword|neon|tiered|clean|boxed, y, groups: [{s, e, spk, words:[{t,s,e,em}]}]}
trans                [{t, type: flash|whip|zoom|iris|tear|glitch}]
camera               {push, drift}       acid: hue cycling      audio: key of the premixed track
```

## Sound (`make.py`)

Each visual event gets one synthesized sound, from the shorts kit (`D:\BOT\gta\shorts3d\sfx.py`):

- whip and zoom: whoosh
- flash: riser and a thunk
- tear: paper flip
- stat: flap rattle
- cards: pop
- slam: stamp
- kinetic keyword: thunk
- punch-in: short whoosh

The music bed comes from `gtavid.music`. Voice, bed and effects are mixed into one track at -14 LUFS.

## Emoji (`emoji.py`)

Emoji are Apple's, as the user asked:

- First choice: 160 px PNGs from Emojipedia's CDN, cached in `assets/emoji/cache/`.
- Fallback: the 96 px strike of `assets/emoji/AppleColorEmoji-Linux.ttf`.

Apple's emoji art is not openly licensed. Shorts use it all the time, but it is Apple's artwork.

## Traps

- **Spec and runtime must be in one generated `main.js`.** A separate inline `window.__SPEC = …` worked in snapshots, but in renders it ran after the runtime, which drew nothing (black video).
- A `filter: blur()` anywhere switches HyperFrames from fast capture to screenshot capture. This is slower but correct.
- Renders are serialized (`ENGINE_RENDERS`, default 1). HyperFrames already uses every core, and the clipper renders clips in parallel threads.
- Speed: a script short renders at about 3.3× its length. A 38 s footage edit takes about 2.5 min, reframe included.
