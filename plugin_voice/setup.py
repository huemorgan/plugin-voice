"""Shared connection/setup flow — used by BOTH the HTTP routes and the agent
tools, so the owner can finish setup from Settings or just by asking in chat.

Key resolution (see ``gemini_live.resolve_gemini_key``):
1. the owner's own pasted key (this plugin's vault entry)
2. env vars (``LUNA_GEMINI_API_KEY`` / ``GEMINI_API_KEY``)

The secret value never passes through the agent: tools trigger this module and
the resolution happens server-side. Connecting probes the key by minting a
throwaway Live session token — the same call every real session makes.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from . import (
    VAULT_GEMINI_KEY,
    VAULT_SETTINGS,
    gemini_live,
    identity,
    persona_config,
    personality,
)
from .gemini_live import RealtimeError

log = logging.getLogger("plugin-voice.setup")


class SetupError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def vault_of(ctx: Any):
    vault = getattr(ctx, "vault", None)
    if vault is None:
        raise SetupError("Vault not available", 503)
    return vault


async def read(ctx: Any, key: str) -> str | None:
    try:
        cred = await vault_of(ctx).get_credential(key)
    except KeyError:
        return None
    value = (getattr(cred, "value", None) or "").strip()
    return value or None


async def settings_of(ctx: Any) -> dict:
    raw = await read(ctx, VAULT_SETTINGS)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


async def save_settings(ctx: Any, settings: dict) -> None:
    await vault_of(ctx).store_credential(VAULT_SETTINGS, json.dumps(settings), kind="config")


async def build_status(ctx: Any) -> dict:
    from . import routes as _routes  # VAULT_PROFILE lives there

    settings = await settings_of(ctx)
    key_res = await gemini_live.resolve_gemini_key(ctx, vault_key=VAULT_GEMINI_KEY)
    # A key that resolves is not a key that works (restricted or revoked keys
    # still resolve), so status probes the API and reports ready/key_error
    # separately from connected.
    ready = False
    key_error = None
    if key_res is not None:
        try:
            await _validate_key(key_res)
            ready = True
        except SetupError as exc:
            key_error = str(exc)
    # The LIVE identity name wins — the stored snapshot is only what the
    # greeting was generated for and goes stale on rename.
    live = await identity.live_name(ctx)
    eff = persona_config.effective(settings)
    rt_model = settings.get("rt_model")
    if rt_model not in gemini_live.MODELS:
        rt_model = gemini_live.DEFAULT_MODEL  # incl. stale OpenAI-era values
    return {
        "connected": key_res is not None,
        "ready": ready,
        "key_error": key_error,
        "key_source": (key_res or {}).get("source"),
        "rt_voice": settings.get("rt_voice"),
        "rt_model": rt_model,
        "persona_name": live or settings.get("persona_name"),
        "greeting": eff.get("greeting"),
        "fillers": eff.get("fillers"),
        "imprint_ready": bool(await read(ctx, _routes.VAULT_PROFILE)),
    }


async def _validate_key(key_res: dict) -> None:
    """Mint (and discard) an ephemeral Live token — the exact call sessions
    make, so a key that passes here works for real. Raises SetupError on any
    failure."""
    probe = gemini_live.GeminiLiveClient(key_res["api_key"])
    try:
        await probe.mint_token(
            gemini_live.setup_message(instructions="Connection check."), uses=1
        )
    except RealtimeError as exc:
        raise SetupError(str(exc)) from exc
    finally:
        await probe.close()


async def do_connect(ctx: Any, *, pasted_key: str | None = None) -> dict:
    """The full connect flow: validate key → store it → persona → voice.

    Personality-matched setup: the agent names itself, writes its own greeting,
    chooses its waiting words, and picks the Realtime voice that fits. Every
    persona step degrades to neutral defaults; connect never fails on it.
    """
    pasted = (pasted_key or "").strip()
    if pasted:
        key_res: dict | None = {"api_key": pasted, "source": "own"}
    else:
        key_res = await gemini_live.resolve_gemini_key(ctx, vault_key=VAULT_GEMINI_KEY)
        if key_res is None:
            raise SetupError(
                "No Gemini key found — paste a Google AI API key in "
                "Settings → Voice"
            )
    await _validate_key(key_res)
    if pasted:
        await vault_of(ctx).store_credential(VAULT_GEMINI_KEY, pasted, kind="api_key")

    settings = await settings_of(ctx)
    persona = await personality.fetch_persona(ctx)
    voice = settings.get("rt_voice")  # an explicit owner choice wins…
    if voice not in gemini_live.VOICE_IDS:
        voice = None  # …unless it's an OpenAI-era name Gemini doesn't know
    if not voice and persona.get("voice_description"):
        voice = await personality.pick_voice(
            ctx, gemini_live.VOICES, persona["voice_description"]
        )
    settings.update({
        "persona_name": persona.get("name"),
        "greeting": persona.get("greeting"),
        "fillers": persona.get("fillers"),
        "persona_brief": persona.get("persona_brief"),
        "rt_voice": voice,
    })
    await save_settings(ctx, settings)
    return await build_status(ctx)


async def resync_persona(ctx: Any) -> dict:
    """Re-ask the agent who it is — new greeting, waiting words, re-matched
    voice. Shared by the manual "Re-match" button; an explicit owner voice
    pick is kept. Returns the updated settings."""
    persona = await personality.fetch_persona(ctx)
    settings = await settings_of(ctx)
    voice = None
    if persona.get("voice_description"):
        voice = await personality.pick_voice(
            ctx, gemini_live.VOICES, persona["voice_description"]
        )
    settings.update({
        # Fall back to the live identity name so a failed persona fetch still
        # converges instead of retrying forever.
        "persona_name": persona.get("name") or await identity.live_name(ctx),
        "greeting": persona.get("greeting"),
        "fillers": persona.get("fillers"),
        "persona_brief": persona.get("persona_brief"),
    })
    if voice:
        settings["rt_voice"] = voice
    await save_settings(ctx, settings)
    return settings
