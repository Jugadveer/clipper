"""Voiceover: Microsoft Edge neural TTS (free) with exact word timings.

Long scripts are synthesized paragraph by paragraph (Edge drops very long requests) and joined;
every word keeps its absolute time. No voice cloning: a stock voice, never a real creator's.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

TICKS = 10_000_000          # edge-tts offsets are in 100 ns units
DEFAULT_VOICE = "en-US-AndrewMultilingualNeural"


async def _one(text: str, voice: str, rate: str, pitch: str, mp3: Path) -> list[dict]:
    import edge_tts
    comm = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch, boundary="WordBoundary")
    words = []
    with open(mp3, "wb") as f:
        async for ch in comm.stream():
            if ch["type"] == "audio":
                f.write(ch["data"])
            elif ch["type"] == "WordBoundary":
                s = ch["offset"] / TICKS
                words.append({"w": ch["text"], "s": round(s, 3), "e": round(s + ch["duration"] / TICKS, 3)})
    return words


def duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                          str(path)], capture_output=True, text=True).stdout.strip()
    return float(out or 0)


def synthesize(paragraphs: list[str], out_dir: Path, voice: str = DEFAULT_VOICE, rate: str = "+12%",
               pitch: str = "+0Hz", gap: float = 0.35) -> tuple[Path, list[dict], list[tuple[float, float]]]:
    """Returns (voiceover.wav, words with absolute times, per-paragraph (start, end))."""
    out_dir.mkdir(parents=True, exist_ok=True)
    cache = out_dir / "voice.json"
    wav = out_dir / "voiceover.wav"
    if cache.exists() and wav.exists():
        d = json.loads(cache.read_text(encoding="utf-8"))
        if (d.get("paragraphs") == paragraphs and d.get("voice") == voice and d.get("rate") == rate
                and d.get("join") == 2):            # join 1 lost the paragraph gaps; never reuse it
            return wav, d["words"], [tuple(x) for x in d["spans"]]
    async def all_paragraphs():
        sem = asyncio.Semaphore(4)                 # 4 requests in flight: ~4x faster, still polite

        async def one(i, p):
            async with sem:
                for attempt in range(3):
                    try:
                        return await _one(p, voice, rate, pitch, out_dir / f"p{i:03d}.mp3")
                    except Exception:
                        if attempt == 2:
                            raise
                        await asyncio.sleep(2 + 3 * attempt)
        return await asyncio.gather(*(one(i, p) for i, p in enumerate(paragraphs)))
    results = asyncio.run(all_paragraphs())
    # every piece is decoded to the SAME pcm format before joining. Joining mp3 parts with a wav
    # gap through the concat demuxer silently dropped every gap (mixed codecs), so the speech ran
    # 0.35 s earlier per paragraph than the word times said: 6.7 s by the end of a 10-minute video.
    parts, words, spans, t = [], [], [], 0.0
    pcm = ["-c:a", "pcm_s16le", "-ar", "48000", "-ac", "1"]
    for i, p in enumerate(paragraphs):
        mp3 = out_dir / f"p{i:03d}.mp3"
        part = out_dir / f"p{i:03d}.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(mp3), *pcm, str(part)], check=True)
        ws = results[i]
        d = duration(part)
        for w in ws:
            words.append({"w": w["w"], "s": round(w["s"] + t, 3), "e": round(w["e"] + t, 3), "p": i})
        spans.append((t, t + d))
        parts.append(part)
        t += d + gap
    lst = out_dir / "concat.txt"
    sil = out_dir / "gap.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
                    "-t", str(gap), *pcm, str(sil)], check=True)
    lines = []
    for p in parts:
        lines += [f"file '{p.name}'", f"file '{sil.name}'"]
    lst.write_text(chr(10).join(lines), encoding="utf-8")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst.name,
                    "-c", "copy", wav.name], check=True, cwd=str(out_dir))
    joined = duration(wav)
    if abs(joined - t) > 0.25:                      # the parts and gaps must add up to the file
        raise RuntimeError(f"voiceover join lost audio: {joined:.2f}s vs {t:.2f}s expected")
    for p in parts:
        p.unlink(missing_ok=True)
        p.with_suffix(".mp3").unlink(missing_ok=True)
    cache.write_text(json.dumps({"paragraphs": paragraphs, "voice": voice, "rate": rate, "join": 2,
                                 "words": words, "spans": spans}), encoding="utf-8")
    return wav, words, spans
