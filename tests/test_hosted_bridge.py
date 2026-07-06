"""Hosted (Fly) bridge routing — the session-close fix.

On hosted tenants the browser-facing base (``https://luna.com.ai/a/{slug}``)
sits behind a cookie-authed proxy, so an ElevenLabs server→server call to the
bridge gets 401/404 and the voice session dies on the first question. The fix:
point the agent's custom LLM at the shared Fly app hostname and pin the
machine with ``fly-force-instance-id`` — and self-heal stale agents on
session open.
"""

from __future__ import annotations

import json

from plugin_voice import VAULT_AGENT_ID, VAULT_API_KEY, VAULT_BRIDGE_SECRET, VAULT_SETTINGS
from plugin_voice.elevenlabs import ElevenLabsClient
from plugin_voice.setup import hosted_bridge

from tests.conftest import FakeEL  # noqa: E402 — shared fake, autouse-patched

FLY_URL = "https://luna-agents.fly.dev/api/p/plugin-voice/v1"
FLY_PIN = {"fly-force-instance-id": "machine-1"}


def _fly_env(monkeypatch):
    monkeypatch.setenv("FLY_APP_NAME", "luna-agents")
    monkeypatch.setenv("FLY_MACHINE_ID", "machine-1")


def _connect(client, **extra):
    resp = client.post(
        "/api/p/plugin-voice/connect",
        json={"api_key": "sk_test_not_real", **extra},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_hosted_bridge_absent_off_fly():
    assert hosted_bridge() is None


def test_hosted_bridge_reads_fly_env(monkeypatch):
    _fly_env(monkeypatch)
    assert hosted_bridge() == (FLY_URL, FLY_PIN)


def test_connect_on_fly_uses_direct_machine_url(client, ctx, monkeypatch):
    """The agent must NEVER be pointed at the cookie-authed tenant proxy."""
    _fly_env(monkeypatch)
    _connect(client)
    cfg = FakeEL.agent_configs[-1]
    assert cfg["url"] == FLY_URL
    assert cfg["request_headers"] == FLY_PIN


def test_connect_off_fly_keeps_public_base(client, ctx):
    _connect(client)
    cfg = FakeEL.agent_configs[-1]
    assert cfg["url"].endswith("/api/p/plugin-voice/v1")
    assert "fly.dev" not in cfg["url"]
    assert not cfg.get("request_headers")


async def _prewire(
    ctx,
    url: str,
    headers: dict | None = None,
    config_v: int | None = ElevenLabsClient.AGENT_CONFIG_V,
):
    """A previously provisioned agent, as a broken/working install would have it.

    ``config_v``: the agent_config_v stamp in stored settings. Defaults to
    current (no migration due); pass None for an unstamped pre-v2 install.
    """
    await ctx.vault.store_credential(VAULT_API_KEY, "sk_test_not_real", kind="api_key")
    await ctx.vault.store_credential(VAULT_AGENT_ID, "agent_x", kind="config")
    await ctx.vault.store_credential(VAULT_BRIDGE_SECRET, "s" * 32, kind="api_key")
    settings = {"persona_name": "Nova", "greeting": "Hi!", "voice_id": "v-luna"}
    if config_v is not None:
        settings["agent_config_v"] = config_v
    await ctx.vault.store_credential(VAULT_SETTINGS, json.dumps(settings), kind="config")
    FakeEL.bridge_urls["agent_x"] = url
    FakeEL.bridge_headers["agent_x"] = headers or {}


def test_session_heals_stale_bridge_on_fly(client, ctx, monkeypatch):
    """Installs provisioned before this fix repair themselves on session open."""
    import anyio

    anyio.run(_prewire, ctx, "https://luna.com.ai/api/p/plugin-voice/v1")
    _fly_env(monkeypatch)
    resp = client.get("/api/p/plugin-voice/session")
    assert resp.status_code == 200
    heal = FakeEL.agent_configs[-1]
    assert heal["op"] == "update" and heal["agent_id"] == "agent_x"
    assert heal["url"] == FLY_URL
    assert heal["request_headers"] == FLY_PIN
    # persona survives the heal — same greeting/voice, only the wiring changes
    assert heal["first_message"] == "Hi!" and heal["voice_id"] == "v-luna"


def test_session_does_not_patch_when_bridge_is_current(client, ctx, monkeypatch):
    import anyio

    anyio.run(_prewire, ctx, FLY_URL, dict(FLY_PIN))
    _fly_env(monkeypatch)
    resp = client.get("/api/p/plugin-voice/session")
    assert resp.status_code == 200
    assert FakeEL.agent_configs == []  # no needless ElevenLabs writes


def test_session_off_fly_leaves_agent_alone(client, ctx):
    import anyio

    anyio.run(_prewire, ctx, "https://example.com/api/p/plugin-voice/v1")
    resp = client.get("/api/p/plugin-voice/session")
    assert resp.status_code == 200
    assert FakeEL.agent_configs == []


def test_session_migrates_unstamped_agent_config_once(client, ctx):
    """Pre-v2 installs (no agent_config_v stamp) get exactly one config PATCH
    on /session — same bridge wiring, new config shape — then never again."""
    import anyio

    anyio.run(_prewire, ctx, "https://example.com/api/p/plugin-voice/v1", None, None)
    resp = client.get("/api/p/plugin-voice/session")
    assert resp.status_code == 200
    assert len(FakeEL.agent_configs) == 1
    patch = FakeEL.agent_configs[-1]
    assert patch["op"] == "update" and patch["agent_id"] == "agent_x"
    # wiring preserved: same bridge url, persona intact
    assert patch["url"] == "https://example.com/api/p/plugin-voice/v1"
    assert patch["first_message"] == "Hi!" and patch["voice_id"] == "v-luna"

    # second session: settings now stamped — no further writes
    resp = client.get("/api/p/plugin-voice/session")
    assert resp.status_code == 200
    assert len(FakeEL.agent_configs) == 1


def test_widget_fetches_live_identity_name(client):
    """persona_name is a connect-time snapshot; the widget asks plugin-identity
    for the CURRENT name so a renamed bot shows up as itself."""
    html = client.get("/api/p/plugin-voice/ui/widgets/voice/").text
    assert "/api/p/plugin-identity/" in html
