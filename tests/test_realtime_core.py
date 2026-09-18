"""007: Gemini key resolution, ephemeral token minting, setup message shape,
talker instructions, /rt/session."""

from __future__ import annotations

import json

import httpx
import pytest

from plugin_voice import VAULT_GEMINI_KEY, gemini_live, talker
from plugin_voice.gemini_live import (
    DEFAULT_MODEL,
    DEFAULT_VOICE,
    LIVE_WS_URL,
    GeminiLiveClient,
    RealtimeError,
    convert_tools,
    resolve_gemini_key,
    setup_message,
)


@pytest.fixture(autouse=True)
def _no_env_keys(monkeypatch):
    monkeypatch.delenv("LUNA_GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


# ---------------------------------------------------------------- key chain


async def test_pasted_key_wins(ctx, monkeypatch):
    ctx.vault.data[VAULT_GEMINI_KEY] = "gk-own"
    monkeypatch.setenv("GEMINI_API_KEY", "gk-env")
    res = await resolve_gemini_key(ctx, vault_key=VAULT_GEMINI_KEY)
    assert res == {"api_key": "gk-own", "source": "own"}


async def test_env_keys_both_spellings(ctx, monkeypatch):
    ctx.get_env = lambda name: "gk-luna-env" if name == "LUNA_GEMINI_API_KEY" else None
    res = await resolve_gemini_key(ctx, vault_key=VAULT_GEMINI_KEY)
    assert res == {"api_key": "gk-luna-env", "source": "env"}

    ctx.get_env = lambda name: None
    monkeypatch.setenv("GEMINI_API_KEY", "gk-bare-env")
    res = await resolve_gemini_key(ctx, vault_key=VAULT_GEMINI_KEY)
    assert res == {"api_key": "gk-bare-env", "source": "env"}


async def test_no_key_anywhere(ctx):
    assert await resolve_gemini_key(ctx, vault_key=VAULT_GEMINI_KEY) is None


# ------------------------------------------------------------------ minting


def _client_with(handler) -> GeminiLiveClient:
    gc = GeminiLiveClient("gk-test")
    gc._http = httpx.AsyncClient(
        base_url=gemini_live.DEFAULT_BASE_URL,
        transport=httpx.MockTransport(handler),
    )
    return gc


async def test_mint_happy_path():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"name": "auth_tokens/abc", "expireTime": "2099-01-01T00:00:00Z"})

    gc = _client_with(handler)
    minted = await gc.mint_token(setup_message(instructions="be luna"))
    await gc.close()
    assert minted["name"] == "auth_tokens/abc"
    assert seen["path"] == "/v1alpha/auth_tokens"
    # the constraint carries the FULL setup, locking it server-side
    body = seen["body"]
    assert body["uses"] == gemini_live.TOKEN_USES
    assert body["expireTime"] and body["newSessionExpireTime"]
    # newSessionExpireTime must span the whole call: Google forces a
    # resumption reconnect (= a NEW session on this token) every ~10 min, and
    # a short window kills the call there (phase-3 soak, 10.3 min, error
    # "new_session_expire_time deadline exceeded").
    assert body["newSessionExpireTime"] == body["expireTime"]
    assert body["bidiGenerateContentSetup"]["model"] == f"models/{DEFAULT_MODEL}"
    assert "be luna" in body["bidiGenerateContentSetup"]["systemInstruction"]["parts"][0]["text"]


@pytest.mark.parametrize(
    ("status", "needle"),
    [
        (401, "rejected the key"),
        (403, "rejected the key"),
        (429, "rate limit"),
        (500, "HTTP 500"),
    ],
)
async def test_mint_errors_are_speakable(status, needle):
    gc = _client_with(lambda request: httpx.Response(status, json={}))
    with pytest.raises(RealtimeError, match=needle):
        await gc.mint_token(setup_message(instructions="x"))
    await gc.close()


async def test_mint_400_surfaces_google_detail():
    gc = _client_with(lambda request: httpx.Response(
        400, json={"error": {"message": "Cannot find field bogus"}}
    ))
    with pytest.raises(RealtimeError, match="Cannot find field bogus"):
        await gc.mint_token(setup_message(instructions="x"))
    await gc.close()


async def test_mint_missing_name_rejected():
    gc = _client_with(lambda request: httpx.Response(200, json={}))
    with pytest.raises(RealtimeError, match="no session token"):
        await gc.mint_token(setup_message(instructions="x"))
    await gc.close()


# ------------------------------------------------------------ setup message


