"""Plan 002/005/007 features: recognizer, enrollment, personality, live check,
env keys, agent tools — against the 0.8.0 Gemini Live surface."""

from __future__ import annotations

import base64
import json
import math

import numpy as np
import pytest

from plugin_voice import dsp, personality
from plugin_voice.routes import VAULT_PROFILE

API = "/api/p/plugin-voice"


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


def _connect(client):
    resp = client.post(f"{API}/connect", json={"api_key": "sk_test_not_real"})
    assert resp.status_code == 200, resp.text


def test_enrollment_builds_profile_and_gates_live_token(client, ctx):
    st = client.get(f"{API}/enroll").json()
    assert st["ready"] is False and st["phrases"] == dsp.ENROLL_PHRASES

    for i in range(dsp.MIN_ENROLL):
        r = client.post(
            f"{API}/enroll",
            json={"phrase_index": i, "pcm_b64": _pcm_b64(VOICE_A, seed=i)},
        )
        assert r.status_code == 200, r.text
    assert client.get(f"{API}/enroll").json()["ready"] is True
    assert VAULT_PROFILE in ctx.vault.data

    # a minted session now arms the live check (after connect)
    _connect(client)
    session = client.get(f"{API}/rt/session").json()
    assert session["live_token"]
    assert session["has_imprint"] is True

    # reset clears everything
    client.request("DELETE", f"{API}/enroll")
    assert client.get(f"{API}/enroll").json()["ready"] is False


def test_enrollment_rejects_silence_and_shorts(client):
    r = client.post(
        f"{API}/enroll",
        json={"phrase_index": 0, "pcm_b64": base64.b64encode(b"\x00" * 64000).decode()},
    )
    assert r.status_code == 400
    r = client.post(f"{API}/enroll", json={"phrase_index": 0, "pcm_b64": "AAAA"})
    assert r.status_code == 400


def test_live_ws_requires_valid_token(client):
    import websockets  # noqa: F401 — just documenting the transport

    with pytest.raises(Exception):
        with client.websocket_connect(f"{API}/live?token=bogus"):
            pass


def _midpoint_threshold(ctx):
    """Synthetic A/B voices sit on a calibration-dependent scale — pin the
    runtime threshold between them so labels are deterministic in tests."""
    a = dsp.profile_from([dsp.embed(synth_voice(**VOICE_A, seed=i)) for i in range(4)])
    s_a = dsp.score(a, dsp.embed(synth_voice(**VOICE_A, seed=42)))
    s_b = dsp.score(a, dsp.embed(synth_voice(**VOICE_B, seed=42)))
    return (s_a + s_b) / 2


def _pin_threshold(ctx):
    from plugin_voice import VAULT_SETTINGS

    settings = json.loads(ctx.vault.data.get(VAULT_SETTINGS, "{}"))
    settings["threshold"] = _midpoint_threshold(ctx)
    ctx.vault.data[VAULT_SETTINGS] = json.dumps(settings)


def test_live_ws_scores_windows_and_updates_speaker_state(client, ctx):
    from plugin_voice import state as live_state

    _pin_threshold(ctx)
    # enroll VOICE_A as owner, connect, mint a call token
    for i in range(dsp.MIN_ENROLL):
        client.post(
            f"{API}/enroll",
            json={"phrase_index": i, "pcm_b64": _pcm_b64(VOICE_A, seed=i)},
        )
    _connect(client)
    token = client.get(f"{API}/rt/session").json()["live_token"]

    with client.websocket_connect(f"{API}/live?token={token}") as ws:
        ws.send_json({"pcm_b64": _pcm_b64(VOICE_B, seed=42)})  # 3s > 1s window
        out = ws.receive_json()
    assert out["speaker"] == "other"
    # the relay's owner lock and lane-3 dispatch read this module state
    assert live_state.recent_speaker()[0] == "other"


def test_owner_voice_passes_live_check(client, ctx):
    _pin_threshold(ctx)
    for i in range(dsp.MIN_ENROLL):
        client.post(
            f"{API}/enroll",
            json={"phrase_index": i, "pcm_b64": _pcm_b64(VOICE_A, seed=i)},
        )
    _connect(client)
    token = client.get(f"{API}/rt/session").json()["live_token"]
    with client.websocket_connect(f"{API}/live?token={token}") as ws:
        ws.send_json({"pcm_b64": _pcm_b64(VOICE_A, seed=42)})
        out = ws.receive_json()
    assert out["speaker"] == "owner"


