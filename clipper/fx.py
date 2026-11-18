"""Frame-level effects applied to the composed 1080x1920 frame, plus the end card and SFX.

Camera: slow push-in across the clip, punch-in zooms on the big beats (with a short shake and
an optional white flash on the biggest), jump-zooms on sentence changes (podcast style).
End card: the last frame freezes, zooms in, darkens and blurs; an emoji and the punchline in
quotes pop in; a synthesized bass hit lands on it. SFX are generated here, never sampled, so
there is nothing to license.
"""
from __future__ import annotations

import math
import wave
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .common import ASSETS, FONTS

EMOJI_FONT = Path("C:/Windows/Fonts/seguiemj.ttf")
SFX = ASSETS / "sfx"


# ---------------- camera ----------------

class Camera:
    def __init__(self, cam: dict, dur: float, punches: list[float], sentence_starts: list[float]):
        self.c, self.dur = cam, max(dur, 0.1)
        self.punches = punches if cam.get("punch") else []
        self.shake = cam.get("shake", 0)
        self.flash_t = punches[0] if (cam.get("flash") and punches) else None
        # jump zoom: alternate zoom levels on each new sentence, never two cuts within 2.5 s
        self.jumps: list[tuple[float, float]] = []
        if cam.get("jump_zoom"):
            lvl, last = 0, -9.0
            for t in sentence_starts:
                if t - last >= 2.5:
                    lvl ^= 1
                    last = t
                    self.jumps.append((t, 1 + cam["jump_zoom"] * lvl))

    def zoom(self, t: float) -> float:
        z = 1 + self.c.get("push", 0) * min(1.0, t / self.dur)
        for p in self.punches:
            d = t - p
            if -0.05 <= d <= 0.9:
                a = min(1.0, (d + 0.05) / 0.1) if d < 0.05 else max(0.0, 1 - (d - 0.05) / 0.85) ** 2
                z = max(z, 1 + self.c["punch"] * a)
        for t0, lvl in reversed(self.jumps):
            if t >= t0:
                z *= lvl
                break
        return z

    def offset(self, t: float) -> tuple[float, float]:
        if not self.shake:
            return 0.0, 0.0
        dx = dy = 0.0
        for p in self.punches:
            d = t - p
            if 0 <= d <= 0.35:
                a = self.shake * (1 - d / 0.35)
                dx += a * math.sin(d * 90)
                dy += a * math.cos(d * 70)
        return dx, dy

    def apply(self, frame: np.ndarray, t: float, out: np.ndarray) -> np.ndarray:
        z = self.zoom(t)
        dx, dy = self.offset(t)
        if z > 1.001 or dx or dy:
            H, W = frame.shape[:2]
            m = np.array([[z, 0, (1 - z) * W / 2 + dx], [0, z, (1 - z) * H / 2 + dy]], np.float32)
            cv2.warpAffine(frame, m, (W, H), dst=out, flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
        else:
            out[:] = frame
        if self.flash_t is not None and 0 <= t - self.flash_t < 0.15:
            a = 0.55 * (1 - (t - self.flash_t) / 0.15)
            cv2.addWeighted(out, 1 - a, np.full_like(out, 255), a, 0, dst=out)
        return out


# ---------------- end card ----------------

@lru_cache(8)
def _font(name: str, px: int):
    return ImageFont.truetype(str(FONTS / name), px)


def end_card_overlay(caption: str, emoji: str, w: int = 1080, h: int = 1920) -> np.ndarray:
    """RGBA overlay: big emoji + "PUNCHLINE" with a glow, centred."""
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cy = int(h * 0.47)
    if emoji and EMOJI_FONT.exists():
        try:
            ef = ImageFont.truetype(str(EMOJI_FONT), 190)
            ew = d.textlength(emoji, font=ef, embedded_color=True)
            d.text(((w - ew) / 2, cy - 330), emoji, font=ef, embedded_color=True)
        except Exception:
            pass
    text = f"\u201C{caption.strip().upper()}\u201D" if caption else ""
    if text:
        f = _font("Montserrat-ExtraBoldItalic.ttf", 64)
        lines, cur = [], ""
        for word in text.split():                     # wrap to ~900 px
            test = (cur + " " + word).strip()
            if d.textlength(test, font=f) > 900 and cur:
                lines.append(cur)
                cur = word
            else:
                cur = test
        lines.append(cur)
        glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow)
        y = cy - 10
        for ln in lines:
            lw = d.textlength(ln, font=f)
            gd.text(((w - lw) / 2, y), ln, font=f, fill=(0, 230, 120, 255), stroke_width=10,
                    stroke_fill=(0, 230, 120, 255))
            y += 84
        glow = glow.filter(ImageFilter.GaussianBlur(12))
        img = Image.alpha_composite(glow, img)
        d = ImageDraw.Draw(img)
        y = cy - 10
        for ln in lines:
            lw = d.textlength(ln, font=f)
            d.text(((w - lw) / 2, y), ln, font=f, fill=(255, 255, 255, 255), stroke_width=3,
                   stroke_fill=(0, 0, 0, 255))
            y += 84
    return cv2.cvtColor(np.array(img), cv2.COLOR_RGBA2BGRA)


