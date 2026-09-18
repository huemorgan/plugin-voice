"""004/005 — live identity + the Voice Persona settings surface.

Covers: the override merge (owner > auto persona > shipped default), the
GET/PUT routes (validation, no upstream writes — everything applies at the
next /rt/session mint), the prompt overrides reaching the talker
instructions, and persona resync keeping owner overrides.
"""

from __future__ import annotations

import json

import anyio
import pytest

from plugin_voice import (
    VAULT_GEMINI_KEY,
    VAULT_SETTINGS,
    persona_config,
    setup,
    talker,
)
from plugin_voice.persona_config import PersonaConfigError

from tests.conftest import FakeLive  # noqa: E402 — shared fake, autouse-patched

API = "/api/p/plugin-voice"


# ------------------------------------------------------------ merge/validation


def test_effective_defaults_match_shipped_constants():
    eff = persona_config.effective({})
    assert eff["voice_system_prompt"] is None   # None → talker.VOICE_STYLE applies
    assert eff["talker_extra"] is None
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
        {"fillers": "not a list"},
        {"fillers": []},
        {"greeting": "   "},
        {"greeting": "x" * 301},
        {"unknown_field": "x"},
        {"soft_timeout_seconds": 8},  # dropped in 0.5.0 — now unknown
    ):
        with pytest.raises(PersonaConfigError):
            persona_config.apply_changes({}, bad)


def test_apply_changes_none_clears_override():
    settings = {persona_config.OVERRIDES_KEY: {"greeting": "Owner greeting"}}
    settings, changed = persona_config.apply_changes(settings, {"greeting": None})
    assert changed == {"greeting"}
    assert "greeting" not in persona_config.overrides_of(settings)


def test_stale_050_dropped_fields_in_stored_overrides_are_ignored():
    """A 0.4.x install carries triage/timeout overrides; the merge must not
    surface them, and current fields still resolve."""
    settings = {
        persona_config.OVERRIDES_KEY: {
            "triage_enabled": False,
            "soft_timeout_seconds": 8.0,
            "greeting": "Still mine.",
        }
    }
    eff = persona_config.effective(settings)
    assert eff["greeting"] == "Still mine."
    assert "triage_enabled" not in eff
    assert "soft_timeout_seconds" not in eff


# ------------------------------------------------------------------ the routes


async def _prewire(ctx, overrides: dict | None = None):
    """A connected install with a persona snapshot in settings."""
    await ctx.vault.store_credential(VAULT_GEMINI_KEY, "gk_test_not_real", kind="api_key")
    settings = {
        "persona_name": "Nova",
        "greeting": "Hi, Nova here!",
        "fillers": ["On it... "],
        "rt_voice": "Kore",
    }
    if overrides:
        settings[persona_config.OVERRIDES_KEY] = overrides
    await ctx.vault.store_credential(VAULT_SETTINGS, json.dumps(settings), kind="config")


def test_get_persona_settings_shape(client, ctx):
    anyio.run(_prewire, ctx)
    data = client.get(f"{API}/persona-settings").json()
    assert data["values"]["greeting"] == "Hi, Nova here!"   # auto layer
    assert data["auto"]["fillers"] == ["On it... "]
    assert data["overrides"] == {}
    assert data["defaults"]["turn_eagerness"] == "patient"
    assert data["persona_name"] == "Nova"
    assert data["rt_voice"] == "Kore"
    assert set(data["turn_eagerness_values"]) == {"eager", "normal", "patient"}


def test_put_persona_settings_saves(client, ctx):
    anyio.run(_prewire, ctx)
    resp = client.put(
        f"{API}/persona-settings",
        json={"greeting": "Yo, it's me.", "turn_eagerness": "normal"},
    )
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["saved"]
    assert set(out["changed"]) == {"greeting", "turn_eagerness"}
    assert out["values"]["greeting"] == "Yo, it's me."       # override beats auto
    assert "applied_to_agent" not in out                     # no upstream to patch

    stored = json.loads(ctx.vault.data[VAULT_SETTINGS])
    assert stored[persona_config.OVERRIDES_KEY]["greeting"] == "Yo, it's me."


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