# ------------------------------------------------------------ 0.2.1 refinements


def test_persona_prompt_demands_real_name():
    assert "REAL given name" in personality.PERSONA_PROMPT
    assert "NOT a roleplay" in personality.PERSONA_SCHEMA["properties"]["name"]["description"]


def test_enrollment_stores_personal_threshold(client, ctx):
    for i in range(dsp.MIN_ENROLL):
        client.post(
            f"{API}/enroll",
            json={"phrase_index": i, "pcm_b64": _pcm_b64(VOICE_A, seed=i)},
        )
    data = json.loads(ctx.vault.data[VAULT_PROFILE])
    assert data["threshold"] is not None
    assert 0.25 <= data["threshold"] <= dsp.effective_threshold()


def test_enroll_test_endpoint_gives_verdict(client, ctx):
    for i in range(dsp.MIN_ENROLL):
        client.post(
            f"{API}/enroll",
            json={"phrase_index": i, "pcm_b64": _pcm_b64(VOICE_A, seed=i)},
        )
    out = client.post(
        f"{API}/enroll/test", json={"pcm_b64": _pcm_b64(VOICE_A, seed=77)}
    ).json()
    assert out["speaker"] in ("owner", "other") and "threshold" in out


def test_session_includes_persona_name(client, ctx):
    _connect(client)
    assert "persona_name" in client.get(f"{API}/rt/session").json()


def test_ui_carries_new_affordances(client):
    settings_html = client.get(f"{API}/ui/settings/").text
    assert 'data-testid="voice-imprint-test"' in settings_html
    assert "Really delete" in settings_html and "rec-meter" in settings_html
    # 004.2: the re-match button lives on the Persona page now
    persona_html = client.get(f"{API}/ui/settings/persona/").text
    assert 'data-testid="voice-refresh-persona"' in persona_html
    assert 'data-testid="voice-refresh-persona"' not in settings_html
    widget_html = client.get(f"{API}/ui/widgets/voice/").text
    assert "agentName" in widget_html and "BroadcastChannel" in widget_html
    assert "Luna is speaking" not in widget_html


# ------------------------------------------------------------- 0.8.0 env keys


