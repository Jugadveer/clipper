"""Word-level transcript with faster-whisper (local GPU, free).

Output `words.json`: {"language", "segments": [{start, end, text, words: [{w, s, e, p}]}]}.
Streams are loud and full of slang, so the default model is large-v3-turbo; on a 6 GB card it
runs in float16 at roughly 15-25x real time with the batched pipeline.
"""
from __future__ import annotations

from pathlib import Path

from .common import MODELS, log, write_json


def load(model: str = "turbo"):
    from faster_whisper import BatchedInferencePipeline, WhisperModel
    import torch

    device = "cuda" if torch.cuda.is_available() else "cpu"
    ctype = "float16" if device == "cuda" else "int8"
    local = MODELS / f"whisper-large-v3-{model}" if model == "turbo" else MODELS / f"whisper-{model}"
    src = str(local) if (local / "model.bin").exists() else model   # bundled copy, no HF download
    log(f"whisper {model} on {device}/{ctype} ({'local' if src != model else 'hub'})")
    return BatchedInferencePipeline(WhisperModel(src, device=device, compute_type=ctype))


def transcribe(audio: Path, out: Path, model: str = "turbo", language: str | None = None,
               batch_size: int = 8, pipe=None) -> dict:
    pipe = pipe or load(model)
    segs, info = pipe.transcribe(str(audio), batch_size=batch_size, word_timestamps=True,
                                 vad_filter=True, language=language,
                                 condition_on_previous_text=False)
    out_segs = []
    for s in segs:
        words = [{"w": w.word.strip(), "s": round(w.start, 3), "e": round(w.end, 3),
                  "p": round(w.probability, 3)} for w in (s.words or []) if w.word.strip()]
        if not words:
            continue
        out_segs.append({"start": round(s.start, 3), "end": round(s.end, 3),
                         "text": s.text.strip(), "words": words})
        if len(out_segs) % 200 == 0:
            log(f"  transcribed to {s.end/60:.1f} min")
    doc = {"language": info.language, "model": model, "segments": out_segs}
    write_json(out, doc)
    log(f"transcript: {len(out_segs)} segments, {sum(len(s['words']) for s in out_segs)} words")
    return doc


def serve(model: str = "turbo") -> None:
    """Keep one model loaded and transcribe "in.wav<TAB>out.json" lines from stdin, answering
    "ok <out>" or "err <message>" per line (the live clipper sends a slice every couple of minutes,
    and loading the model each time would cost more than transcribing the slice)."""
    import sys
    pipe = load(model)
    print("ready", flush=True)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            a, b = line.split(chr(9))
            transcribe(Path(a), Path(b), model, pipe=pipe)
            print(f"ok {b}", flush=True)
        except Exception as e:                       # one bad slice never kills the worker
            print(f"err {e}", flush=True)


if __name__ == "__main__":
    import sys
    if sys.argv[1:2] == ["--serve"]:
        serve(*(sys.argv[2:3] or []))
    else:
        transcribe(Path(sys.argv[1]), Path(sys.argv[2]), *(sys.argv[3:4] or []))
