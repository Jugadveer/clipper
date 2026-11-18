"""Make a short with the engine.

  # voiceover-only motion graphics from a script (Edge TTS voice unless --voice <file> is given)
  py -m clipper.engine.make script script.txt --look kinetic -o out/short.mp4
  # edit footage (the user's own clip or assets) with captions + graphics
  py -m clipper.engine.make footage clip.mp4 --look speaker -o out/clip.mp4 [--words words.json --start s --end e]

Audio: voice + a synthesized music bed + one synthesized sound per visual event (no samples, no
licences), loudness-normalised to -14 LUFS. Cost: $0 locally, plus one flash-lite call (~$0.0005)
for the plan unless --no-llm.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np

from . import compose, plan
from .looks import LOOKS

SR = 48000


def _norm(s: str) -> str:
    return re.sub(r"[^\w']", "", s.lower())


def punctuate(tts_words: list[dict], script: str) -> list[dict]:
    """Give each spoken word its script token (with punctuation); TTS timings drop punctuation."""
    toks = script.split()
    out, j = [], 0
    for w in tts_words:
        k = _norm(w["w"])
        t = w["w"]
        for jj in range(j, min(j + 6, len(toks))):
            if _norm(toks[jj]) == k or (k and _norm(toks[jj]).startswith(k)):
                t, j = toks[jj], jj + 1
                break
        out.append({"t": t, "s": w["s"], "e": w["e"]})
    return out


def _sfx(edl: dict, dur: float, out: Path) -> Path:
    """One sound per visual event, using the shorts kit's synth (no bells, every hit varied)."""
    from .audio.sfx import Synth, SR as S, wavfile
    syn = Synth(sum(map(ord, out.name)))
    track = np.zeros(int((dur + 2) * S))

    def add(t, x):
        i = int(max(0, t) * S); j = min(len(track), i + len(x)); track[i:j] += x[: j - i]
    kind = {"whip": lambda: syn.whoosh(0.5), "zoom": lambda: syn.whoosh(0.6), "flash": lambda: syn.riser(0.5),
            "tear": syn.paper_flip, "iris": lambda: syn.swell(0.7), "glitch": syn.click}
    for tr in edl.get("trans", []):
        f = kind.get(tr["type"])
        if f:
            x = f(); add(tr["t"] - (len(x) / S) * (0.7 if tr["type"] in ("whip", "zoom", "flash") else 0.1), x)
        if tr["type"] == "flash":
            add(tr["t"], syn.thunk(0.8))
    for o in edl.get("overlays", []) + edl.get("scenes", []):
        t = o["s"]
        if o["type"] in ("stat",):
            add(t + 0.1, syn.flap_rattle(1.1))
        elif o["type"] in ("sticker", "pill", "coins", "chat", "grid", "lower"):
            add(t, syn.pop(0.9 + 0.2 * (hash(o["type"]) % 3) / 3))
        elif o["type"] in ("slam", "checks"):
            add(t + 0.12, syn.stamp())
        elif o["type"] == "kinetic":
            for w in o["p"].get("words", []):
                if w.get("em"):
                    add(w["s"], syn.thunk(1.1))
    for c in edl.get("clips", []):
        for p in c.get("punches", []):
            add(p - 0.05, syn.whoosh(0.35))
    wavfile.write(str(out), S, (np.clip(track, -1, 1) * 32767).astype(np.int16))
    return out


def _mix(voice: Path | None, dur: float, edl: dict, work: Path, music: float = 0.18, orig: Path | None = None) -> Path:
    from .audio import music as M
    bed = M.make(dur, work / "music.wav", seed=sum(map(ord, work.name)))
    fx = _sfx(edl, dur, work / "sfx.wav")
    ins, filt = [], []
    for p in [voice or orig, bed, fx]:
        ins += ["-i", str(p)]
    filt = (f"[1:a]volume={music}[m];[2:a]volume=0.55[s];[0:a][m][s]amix=inputs=3:duration=longest:normalize=0,"
            f"apad,atrim=0:{dur},loudnorm=I=-14:TP=-1.5:LRA=11,aresample={SR}")
    out = work / "mix.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", *ins, "-filter_complex", filt, "-ac", "2", str(out)], check=True)
    return out


