"""Plan 002 features: recognizer, enrollment, personality, live check, annotation."""

from __future__ import annotations

import base64
import json
import math

import numpy as np
import pytest

from plugin_voice import dsp, personality
from plugin_voice.routes import VAULT_PROFILE


# ------------------------------------------------------------------ synthetic voices


def synth_voice(f0: float, tilt: float, seconds: float = 2.0, seed: int = 0) -> bytes:
    """A crude 'speaker': harmonic stack at f0 with a spectral tilt envelope."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(dsp.SAMPLE_RATE * seconds)) / dsp.SAMPLE_RATE
    x = np.zeros_like(t)
    for h in range(1, 24):
        amp = (1.0 / h) ** tilt
        x += amp * np.sin(2 * math.pi * f0 * h * t + rng.uniform(0, 2 * math.pi))
    # amplitude modulation ≈ syllables, so the energy gate keeps frames
    x *= 0.4 + 0.3 * np.abs(np.sin(2 * math.pi * 3.1 * t))
    x += rng.normal(0, 0.01, len(x))
    x = x / np.max(np.abs(x)) * 0.6
    return (x * 32767).astype("<i2").tobytes()


VOICE_A = dict(f0=110.0, tilt=1.0)   # low, dark voice
VOICE_B = dict(f0=210.0, tilt=1.8)   # high, bright voice


def test_embed_shape_and_norm():
    e = dsp.embed(synth_voice(**VOICE_A))
    assert e is not None and e.shape == (2 * dsp.N_MELS,)
    assert abs(np.linalg.norm(e) - 1.0) < 1e-6


def test_embed_rejects_silence():
    assert dsp.embed(b"\x00" * 32000) is None


def test_same_voice_scores_higher_than_different_voice():
    enroll = [dsp.embed(synth_voice(**VOICE_A, seed=i)) for i in range(4)]
    profile = dsp.profile_from(enroll)
    same = dsp.score(profile, dsp.embed(synth_voice(**VOICE_A, seed=99)))
    other = dsp.score(profile, dsp.embed(synth_voice(**VOICE_B, seed=99)))
    assert same > other  # absolute scale is calibration-dependent; see dojo report


def test_verdict_labels():
    profile = dsp.profile_from([dsp.embed(synth_voice(**VOICE_A, seed=i)) for i in range(4)])
    s_same = dsp.score(profile, dsp.embed(synth_voice(**VOICE_A, seed=7)))
    s_other = dsp.score(profile, dsp.embed(synth_voice(**VOICE_B, seed=7)))
    midpoint = (s_same + s_other) / 2
    assert dsp.verdict(profile, synth_voice(**VOICE_A, seed=7), midpoint)[0] == "owner"
    assert dsp.verdict(profile, synth_voice(**VOICE_B, seed=7), midpoint)[0] == "other"
    assert dsp.verdict(None, synth_voice(**VOICE_A))[0] == "unknown"


# ------------------------------------------------------------------ personality


def test_clean_persona_normalizes_fillers():
    p = personality._clean_persona(
        {
            "name": "T-800",
            "greeting": "I am T-800. Talk.",
            "fillers": ["Processing.", "Stand by", "Target acquired…"],
            "voice_description": "deep, metallic, slow male voice",
        }
    )
    assert p["name"] == "T-800"
    assert all(f.endswith("... ") for f in p["fillers"])


def test_clean_persona_rejects_junk():
    assert personality._clean_persona("not a dict") is None
    assert personality._clean_persona({"name": "", "greeting": ""}) is None


@pytest.mark.anyio
async def test_fetch_persona_falls_back_to_neutral():
    class NoAgentCtx:
        agent = None

    p = await personality.fetch_persona(NoAgentCtx())
    assert p["greeting"] == personality.NEUTRAL["greeting"]


# ------------------------------------------------------------------ enrollment flow


def _pcm_b64(voice_kw, seed=0):
    return base64.b64encode(synth_voice(**voice_kw, seconds=3.0, seed=seed)).decode()


def test_enrollment_builds_profile_and_gates_live_token(client, ctx):
    st = client.get("/api/p/plugin-voice/enroll").json()
    assert st["ready"] is False and st["phrases"] == dsp.ENROLL_PHRASES

    for i in range(dsp.MIN_ENROLL):
        r = client.post(
            "/api/p/plugin-voice/enroll",
            json={"phrase_index": i, "pcm_b64": _pcm_b64(VOICE_A, seed=i)},
        )
        assert r.status_code == 200, r.text
    assert client.get("/api/p/plugin-voice/enroll").json()["ready"] is True
    assert VAULT_PROFILE in ctx.vault.data

    # session now mints a live token (after connect)
    client.post("/api/p/plugin-voice/connect", json={"api_key": "sk_test_not_real"})
    session = client.get("/api/p/plugin-voice/session").json()
    assert session["live_token"]

    # reset clears everything
    client.request("DELETE", "/api/p/plugin-voice/enroll")
    assert client.get("/api/p/plugin-voice/enroll").json()["ready"] is False


def test_enrollment_rejects_silence_and_shorts(client):
    r = client.post(
        "/api/p/plugin-voice/enroll",
        json={"phrase_index": 0, "pcm_b64": base64.b64encode(b"\x00" * 64000).decode()},
    )
    assert r.status_code == 400
    r = client.post("/api/p/plugin-voice/enroll", json={"phrase_index": 0, "pcm_b64": "AAAA"})
    assert r.status_code == 400


def test_live_ws_requires_valid_token(client):
    import websockets  # noqa: F401 — just documenting the transport

    with pytest.raises(Exception):
        with client.websocket_connect("/api/p/plugin-voice/live?token=bogus"):
            pass


def _midpoint_threshold(ctx):
    """Synthetic A/B voices sit on a calibration-dependent scale — pin the
    runtime threshold between them so labels are deterministic in tests."""
    a = dsp.profile_from([dsp.embed(synth_voice(**VOICE_A, seed=i)) for i in range(4)])
    s_a = dsp.score(a, dsp.embed(synth_voice(**VOICE_A, seed=42)))
    s_b = dsp.score(a, dsp.embed(synth_voice(**VOICE_B, seed=42)))
    return (s_a + s_b) / 2


def test_live_ws_scores_windows_and_feeds_bridge_annotation(client, ctx):
    from plugin_voice import state as live_state

    client.post("/api/p/plugin-voice/settings", json={"voice_id": None})
    import json as _json
    from plugin_voice import VAULT_SETTINGS
    settings = _json.loads(ctx.vault.data.get(VAULT_SETTINGS, "{}"))
    settings["threshold"] = _midpoint_threshold(ctx)
    ctx.vault.data[VAULT_SETTINGS] = _json.dumps(settings)

    # enroll VOICE_A as owner, connect, mint a live token
    for i in range(dsp.MIN_ENROLL):
        client.post(
            "/api/p/plugin-voice/enroll",
            json={"phrase_index": i, "pcm_b64": _pcm_b64(VOICE_A, seed=i)},
        )
    client.post("/api/p/plugin-voice/connect", json={"api_key": "sk_test_not_real"})
    token = client.get("/api/p/plugin-voice/session").json()["live_token"]

    with client.websocket_connect(f"/api/p/plugin-voice/live?token={token}") as ws:
        ws.send_json({"pcm_b64": _pcm_b64(VOICE_B, seed=42)})  # 3s > 1s window
        out = ws.receive_json()
    assert out["speaker"] == "other"
    assert live_state.recent_speaker()[0] == "other"

    # the very next bridge turn carries the annotation
    from plugin_voice import VAULT_BRIDGE_SECRET

    secret = ctx.vault.data[VAULT_BRIDGE_SECRET]
    client.post(
        "/api/p/plugin-voice/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "delete everything"}], "stream": False},
        headers={"authorization": f"Bearer {secret}"},
    )
    assert "Voice note" in ctx.agent.calls[-1]["prompt"]


def test_owner_voice_passes_live_check(client, ctx):
    import json as _json
    from plugin_voice import VAULT_SETTINGS
    ctx.vault.data[VAULT_SETTINGS] = _json.dumps({"threshold": _midpoint_threshold(ctx)})
    for i in range(dsp.MIN_ENROLL):
        client.post(
            "/api/p/plugin-voice/enroll",
            json={"phrase_index": i, "pcm_b64": _pcm_b64(VOICE_A, seed=i)},
        )
    client.post("/api/p/plugin-voice/connect", json={"api_key": "sk_test_not_real"})
    token = client.get("/api/p/plugin-voice/session").json()["live_token"]
    with client.websocket_connect(f"/api/p/plugin-voice/live?token={token}") as ws:
        ws.send_json({"pcm_b64": _pcm_b64(VOICE_A, seed=42)})
        out = ws.receive_json()
    assert out["speaker"] == "owner"


# ------------------------------------------------------------ 0.2.1 refinements


def test_persona_prompt_demands_real_name():
    assert "REAL given name" in personality.PERSONA_PROMPT
    assert "NOT a roleplay" in personality.PERSONA_SCHEMA["properties"]["name"]["description"]


def test_annotation_is_soft_not_refusing(client, ctx):
    from plugin_voice import VAULT_BRIDGE_SECRET, state as live_state

    client.post("/api/p/plugin-voice/connect", json={"api_key": "sk_test_not_real"})
    live_state.set_last_speaker("other", 0.1)
    secret = ctx.vault.data[VAULT_BRIDGE_SECRET]
    client.post(
        "/api/p/plugin-voice/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "hi"}], "stream": False},
        headers={"authorization": f"Bearer {secret}"},
    )
    prompt = ctx.agent.calls[-1]["prompt"]
    assert "do NOT refuse" in prompt and "Voice note" in prompt


def test_enrollment_stores_personal_threshold(client, ctx):
    for i in range(dsp.MIN_ENROLL):
        client.post(
            "/api/p/plugin-voice/enroll",
            json={"phrase_index": i, "pcm_b64": _pcm_b64(VOICE_A, seed=i)},
        )
    data = json.loads(ctx.vault.data[VAULT_PROFILE])
    assert data["threshold"] is not None
    assert 0.25 <= data["threshold"] <= dsp.effective_threshold()


def test_enroll_test_endpoint_gives_verdict(client, ctx):
    for i in range(dsp.MIN_ENROLL):
        client.post(
            "/api/p/plugin-voice/enroll",
            json={"phrase_index": i, "pcm_b64": _pcm_b64(VOICE_A, seed=i)},
        )
    out = client.post(
        "/api/p/plugin-voice/enroll/test", json={"pcm_b64": _pcm_b64(VOICE_A, seed=77)}
    ).json()
    assert out["speaker"] in ("owner", "other") and "threshold" in out


def test_refresh_persona_updates_agent_and_settings(client, ctx):
    from tests.conftest import FakeEL

    client.post("/api/p/plugin-voice/connect", json={"api_key": "sk_test_not_real"})
    before = len(FakeEL.agent_configs)
    resp = client.post("/api/p/plugin-voice/refresh-persona")
    assert resp.status_code == 200, resp.text
    assert len(FakeEL.agent_configs) == before + 1
    assert FakeEL.agent_configs[-1]["op"] == "update"


def test_session_includes_persona_name(client, ctx):
    client.post("/api/p/plugin-voice/connect", json={"api_key": "sk_test_not_real"})
    assert "persona_name" in client.get("/api/p/plugin-voice/session").json()


def test_ui_carries_new_affordances(client):
    settings_html = client.get("/api/p/plugin-voice/ui/settings/").text
    assert 'data-testid="voice-imprint-test"' in settings_html
    assert 'data-testid="voice-refresh-persona"' in settings_html
    assert "Really delete" in settings_html and "rec-meter" in settings_html
    widget_html = client.get("/api/p/plugin-voice/ui/widgets/voice/").text
    assert "agentName" in widget_html and "BroadcastChannel" in widget_html
    assert "Luna is speaking" not in widget_html