def test_status_detects_env_key_without_pasted_key(client, ctx, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gk-env")
    st = client.get(f"{API}/status").json()
    assert st["connected"] is True and st["key_source"] == "env"
    # status probed the key with a real mint before claiming it works
    assert st["ready"] is True and st["key_error"] is None


def test_status_key_resolves_but_cannot_mint(client, ctx, monkeypatch):
    """A restricted/revoked key still resolves, so it looks 'connected', but
    token minting fails — status must not claim ready."""
    from tests.conftest import FakeLive

    monkeypatch.setenv("GEMINI_API_KEY", "gk-env")
    FakeLive.fail_mint = True
    st = client.get(f"{API}/status").json()
    assert st["connected"] is True and st["key_source"] == "env"
    assert st["ready"] is False
    assert "check it in Settings" in st["key_error"]


def test_status_without_any_key_is_not_ready(client):
    st = client.get(f"{API}/status").json()
    assert st["connected"] is False and st["ready"] is False
    assert st["key_error"] is None


def test_settings_page_keys_readiness_on_probe_not_resolution(client):
    html = client.get(f"{API}/ui/settings/").text
    # the paste field and status line follow s.ready, not s.connected
    assert "!!s.ready" in html
    assert "Voice can't start with" in html


def test_connect_without_key_uses_env_key(client, ctx, monkeypatch):
    from plugin_voice import VAULT_GEMINI_KEY
    from tests.conftest import FakeLive

    monkeypatch.setenv("GEMINI_API_KEY", "gk-env")
    resp = client.post(f"{API}/connect", json={})
    assert resp.status_code == 200, resp.text
    st = resp.json()
    assert st["connected"] is True and st["key_source"] == "env"
    # nothing stored as the owner's own key — the env stays the source
    assert VAULT_GEMINI_KEY not in ctx.vault.data
    # the probe used the env key
    assert FakeLive.instances[0].api_key == "gk-env"


def test_connect_without_any_key_still_friendly_400(client):
    resp = client.post(f"{API}/connect", json={})
    assert resp.status_code == 400
    assert isinstance(resp.json()["detail"], str)


def test_resolve_never_uses_the_gateway(client, ctx):
    """007 removed the vault.connect/gateway lane on purpose: ephemeral token
    minting authenticates directly against Google, which a gateway virtual key
    cannot do. A wired gateway connection must NOT make status 'connected'."""
    import sys

    sdk = sys.modules["luna_sdk"]
    ctx.vault.gateway_connection = sdk.Connection(
        base_url="https://gw/proxy/gemini", secret="tok",
        auth=sdk.AuthSpec(location="header", name="Authorization", scheme="Bearer"),
        source="virtual",
    )
    st = client.get(f"{API}/status").json()
    assert st["connected"] is False and st["key_source"] is None


def test_resolve_skips_lsv1_gateway_tokens_in_env(client, ctx, monkeypatch):
    """0.8.1: hosted machines carry LUNA_GEMINI_API_KEY/GEMINI_API_KEY set to
    the platform's lsv1- gateway token (the chat-proxy pair). That token can
    never mint against Google — resolution must skip it (falling through to a
    real key if one exists, else 'not connected') instead of forwarding it to
    Google to die as an opaque HTTP 400."""
    monkeypatch.setenv("LUNA_GEMINI_API_KEY", "lsv1-tenant-token")
    monkeypatch.setenv("GEMINI_API_KEY", "lsv1-tenant-token")
    st = client.get(f"{API}/status").json()
    assert st["connected"] is False and st["key_source"] is None

    # a real key behind the poisoned LUNA_ var still wins
    monkeypatch.setenv("GEMINI_API_KEY", "gk-real")
    st = client.get(f"{API}/status").json()
    assert st["connected"] is True and st["key_source"] == "env"


def test_settings_page_gates_cards_until_ready(client):
    html = client.get(f"{API}/ui/settings/").text
    assert "gateCards" in html and 'data-testid="voice-connect-gateway"' in html


# ------------------------------------------------------------- 003 agent tools


def test_agent_tools_registered_with_honest_policies(ctx):
    import asyncio

    from plugin_voice import VoicePlugin
    from tests.conftest import FakeToolRegistry

    class Reg(FakeToolRegistry):
        def __init__(self):
            self.tools = []
            self.defs = {}
            self.handlers = {}

        def register(self, plugin, tool_def, handler, **kw):
            self.defs[tool_def.name] = tool_def
            self.handlers[tool_def.name] = handler

    ctx.tool_registry = Reg()
    asyncio.run(VoicePlugin().on_load(ctx))
    assert set(ctx.tool_registry.defs) == {"voice_status", "voice_connect"}
    assert ctx.tool_registry.defs["voice_status"].policy == "auto_approve"
    assert ctx.tool_registry.defs["voice_connect"].policy == "ask"

    # status tool works and never leaks a key value
    out = asyncio.run(ctx.tool_registry.handlers["voice_status"]())
    assert "connected" in out
    assert "api_key" not in json.dumps(out)


def test_voice_connect_tool_completes_setup_with_env_key(ctx, monkeypatch):
    """The chat flow: a key exists in the environment, voice_connect finishes."""
    import asyncio

    monkeypatch.setenv("GEMINI_API_KEY", "gk-env")

    from plugin_voice import VoicePlugin

    reg_calls = {}

    class Reg:
        def register(self, plugin, tool_def, handler, **kw):
            reg_calls[tool_def.name] = handler

    old_reg = ctx.tool_registry
    ctx.tool_registry = Reg()
    asyncio.run(VoicePlugin().on_load(ctx))
    ctx.tool_registry = old_reg

    out = asyncio.run(reg_calls["voice_connect"]())
    assert out.get("connected") is True, out
    assert out.get("key_source") == "env"


def test_voice_connect_tool_without_any_key_is_friendly(ctx):
    import asyncio

    from plugin_voice import VoicePlugin

    handlers = {}

    class Reg:
        def register(self, plugin, tool_def, handler, **kw):
            handlers[tool_def.name] = handler

    old = ctx.tool_registry
    ctx.tool_registry = Reg()
    asyncio.run(VoicePlugin().on_load(ctx))
    ctx.tool_registry = old
    out = asyncio.run(handlers["voice_connect"]())
    assert out["connected"] is False and "key" in out["error"].lower()


def test_settings_page_hides_paste_input_by_default(client):
    html = client.get(f"{API}/ui/settings/").text
    assert '<div id="paste-block" style="display:none">' in html
    assert "Gemini" in html and "OpenAI" not in html
