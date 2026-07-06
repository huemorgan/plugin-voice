"""Pure-Python fallback for :mod:`dsp` — same math, no numpy.

Hosted Lunas never pip-install plugin dependencies, so numpy may be missing;
this module mirrors dsp.py's public API (embed / profile_from / score /
verdict / as_vector, plus the enrollment constants) using only the stdlib.
Vectors are plain ``list[float]``. Numerical parity with the numpy path is
asserted by tests/test_dsp_pure.py so the shipped dsp_calibration constants
apply equally to both backends.

A 1 s live window (~98 frames × 512-pt FFT) costs a few hundred ms of pure
Python — fine for the 1 Hz live-check cadence and one-shot enrollment.
"""

from __future__ import annotations

import cmath
import math
import sys
from array import array

SAMPLE_RATE = 16000
FRAME = 400        # 25 ms
HOP = 160          # 10 ms
N_MELS = 32
N_FFT = 512

DEFAULT_THRESHOLD = 0.90

ENROLL_PHRASES = [
    "Hey, it's me — I'm setting up my voice so you can recognize me later.",
    "The quick brown fox jumps over the lazy dog, twice on Sundays.",
    "Would you check my calendar, the weather, and anything urgent this morning?",
    "Seven, three, nineteen, forty two — numbers sound different in every voice.",
    "Alright, that should be enough for you to know exactly how I sound.",
]
MIN_ENROLL = 4

try:
    from .dsp_calibration import COHORT as _CAL_COHORT
    from .dsp_calibration import MU as _CAL_MU
    from .dsp_calibration import SIGMA as _CAL_SIGMA
    from .dsp_calibration import THRESHOLD as _CAL_THRESHOLD
except ImportError:
    _CAL_MU = _CAL_SIGMA = None
    _CAL_THRESHOLD = None
    _CAL_COHORT = None


# ── primitives ────────────────────────────────────────────────────────────────

def as_vector(values) -> list[float]:
    return [float(x) for x in values]


def _dot(a, b) -> float:
    return sum(x * y for x, y in zip(a, b))


def _mean(xs) -> float:
    return sum(xs) / len(xs)


def _pstd(xs) -> float:
    mu = _mean(xs)
    return math.sqrt(sum((x - mu) ** 2 for x in xs) / len(xs))


def _percentile_linear(sorted_xs: list[float], q: float) -> float:
    """np.percentile's default linear interpolation on pre-sorted data."""
    rank = q / 100.0 * (len(sorted_xs) - 1)
    lo = int(rank)
    frac = rank - lo
    if lo + 1 >= len(sorted_xs):
        return sorted_xs[-1]
    return sorted_xs[lo] + (sorted_xs[lo + 1] - sorted_xs[lo]) * frac


def pcm_to_float(pcm: bytes) -> list[float]:
    """s16le mono bytes → float in [-1, 1]."""
    samples = array("h")
    samples.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])
    if sys.byteorder == "big":
        samples.byteswap()
    return [s / 32768.0 for s in samples]


# ── FFT (iterative radix-2, N=512) ───────────────────────────────────────────

def _bit_reverse_table(n: int) -> list[int]:
    bits = n.bit_length() - 1
    return [int(format(i, f"0{bits}b")[::-1], 2) for i in range(n)]