def test_setup_message_shape():
    setup = setup_message(
        instructions="be luna",
        voice="Kore",
        model="gemini-3.8-live-extended-thinking",
        tools=[{"type": "function", "name": "get_weather", "description": "d",
                "parameters": {"type": "object", "properties": {"city": {"type": "string"}},
                               "required": ["city"]}}],
        turn_eagerness="patient",
    )["setup"]
    assert setup["model"] == "models/gemini-3.8-live-extended-thinking"
    gen = setup["generationConfig"]
    assert gen["responseModalities"] == ["AUDIO"]
    assert gen["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"] == "Kore"
    assert setup["systemInstruction"]["parts"] == [{"text": "be luna"}]
    decls = setup["tools"][0]["functionDeclarations"]
    assert decls[0]["name"] == "get_weather"
    assert decls[0]["parameters"]["type"] == "OBJECT"
    assert decls[0]["parameters"]["properties"]["city"]["type"] == "STRING"
    vad = setup["realtimeInputConfig"]["automaticActivityDetection"]
    assert vad["startOfSpeechSensitivity"] == "START_SENSITIVITY_LOW"
    assert vad["silenceDurationMs"] == 800
    # the noise-robustness + long-call knobs are always on
    assert setup["proactivity"] == {"proactiveAudio": True}
    assert setup["outputAudioTranscription"] == {}
    assert setup["contextWindowCompression"]["slidingWindow"]
    assert setup["sessionResumption"] == {}


def test_setup_message_falls_back_on_unknown_values():
    setup = setup_message(
        instructions="x", voice="marin", model="gpt-realtime-2.1", turn_eagerness="bogus"
    )["setup"]
    assert setup["model"] == f"models/{DEFAULT_MODEL}"
    voice = setup["generationConfig"]["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"]
    assert voice == DEFAULT_VOICE
    vad = setup["realtimeInputConfig"]["automaticActivityDetection"]
    assert vad == gemini_live.VAD_PRESETS["normal"]


def test_vad_presets_keep_noise_out_except_eager():
    # LOW start sensitivity everywhere except eager: beeps and background
    # chatter must not open turns (the owner's core requirement).
    for name, preset in gemini_live.VAD_PRESETS.items():
        expected = "START_SENSITIVITY_HIGH" if name == "eager" else "START_SENSITIVITY_LOW"
        assert preset["startOfSpeechSensitivity"] == expected
    assert set(gemini_live.VAD_PRESETS) == {"patient", "normal", "eager"}


def test_convert_tools_shapes_and_empty_params():
    out = convert_tools([
        {"type": "function", "name": "a", "description": "da",
         "parameters": {"type": "object", "properties": {}}},
        {"type": "function", "name": "b", "description": "db",
         "parameters": {"type": "object", "properties": {
             "items": {"type": "array", "items": {"type": "integer"}}}}},
    ])
    assert len(out) == 1
    decls = out[0]["functionDeclarations"]
    assert decls[0] == {"name": "a", "description": "da"}  # empty schema omitted
    assert decls[1]["parameters"]["properties"]["items"]["type"] == "ARRAY"
    assert decls[1]["parameters"]["properties"]["items"]["items"]["type"] == "INTEGER"
    assert convert_tools([]) == []
    assert convert_tools(None) == []


def test_ws_url_is_the_constrained_method():
    # Tokens are only accepted by BidiGenerateContentConstrained on v1alpha —
    # the plain method refuses them ("unregistered callers").
    assert "v1alpha" in LIVE_WS_URL
    assert LIVE_WS_URL.endswith("BidiGenerateContentConstrained")


# ------------------------------------------------------------- instructions


def test_instructions_carry_persona_and_lane_rules():
    text = talker.build_instructions(
        persona_name="T-800",
        greeting="This is T-800. State your objective.",
        fillers=["Running tactical assessment... "],
        has_imprint=False,
    )
    assert "live voice of T-800" in text
    assert "State your objective" in text
    assert "Running tactical assessment" in text
    assert "luna_do" in text and "luna_task_status" in text
    assert "[task update]" in text
    assert "Never invent" in text
    # no imprint → no open-mic block
    assert "[voice check:" not in text


def test_instructions_open_mic_only_with_imprint():
    text = talker.build_instructions(persona_name="Luna", has_imprint=True)
    assert "[voice check:" in text


def test_instructions_carry_real_personality_and_mission():
    # The talker must receive the agent's ACTUAL character + mission, not just
    # its name — otherwise it's a generic assistant wearing the name.
    text = talker.build_instructions(
        persona_name="Nova",
        persona_brief="You are dry and terse. You never gush and you skip pleasantries.",
        mission="Keep the owner's infrastructure healthy.",
    )
    assert "dry and terse" in text
    assert "Keep the owner's infrastructure healthy." in text


def test_instructions_carry_quiet_discipline():
    # "be quiet" must map to actual silence, not a spoken acknowledgement.
    text = talker.build_instructions(persona_name="Luna")
    assert "stay silent" in text
    assert "do not acknowledge" in text.lower()