def make_script(script_text: str, out_mp4: Path, look: str = "kinetic", voice: str = "en-US-BrianMultilingualNeural",
                rate: str = "+6%", llm: bool = True, voice_file: Path | None = None) -> Path:
    work = out_mp4.with_suffix("")
    work.mkdir(parents=True, exist_ok=True)
    from .audio import voice as V
    paras = [p.strip() for p in re.split(r"\n\s*\n", script_text.strip()) if p.strip()]
    wav, ws, spans = V.synthesize(paras, work / "voice", voice=voice, rate=rate)
    words = punctuate(ws, " ".join(paras))
    dur = round(spans[-1][1] + 0.8, 2)
    edl = plan.script(words, dur, look=look, llm=llm)
    mix = _mix(wav, dur, edl, work)
    proj = compose.build_project(edl, work / "project", {"mix": mix})
    (work / "edl.json").write_text(json.dumps(edl, indent=1, ensure_ascii=False), encoding="utf-8")
    return compose.render(proj, out_mp4)


def edit(base: Path, words: list[dict], out_mp4: Path, look: str = "speaker", title: str = "", llm: bool = True,
         fill: str = "", work: Path | None = None) -> Path:
    """The engine's footage edit: `base` is a vertical clip, `words` its transcript on the same
    timeline ([{t, s, e}]). Captions, graphics, punch-ins, flashes, grade and sound go on top."""
    work = Path(work or out_mp4.with_suffix(""))
    work.mkdir(parents=True, exist_ok=True)
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(base)],
                               capture_output=True, text=True).stdout.strip())
    words = [w for w in words if w["s"] < dur]
    edl = plan.footage(words, round(dur, 3), "clip", look=look, title=title, llm=llm, fill=fill)
    audio = work / "orig.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(base), "-vn", "-ac", "2", "-ar", str(SR), str(audio)], check=True)
    mix = _mix(None, round(dur, 3), edl, work, music=0.0, orig=audio)
    proj = compose.build_project(edl, work / "project", {"clip": base, "mix": mix})
    (work / "edl.json").write_text(json.dumps(edl, indent=1, ensure_ascii=False), encoding="utf-8")
    return compose.render(proj, out_mp4)


def make_footage(video: Path, out_mp4: Path, look: str = "speaker", words_json: Path | None = None,
                 start: float = 0.0, end: float | None = None, title: str = "", llm: bool = True, fill: str = "",
                 genre: str = "podcast") -> Path:
    """Any footage: a landscape source is reframed to vertical by the clipper (face tracking), a
    vertical one is used as it is; then edit()."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from clipper import render as R
    from clipper.common import ffprobe_json
    work = out_mp4.with_suffix("")
    work.mkdir(parents=True, exist_ok=True)
    info = ffprobe_json(video)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    sw, sh, dur = int(v["width"]), int(v["height"]), float(info["format"]["duration"])
    end = min(end if end is not None else dur, dur)
    if words_json is None:
        from clipper.transcribe import transcribe
        words_json = work / "words.json"
        if not words_json.exists():
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(video), "-ac", "1", "-ar", "16000", str(work / "a.wav")], check=True)
            transcribe(work / "a.wav", words_json)
    doc = json.loads(Path(words_json).read_text(encoding="utf-8"))
    segs = [(start, end)]
    if sw > sh:                                       # reframe to vertical with the clipper's face tracking
        base = work / "base.mp4"
        if not base.exists():
            R.render_base(video, sw, sh, segs, base, genre=genre)
        words = R.clip_words_out(doc, segs)
    else:
        base = video
        words = [{"t": w["w"], "s": round(w["s"] - start, 3), "e": round(w["e"] - start, 3)}
                 for seg in doc["segments"] for w in seg["words"] if start <= w["s"] < end]
    return edit(base, words, out_mp4, look=look, title=title, llm=llm, fill=fill, work=work)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="clipper.engine.make")
    sub = ap.add_subparsers(dest="mode", required=True)
    a = sub.add_parser("script"); a.add_argument("script"); a.add_argument("--look", default="kinetic", choices=list(LOOKS))
    a.add_argument("--voice", default="en-US-BrianMultilingualNeural"); a.add_argument("-o", required=True); a.add_argument("--no-llm", action="store_true")
    b = sub.add_parser("footage"); b.add_argument("video"); b.add_argument("--look", default="speaker", choices=list(LOOKS))
    b.add_argument("--words"); b.add_argument("--start", type=float, default=0.0); b.add_argument("--end", type=float)
    b.add_argument("--title", default=""); b.add_argument("--fill", default=""); b.add_argument("-o", required=True); b.add_argument("--no-llm", action="store_true")
    x = ap.parse_args(argv)
    if x.mode == "script":
        out = make_script(Path(x.script).read_text(encoding="utf-8"), Path(x.o), look=x.look, voice=x.voice, llm=not x.no_llm)
    else:
        out = make_footage(Path(x.video), Path(x.o), look=x.look, words_json=Path(x.words) if x.words else None,
                           start=x.start, end=x.end, title=x.title, llm=not x.no_llm, fill=x.fill)
    print(out)


if __name__ == "__main__":
    main()