_BITREV = _bit_reverse_table(N_FFT)
# per-stage twiddle factors: size → [e^(-2πik/size) for k in 0..size/2)
_TWIDDLES: dict[int, list[complex]] = {}
_size = 2
while _size <= N_FFT:
    _TWIDDLES[_size] = [cmath.exp(-2j * math.pi * k / _size) for k in range(_size // 2)]
    _size *= 2


def _power_spectrum(frame: list[float]) -> list[float]:
    """|rfft(frame, 512)|² — first N_FFT//2+1 bins of the full complex FFT."""
    buf: list[complex] = [0j] * N_FFT
    for i, v in enumerate(frame):
        buf[_BITREV[i]] = complex(v, 0.0)
    size = 2
    while size <= N_FFT:
        half = size // 2
        tw = _TWIDDLES[size]
        for start in range(0, N_FFT, size):
            for k in range(half):
                t = tw[k] * buf[start + half + k]
                u = buf[start + k]
                buf[start + k] = u + t
                buf[start + half + k] = u - t
        size *= 2
    return [c.real * c.real + c.imag * c.imag for c in buf[: N_FFT // 2 + 1]]


# ── mel filterbank (same construction as dsp.py, stored sparse) ──────────────

def _mel_filterbank_sparse() -> list[tuple[int, list[float]]]:
    def hz_to_mel(hz: float) -> float:
        return 2595.0 * math.log10(1.0 + hz / 700.0)

    def mel_to_hz(mel: float) -> float:
        return 700.0 * (10.0 ** (mel / 2595.0) - 1.0)

    lo_mel, hi_mel = hz_to_mel(50.0), hz_to_mel(SAMPLE_RATE / 2)
    mel_pts = [lo_mel + (hi_mel - lo_mel) * i / (N_MELS + 1) for i in range(N_MELS + 2)]
    bins = [int((N_FFT + 1) * mel_to_hz(m) / SAMPLE_RATE) for m in mel_pts]
    bank: list[tuple[int, list[float]]] = []
    for m in range(1, N_MELS + 1):
        lo, ctr, hi = bins[m - 1], bins[m], bins[m + 1]
        weights = []
        for k in range(lo, ctr):
            weights.append((k - lo) / (ctr - lo) if ctr > lo else 0.0)
        for k in range(ctr, hi):
            weights.append((hi - k) / (hi - ctr) if hi > ctr else 0.0)
        bank.append((lo, weights))
    return bank


_FB_SPARSE = _mel_filterbank_sparse()
_WINDOW = [0.54 - 0.46 * math.cos(2.0 * math.pi * i / (FRAME - 1)) for i in range(FRAME)]


# ── feature pipeline (mirrors dsp.py exactly) ─────────────────────────────────

def _logmel_frames(x: list[float]) -> list[list[float]]:
    if len(x) < FRAME:
        return []
    n = 1 + (len(x) - FRAME) // HOP
    mels: list[list[float]] = []
    energies: list[float] = []
    for f in range(n):
        start = HOP * f
        frame = [x[start + i] * _WINDOW[i] for i in range(FRAME)]
        energies.append(_pstd(frame))
        spec = _power_spectrum(frame)
        mels.append([
            math.log(_dot(spec[lo:lo + len(w)], w) + 1e-9)
            for lo, w in _FB_SPARSE
        ])
    gate = max(0.004, _percentile_linear(sorted(energies), 40))
    return [m for m, e in zip(mels, energies) if e > gate]


def raw_vector(pcm: bytes) -> list[float] | None:
    mel = _logmel_frames(pcm_to_float(pcm))
    if len(mel) < 20:  # <0.2s of voiced audio — not scoreable
        return None
    mu = [_mean([row[b] for row in mel]) for b in range(N_MELS)]
    sd = [_pstd([row[b] for row in mel]) for b in range(N_MELS)]
    mu_bar = _mean(mu)
    shape = [m - mu_bar for m in mu]
    return shape + sd


def embed(pcm: bytes) -> list[float] | None:
    v = raw_vector(pcm)
    if v is None:
        return None
    if _CAL_MU is not None:
        v = [(x - m) / (s + 1e-9) for x, m, s in zip(v, _CAL_MU, _CAL_SIGMA)]
    norm = math.sqrt(_dot(v, v)) + 1e-9
    return [x / norm for x in v]


def profile_from(embeddings: list[list[float]]) -> list[float]:
    dims = len(embeddings[0])
    p = [_mean([e[d] for e in embeddings]) for d in range(dims)]
    norm = math.sqrt(_dot(p, p)) + 1e-9
    return [x / norm for x in p]


def score(profile, embedding) -> float:
    s0 = _dot(profile, embedding)
    if _CAL_COHORT:
        cs = [_dot(c, embedding) for c in _CAL_COHORT]
        return (s0 - _mean(cs)) / (_pstd(cs) + 1e-6)
    return s0


def effective_threshold() -> float:
    return float(_CAL_THRESHOLD) if _CAL_THRESHOLD else DEFAULT_THRESHOLD


def verdict(profile, pcm: bytes, threshold: float | None = None) -> tuple[str, float]:
    """('owner'|'other'|'unknown', score) for a ~1s audio window."""
    if threshold is None:
        threshold = effective_threshold()
    if profile is None:
        return "unknown", 0.0
    e = embed(pcm)
    if e is None:
        return "unknown", 0.0
    s = score(profile, e)
    return ("owner" if s >= threshold else "other"), s
