"""Numerical parity: dsp_pure (stdlib) must match dsp (numpy) closely enough
that the shipped dsp_calibration constants and stored profiles work on both
backends — hosted Lunas fall back to dsp_pure because plugin pip deps are
never installed there."""

from __future__ import annotations

import math
import struct

import numpy as np
import pytest

from plugin_voice import dsp, dsp_pure


def _tone_pcm(freqs, seconds=1.2, sr=16000, amp=0.3) -> bytes:
    """Deterministic synthetic 'speech': sum of tones with slow AM."""
    n = int(seconds * sr)
    samples = []
    for i in range(n):
        t = i / sr
        v = sum(math.sin(2 * math.pi * f * t) for f in freqs) / len(freqs)
        v *= amp * (0.6 + 0.4 * math.sin(2 * math.pi * 3.0 * t))
        samples.append(int(max(-1.0, min(1.0, v)) * 32767))
    return struct.pack(f"<{n}h", *samples)


VOICE_A = _tone_pcm([220, 440, 1320])
VOICE_A2 = _tone_pcm([220, 440, 1320], seconds=1.0)
VOICE_B = _tone_pcm([150, 600, 2400])


def test_constants_match():
    assert dsp_pure.ENROLL_PHRASES == dsp.ENROLL_PHRASES
    assert dsp_pure.MIN_ENROLL == dsp.MIN_ENROLL
    assert dsp_pure.DEFAULT_THRESHOLD == dsp.DEFAULT_THRESHOLD
    assert dsp_pure.effective_threshold() == dsp.effective_threshold()


def test_embed_parity():
    for pcm in (VOICE_A, VOICE_B):
        e_np = dsp.embed(pcm)
        e_py = dsp_pure.embed(pcm)
        assert e_np is not None and e_py is not None
        assert len(e_py) == len(e_np)
        assert float(np.abs(np.array(e_py) - e_np).max()) < 1e-4


def test_embed_too_short_is_none_on_both():
    silence = b"\x00\x00" * 1600  # 0.1 s
    assert dsp.embed(silence) is None
    assert dsp_pure.embed(silence) is None


def test_score_and_verdict_parity():
    embs_np = [dsp.embed(p) for p in (VOICE_A, VOICE_A2)]
    embs_py = [dsp_pure.embed(p) for p in (VOICE_A, VOICE_A2)]
    prof_np = dsp.profile_from(embs_np)
    prof_py = dsp_pure.profile_from(embs_py)
    assert float(np.abs(np.array(prof_py) - prof_np).max()) < 1e-4

    for probe in (VOICE_A2, VOICE_B):
        s_np = dsp.score(prof_np, dsp.embed(probe))
        s_py = dsp_pure.score(prof_py, dsp_pure.embed(probe))
        assert s_py == pytest.approx(s_np, abs=1e-3)
        label_np, _ = dsp.verdict(prof_np, probe, threshold=0.5)
        label_py, _ = dsp_pure.verdict(prof_py, probe, threshold=0.5)
        assert label_py == label_np


def test_backends_interchangeable_on_stored_profile():
    """A profile enrolled under numpy must score the same via dsp_pure —
    that's exactly what happens when a local enrollment runs on hosted."""
    stored = [float(x) for x in dsp.profile_from([dsp.embed(VOICE_A), dsp.embed(VOICE_A2)])]
    s_np = dsp.score(dsp.as_vector(stored), dsp.embed(VOICE_A2))
    s_py = dsp_pure.score(dsp_pure.as_vector(stored), dsp_pure.embed(VOICE_A2))
    assert s_py == pytest.approx(s_np, abs=1e-3)
