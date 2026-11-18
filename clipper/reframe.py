"""Where to point a vertical frame: face tracking + layout choice for one clip.

Layouts (output 1080x1920):
  split    gameplay on top (60%), streamer below (40%): for gaming streams with a webcam box
           OR a keyed cut-out (IShowSpeed). Measured on viral gaming shorts (12M views): game
           on top, streamer at the bottom. `split_order="cam_top"` flips it.
  single   one 9:16 crop that follows the face (IRL / just chatting / one-camera podcast)
  speaker  two people in one wide shot (podcast): hard cuts to whoever is talking, decided by
           mouth movement inside each face box, at least 2 s per shot
  stack    two people stacked top/bottom, both always visible
  fit      no face: the whole frame across the width over a blurred copy

Tracking: YuNet face detection at 4 fps, hold-on-loss, reset at hard cuts, dead-zone camera
with eased follow. Zooms/shake/flash are applied afterwards to the composed frame (fx.Camera).
"""
from __future__ import annotations

import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from .common import FFMPEG, MODELS

OUT_W, OUT_H = 1080, 1920
SAMPLE_FPS = 4
DET_W = 960


@dataclass
class Plan:
    layout: str
    src_w: int
    src_h: int
    times: np.ndarray
    cx: np.ndarray
    cy: np.ndarray
    face_h: float
    game_cx: float = 0.0
    cam_h_out: int = 768                    # split: streamer panel height (40%)
    split_order: str = "game_top"
    people: list = field(default_factory=list)   # [(cx, cy, face_h)] for speaker/stack
    ch: np.ndarray | None = None            # single: crop height per sample (per-shot zoom)

    @property
    def seam_y(self) -> int:
        if self.layout == "split":
            return OUT_H - self.cam_h_out if self.split_order == "game_top" else self.cam_h_out
        if self.layout == "stack":
            return OUT_H // 2
        return int(OUT_H * 0.55)

    def crop_h(self, t: float) -> float:
        if self.ch is None:
            return float(self.src_h)
        i = min(len(self.times) - 1, max(0, int(round(t * SAMPLE_FPS))))
        return float(self.ch[i])

    def at(self, t: float) -> tuple[float, float]:
        if self.layout == "speaker":           # hard cuts, no interpolation between people
            i = min(len(self.times) - 1, max(0, int(np.searchsorted(self.times, t, side="right")) - 1))
            return float(self.cx[i]), float(self.cy[i])
        return float(np.interp(t, self.times, self.cx)), float(np.interp(t, self.times, self.cy))

    def to_dict(self):
        return {"layout": self.layout, "face_h": round(self.face_h, 1), "game_cx": round(self.game_cx, 1),
                "split_order": self.split_order, "people": self.people,
                "track": [[round(float(t), 2), round(float(x)), round(float(y))]
                          for t, x, y in zip(self.times[::4], self.cx[::4], self.cy[::4])]}


_TLS = threading.local()


def _detector(w: int, h: int):
    """One detector per thread: a cv2 FaceDetectorYN is not safe to share (clips render in parallel)."""
    det = getattr(_TLS, "det", None)
    if det is None:
        det = cv2.FaceDetectorYN.create(str(MODELS / "face_detection_yunet_2023mar.onnx"), "",
                                        (w, h), score_threshold=0.6, nms_threshold=0.3, top_k=20)
        _TLS.det = det
    det.setInputSize((w, h))
    return det


