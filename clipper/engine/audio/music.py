"""Original synthwave music bed, synthesized with numpy (no samples, nothing to license).

A minor-key 4-chord loop at ~100 BPM: detuned saw pads, an 8th-note bass, a 16th-note arp,
kick/snare/hats. Each video gets a seed (key, progression, tempo vary), so channels don't
reuse one identical bed. It sits ~20 dB under the voice and is side-chain ducked in the mix.
"""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

SR = 44100
PROGS = [[0, -4, 3, -2], [0, 3, -2, -4], [0, -2, -4, -5], [0, -4, -7, -5]]   # semitone roots (minor-ish)


def _saw(f, t):
    return 2 * ((t * f) % 1.0) - 1


def _lp_fast(x, cutoff):
    from scipy.signal import lfilter
    a = np.exp(-2 * np.pi * cutoff / SR)
    return lfilter([1 - a], [1, -a], x)


def make(seconds: float, out: Path, seed: int = 0) -> Path:
    rng = np.random.default_rng(seed)
    bpm = float(rng.choice([96, 100, 104, 108]))
    root = 220.0 * 2 ** (int(rng.integers(-3, 3)) / 12)        # around A3
    prog = PROGS[int(rng.integers(len(PROGS)))]
    beat = 60 / bpm
    bar = 4 * beat
    loop_bars = 8
    n = int(SR * bar * loop_bars)
    t = np.arange(n) / SR
    pad = np.zeros(n)
    bass = np.zeros(n)
    arp = np.zeros(n)
    for b in range(loop_bars):
        r = root * 2 ** (prog[b % 4] / 12)
        s0, s1 = int(b * bar * SR), int((b + 1) * bar * SR)
        tt = t[s0:s1] - t[s0]
        env = np.minimum(1, tt / 0.4) * np.minimum(1, (bar - tt) / 0.3)
        chord = [r, r * 2 ** (3 / 12), r * 2 ** (7 / 12)]
        for f in chord:
            for det in (-0.12, 0.0, 0.12):
                pad[s0:s1] += _saw(f * 2 ** (det / 12), tt) * env * 0.06
        for k in range(8):                                     # 8th-note bass
            a = s0 + int(k * beat / 2 * SR)
            m = min(s1, a + int(beat / 2 * SR))
            ts = t[a:m] - t[a]
            bass[a:m] += _saw(r / 2, ts) * np.exp(-ts * 6) * 0.35
        notes = chord + [r * 2]
        for k in range(16):                                    # 16th arp
            a = s0 + int(k * beat / 4 * SR)
            m = min(s1, a + int(beat / 4 * SR))
            ts = t[a:m] - t[a]
            f = notes[k % 4] * 2
            arp[a:m] += np.sign(np.sin(2 * np.pi * f * ts)) * np.exp(-ts * 14) * 0.05
    pad = _lp_fast(pad, 1800)
    bass = _lp_fast(bass, 380)
    arp = _lp_fast(arp, 3200)
    drums = np.zeros(n)
    for k in range(loop_bars * 4):
        a = int(k * beat * SR)
        ts = np.arange(int(0.35 * SR)) / SR
        m = min(n, a + len(ts))
        if k % 2 == 0:                                         # kick on 1 and 3
            f = 50 + 90 * np.exp(-ts * 30)
            drums[a:m] += (np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-ts * 9))[:m - a] * 0.7
        else:                                                  # snare on 2 and 4
            nz = rng.normal(0, 1, len(ts)) * np.exp(-ts * 18) * 0.35
            drums[a:m] += (nz + np.sin(2 * np.pi * 190 * ts) * np.exp(-ts * 25) * 0.3)[:m - a]
        for h in (0, 0.5):                                     # hats on 8ths
            ha = a + int(h * beat * SR)
            hl = int(0.05 * SR)
            hm = min(n, ha + hl)
            hz = rng.normal(0, 1, hl) * np.exp(-np.arange(hl) / SR * 80) * 0.08
            drums[ha:hm] += (hz - _lp_fast(hz, 6000))[:hm - ha]
    # simple delay on the arp for space
    dly = int(beat * 0.75 * SR)
    arp[dly:] += arp[:-dly] * 0.35
    loop = pad + bass + arp + drums
    loop /= np.abs(loop).max() + 1e-9
    reps = int(np.ceil(seconds * SR / n))
    full = np.tile(loop, reps)[: int(seconds * SR)]
    fade = int(3 * SR)
    full[:fade] *= np.linspace(0, 1, fade)
    full[-fade:] *= np.linspace(1, 0, fade)
    st = np.stack([full, np.roll(full, 220)], axis=1) * 0.8   # a touch of width
    out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((st * 32767).astype(np.int16).tobytes())
    return out


def sfx_track(times: list[float], seconds: float, out: Path) -> Path:
    """One wav holding a whoosh (band-swept noise, 0.5 s) at each time: chapter-card transitions."""
    rng = np.random.default_rng(7)
    n = int(0.55 * SR)
    noise = rng.normal(0, 1, n)
    env = np.sin(np.pi * np.arange(n) / n) ** 2
    spec = np.fft.rfft(noise)
    fr = np.fft.rfftfreq(n, 1 / SR)
    spec *= np.exp(-((fr - 1600) / 1400) ** 2)
    wh = np.fft.irfft(spec, n) * env
    wh /= np.abs(wh).max() + 1e-9
    track = np.zeros(int(seconds * SR) + n)
    for t in times:
        a = int(max(0, t - 0.25) * SR)
        track[a:a + n] += wh * 0.5
    st = np.stack([track, track], axis=1)
    out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(st, -1, 1) * 32767).astype(np.int16).tobytes())
    return out