def test_instructions_carry_noise_and_interruption_demeanor():
    # A false barge-in (beep, cough) cuts Gemini's audio — the talker must
    # resume naturally, never comment on noise, never go quiet from it.
    text = talker.build_instructions(persona_name="Luna", has_imprint=False)
    assert "pick up naturally where you left off" in text
    assert "don't restart the whole sentence" in text
    assert "never go quiet just because the room is noisy" in text


def test_instructions_owner_style_and_extra_win():
    text = talker.build_instructions(
        persona_name="Luna",
        voice_style="Always answer in rhyme.",
        talker_extra="Never discuss the weather.",
        has_imprint=False,
    )
    assert "Always answer in rhyme." in text
    assert talker.VOICE_STYLE not in text
    assert text.strip().endswith("Never discuss the weather.")


# ------------------------------------------------------------- /rt/session


@pytest.fixture()
def rt():
    # conftest's autouse patch replaced GeminiLiveClient with FakeLive —
    # reading it off the module dodges an import-by-path of conftest itself.
    return gemini_live.GeminiLiveClient


def test_rt_session_requires_a_key(client, rt):
    resp = client.get("/api/p/plugin-voice/rt/session")
    assert resp.status_code == 400
    assert "Gemini key" in resp.json()["detail"]


def test_rt_session_happy_path(client, ctx, rt):
    ctx.vault.data[VAULT_GEMINI_KEY] = "gk-own"
    ctx.vault.data["plugin_voice.settings"] = json.dumps(
        {"persona_name": "T-800", "rt_voice": "Kore", "rt_model": "gemini-3.8-live-extended-thinking"}
    )
    resp = client.get("/api/p/plugin-voice/rt/session")
    assert resp.status_code == 200
    data = resp.json()
    assert data["access_token"] == "auth_tokens/test-token"
    assert data["ws_url"] == LIVE_WS_URL
    assert data["model"] == "gemini-3.8-live-extended-thinking"
    assert data["voice"] == "Kore"
    assert data["persona_name"] == "T-800"
    assert data["rt_token"]
    assert data["has_imprint"] is False and data["live_token"] is None
    # the payload carries the FULL setup for the client to send verbatim —
    # it must match what the token was constrained with
    setup = data["setup"]["setup"]
    assert setup == rt.minted[-1]["setup"]
    assert "live voice of T-800" in setup["systemInstruction"]["parts"][0]["text"]
    assert setup["generationConfig"]["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"] == "Kore"
    # the plugin token is armed for the relay surfaces
    from plugin_voice import state as live_state

    assert live_state.live_token_valid(data["rt_token"])
    # server key never reaches the browser payload
    assert "gk-own" not in json.dumps(data)
    # POST works the same (hosted widgets may prefer it)
    assert client.post("/api/p/plugin-voice/rt/session").status_code == 200


def test_rt_session_stale_openai_settings_fall_back(client, ctx, rt):
    """A migrated install still has 'marin'/'gpt-realtime-2.1' stored — the
    mint must fall back to Gemini defaults, never dead-end the call button."""
    ctx.vault.data[VAULT_GEMINI_KEY] = "gk-own"
    ctx.vault.data["plugin_voice.settings"] = json.dumps(
        {"rt_voice": "marin", "rt_model": "gpt-realtime-2.1"}
    )
    data = client.get("/api/p/plugin-voice/rt/session").json()
    assert data["model"] == DEFAULT_MODEL
    assert data["voice"] == DEFAULT_VOICE


def test_rt_session_live_token_when_imprinted(client, ctx, rt):
    ctx.vault.data[VAULT_GEMINI_KEY] = "gk-own"
    ctx.vault.data["plugin_voice.voice_profile"] = json.dumps({"profile": [0.1] * 8})
    data = client.get("/api/p/plugin-voice/rt/session").json()
    assert data["has_imprint"] is True
    assert data["live_token"] == data["rt_token"]
    setup = rt.minted[-1]["setup"]
    assert "[voice check:" in setup["systemInstruction"]["parts"][0]["text"]


def test_rt_session_mint_failure_is_400_with_detail(client, ctx, rt):
    """400, not 502 — hosted edges replace 5xx JSON bodies with HTML pages,
    which would hide the actionable message from the widget."""
    ctx.vault.data[VAULT_GEMINI_KEY] = "gk-own"
    rt.fail_mint = True
    resp = client.get("/api/p/plugin-voice/rt/session")
    assert resp.status_code == 400
    assert "rejected the key" in resp.json()["detail"]
    assert rt.instances and rt.instances[-1].closed  # client closed on failure too


def test_rt_session_no_persona_fetch(client, ctx, rt):
    """Session minting must never block on the ~30s persona LLM fetch."""
    ctx.vault.data[VAULT_GEMINI_KEY] = "gk-own"
    client.get("/api/p/plugin-voice/rt/session")
    assert ctx.agent.calls == []