# ------------------------------------------- overrides reach the minted session


def _mint(client):
    resp = client.get(f"{API}/rt/session")
    assert resp.status_code == 200, resp.text
    assert FakeLive.minted, "no setup was minted"
    return FakeLive.minted[-1]["setup"]


def test_custom_voice_prompt_reaches_talker_instructions(client, ctx):
    anyio.run(_prewire, ctx, {"voice_system_prompt": "Talk like a pirate."})
    session = _mint(client)
    text = session["systemInstruction"]["parts"][0]["text"]
    assert "Talk like a pirate." in text
    assert talker.VOICE_STYLE not in text  # replaced, not appended


def test_talker_extra_lands_last_in_instructions(client, ctx):
    anyio.run(_prewire, ctx, {"talker_extra": "Always answer in French."})
    session = _mint(client)
    text = session["systemInstruction"]["parts"][0]["text"]
    assert text.rstrip().endswith("Always answer in French.")


def test_turn_eagerness_override_maps_to_semantic_vad(client, ctx):
    anyio.run(_prewire, ctx, {"turn_eagerness": "eager"})
    session = _mint(client)
    vad = session["realtimeInputConfig"]["automaticActivityDetection"]
    assert vad["startOfSpeechSensitivity"] == "START_SENSITIVITY_HIGH"
    assert vad["silenceDurationMs"] == 450


def test_greeting_override_reaches_instructions(client, ctx):
    anyio.run(_prewire, ctx, {"greeting": "Owner greeting."})
    session = _mint(client)
    text = session["systemInstruction"]["parts"][0]["text"]
    assert "Owner greeting." in text
    assert "Hi, Nova here!" not in text


# -------------------------------------------------- live identity + resync


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


def test_session_uses_live_name_after_rename(client, ctx):
    """Rename in plugin-identity → the NEXT minted session already speaks as
    the new name; no upstream agent to re-patch anymore."""
    anyio.run(_prewire, ctx)
    ctx.config_registry = _FakeConfigRegistry({"name": "Rayla"})
    resp = client.get(f"{API}/rt/session")
    assert resp.status_code == 200
    assert resp.json()["persona_name"] == "Rayla"
    assert "the live voice of Rayla" in FakeLive.minted[-1]["setup"]["systemInstruction"]["parts"][0]["text"]


def test_refresh_persona_route_updates_snapshot(client, ctx):
    anyio.run(_prewire, ctx)

    async def run_turn(prompt, **kw):
        if kw.get("output_schema"):
            return ({"name": "Rayla", "greeting": "Rayla here — speak.",
                     "fillers": ["hold on"], "voice_description": ""}, None)
        return ("ok", None)

    ctx.agent.run_turn = run_turn
    resp = client.post(f"{API}/refresh-persona")
    assert resp.status_code == 200, resp.text
    stored = json.loads(ctx.vault.data[VAULT_SETTINGS])
    assert stored["persona_name"] == "Rayla"
    assert stored["greeting"] == "Rayla here — speak."


def test_resync_persona_keeps_owner_overrides(ctx):
    """Resync must not clobber the owner's saved greeting override or an
    explicit voice pick."""
    async def scenario():
        await _prewire(ctx, overrides={"greeting": "Owner greeting."})

        async def run_turn(prompt, **kw):
            if kw.get("output_schema"):
                return ({"name": "Rayla", "greeting": "Rayla here.",
                         "fillers": ["hm"], "voice_description": ""}, None)
            return ("ok", None)

        ctx.agent.run_turn = run_turn
        await setup.resync_persona(ctx)

    anyio.run(scenario)
    stored = json.loads(ctx.vault.data[VAULT_SETTINGS])
    assert stored["persona_name"] == "Rayla"             # snapshot converges
    assert stored["rt_voice"] == "Kore"                  # explicit pick kept
    assert stored[persona_config.OVERRIDES_KEY]["greeting"] == "Owner greeting."
    # and the merge still favors the owner:
    assert persona_config.effective(stored)["greeting"] == "Owner greeting."
