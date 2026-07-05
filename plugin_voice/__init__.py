"""plugin-voice — talk to Luna by voice in the browser.

ElevenLabs Agents handles the audio loop (mic, STT, TTS, barge-in) between the
browser and their edge; Luna stays the brain via an OpenAI-compatible bridge
route this plugin serves (see `bridge.py` / `routes.py`). Authored against
`luna_sdk` only.
"""

from __future__ import annotations

import logging

from luna_sdk import LunaPlugin, PluginContext, PluginManifest, SettingsTab, ToolDef, ToolDef, ToolDef

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
        version="0.3.1",
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
        from . import setup

        # 003: the agent can check and COMPLETE the voice setup from chat —
        # "connect the voice plugin" just works once a key exists anywhere in
        # the chain (own/vault-grant/gateway/env). The key value never passes
        # through the agent; resolution happens server-side in setup.py.
        async def _voice_status() -> dict:
            try:
                st = await setup.build_status(ctx)
            except setup.SetupError as exc:
                return {"error": str(exc)}
            st.pop("bridge_secret", None)  # owner-only; never surface to the agent
            st["note"] = (
                "connected=key resolvable (source in key_source: own/vault/"
                "gateway/env); agent_ready=ElevenLabs agent provisioned. If "
                "connected but not agent_ready, call voice_connect to finish."
            )
            return st

        async def _voice_connect() -> dict:
            try:
                st = await setup.do_connect(ctx)
            except setup.SetupError as exc:
                return {"connected": False, "error": str(exc)}
            st.pop("bridge_secret", None)
            st["note"] = "Voice setup complete — the owner can talk via the sidebar Voice widget."
            return st

        ctx.tool_registry.register(
            self.manifest.name,
            ToolDef(
                name="voice_status",
                description=(
                    "Status of the voice (plugin-voice) setup: whether an "
                    "ElevenLabs/11labs key is available (pasted, vault grant, "
                    "hosted gateway, or env), whether the voice agent is "
                    "provisioned, and whether the owner's voice imprint exists."
                ),
                parameters={"type": "object", "properties": {}},
                policy="auto_approve",
                risk_level="low",
            ),
            _voice_status,
        )

        ctx.tool_registry.register(
            self.manifest.name,
            ToolDef(
                name="voice_connect",
                description=(
                    "Complete the voice (plugin-voice) setup using whatever "
                    "ElevenLabs/11labs key is already available (vault grant, "
                    "hosted gateway key, or env) — provisions the ElevenLabs "
                    "voice agent with this agent's own personality. Use after "
                    "wiring a gateway key, or when voice_status says connected "
                    "but not agent_ready. No key value is exposed."
                ),
                parameters={"type": "object", "properties": {}},
                policy="ask",
                risk_level="medium",
            ),
            _voice_connect,
        )

        log.info("plugin-voice loaded (widget=voice, settings tab=voice, tools=2)")

    async def on_unload(self) -> None:
        from .state import close_client

        await close_client()
