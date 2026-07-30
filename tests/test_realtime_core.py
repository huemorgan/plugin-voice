"""Phase 01 (005): OpenAI key resolution, client-secret minting, talker
instructions, /rt/session."""

from __future__ import annotations

import json

import httpx
import pytest

from plugin_voice import VAULT_OPENAI_KEY, openai_realtime, talker
from plugin_voice.openai_realtime import (
    DEFAULT_MODEL,
    DEFAULT_VOICE,
    RealtimeClient,
    RealtimeError,
    resolve_openai_key,
    session_config,
)


@pytest.fixture(autouse=True)
def _no_env_keys(monkeypatch):
    monkeypatch.delenv("LUNA_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


# ---------------------------------------------------------------- key chain


async def test_pasted_key_wins(ctx, monkeypatch):
    ctx.vault.data[VAULT_OPENAI_KEY] = "sk-own"
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    res = await resolve_openai_key(ctx, vault_key=VAULT_OPENAI_KEY)
    assert res == {"api_key": "sk-own", "source": "own"}


async def test_gateway_connection_used(ctx):
    from luna_sdk import AuthSpec, Connection

    ctx.vault.gateway_connection = Connection(
        base_url="https://gw.example/openai",
        secret="virt-123",
        auth=AuthSpec(location="header", name="Authorization", scheme="Bearer"),
        source="virtual",
    )
    res = await resolve_openai_key(ctx, vault_key=VAULT_OPENAI_KEY)
    assert res["source"] == "gateway"
    assert res["base_url"] == "https://gw.example/openai"
    assert res["headers"]["Authorization"] == "Bearer virt-123"


async def test_env_keys_both_spellings(ctx, monkeypatch):
    ctx.get_env = lambda name: "sk-luna-env" if name == "LUNA_OPENAI_API_KEY" else None
    res = await resolve_openai_key(ctx, vault_key=VAULT_OPENAI_KEY)
    assert res == {"api_key": "sk-luna-env", "source": "env"}

    ctx.get_env = lambda name: None
    monkeypatch.setenv("OPENAI_API_KEY", "sk-bare-env")
    res = await resolve_openai_key(ctx, vault_key=VAULT_OPENAI_KEY)
    assert res == {"api_key": "sk-bare-env", "source": "env"}


async def test_no_key_anywhere(ctx):
    assert await resolve_openai_key(ctx, vault_key=VAULT_OPENAI_KEY) is None


# ------------------------------------------------------------------ minting


def _client_with(handler) -> RealtimeClient:
    rc = RealtimeClient("sk-test")
    rc._http = httpx.AsyncClient(
        base_url="https://api.openai.com",
        transport=httpx.MockTransport(handler),
    )
    return rc


async def test_mint_happy_path():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"value": "ek_abc", "expires_at": 123, "session": {}})

    rc = _client_with(handler)
    minted = await rc.mint_client_secret({"type": "realtime", "model": DEFAULT_MODEL})
    await rc.close()
    assert minted["value"] == "ek_abc"
    assert seen["path"] == "/v1/realtime/client_secrets"
    assert seen["body"]["session"]["model"] == DEFAULT_MODEL


@pytest.mark.parametrize(
    ("status", "needle"),
    [(401, "rejected the key"), (404, "may not support Realtime"), (500, "HTTP 500")],
)
async def test_mint_errors_are_speakable(status, needle):
    rc = _client_with(lambda request: httpx.Response(status, json={}))
    with pytest.raises(RealtimeError, match=needle):
        await rc.mint_client_secret({"type": "realtime"})
    await rc.close()


async def test_mint_missing_value_rejected():
    rc = _client_with(lambda request: httpx.Response(200, json={"session": {}}))
    with pytest.raises(RealtimeError, match="no client secret"):
        await rc.mint_client_secret({"type": "realtime"})
    await rc.close()


# ----------------------------------------------------------- session config


def test_session_config_shape():
    cfg = session_config(
        instructions="be luna", voice="cedar", model="gpt-realtime-2.1-mini",
        tools=[{"type": "function", "name": "x"}], turn_eagerness="patient",
    )
    assert cfg["type"] == "realtime"
    assert cfg["model"] == "gpt-realtime-2.1-mini"
    assert cfg["instructions"] == "be luna"
    assert cfg["tools"] == [{"type": "function", "name": "x"}]
    assert cfg["audio"]["input"]["turn_detection"] == {"type": "semantic_vad", "eagerness": "low"}
    assert cfg["audio"]["output"]["voice"] == "cedar"