def sample_frames(video: Path, start: float, dur: float, w: int, h: int):
    cmd = [FFMPEG, "-v", "error", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(video),
           "-vf", f"fps={SAMPLE_FPS},scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    sz = w * h * 3
    while True:
        buf = p.stdout.read(sz)
        if len(buf) < sz:
            break
        yield np.frombuffer(buf, np.uint8).reshape(h, w, 3)
    p.wait()


def _camera(raw: np.ndarray, valid: np.ndarray, cuts: set[int], dead: float, follow: float) -> np.ndarray:
    """Hold-on-loss + dead zone + eased follow; snaps at hard cuts."""
    n = len(raw)
    if not valid.any():
        return np.full(n, np.nan)
    held = raw.copy()
    last = raw[int(np.argmax(valid))]
    for i in range(n):
        if valid[i]:
            last = raw[i]
        held[i] = last
    k = 5
    pad = np.pad(held, (k // 2, k // 2), mode="edge")
    med = np.array([np.median(pad[i:i + k]) for i in range(n)])
    cam = np.empty(n)
    pos = med[0]
    for i in range(n):
        if i in cuts:
            pos = med[i]
        err = med[i] - pos
        if abs(err) > dead:
            pos += follow * (err - np.sign(err) * dead)
        cam[i] = pos
    ker = np.array([1, 2, 3, 2, 1], float)
    ker /= ker.sum()
    return np.convolve(np.pad(cam, (2, 2), mode="edge"), ker, mode="valid")


def _two_people(samples: list[list], src_w: float) -> tuple | None:
    """If the shot holds two people side by side most of the time, return their x split."""
    xs = [f[0] for fs in samples for f in fs]
    if len(xs) < 8:
        return None
    xs = np.sort(np.array(xs))
    gaps = np.diff(xs)
    i = int(np.argmax(gaps))
    if gaps[i] < 0.18 * src_w:
        return None
    split = (xs[i] + xs[i + 1]) / 2
    both = sum(1 for fs in samples if any(f[0] < split for f in fs) and any(f[0] >= split for f in fs))
    return split if both >= 0.4 * len(samples) else None


def plan_clip(video: Path, start: float, dur: float, src_w: int, src_h: int,
              layout: str = "auto", split_order: str = "game_top", genre: str = "stream") -> Plan:
    # small faces in a podcast's wide shot (~2% of the height) vanish at 960 px
    dw = min(src_w, 1920 if genre == "podcast" else 1280)
    dh = int(round(src_h * dw / src_w / 2) * 2)
    det = _detector(dw, dh)
    scale = src_w / dw
    samples: list[list] = []                 # per sample: [(cx, cy, h, mouth_motion)]
    prev_g, prev_full, cuts = None, None, set()
    for i, f in enumerate(sample_frames(video, start, dur, dw, dh)):
        grey = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
        g = cv2.resize(grey, (64, 36)).astype(np.int16)
        if prev_g is not None and np.abs(g - prev_g).mean() > 22:
            cuts.add(i)
        _, faces = det.detect(f)
        cur = []
        for r in (faces if faces is not None else []):
            x, y, w, h = r[:4]
            mouth = 0.0
            if prev_full is not None and i not in cuts:
                y0, y1 = int(y + 0.62 * h), int(y + 1.0 * h)
                x0, x1 = int(x + 0.2 * w), int(x + 0.8 * w)
                if y1 > y0 + 2 and x1 > x0 + 2 and y0 >= 0 and x0 >= 0:
                    a = grey[y0:y1, x0:x1].astype(np.int16)
                    b = prev_full[y0:y1, x0:x1].astype(np.int16)
                    if a.shape == b.shape and a.size:
                        mouth = float(np.abs(a - b).mean())
            cur.append(((x + w / 2) * scale, (y + h / 2) * scale, h * scale, mouth, r[-1]))
        # a jump in face size between samples is a camera cut even when the frames look alike
        # (dark wide shot -> dark close-up)
        if samples and cur and samples[-1]:
            h_now = max(r[2] for r in cur)
            h_prev = max(r[2] for r in samples[-1])
            if max(h_now, h_prev) > 1.8 * min(h_now, h_prev):
                cuts.add(i)
        samples.append(cur)
        prev_g, prev_full = g, grey
    n = len(samples)
    times = np.arange(max(n, 1)) / SAMPLE_FPS
    best = [max(fs, key=lambda r: r[2] * r[2] * (0.5 + r[4])) if fs else None for fs in samples]
    ok = np.array([b is not None for b in best], bool)
    xs = np.array([b[0] if b else np.nan for b in best], float)
    ys = np.array([b[1] if b else np.nan for b in best], float)
    found = ok.mean() if n else 0.0
    face_h = float(np.nanmedian([b[2] for b in best if b])) if ok.any() else 0.0

    two = _two_people(samples, src_w) if (genre == "podcast" or layout in ("speaker", "stack")) else None
    if layout == "auto":
        if found < 0.12:
            layout = "fit"
        elif two is not None:
            layout = "speaker"
        else:
            rel = face_h / src_h
            spread = float(np.nanstd(xs)) / src_w if ok.sum() > 2 else 0.0
            if genre == "podcast":
                layout = "single"
            else:
                layout = "single" if (rel > 0.24 or spread > 0.12) else "split"
    if layout in ("speaker", "stack") and two is None:
        layout = "single"

    if layout == "fit" or not ok.any():
        return Plan("fit", src_w, src_h, times, np.full(len(times), src_w / 2),
                    np.full(len(times), src_h / 2), face_h, split_order=split_order)

    if layout in ("speaker", "stack"):
        sides = [[f for f in fs if f[0] < two] for fs in samples], [[f for f in fs if f[0] >= two] for fs in samples]
        people, act = [], []
        for side in sides:
            pts = [max(fs, key=lambda r: r[2]) for fs in side if fs]
            people.append((float(np.median([p[0] for p in pts])), float(np.median([p[1] for p in pts])),
                           float(np.median([p[2] for p in pts]))))
            act.append(np.array([max((f[3] for f in fs), default=0.0) for fs in side]))
        plan = Plan(layout, src_w, src_h, times, np.zeros(n), np.zeros(n), face_h,
                    split_order=split_order, people=people)
        if layout == "speaker":
            k = 6                                           # 1.5 s activity window
            sm = [np.convolve(a, np.ones(k) / k, mode="same") for a in act]
            cur, since = int(sm[1][0] > sm[0][0]), 0.0
            for i in range(n):
                other = 1 - cur
                if since >= 2.0 and sm[other][i] > 1.3 * sm[cur][i] + 0.4:
                    cur, since = other, 0.0
                plan.cx[i], plan.cy[i] = people[cur][0], people[cur][1]
                since += 1 / SAMPLE_FPS
            plan.face_h = float(np.mean([p[2] for p in people]))
        return plan

    dead_x = 0.06 * src_w if layout == "single" else 0.03 * src_w
    cx = _camera(xs, ok, cuts, dead_x, 0.18)
    cy = _camera(ys, ok, cuts, 0.05 * src_h, 0.18)
    # a shot in which no face was ever found must not inherit the previous shot's position
    bounds = sorted({0, n, *cuts})
    for a0, b0 in zip(bounds[:-1], bounds[1:]):
        if not ok[a0:b0].any():
            cx[a0:b0], cy[a0:b0] = src_w / 2, src_h / 2
    plan = Plan(layout, src_w, src_h, times, cx, cy, face_h, split_order=split_order)
    if layout == "single":
        # per-shot zoom: a multi-camera podcast cuts between close-ups and a wide shot; one
        # full-height crop leaves the people tiny in the wide. Inside each shot (between hard
        # cuts) the crop height follows that shot's median face size.
        mult, floor = (4.6, 0.42) if genre == "podcast" else (5.5, 0.62)
        hs = np.array([b[2] if b else np.nan for b in best], float)
        bounds = sorted({0, n, *cuts})
        ch = np.full(n, float(src_h))
        for a0, b0 in zip(bounds[:-1], bounds[1:]):
            seg = hs[a0:b0]
            if np.isfinite(seg).any():
                ch[a0:b0] = float(np.clip(np.nanmedian(seg) * mult, floor * src_h, src_h))
        plan.ch = ch
    if layout == "split":
        # gameplay crop: full height, near the centre, keeping the streamer out of it
        gw = src_h * OUT_W / (OUT_H - plan.cam_h_out)
        fx = float(np.nanmedian(xs))
        half_face = face_h * 1.2
        best_c, best_cost = src_w / 2, 1e9
        for c in np.linspace(gw / 2, src_w - gw / 2, 41):
            overlap = max(0.0, min(c + gw / 2, fx + half_face) - max(c - gw / 2, fx - half_face))
            cost = overlap / (2 * half_face) + abs(c - src_w / 2) / src_w * 1.6
            if cost < best_cost:
                best_c, best_cost = c, cost
        plan.game_cx = float(best_c)
    return plan


# ---------- per-frame composition (source BGR frame -> 1080x1920 BGR) ----------

def _crop(frame, cx, cy, cw, ch):
    H, W = frame.shape[:2]
    cw, ch = min(cw, W), min(ch, H)
    x0 = int(round(min(max(cx - cw / 2, 0), W - cw)))
    y0 = int(round(min(max(cy - ch / 2, 0), H - ch)))
    return frame[y0:y0 + int(ch), x0:x0 + int(cw)]


def _face_panel(frame, cx, cy, face_h, out_w, out_h, min_frac=0.30, mult=2.6):
    H, W = frame.shape[:2]
    ch = max(face_h * mult, H * min_frac)
    cw = ch * out_w / out_h
    if cw > W:
        cw, ch = W, W * out_h / out_w
    return cv2.resize(_crop(frame, cx, cy + face_h * 0.25, cw, ch), (out_w, out_h), interpolation=cv2.INTER_LINEAR)


def compose(frame: np.ndarray, plan: Plan, t: float, out=None) -> np.ndarray:
    if out is None:
        out = np.zeros((OUT_H, OUT_W, 3), np.uint8)
    H, W = frame.shape[:2]
    if plan.layout in ("single", "speaker"):
        cx, cy = plan.at(t)
        if plan.layout == "speaker":
            # a closer crop on the speaker than a full-height strip
            out[:] = _face_panel(frame, cx, cy, plan.face_h, OUT_W, OUT_H, min_frac=0.55, mult=4.2)
            return out
        ch = plan.crop_h(t)
        cw = ch * OUT_W / OUT_H
        # face in the upper third of a zoomed crop; a full-height crop is just centred
        y = H / 2 if ch >= H - 1 else cy + ch * 0.14
        cv2.resize(_crop(frame, cx, y, cw, ch), (OUT_W, OUT_H), dst=out, interpolation=cv2.INTER_LINEAR)
        return out
    if plan.layout == "stack":
        half = OUT_H // 2
        for k, p in enumerate(plan.people[:2]):
            out[k * half:(k + 1) * half] = _face_panel(frame, p[0], p[1], p[2], OUT_W, half, 0.4, 3.2)
        out[half - 3:half + 3] = 0
        return out
    if plan.layout == "split":
        hc = plan.cam_h_out
        gh = OUT_H - hc
        cx, cy = plan.at(t)
        cam = _face_panel(frame, cx, cy, plan.face_h, OUT_W, hc)
        game = cv2.resize(_crop(frame, plan.game_cx, H / 2, H * OUT_W / gh, H), (OUT_W, gh),
                          interpolation=cv2.INTER_LINEAR)
        if plan.split_order == "game_top":
            out[:gh], out[gh:] = game, cam
            out[gh - 3:gh + 3] = 0
        else:
            out[:hc], out[hc:] = cam, game
            out[hc - 3:hc + 3] = 0
        return out
    # fit: blurred fill + the whole frame across the width
    bg = cv2.resize(_crop(frame, W / 2, H / 2, H * OUT_W / OUT_H, H), (OUT_W // 10, OUT_H // 10))
    bg = cv2.GaussianBlur(bg, (0, 0), 6)
    cv2.resize(bg, (OUT_W, OUT_H), dst=out)
    np.multiply(out, 0.55, out=out, casting="unsafe")
    fh = int(OUT_W * H / W)
    y0 = (OUT_H - fh) // 2
    out[y0:y0 + fh] = cv2.resize(frame, (OUT_W, fh))
    return out
