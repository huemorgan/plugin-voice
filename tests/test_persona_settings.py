"""004 — live identity + the Voice Persona settings surface.

Covers: the override merge (owner > auto persona > shipped default), the
GET/PUT routes (validation, ElevenLabs re-PATCH on voice-side changes), the
prompt overrides reaching the bridge turn/triage, and the automatic persona
resync when the agent's live identity name no longer matches the name the
greeting was generated for.
"""

from __future__ import annotations

import json

import anyio
import pytest

from plugin_voice import (
    VAULT_AGENT_ID,
    VAULT_API_KEY,
    VAULT_BRIDGE_SECRET,
    VAULT_SETTINGS,
    persona_config,
    setup,
)
from plugin_voice.persona_config import PersonaConfigError

from tests.conftest import FakeEL  # noqa: E402 — shared fake, autouse-patched

API = "/api/p/plugin-voice"


# ------------------------------------------------------------ merge/validation


def test_effective_defaults_match_shipped_constants():
    from plugin_voice import bridge

    eff = persona_config.effective({})
    assert eff["voice_system_prompt"] == bridge.VOICE_SYSTEM_PROMPT
    assert eff["triage_system"] == bridge.TRIAGE_SYSTEM
    assert eff["triage_enabled"] is True
    assert eff["soft_timeout_seconds"] == 5.0
    assert eff["max_soft_timeouts"] == 3
    assert eff["turn_eagerness"] == "patient"
    assert eff["greeting"] == persona_config.NEUTRAL_GREETING
    assert eff["fillers"] == persona_config.NEUTRAL_FILLERS


def test_effective_precedence_override_beats_auto_beats_default():
    settings = {
        "greeting": "Auto greeting",
        "fillers": ["auto one...", "auto two..."],
        persona_config.OVERRIDES_KEY: {"greeting": "Owner greeting"},
    }
    eff = persona_config.effective(settings)
    assert eff["greeting"] == "Owner greeting"      # override wins
    assert eff["fillers"] == ["auto one...", "auto two..."]  # auto wins over default


def test_apply_changes_validates():
    for bad in (
        {"turn_eagerness": "hyper"},
        {"soft_timeout_seconds": 99},
        {"max_soft_timeouts": -1},
        {"max_soft_timeouts": 2.5},
        {"triage_enabled": "yes"},
        {"fillers": "not a list"},
        {"fillers": []},
        {"greeting": "   "},
        {"unknown_field": "x"},
    ):
        with pytest.raises(PersonaConfigError):
            persona_config.apply_changes({}, bad)


def test_apply_changes_none_clears_override():
    settings = {persona_config.OVERRIDES_KEY: {"greeting": "Owner greeting"}}
    settings, changed = persona_config.apply_changes(settings, {"greeting": None})
    assert changed == {"greeting"}
    assert "greeting" not in persona_config.overrides_of(settings)


# ------------------------------------------------------------------ the routes


async def _prewire(ctx, *, overrides: dict | None = None):
    """A connected, provisioned install with the current config stamp."""
    from plugin_voice.elevenlabs import ElevenLabsClient

    await ctx.vault.store_credential(VAULT_API_KEY, "sk_test_not_real", kind="api_key")
    await ctx.vault.store_credential(VAULT_AGENT_ID, "agent_x", kind="config")
    await ctx.vault.store_credential(VAULT_BRIDGE_SECRET, "s" * 32, kind="api_key")
    settings = {
        "persona_name": "Nova",
        "greeting": "Hi, Nova here!",
        "fillers": ["On it... "],
        "voice_id": "v-luna",
        "agent_config_v": ElevenLabsClient.AGENT_CONFIG_V,
    }
    if overrides:
        settings[persona_config.OVERRIDES_KEY] = overrides
    await ctx.vault.store_credential(VAULT_SETTINGS, json.dumps(settings), kind="config")
    FakeEL.bridge_urls["agent_x"] = "https://example.com/api/p/plugin-voice/v1"
    FakeEL.bridge_headers["agent_x"] = {}


