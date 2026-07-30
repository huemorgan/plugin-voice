"""plugin-voice — full-duplex, interruptible voice conversations with Luna.

OpenAI Realtime speech-to-speech does the talking (lane 1) in Luna's persona,
read-only info tools answer fast questions in-call (lane 2), and the real Luna
agent runs delegated work in the background (lane 3) — its results come back
as short spoken summaries, never verbatim text. Authored against `luna_sdk`
only; everything lives in this plugin.
"""

from __future__ import annotations

import logging

from luna_sdk import LunaPlugin, PluginContext, PluginManifest, SettingsTab, ToolDef

log = logging.getLogger("plugin-voice")

# Vault keys (all owned by this plugin; ACL-scoped by the vault provider).
VAULT_OPENAI_KEY = "plugin_voice.openai_api_key"  # 005: realtime S2S talker
VAULT_SETTINGS = "plugin_voice.settings"  # non-secret JSON; vault used as the plugin's durable KV


class VoicePlugin(LunaPlugin):
    manifest = PluginManifest(
        name="plugin-voice",
        shown_name="Voice",
        icon="mic",
        version="0.6.0",
        description=(
            "Full-duplex voice conversations that know who is speaking — "
            "OpenAI Realtime speech-to-speech in this agent's own persona, "
            "owner voice imprint, in-call knowledge tools, Luna stays the "
            "hands for real work."
        ),
        category="global",
        depends_on=["plugin-vault"],
        routes_module="routes",
        settings_tabs=[
            # ONE tab; the page itself has a Setup/Persona switcher (004.1) —
            # the persona editor lives at ui/settings/persona/ inside it.
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
        # Luna View — the full-pane dot avatar. `hidden` needs a luna core
        # that knows the field (pydantic extra="ignore" elsewhere): new cores
        # keep it out of the left nav and open it via the widget's expand
        # button; older cores simply show a visible "Luna" link. Both work.
        sidebar_sections=[
            {
                "id": "luna-view",
                "label": "Luna",
                "icon": "sparkles",
                "path": "ui/view/",
                "hidden": True,
            },
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
            st["note"] = (
                "connected=OpenAI key resolvable (source in key_source: own/"
                "vault/gateway/env). If not connected, the owner can paste a "
                "key in Settings → Voice, or call voice_connect after wiring "
                "a gateway key."
            )
            return st

        async def _voice_connect() -> dict:
            try:
                st = await setup.do_connect(ctx)
            except setup.SetupError as exc:
                return {"connected": False, "error": str(exc)}
            st["note"] = "Voice setup complete — the owner can talk via the sidebar Voice widget."
            return st

        ctx.tool_registry.register(
            self.manifest.name,
            ToolDef(
                name="voice_status",
                description=(
                    "Status of the voice (plugin-voice) setup: whether an "
                    "OpenAI key is available (pasted, vault grant, hosted "
                    "gateway, or env), the realtime voice/model in use, and "
                    "whether the owner's voice imprint exists."
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
                    "OpenAI key is already available (vault grant, hosted "
                    "gateway key, or env) — validates the key against the "
                    "Realtime API and sets up this agent's own persona and "
                    "voice for the talker. Use after wiring a gateway key, or "
                    "when voice_status says not connected. No key value is "
                    "exposed."
                ),
                parameters={"type": "object", "properties": {}},
                policy="ask",
                risk_level="medium",
            ),
            _voice_connect,
        )

        log.info("plugin-voice loaded (widget=voice, settings tab=voice, tools=2)")
