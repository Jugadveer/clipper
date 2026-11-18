"""Sound design for the 3D shorts: every sound belongs to something you SEE happen.

No bells, no tonal ringing, nothing repeated identically: each event is synthesized with small
seeded variations (pitch, length, filter). All sounds are quiet and short; they sit under the
voice. `piece_sounds()` lists the events of each set-piece using the same timings as kit4.html.
"""
from __future__ import annotations

import numpy as np
from scipy.io import wavfile
from scipy.signal import butter, lfilter

SR = 48000


def _bp(x, lo, hi):
    b, a = butter(2, [max(20, lo) / (SR / 2), min(SR / 2 - 100, hi) / (SR / 2)], "band")
    return lfilter(b, a, x)


def _lp(x, fc):
    b, a = butter(2, fc / (SR / 2))
    return lfilter(b, a, x)


def _hp(x, fc):
    b, a = butter(2, fc / (SR / 2), "high")
    return lfilter(b, a, x)


def _env(n, attack, decay_rate):
    t = np.arange(n) / SR
    return np.minimum(1, t / max(attack, 1e-4)) * np.exp(-t * decay_rate)


class Synth:
    def __init__(self, seed: int = 1):
        self.r = np.random.default_rng(seed)

    def noise(self, sec):
        return self.r.standard_normal(int(sec * SR))

    # ---- movement
    def whoosh(self, sec=0.8):
        """Air past the camera: a filtered-noise swell whose colour sweeps, varied every time."""
        n = int(sec * SR); x = np.linspace(0, 1, n)
        src = lfilter([1], [1, -0.96], self.r.standard_normal(n)); src /= np.abs(src).max()
        lo, hi = 200 + self.r.random() * 150, 1800 + self.r.random() * 1800
        out = np.zeros(n); blk = 1024
        for i in range(0, n, blk):
            u = (i + blk / 2) / n; fc = lo + (hi - lo) * np.sin(np.pi * u) ** 2
            out[i:i + blk] = _lp(src[i:i + blk], fc)
        return out * np.sin(np.pi * x) ** 2.5 * 0.5

    def riser(self, sec=0.6):
        n = int(sec * SR); x = np.linspace(0, 1, n)
        src = self.r.standard_normal(n); out = np.zeros(n); blk = 1024
        for i in range(0, n, blk):
            out[i:i + blk] = _lp(src[i:i + blk], 300 + 3500 * ((i + blk / 2) / n) ** 2)
        return out * x ** 2 * 0.28

    def rumble(self, sec=0.9):
        n = int(sec * SR); x = np.linspace(0, 1, n)
        s = _lp(self.r.standard_normal(n), 140 + self.r.random() * 40)
        s /= np.abs(s).max() + 1e-9
        return s * np.sin(np.pi * x) * 0.5

    # ---- materials
    def glass_crack(self):
        """A sharp crack with a crackle tail (no tone)."""
        n = int(0.35 * SR); t = np.arange(n) / SR
        s = _hp(self.noise(0.35), 1800) * np.exp(-t * 28) * 0.55
        for _ in range(9):
            k = int(self.r.random() * 0.25 * SR); m = int(0.004 * SR)
            s[k:k + m] += _hp(self.noise(0.004), 3000)[: len(s[k:k + m])] * 0.6
        return s

    def shards(self, sec=0.8):
        """Little pieces landing: short dull ticks, not chimes."""
        s = np.zeros(int(sec * SR))
        for _ in range(14):
            k = int((self.r.random() ** 0.7) * (sec - 0.05) * SR); m = int(0.012 * SR)
            blip = _bp(self.noise(0.012), 2000, 5000) * np.exp(-np.arange(m) / SR * 300) * (0.08 + self.r.random() * 0.12)
            s[k:k + m] += blip
        return s

    def paper_flip(self):
        n = int(0.32 * SR); x = np.linspace(0, 1, n)
        return _bp(self.noise(0.32), 900, 4200) * np.sin(np.pi * x) ** 1.5 * (1 + 0.6 * np.sin(x * 60)) * 0.22

    def stamp(self):
        n = int(0.22 * SR); t = np.arange(n) / SR
        thud = np.sin(2 * np.pi * (90 + 40 * np.exp(-t * 40)) * t) * np.exp(-t * 26) * 0.6
        slap = _bp(self.noise(0.22), 300, 2500) * np.exp(-t * 70) * 0.35
        return thud + slap

    def thunk(self, pitch=1.0):
        """A small wooden/solid knock: something lands or locks into place."""
        n = int(0.16 * SR); t = np.arange(n) / SR; f = (160 + self.r.random() * 40) * pitch
        return (np.sin(2 * np.pi * f * t) * np.exp(-t * 40) * 0.45 + _bp(self.noise(0.16), 800, 3000) * np.exp(-t * 120) * 0.12)

    def swish(self):
        n = int(0.18 * SR); x = np.linspace(0, 1, n)
        return _bp(self.noise(0.18), 2500, 7000) * np.sin(np.pi * x) ** 2 * 0.2

    def pop(self, pitch=1.0):
        n = int(0.07 * SR); t = np.arange(n) / SR; f0 = (520 + self.r.random() * 120) * pitch
        return np.sin(2 * np.pi * (f0 - 2500 * t) * t) * np.exp(-t * 55) * 0.22

    def flap_rattle(self, sec=2.0):
        """A mechanical split-flap counter: fast clicks that slow down as the number settles."""
        s = np.zeros(int(sec * SR)); t = 0.0
        while t < sec - 0.02:
            m = int(0.006 * SR); k = int(t * SR)
            s[k:k + m] += _hp(self.noise(0.006), 2500)[: len(s[k:k + m])] * (0.10 + self.r.random() * 0.05)
            t += 0.028 + 0.18 * (t / sec) ** 3
        return s

    def neon_buzz(self):
        """A tube flickering on: mains hum with a crackle, gated in stutters."""
        n = int(0.22 * SR); t = np.arange(n) / SR
        hum = (np.sin(2 * np.pi * 100 * t) + 0.5 * np.sin(2 * np.pi * 200 * t) + 0.3 * np.sign(np.sin(2 * np.pi * 50 * t)) * 0.3)
        gate = (np.sin(2 * np.pi * (14 + self.r.random() * 10) * t) > -0.2).astype(float)
        return (hum * gate * 0.06 + _hp(self.noise(0.22), 4000) * gate * 0.03) * np.exp(-t * 6)

    def click(self):
        n = int(0.03 * SR); t = np.arange(n) / SR
        return _hp(self.noise(0.03), 2000) * np.exp(-t * 200) * 0.35

    def beep(self, f=1150):
        n = int(0.075 * SR); t = np.arange(n) / SR
        return np.sin(2 * np.pi * f * t) * np.minimum(1, t / 0.004) * np.exp(-t * 30) * 0.07

    def siren_chirp(self):
        n = int(0.42 * SR); t = np.arange(n) / SR
        f = 650 + 260 * (np.sin(2 * np.pi * 2.4 * t) > 0)
        ph = 2 * np.pi * np.cumsum(f) / SR
        return np.sin(ph) * np.sin(np.pi * t / t[-1]) * 0.05

    def cash_flutter(self, sec=0.8):
        n = int(sec * SR); x = np.linspace(0, 1, n)
        am = 0.5 + 0.5 * np.sin(2 * np.pi * (18 + self.r.random() * 8) * np.arange(n) / SR)
        return _bp(self.noise(sec), 1200, 5000) * am * np.sin(np.pi * x) * 0.16

    def mech(self):
        """A latch or lock mechanism: two quick metal clicks."""
        s = np.zeros(int(0.12 * SR))
        for k0 in (0, int(0.05 * SR)):
            m = int(0.02 * SR); t = np.arange(m) / SR
            s[k0:k0 + m] += _bp(self.noise(0.02), 2500, 8000) * np.exp(-t * 150) * 0.3
        return s

    def door_roll(self, sec=0.7):
        n = int(sec * SR); x = np.linspace(0, 1, n)
        rattle = _bp(self.noise(sec), 300, 1800) * (0.6 + 0.4 * np.sign(np.sin(2 * np.pi * 26 * np.arange(n) / SR)))
        return rattle * np.sin(np.pi * x) * 0.12

    def swell(self, sec=0.9):
        """A low warm pad swelling under a reveal (no high partials, so nothing rings)."""
        n = int(sec * SR); t = np.arange(n) / SR; x = t / t[-1]
        pad = np.sin(2 * np.pi * 110 * t) + 0.6 * np.sin(2 * np.pi * 165 * t) + 0.4 * np.sin(2 * np.pi * 82.5 * t)
        return pad * np.sin(np.pi * x) ** 2 * 0.06