def test_get_persona_settings_shape(client, ctx):
    anyio.run(_prewire, ctx)
    data = client.get(f"{API}/persona-settings").json()
    assert data["values"]["greeting"] == "Hi, Nova here!"   # auto layer
    assert data["auto"]["fillers"] == ["On it... "]
    assert data["overrides"] == {}
    assert data["defaults"]["turn_eagerness"] == "patient"
    assert data["persona_name"] == "Nova"
    assert data["voice_id"] == "v-luna"
    assert set(data["turn_eagerness_values"]) == {"eager", "normal", "patient"}


def test_put_persona_settings_saves_and_repatches_agent(client, ctx):
    anyio.run(_prewire, ctx)
    resp = client.put(
        f"{API}/persona-settings",
        json={
            "greeting": "Yo, it's me.",
            "soft_timeout_seconds": 8,
            "turn_eagerness": "normal",
        },
    )
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["saved"] and out["applied_to_agent"]
    assert set(out["changed"]) == {"greeting", "soft_timeout_seconds", "turn_eagerness"}

    patch = FakeEL.agent_configs[-1]
    assert patch["op"] == "update" and patch["agent_id"] == "agent_x"
    assert patch["first_message"] == "Yo, it's me."          # override beats auto
    assert patch["fillers"] == ["On it... "]                  # auto kept
    assert patch["overrides"]["soft_timeout_seconds"] == 8.0
    assert patch["overrides"]["turn_eagerness"] == "normal"

    stored = json.loads(ctx.vault.data[VAULT_SETTINGS])
    assert stored[persona_config.OVERRIDES_KEY]["greeting"] == "Yo, it's me."


def test_put_prompt_only_change_does_not_patch_agent(client, ctx):
    anyio.run(_prewire, ctx)
    resp = client.put(f"{API}/persona-settings", json={"voice_system_prompt": "Talk like a pirate."})
    assert resp.status_code == 200
    assert resp.json()["applied_to_agent"] is False
    assert FakeEL.agent_configs == []  # bridge-side only — no ElevenLabs write


def test_put_persona_settings_rejects_bad_values(client, ctx):
    anyio.run(_prewire, ctx)
    resp = client.put(f"{API}/persona-settings", json={"turn_eagerness": "hyper"})
    assert resp.status_code == 400
    assert "turn_eagerness" in resp.json()["detail"]
    assert persona_config.OVERRIDES_KEY not in json.loads(ctx.vault.data[VAULT_SETTINGS])


def test_persona_ui_page_served(client):
    html = client.get(f"{API}/ui/settings/persona/").text
    assert 'data-testid="persona-save"' in html
    assert "/persona-settings" in html
    # hosted-tenant safe: API base derived from the iframe's own pathname
    assert "location.pathname.split" in html


# ------------------------------------------------- overrides reach the bridge


def _wire_bridge(ctx, *, overrides: dict | None = None, run_llm=None):
    ctx.vault.data[VAULT_BRIDGE_SECRET] = "s" * 32
    settings: dict = {"greeting": "Hi!", "fillers": ["Working... "]}
    if overrides:
        settings[persona_config.OVERRIDES_KEY] = overrides
    ctx.vault.data[VAULT_SETTINGS] = json.dumps(settings)
    if run_llm is not None:
        ctx.agent.run_llm = run_llm


def _chat(client, text="What time is it?"):
    return client.post(
        f"{API}/v1/chat/completions",
        headers={"Authorization": "Bearer " + "s" * 32},
        json={"messages": [{"role": "user", "content": text}], "stream": True},
    )


def test_custom_voice_prompt_reaches_run_turn(client, ctx):
    _wire_bridge(ctx, overrides={"voice_system_prompt": "Talk like a pirate."})
    resp = _chat(client)
    assert resp.status_code == 200
    prompt = ctx.agent.calls[-1]["prompt"]
    assert prompt.startswith("Talk like a pirate.")
    assert "real-time VOICE conversation" not in prompt


