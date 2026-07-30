"""Shared connection/setup flow — used by BOTH the HTTP routes and the agent
tools, so the owner can finish setup from Settings or just by asking in chat.

Key resolution (see ``openai_realtime.resolve_openai_key``):
1. the owner's own pasted key (this plugin's vault entry)
2. a granted vault credential or the hosting gateway's virtual key via
   ``ctx.vault.connect("openai", ...)``
3. env vars (``LUNA_OPENAI_API_KEY`` / ``OPENAI_API_KEY``)

The secret value never passes through the agent: tools trigger this module and
the resolution happens server-side. Connecting probes the key by minting a
throwaway Realtime client secret — the same call every real session uses.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from . import (
    VAULT_OPENAI_KEY,
    VAULT_SETTINGS,
    identity,
    openai_realtime,
    persona_config,
    personality,
)
from .openai_realtime import RealtimeError

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
    key_res = await openai_realtime.resolve_openai_key(ctx, vault_key=VAULT_OPENAI_KEY)
    # The LIVE identity name wins — the stored snapshot is only what the
    # greeting was generated for and goes stale on rename.
    live = await identity.live_name(ctx)
    eff = persona_config.effective(settings)
    return {
        "connected": key_res is not None,
        "key_source": (key_res or {}).get("source"),
        "rt_voice": settings.get("rt_voice"),
        "rt_model": settings.get("rt_model") or openai_realtime.DEFAULT_MODEL,
        "persona_name": live or settings.get("persona_name"),
        "greeting": eff.get("greeting"),
        "fillers": eff.get("fillers"),
        "imprint_ready": bool(await read(ctx, _routes.VAULT_PROFILE)),
    }


def _probe_client(key_res: dict) -> openai_realtime.RealtimeClient:
    kwargs = {
        k: v for k, v in key_res.items()
        if k in ("api_key", "base_url", "headers", "params") and v
    }
    return openai_realtime.RealtimeClient(**kwargs)


async def _validate_key(key_res: dict) -> None:
    """Mint (and discard) a client secret — the exact call sessions make, so a
    key that passes here works for real. Raises SetupError on any failure."""
    probe = _probe_client(key_res)
    try:
        await probe.mint_client_secret(
            openai_realtime.session_config(instructions="Connection check.")
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
        key_res = await openai_realtime.resolve_openai_key(ctx, vault_key=VAULT_OPENAI_KEY)
        if key_res is None:
            raise SetupError(
                "No OpenAI key found — paste one in Settings → Voice, or "
                "connect the openai gateway key first"
            )
    await _validate_key(key_res)
    if pasted:
        await vault_of(ctx).store_credential(VAULT_OPENAI_KEY, pasted, kind="api_key")

    settings = await settings_of(ctx)
    persona = await personality.fetch_persona(ctx)
    voice = settings.get("rt_voice")  # an explicit owner choice wins
    if not voice and persona.get("voice_description"):
        voice = await personality.pick_voice(
            ctx, openai_realtime.VOICES, persona["voice_description"]
        )
    settings.update({
        "persona_name": persona.get("name"),
        "greeting": persona.get("greeting"),
        "fillers": persona.get("fillers"),
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
            ctx, openai_realtime.VOICES, persona["voice_description"]
        )
    settings.update({
        # Fall back to the live identity name so a failed persona fetch still
        # converges instead of retrying forever.
        "persona_name": persona.get("name") or await identity.live_name(ctx),
        "greeting": persona.get("greeting"),
        "fillers": persona.get("fillers"),
    })
    if voice:
        settings["rt_voice"] = voice
    await save_settings(ctx, settings)
    return settings
