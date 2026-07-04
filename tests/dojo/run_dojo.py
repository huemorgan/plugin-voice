"""Voice dojo — recognizer accuracy across a matrix of ElevenLabs voices.

Every premade voice takes a turn as "the owner": enroll on its enrollment
phrases, then score its own held-out utterances (genuine trials) and every
other voice's utterances (impostor trials). Sweep the decision threshold,
find the equal-error point, and report.

Run:  EL_KEY=sk_... .venv/bin/python tests/dojo/run_dojo.py
Clips are cached under tests/dojo/cache/ so iterations don't re-bill TTS.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import conftest  # noqa: E402,F401 — installs the luna_sdk stub for standalone runs
from plugin_voice import dsp  # noqa: E402

EL_KEY = os.environ.get("EL_KEY", "")
CACHE = Path(__file__).parent / "cache"
REPORT = Path(__file__).parent / "report.md"
N_VOICES = 10

TEST_PHRASES = [
    "Could you dim the lights and play some jazz in the living room?",
    "I think we should reschedule tomorrow's meeting to the afternoon.",
    "What's the fastest route to the airport right now, with traffic?",
]


def tts(voice_id: str, text: str, idx: str) -> bytes:
    path = CACHE / voice_id / f"{idx}.pcm"
    if path.exists():
        return path.read_bytes()
    path.parent.mkdir(parents=True, exist_ok=True)
    r = httpx.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
        params={"output_format": "pcm_16000"},
        headers={"xi-api-key": EL_KEY},
        json={"text": text, "model_id": "eleven_flash_v2_5"},
        timeout=60,
    )
    r.raise_for_status()
    path.write_bytes(r.content)
    return r.content


def main() -> None:
    if not EL_KEY:
        raise SystemExit("EL_KEY env var required")

    voices = httpx.get(
        "https://api.elevenlabs.io/v1/voices", headers={"xi-api-key": EL_KEY}, timeout=30
    ).json()["voices"]
    pool = [(v["voice_id"], v["name"]) for v in voices if v.get("category") == "premade"][:N_VOICES]
    print(f"voice pool ({len(pool)}):", ", ".join(n for _, n in pool))

    # RAW vectors for every voice: enrollment set + held-out test set
    enroll_raw: dict[str, list[np.ndarray]] = {}
    test_raw: dict[str, list[np.ndarray]] = {}
    for vid, name in pool:
        enroll_raw[vid] = [
            v for i, phrase in enumerate(dsp.ENROLL_PHRASES[: dsp.MIN_ENROLL])
            if (v := dsp.raw_vector(tts(vid, phrase, f"enroll-{i}"))) is not None
        ]
        test_raw[vid] = [
            v for i, phrase in enumerate(TEST_PHRASES)
            if (v := dsp.raw_vector(tts(vid, phrase, f"test-{i}"))) is not None
        ]
        print(f"  {name}: {len(enroll_raw[vid])} enroll / {len(test_raw[vid])} test clips")

    # "training": per-dim whitening over the whole population of raw vectors
    population = np.stack([v for vs in enroll_raw.values() for v in vs]
                          + [v for vs in test_raw.values() for v in vs])
    mu_g, sigma_g = population.mean(axis=0), population.std(axis=0)

    def calibrated(v: np.ndarray) -> np.ndarray:
        w = (v - mu_g) / (sigma_g + 1e-9)
        return w / (np.linalg.norm(w) + 1e-9)

    profiles = {vid: dsp.profile_from([calibrated(v) for v in vs]) for vid, vs in enroll_raw.items()}
    tests = {vid: [calibrated(v) for v in vs] for vid, vs in test_raw.items()}

    # cohort z-norm: each trial score is normalized against the OTHER owners'
    # profiles (leave the trial owner out — at runtime the real owner is never
    # in the shipped cohort either)
    def znorm(owner_vid: str, e: np.ndarray) -> float:
        s0 = float(np.dot(profiles[owner_vid], e))
        cs = np.array([float(np.dot(profiles[o], e)) for o, _ in pool if o != owner_vid])
        return (s0 - cs.mean()) / (cs.std() + 1e-6)

    genuine, impostor = [], []
    for vid, _ in pool:
        for e in tests[vid]:
            genuine.append(znorm(vid, e))
        for other, _ in pool:
            if other == vid:
                continue
            for e in tests[other]:
                impostor.append(znorm(vid, e))

    g, im = np.array(genuine), np.array(impostor)
    print(f"\ngenuine  n={len(g)}  mean={g.mean():.3f}  min={g.min():.3f}")
    print(f"impostor n={len(im)} mean={im.mean():.3f}  max={im.max():.3f}")

    # threshold sweep → equal error rate
    best = None
    for th in np.linspace(float(min(g.min(), im.min())), float(max(g.max(), im.max())), 800):
        frr = float((g < th).mean())   # owner rejected
        far = float((im >= th).mean())  # impostor accepted
        if best is None or abs(far - frr) < abs(best[1] - best[2]):
            best = (float(th), far, frr)
    th, far, frr = best
    eer = (far + frr) / 2
    print(f"\nEER ≈ {eer:.1%} at threshold {th:.3f} (FAR {far:.1%} / FRR {frr:.1%})")

    # operating point: prefer rejecting impostors (advisory feature, false
    # "other" is annoying but false "owner" is the security-relevant miss)
    op = None
    for cand in np.linspace(th, float(max(g.max(), im.max())), 400):
        far_c = float((im >= cand).mean())
        frr_c = float((g < cand).mean())
        if far_c <= 0.05 and (op is None or frr_c < op[2]):
            op = (float(cand), far_c, frr_c)
    if op is None:
        op = (th, far, frr)

    lines = [
        "# plugin-voice — Voice Dojo Report",
        "",
        f"Matrix: {len(pool)} ElevenLabs premade voices, each enrolled as owner in turn.",
        f"Trials: {len(g)} genuine / {len(im)} impostor.",
        "",
        f"- genuine scores:  mean **{g.mean():.3f}**, min {g.min():.3f}",
        f"- impostor scores: mean **{im.mean():.3f}**, max {im.max():.3f}",
        f"- **EER ≈ {eer:.1%}** at threshold {th:.3f}",
        f"- operating point (FAR ≤ 5%): threshold **{op[0]:.3f}** → FAR {op[1]:.1%}, FRR {op[2]:.1%}",
        "",
        "Voices: " + ", ".join(n for _, n in pool),
        "",
        "Recognizer: 24 log-mel bands, voiced-frame stats (shape+std), cosine vs",
        "enrollment-mean profile (`plugin_voice/dsp.py`). Clean-TTS numbers — real",
        "microphones will be noisier; the verdict stays advisory by design.",
    ]
    REPORT.write_text("\n".join(lines) + "\n")
    print(f"\nreport → {REPORT}")
    print(f"TUNED_THRESHOLD={op[0]:.3f}")

    cal_path = Path(__file__).resolve().parents[2] / "plugin_voice" / "dsp_calibration.py"
    cal_path.write_text(
        '"""Whitening calibration — GENERATED by dojo/run_dojo.py; do not edit."""\n\n'
        f"MU = {[round(float(x), 6) for x in mu_g]}\n\n"
        f"SIGMA = {[round(float(x), 6) for x in sigma_g]}\n\n"
        f"THRESHOLD = {op[0]:.3f}\n\n"
        "COHORT = "
        + repr([[round(float(x), 6) for x in profiles[vid]] for vid, _ in pool])
        + "\n"
    )
    print(f"calibration → {cal_path}")


if __name__ == "__main__":
    main()