def end_card_frames(last: np.ndarray, overlay: np.ndarray, n: int):
    """Yield n frames: the last frame zooming in, darkening and blurring, overlay popping in."""
    H, W = last.shape[:2]
    alpha = overlay[:, :, 3:4].astype(np.float32) / 255.0
    ov = overlay[:, :, :3].astype(np.float32)
    for i in range(n):
        p = i / max(1, n - 1)
        e = 1 - (1 - p) ** 3
        z = 1 + 0.12 * e
        m = np.array([[z, 0, (1 - z) * W / 2], [0, z, (1 - z) * H / 2]], np.float32)
        f = cv2.warpAffine(last, m, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
        k = 1 + 2 * int(6 * min(1, p * 3))
        if k > 1:
            f = cv2.GaussianBlur(f, (k, k), 0)
        f = (f.astype(np.float32) * (1 - 0.5 * min(1, p * 4)))
        pop = min(1.0, p * 6)                         # overlay pops in over the first ~0.2 s
        s = 0.7 + 0.3 * (1 - (1 - pop) ** 2) if pop < 1 else 1.0
        if s < 1:
            mm = np.array([[s, 0, (1 - s) * W / 2], [0, s, (1 - s) * H / 2]], np.float32)
            a = cv2.warpAffine(alpha, mm, (W, H))[:, :, None] * pop
            o = cv2.warpAffine(ov, mm, (W, H))
        else:
            a, o = alpha, ov
        yield (f * (1 - a) + o * a).clip(0, 255).astype(np.uint8)


# ---------------- sound effects (synthesized) ----------------

def _write_wav(path: Path, x: np.ndarray, sr: int = 48000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = (np.clip(x, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return path


def boom(sr: int = 48000) -> Path:
    """A deep bass hit (pitch drop 110 -> 40 Hz, exponential decay, a click transient)."""
    p = SFX / "boom.wav"
    if p.exists():
        return p
    t = np.arange(int(sr * 1.4)) / sr
    f = 40 + 70 * np.exp(-t * 9)
    ph = 2 * np.pi * np.cumsum(f) / sr
    x = np.sin(ph) * np.exp(-t * 2.6)
    x += 0.35 * np.sin(2 * ph) * np.exp(-t * 6)
    click = np.random.default_rng(1).normal(0, 1, len(t)) * np.exp(-t * 90) * 0.5
    x = np.tanh(1.8 * (x + click)) * 0.85
    return _write_wav(p, x, sr)


def whoosh(sr: int = 48000) -> Path:
    """Band-swept noise for a cold-open -> clip transition."""
    p = SFX / "whoosh.wav"
    if p.exists():
        return p
    n = int(sr * 0.5)
    noise = np.random.default_rng(2).normal(0, 1, n)
    t = np.arange(n) / n
    env = np.sin(np.pi * t) ** 2
    spec = np.fft.rfft(noise)
    freqs = np.fft.rfftfreq(n, 1 / sr)
    spec *= np.exp(-((freqs - 1800) / 1500) ** 2)
    x = np.fft.irfft(spec, n) * env
    x /= np.abs(x).max() + 1e-9
    return _write_wav(p, x * 0.6, sr)
