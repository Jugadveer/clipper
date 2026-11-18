"""PANNs Cnn14 AudioSet tagger, self-contained (no torchlibrosa / panns_inference).

The checkpoint carries its own mel filterbank (`logmel_extractor.melW`), so the front end is
torch.stft + that matrix; only the conv trunk is re-declared here. Used to find laughter,
screaming, shouting, cheering, gasps and explosions: the sounds a stream makes at its peaks.
"""
from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

from .common import MODELS, REPO

CKPT_CANDIDATES = [REPO / "YOLO" / "Cnn14_mAP=0.431.pth", MODELS / "Cnn14_mAP=0.431.pth"]
SR = 32000


class _Block(nn.Module):
    def __init__(self, cin, cout):
        super().__init__()
        self.conv1 = nn.Conv2d(cin, cout, 3, 1, 1, bias=False)
        self.conv2 = nn.Conv2d(cout, cout, 3, 1, 1, bias=False)
        self.bn1 = nn.BatchNorm2d(cout)
        self.bn2 = nn.BatchNorm2d(cout)

    def forward(self, x, pool):
        x = F.relu_(self.bn1(self.conv1(x)))
        x = F.relu_(self.bn2(self.conv2(x)))
        return F.avg_pool2d(x, pool)


class Cnn14(nn.Module):
    def __init__(self):
        super().__init__()
        self.bn0 = nn.BatchNorm2d(64)
        chans = [1, 64, 128, 256, 512, 1024, 2048]
        for i in range(6):
            setattr(self, f"conv_block{i+1}", _Block(chans[i], chans[i + 1]))
        self.fc1 = nn.Linear(2048, 2048)
        self.fc_audioset = nn.Linear(2048, 527)
        self.register_buffer("melW", torch.zeros(513, 64))
        self.register_buffer("window", torch.hann_window(1024))

    def logmel(self, wav):                       # wav (B, N)
        spec = torch.stft(wav, 1024, 320, 1024, self.window, center=True,
                          pad_mode="reflect", return_complex=True)
        power = spec.real ** 2 + spec.imag ** 2  # (B, 513, T)
        mel = torch.matmul(power.transpose(1, 2), self.melW)   # (B, T, 64)
        return 10.0 * torch.log10(torch.clamp(mel, min=1e-10)).unsqueeze(1)

    def forward(self, wav):
        x = self.logmel(wav)                     # (B,1,T,64)
        x = x.transpose(1, 3)
        x = self.bn0(x)
        x = x.transpose(1, 3)
        for i in range(1, 6):
            x = getattr(self, f"conv_block{i}")(x, (2, 2))
        x = self.conv_block6(x, (1, 1))
        x = torch.mean(x, dim=3)
        x = torch.max(x, dim=2)[0] + torch.mean(x, dim=2)
        x = F.relu_(self.fc1(x))
        return torch.sigmoid(self.fc_audioset(x))


def labels() -> list[str]:
    with open(MODELS / "class_labels_indices.csv", encoding="utf-8") as f:
        return [r["display_name"] for r in csv.DictReader(f)]


CKPT_URL = "https://zenodo.org/records/3987831/files/Cnn14_mAP%3D0.431.pth?download=1"   # PANNs authors' release


def _fetch() -> Path | None:
    """First run on a new PC: download the 327 MB checkpoint once into assets/models."""
    import urllib.request
    from .common import log
    dst = MODELS / "Cnn14_mAP=0.431.pth"
    part = dst.with_suffix(".part")
    try:
        log("PANNs checkpoint missing; downloading it once (327 MB) from zenodo.org")
        MODELS.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(CKPT_URL, part)
        if part.stat().st_size < 300_000_000:
            raise RuntimeError(f"short download ({part.stat().st_size} bytes)")
        part.replace(dst)
        return dst
    except Exception as e:                  # no checkpoint = no audio events, never a failed job
        log(f"PANNs download failed ({e}); clipping without scream/laugh detection")
        part.unlink(missing_ok=True)
        return None


def load(device: str) -> Cnn14 | None:
    ckpt = next((p for p in CKPT_CANDIDATES if p.exists()), None) or _fetch()
    if ckpt is None:
        return None
    sd = torch.load(str(ckpt), map_location="cpu", weights_only=False)
    sd = sd.get("model", sd)
    m = Cnn14()
    own = m.state_dict()
    new = {k: v for k, v in sd.items() if k in own and own[k].shape == v.shape}
    new["melW"] = sd["logmel_extractor.melW"]
    own.update(new)
    m.load_state_dict(own)
    return m.eval().to(device)


# Which AudioSet classes count as which stream event.
EVENTS = {
    "laugh": ["Laughter", "Belly laugh", "Giggle", "Chuckle, chortle", "Snicker"],
    "scream": ["Screaming", "Shout", "Yell", "Bellow", "Whoop", "Battle cry", "Children shouting"],
    "cheer": ["Cheering", "Applause", "Crowd", "Hubbub, speech noise, speech babble"],
    "gasp": ["Gasp"],
    "boom": ["Explosion", "Gunshot, gunfire", "Bang", "Burst, pop", "Smash, crash"],
    "music": ["Music"],
}


def tag_events(wav_path: Path, win_s: float = 2.0, hop_s: float = 1.0, batch: int = 64,
               block_s: int = 600) -> dict:
    """Per-second event probabilities for a mono 32 kHz wav, read in 10-minute blocks so a
    12-hour stream never sits in RAM (12 h of float32 audio would be 5.5 GB)."""
    import soundfile as sf
    device = "cuda" if torch.cuda.is_available() else "cpu"
    m = load(device)
    if m is None:
        return {}
    names = labels()
    idx = {ev: [names.index(n) for n in cls if n in names] for ev, cls in EVENTS.items()}
    win, hop = int(win_s * SR), int(hop_s * SR)
    out = {ev: [] for ev in EVENTS}
    with sf.SoundFile(str(wav_path)) as f, torch.inference_mode():
        carry = np.zeros(0, np.float32)
        while True:
            blk = f.read(block_s * SR, dtype="float32")
            if blk.ndim > 1:
                blk = blk.mean(axis=1)
            last = len(blk) < block_s * SR
            buf = np.concatenate([carry, blk])
            n = max(0, (len(buf) - win) // hop + 1) if not last else max(1, -(-(len(buf) - win) // hop) + 1)
            for b0 in range(0, n, batch):
                rows = [buf[i * hop: i * hop + win] for i in range(b0, min(n, b0 + batch))]
                rows = [np.pad(r, (0, win - len(r))) for r in rows]
                x = torch.from_numpy(np.stack(rows)).float().to(device)
                p = m(x).float().cpu().numpy()
                for ev, ii in idx.items():
                    out[ev].append(p[:, ii].max(axis=1))
            if last:
                break
            carry = buf[n * hop:]
    # window i covers [i, i+2) s; centre it on second i+1
    return {ev: np.concatenate([[0.0], *v]).astype(np.float32) for ev, v in out.items()}