def test_custom_triage_prompt_reaches_run_llm(client, ctx):
    seen = {}

    async def run_llm(prompt, *, system=None, **kw):
        seen["system"] = system
        return "RESPOND"

    _wire_bridge(ctx, overrides={"triage_system": "Custom gate rules."}, run_llm=run_llm)
    assert _chat(client).status_code == 200
    assert seen["system"] == "Custom gate rules."


def test_triage_disabled_skips_the_gate(client, ctx):
    async def run_llm(prompt, **kw):  # would SKIP everything
        return "SKIP"

    _wire_bridge(ctx, overrides={"triage_enabled": False}, run_llm=run_llm)
    resp = _chat(client)
    assert resp.status_code == 200
    assert ctx.agent.calls  # the full turn ran — triage never got a veto


# -------------------------------------------------- live identity + auto resync


class _IdentitySection:
    def __init__(self, values: dict):
        self.values = values

    async def reader(self):
        return self.values


class _FakeConfigRegistry:
    def __init__(self, identity: dict):
        self._identity = _IdentitySection(identity)

    def get(self, section_id: str):
        return self._identity if section_id == "identity" else None


def test_status_reports_live_name_over_snapshot(client, ctx):
    anyio.run(_prewire, ctx)
    ctx.config_registry = _FakeConfigRegistry({"name": "Rayla"})
    data = client.get(f"{API}/status").json()
    assert data["persona_name"] == "Rayla"  # live identity, not the "Nova" snapshot


def test_session_resyncs_persona_after_rename(client, ctx):
    """Rename in plugin-identity → /session re-fetches the persona and
    re-PATCHes the ElevenLabs agent so the NEXT call greets with the new name."""
    from plugin_voice import state as live_state

    anyio.run(_prewire, ctx)
    ctx.config_registry = _FakeConfigRegistry({"name": "Rayla"})

    async def run_turn(prompt, **kw):
        if kw.get("output_schema"):
            return ({"name": "Rayla", "greeting": "Rayla here — speak.",
                     "fillers": ["hold on"], "voice_description": ""}, None)
        return ("ok", None)

    ctx.agent.run_turn = run_turn
    live_state.end_resync()  # clean slate

    resp = client.get(f"{API}/session")
    assert resp.status_code == 200
    assert resp.json()["persona_name"] == "Rayla"  # live name immediately

    task = live_state.resync_task()
    assert task is not None  # mismatch detected → resync scheduled

    async def _wait():
        await task

    client.portal.call(_wait)

    patch = FakeEL.agent_configs[-1]
    assert patch["op"] == "update" and patch["agent_id"] == "agent_x"
    assert patch["first_message"] == "Rayla here — speak."

    stored = json.loads(ctx.vault.data[VAULT_SETTINGS])
    assert stored["persona_name"] == "Rayla"
    assert live_state.try_begin_resync()  # guard released after the task ended
    live_state.end_resync()


def test_session_no_resync_when_name_matches(client, ctx):
    anyio.run(_prewire, ctx)
    ctx.config_registry = _FakeConfigRegistry({"name": "Nova"})
    resp = client.get(f"{API}/session")
    assert resp.status_code == 200
    assert FakeEL.agent_configs == []  # nothing to fix, no ElevenLabs writes


def test_resync_persona_keeps_owner_overrides(ctx):
    """The background resync must not clobber the owner's saved greeting."""
    async def scenario():
        await _prewire(ctx, overrides={"greeting": "Owner greeting."})

        async def run_turn(prompt, **kw):
            if kw.get("output_schema"):
                return ({"name": "Rayla", "greeting": "Rayla here.",
                         "fillers": ["hm"], "voice_description": ""}, None)
            return ("ok", None)

        ctx.agent.run_turn = run_turn
        await setup.resync_persona(ctx, FakeEL(), "agent_x")

    anyio.run(scenario)
    patch = FakeEL.agent_configs[-1]
    assert patch["first_message"] == "Owner greeting."   # override survives
    stored = json.loads(ctx.vault.data[VAULT_SETTINGS])
    assert stored["persona_name"] == "Rayla"             # snapshot converges