def test_session_config_falls_back_on_unknown_values():
    cfg = session_config(instructions="x", voice="not-a-voice", model="gpt-5-imaginary")
    assert cfg["model"] == DEFAULT_MODEL
    assert cfg["audio"]["output"]["voice"] == DEFAULT_VOICE
    assert cfg["audio"]["input"]["turn_detection"]["eagerness"] == "auto"


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


class FakeRT:
    """Stands in for RealtimeClient in routes — no network."""

    instances: list["FakeRT"] = []
    fail: RealtimeError | None = None
    last_session: dict | None = None

    def __init__(self, api_key=None, **kw):
        self.api_key = api_key
        self.kwargs = kw
        self.closed = False
        FakeRT.instances.append(self)

    async def mint_client_secret(self, session):
        if FakeRT.fail is not None:
            raise FakeRT.fail
        FakeRT.last_session = session
        return {"value": "ek_test", "expires_at": 42, "session": session}

    async def close(self):
        self.closed = True


@pytest.fixture()
def rt(monkeypatch):
    FakeRT.instances = []
    FakeRT.fail = None
    FakeRT.last_session = None
    monkeypatch.setattr(openai_realtime, "RealtimeClient", FakeRT)
    return FakeRT


def test_rt_session_requires_a_key(client, rt):
    resp = client.get("/api/p/plugin-voice/rt/session")
    assert resp.status_code == 400
    assert "OpenAI key" in resp.json()["detail"]


def test_rt_session_happy_path(client, ctx, rt):
    ctx.vault.data[VAULT_OPENAI_KEY] = "sk-own"
    ctx.vault.data["plugin_voice.settings"] = json.dumps(
        {"persona_name": "T-800", "rt_voice": "cedar", "rt_model": "gpt-realtime-2.1-mini"}
    )
    resp = client.get("/api/p/plugin-voice/rt/session")
    assert resp.status_code == 200
    data = resp.json()
    assert data["client_secret"] == "ek_test"
    assert data["model"] == "gpt-realtime-2.1-mini"
    assert data["voice"] == "cedar"
    assert data["persona_name"] == "T-800"
    assert data["webrtc_url"].startswith("https://api.openai.com/")
    assert data["rt_token"]
    assert data["has_imprint"] is False and data["live_token"] is None
    # minted session carries the talker instructions and persona voice
    assert "live voice of T-800" in FakeRT.last_session["instructions"]
    assert FakeRT.last_session["audio"]["output"]["voice"] == "cedar"
    # the plugin token is armed for the relay surfaces
    from plugin_voice import state as live_state

    assert live_state.live_token_valid(data["rt_token"])
    # server key never reaches the browser payload
    assert "sk-own" not in json.dumps(data)
    # POST works the same (hosted widgets may prefer it)
    assert client.post("/api/p/plugin-voice/rt/session").status_code == 200


def test_rt_session_live_token_when_imprinted(client, ctx, rt):
    ctx.vault.data[VAULT_OPENAI_KEY] = "sk-own"
    ctx.vault.data["plugin_voice.voice_profile"] = json.dumps({"profile": [0.1] * 8})
    data = client.get("/api/p/plugin-voice/rt/session").json()
    assert data["has_imprint"] is True
    assert data["live_token"] == data["rt_token"]
    assert "[voice check:" in FakeRT.last_session["instructions"]


def test_rt_session_mint_failure_is_502(client, ctx, rt):
    ctx.vault.data[VAULT_OPENAI_KEY] = "sk-own"
    rt.fail = RealtimeError("OpenAI rejected the key (HTTP 401) — check it in Settings → Voice")
    resp = client.get("/api/p/plugin-voice/rt/session")
    assert resp.status_code == 502
    assert "rejected the key" in resp.json()["detail"]
    assert rt.instances and rt.instances[-1].closed  # client closed on failure too


def test_rt_session_no_persona_fetch(client, ctx, rt):
    """Session minting must never block on the ~30s persona LLM fetch."""
    ctx.vault.data[VAULT_OPENAI_KEY] = "sk-own"
    client.get("/api/p/plugin-voice/rt/session")
    assert ctx.agent.calls == []
