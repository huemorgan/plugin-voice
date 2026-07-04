"""plugin-voice — talk to Luna by voice in the browser.

ElevenLabs Agents handles the audio loop (mic, STT, TTS, barge-in) between the
browser and their edge; Luna stays the brain via an OpenAI-compatible bridge
route this plugin serves (see `bridge.py` / `routes.py`). Authored against
`luna_sdk` only.
"""

from __future__ import annotations

import logging

from luna_sdk import LunaPlugin, PluginContext, PluginManifest, SettingsTab

log = logging.getLogger("plugin-voice")

# Vault keys (all owned by this plugin; ACL-scoped by the vault provider).
VAULT_API_KEY = "plugin_voice.elevenlabs_api_key"
VAULT_AGENT_ID = "plugin_voice.agent_id"
VAULT_BRIDGE_SECRET = "plugin_voice.bridge_secret"
VAULT_SETTINGS = "plugin_voice.settings"  # non-secret JSON (voice_id, ...); vault used as the plugin's durable KV


class VoicePlugin(LunaPlugin):
    manifest = PluginManifest(
        name="plugin-voice",
        shown_name="Voice",
        icon="mic",
        version="0.2.1",
        description=(
            "Voice conversations that know who is speaking — owner voice "
            "imprint, personality-matched voice and fillers, ElevenLabs "
            "audio, Luna stays the brain."
        ),
        category="global",
        depends_on=["plugin-vault"],
        routes_module="routes",
        settings_tabs=[
            SettingsTab(
                id="voice",
                label="Voice",
                icon="mic",
                sort_order=75,
                iframe_src="/api/p/plugin-voice/ui/settings/",
            ),
        ],
        # WidgetSlot isn't re-exported from luna_sdk yet (only SettingsTab is);
        # PluginManifest.widgets is pydantic-validated, so a plain dict works.
        widgets=[
            {"id": "voice", "slot": "sidebar.bottom", "label": "Voice", "height": 90},
        ],
    )

    async def on_load(self, ctx: PluginContext) -> None:
        # Everything lives in routes (bridge + session + settings + static UI).
        # No agent tools are registered — the plugin is an interface, not a
        # capability; the bridge turn borrows the owner's installed tools.
        log.info("plugin-voice loaded (widget=talk, settings tab=talk)")

    async def on_unload(self) -> None:
        from .state import close_client

        await close_client()