# every sound is normalised to its own peak, so a loud noise burst can never poke out of the mix
LEVEL = {"whoosh": 0.32, "riser": 0.22, "rumble": 0.38, "glass_crack": 0.42, "shards": 0.18, "paper_flip": 0.3,
         "stamp": 0.5, "thunk": 0.32, "swish": 0.2, "pop": 0.24, "flap_rattle": 0.16, "neon_buzz": 0.14,
         "click": 0.22, "beep": 0.1, "siren_chirp": 0.12, "cash_flutter": 0.2, "mech": 0.24, "door_roll": 0.2,
         "swell": 0.14}


def _levelled(name, fn):
    def wrapped(self, *a, **k):
        x = fn(self, *a, **k)
        return x / (np.abs(x).max() + 1e-9) * LEVEL[name]
    return wrapped


for _name in LEVEL:
    setattr(Synth, _name, _levelled(_name, getattr(Synth, _name)))


def piece_sounds(piece: str, p: dict, s: float, dur: float, syn: Synth) -> list[tuple[float, np.ndarray]]:
    """(time, sound) for one set-piece, matching kit4.html's timings."""
    ev = []
    if piece == "billboard":
        c = p.get("crack_at", 1.9)
        ev += [(s + 0.3, syn.riser(0.5)), (s + c, syn.glass_crack()), (s + c + 0.2, syn.shards(0.9))]
    elif piece == "calendarflip":
        f = p.get("flip_at", 1.3)
        ev += [(s + f, syn.paper_flip())]
        if p.get("stamp"): ev += [(s + f + 0.9, syn.stamp())]
    elif piece == "playdial":
        b = p.get("burst_at", 0.9)
        ev += [(s + b, syn.pop(0.8)), (s + b + 0.05, syn.flap_rattle(2.2)), (s + b + 1.8, syn.riser(0.6)), (s + b + 2.4, syn.thunk(0.8))]
    elif piece == "timeline":
        for j, d in enumerate(p["dates"]):
            at = 0.3 + j * 1.35
            ev += [(s + at + 0.25, syn.thunk(1 + 0.1 * j))]
            if d.get("crossed"): ev += [(s + at + 0.75, syn.swish()), (s + at + 1.3, syn.rumble(0.5))]
            if d.get("final"): ev += [(s + at + 0.4, syn.swell(0.9))]
    elif piece == "screens":
        ev += [(s + 0.2 + k * 0.12 + 0.15, syn.pop(0.9 + 0.08 * k)) for k in range(9)]
        ev += [(s + 1.28, syn.riser(0.45)), (s + 1.68, syn.flap_rattle(2.0))]
    elif piece == "skyline":
        ev += [(s + 0.3 + k * 0.4, syn.rumble(0.9)) for k in range(len(p["bars"]))]
    elif piece == "finale":
        ev += [(s + 0.5 + k * 0.16, syn.neon_buzz()) for k in range(len(p.get("big", "FOLLOW")))]
    elif piece == "streethero":
        kind = p.get("prop", "cop")
        ev += [(s + 0.3, syn.swish())]                                   # the word rises
        if kind == "cop":
            ev += [(s + 0.1, syn.rumble(1.5)), (s + 0.7, syn.siren_chirp()), (s + 1.55, syn.thunk(0.7))]
        elif kind == "lock":
            ev += [(s + 0.35, syn.pop(1.0)), (s + 1.2, syn.mech()), (s + 1.72, syn.thunk(0.8))]
        elif kind == "cash":
            ev += [(s + 0.3 + k * 0.16 + 0.33, syn.thunk(0.75 + 0.05 * (k % 3))) for k in range(8)]
        elif kind == "clock":
            ev += [(s + 0.4, syn.flap_rattle(1.8)), (s + 2.2, syn.thunk(0.9)), (s + 2.2, syn.swell(0.9))]
        elif kind == "neon":
            ev += [(s + 0.3, syn.neon_buzz()), (s + 0.55, syn.neon_buzz()), (s + 0.85, syn.neon_buzz()), (s + 1.0, syn.swell(1.2))]
    elif piece == "twoclocks":
        a, b = p.get("a_at", 2.0), p.get("b_at", 4.4)
        ev += [(s + 0.2, syn.flap_rattle(a - 0.2)), (s + a, syn.thunk(0.9)), (s + a + 0.02, syn.swell(0.8)),
               (s + a + 0.1, syn.flap_rattle(b - a - 0.1)), (s + b, syn.thunk(1.1)), (s + b + 0.02, syn.swell(0.8))]
    elif piece == "storage":
        n = 10
        ev += [(s + 0.5 + (4 - k) * 0.17 + 0.35, syn.swish()) for k in (4, 2, 0)]      # old games pushed out
        fill_end = 1.7 + n * 0.17 + 0.25
        ev += [(s + 1.7, syn.flap_rattle(fill_end - 1.7)), (s + fill_end, syn.thunk(0.9)), (s + fill_end, syn.swell(1.0))]
    elif piece == "gamebox":
        o = p.get("open_at", 1.4)
        ev += [(s + o, syn.mech()), (s + o + 0.15, syn.swish()), (s + o + 1.0, syn.stamp()), (s + o + 2.0, syn.swish()), (s + o + 2.3, syn.swell(0.9))]
    elif piece == "download":
        end = p.get("end_at", 3.8)
        ev += [(s + 0.25, syn.thunk(1.0)), (s + 0.45, syn.thunk(1.15)), (s + 0.8, syn.riser(end - 0.8)),
               (s + end, syn.pop(1.0)), (s + end + 0.05, syn.swell(1.0))]
    elif piece == "scanner":
        ev += [(s + 0.6, syn.riser(0.7))]
        ev += [(s + at, syn.beep(1050 + 120 * k)) for k, at in enumerate(p.get("tag_at", [2.8, 3.5, 4.2, 5.1]))]
    elif piece == "heatzone":
        esc = p.get("escape_at", 4.4)
        ev += [(s + 1.4, syn.swell(0.9)), (s + 2.0, syn.siren_chirp()), (s + esc, syn.rumble(1.6)), (s + esc + 2.0, syn.swish()),
               (s + esc + 2.2, syn.pop(1.1))]
    elif piece == "wardrobe":
        sw = p.get("swaps", [1.0, 2.1, 3.3]); no = p.get("no_at", 4.6)
        ev += [(s + sw[0] - 0.1, syn.cash_flutter(0.5)), (s + sw[1] - 0.1, syn.swish()), (s + sw[2], syn.pop(0.8)), (s + no, syn.stamp())]
    elif piece == "alley":
        ta = p.get("turn_at", 2.2)
        ev += [(s + 0.2, syn.rumble(ta)), (s + 1.0, syn.siren_chirp()), (s + ta, syn.swish()), (s + 2.8, syn.siren_chirp()),
               (s + 3.0, syn.rumble(1.2)), (s + 3.9, syn.pop(1.1))]
    elif piece == "splitpair":
        sp = p.get("split_at", 1.8)
        ev += [(s + 0.15, syn.mech()), (s + 0.3, syn.pop(0.9)), (s + 0.45, syn.pop(1.05)), (s + sp, syn.swish()), (s + sp + 0.15, syn.swish()),
               (s + sp + 2.4, syn.swell(0.9))]
    elif piece == "splitbank":
        ba, ca = p.get("bank_at", 1.6), p.get("cash_at", 3.0)
        ev += [(s + ba, syn.thunk(0.7)), (s + ba + 0.1, syn.rumble(0.6)), (s + ca - 0.3, syn.thunk(1.0)),
               (s + ca, syn.cash_flutter(0.7)), (s + ca + 1.0, syn.cash_flutter(1.2))]
    elif piece == "cashburn":
        la = p.get("lose_at", 2.0)
        ev += [(s + la, syn.thunk(0.6)), (s + la + 0.05, syn.cash_flutter(1.6)), (s + la + 0.25, syn.stamp())]
    elif piece == "tiers":
        at = p.get("at", [2.6, 4.4, 5.3]); ca = p.get("cop_at", 7.0)
        ev += [(s + a, syn.thunk(0.8 + 0.15 * k)) for k, a in enumerate(at)]
        ev += [(s + ca, syn.siren_chirp()), (s + ca + 0.1, syn.pop(0.9)), (s + ca + 0.35, syn.pop(1.0)), (s + ca + 0.6, syn.pop(1.12))]
    elif piece == "checklist":
        ev += [(s + 0.2, syn.paper_flip())]
        ev += [(s + a, syn.swish()) for a in p.get("at", [0.9 + k * 1.4 for k in range(len(p["items"]))])]
    elif piece == "safe":
        oa, bo = p.get("open_at", 2.0), p.get("boom_at", 3.6)
        ev += [(s + 0.2, syn.flap_rattle(oa - 0.4)), (s + oa - 0.15, syn.mech()), (s + oa, syn.door_roll(0.8)), (s + oa + 0.3, syn.swell(1.0)),
               (s + bo - 0.7, syn.pop(0.8)), (s + bo, syn.thunk(0.7))]
    elif piece == "lockpick":
        ja, sa, ca = p.get("jim_at", 1.6), p.get("smash_at", 4.6), p.get("cloner_at", 6.8)
        ev += [(s + ja - 0.6, syn.swish()), (s + ja, syn.mech()), (s + sa, syn.glass_crack()), (s + sa + 0.1, syn.shards(0.8)),
               (s + sa + 0.3, syn.thunk(0.7)), (s + ca - 0.5, syn.whoosh(0.5)), (s + ca, syn.beep(1300)), (s + ca + 0.2, syn.mech())]
    elif piece == "phonescan":
        at = p.get("at", [1.4 + k * 0.7 for k in range(len(p["rows"]))])
        ev += [(s + 0.6, syn.riser(max(0.5, at[-1] - 0.6)))] + [(s + a, syn.beep(1000 + 90 * k)) for k, a in enumerate(at)]
    elif piece == "tracker":
        ra = p.get("remove_at", 3.6)
        ev += [(s + 0.6 + k * 0.75, syn.beep(880)) for k in range(int((ra - 0.6) / 0.75) + 1)]
        ev += [(s + ra, syn.mech()), (s + ra + 0.1, syn.whoosh(0.7)), (s + ra + 0.9, syn.pop(1.1))]
    elif piece == "spraybooth":
        fa, pa, ra = p.get("fix_at", 1.3), p.get("paint_at", 2.6), p.get("reg_at", 4.3)
        ev += [(s + 0.1, syn.rumble(fa)), (s + fa, syn.mech()), (s + fa + 0.05, syn.shards(0.5)), (s + pa, syn.cash_flutter(1.0)),
               (s + pa + 1.0, syn.rumble(1.2)), (s + ra, syn.stamp())]
    elif piece == "garage":
        fa, ta, ba = p.get("fade_at", 1.0), p.get("three_at", 4.4), p.get("buy_at", 6.2)
        ev += [(s + fa, syn.neon_buzz()), (s + fa + 0.5, syn.neon_buzz()), (s + fa + 1.0, syn.swish())]
        ev += [(s + ta + k * 0.25, syn.door_roll(0.6)) for k in range(3)]
        ev += [(s + ba, syn.thunk(0.8)), (s + ba + 0.5, syn.door_roll(0.6)), (s + ba + 0.3, syn.pop(1.0))]
    elif piece == "odometer":
        ev += [(s + a, syn.flap_rattle(0.9)) for a in p.get("at", [0.6, 2.6, 5.0])]
        ev += [(s + a + 0.9, syn.thunk(0.8 + 0.12 * k)) for k, a in enumerate(p.get("at", [0.6, 2.6, 5.0]))]
        ev += [(s + p.get("at", [0.6, 2.6, 5.0])[-1] + 0.9, syn.swell(1.0))]
    elif piece == "statemap":
        sa, na = p.get("state_at", 2.6), p.get("name_at", 3.6)
        ev += [(s + 0.3, syn.pop(0.9)), (s + sa - 0.4, syn.whoosh(1.4)), (s + sa, syn.rumble(1.0)), (s + na, syn.swish()), (s + na + 0.1, syn.swell(1.0))]
    elif piece == "postcards":
        ev += [(s + a, syn.paper_flip()) for a in p.get("at", [0.5 + k * 1.3 for k in range(len(p["cards"]))])]
    elif piece == "cards":
        aa, ba, da = p.get("a_at", 1.0), p.get("b_at", 1.8), p.get("deck_at", 3.4)
        ev += [(s + aa - 0.6, syn.swish()), (s + aa, syn.paper_flip()), (s + ba - 0.6, syn.swish()), (s + ba, syn.paper_flip()),
               (s + da, syn.flap_rattle(1.3))]
    # a spec may add its own "sfx": [[t, kind, arg?], ...]
    for item in p.get("sfx", []):
        t, kind = item[0], item[1]
        arg = item[2] if len(item) > 2 else None
        fn = getattr(syn, kind)
        ev.append((s + t, fn(arg) if arg is not None else fn()))
    return ev


def render(total: float, beats: list[dict], out, seed: int = 7):
    syn = Synth(seed)
    track = np.zeros(int((total + 2) * SR))

    def add(t0, sig):
        i = int(max(0, t0) * SR); j = min(len(track), i + len(sig)); track[i:j] += sig[: j - i]

    for k, b in enumerate(beats):
        if k:
            add(b["s"] - 0.45, syn.whoosh(0.75 + syn.r.random() * 0.25))
        for t, sig in piece_sounds(b.get("piece", ""), b["p"], b["s"], b["e"] - b["s"], syn):
            add(t, sig)
    track = np.clip(track, -1, 1)
    wavfile.write(str(out), SR, (track * 32767).astype(np.int16))
    return out
