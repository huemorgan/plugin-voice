"""Shared connection/setup flow — used by BOTH the HTTP routes and the agent
tools, so the owner can finish setup from Settings or just by asking in chat.

Key resolution precedence:
1. the owner's own pasted key (this plugin's vault entry)
2. a granted vault credential or the hosting gateway's virtual key via the
   sanctioned ``ctx.vault.connect(slug, ...)`` — tried for BOTH service slugs
   the ecosystem uses: ``elevenlabs`` and ``11labs`` (the hosted gateway
   registers it as ``11labs``; naming convention ``{slug}_api_key``)
3. gateway-provisioned env vars, both spellings

The secret value never passes through the agent: tools trigger this module and
the resolution happens server-side.
"""

from __future__ import annotations

import json
import logging
import os
import secrets as _secrets
from typing import Any

from . import (
    VAULT_AGENT_ID,
    VAULT_API_KEY,
    VAULT_BRIDGE_SECRET,
    VAULT_SETTINGS,
    identity,
    persona_config,
    personality,
)
from .elevenlabs import ElevenLabsClient, ElevenLabsError

log = logging.getLogger("plugin-voice.setup")

AGENT_NAME = "Luna (plugin-voice)"
UPSTREAM = "https://api.elevenlabs.io"
SLUGS = ("elevenlabs", "11labs")
ENV_PAIRS = (
    ("LUNA_ELEVENLABS_API_KEY", "LUNA_ELEVENLABS_BASE_URL"),
    ("LUNA_11LABS_API_KEY", "LUNA_11LABS_BASE_URL"),
)


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


def hosted_bridge() -> tuple[str, dict[str, str]] | None:
    """Fly-hosted tenants: ``(bridge base URL, pinning headers)``, else None.

    The tenant's browser-facing URL (``https://luna.com.ai/a/{slug}/...``) sits
    behind a proxy that requires a login cookie, so ElevenLabs (server→server)
    can never reach the bridge through it. But the machine itself is publicly
    reachable on the shared Fly app hostname, and Fly routes a request to THIS
    machine when it carries ``fly-force-instance-id`` — the same mechanism
    luna-service's own proxy uses. Fly injects both env vars into every
    machine; ``ctx.get_env`` only allows LUNA_* names, hence ``os.environ``.
    """
    app = (os.environ.get("FLY_APP_NAME") or "").strip()
    machine = (os.environ.get("FLY_MACHINE_ID") or "").strip()
    if not app or not machine:
        return None
    return (
        f"https://{app}.fly.dev/api/p/plugin-voice/v1",
        {"fly-force-instance-id": machine},
    )


def is_local_host(base: str) -> bool:
    host = base.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0].lower()
    return host in ("localhost", "127.0.0.1", "0.0.0.0", "::1") or host.endswith(".local")


async def capture_public_base(ctx: Any, base: str | None) -> None:
    """Remember the tenant's public base URL (from any authed request) so a
    chat-initiated connect knows where the bridge lives."""
    if not base or is_local_host(base):
        return
    settings = await settings_of(ctx)
    if settings.get("public_base") != base:
        settings["public_base"] = base
        await save_settings(ctx, settings)


async def resolve_key(ctx: Any) -> dict | None:
    """Client-construction kwargs + a ``source`` label, or None."""
    own = await read(ctx, VAULT_API_KEY)
    if own:
        return {"api_key": own, "source": "own"}

    vault = getattr(ctx, "vault", None)
    connect_fn = getattr(vault, "connect", None)
    if callable(connect_fn):
        for slug in SLUGS:
            try:
                from luna_sdk import AuthSpec

                conn = await connect_fn(
                    slug,
                    upstream_default=UPSTREAM,
                    auth=AuthSpec(location="header", name="xi-api-key", scheme=None),
                )
            except Exception as exc:  # noqa: BLE001 — older SDK / no gateway
                log.debug("plugin-voice: vault.connect(%s) unavailable: %s", slug, exc)
                conn = None
            if conn is not None:
                headers: dict = {}
                params: dict = {}
                conn.apply(headers, params)
                return {
                    "headers": headers,
                    "params": params,
                    "base_url": getattr(conn, "base_url", None) or UPSTREAM,
                    "source": "gateway" if getattr(conn, "source", "real") == "virtual" else "vault",
                    "slug": slug,
                }

    if getattr(ctx, "get_env", None) is not None:
        for key_var, base_var in ENV_PAIRS:
            env_key = (ctx.get_env(key_var) or "").strip()
            if env_key:
                return {
                    "api_key": env_key,
                    "base_url": (ctx.get_env(base_var) or "").strip() or UPSTREAM,
                    "source": "env",
                }
    return None


def client_from(res: dict) -> ElevenLabsClient:
    kwargs = {k: v for k, v in res.items() if k in ("api_key", "base_url", "headers", "params") and v}
    return ElevenLabsClient(**kwargs)


async def build_status(ctx: Any) -> dict:
    from . import routes as _routes  # VAULT_PROFILE lives there

    agent_id = await read(ctx, VAULT_AGENT_ID)
    secret = await read(ctx, VAULT_BRIDGE_SECRET)
    settings = await settings_of(ctx)
    key_res = await resolve_key(ctx)
    # 004: the LIVE identity name wins — the stored snapshot is only what the
    # greeting was generated for and goes stale on rename.
    live = await identity.live_name(ctx)
    eff = persona_config.effective(settings)
    return {
        "connected": key_res is not None,
        "key_source": (key_res or {}).get("source"),
        "agent_ready": bool(agent_id),
        "agent_id": agent_id,
        "voice_id": settings.get("voice_id"),
        "persona_name": live or settings.get("persona_name"),
        "greeting": eff.get("greeting"),
        "fillers": eff.get("fillers"),
        "imprint_ready": bool(await read(ctx, _routes.VAULT_PROFILE)),
        "bridge_path": "/api/p/plugin-voice/v1/chat/completions",
        "bridge_secret": secret,
    }


async def do_connect(
    ctx: Any,
    *,
    pasted_key: str | None = None,
    agent_override: str | None = None,
    public_base: str | None = None,
) -> dict:
    """The full connect flow: validate key → persona → provision agent.

    ``public_base``: required for a reachable bridge; falls back to the last
    base captured from an authed request. Raises SetupError with a friendly
    message on every failure path.
    """
    from .state import get_client, set_client

    pasted = (pasted_key or "").strip()
    if pasted:
        probe = ElevenLabsClient(pasted)
    else:
        res = await resolve_key(ctx)
        if res is None:
            raise SetupError(
                "No ElevenLabs key found — paste one in Settings → Voice, or "
                "connect the 11labs gateway key first"
            )
        probe = client_from(res)
    try:
        await probe.list_voices()
    except ElevenLabsError as exc:
        await probe.close()
        raise SetupError(f"ElevenLabs rejected the key: {exc}") from exc

    vault = vault_of(ctx)
    if pasted:
        await vault.store_credential(VAULT_API_KEY, pasted, kind="api_key")
    if not await read(ctx, VAULT_BRIDGE_SECRET):
        await vault.store_credential(
            VAULT_BRIDGE_SECRET, _secrets.token_urlsafe(32), kind="api_key"
        )
    secret = await read(ctx, VAULT_BRIDGE_SECRET)

    settings = await settings_of(ctx)
    # ElevenLabs appends /chat/completions — hand it the base ending at /v1.
    # Fly-direct wins over the browser-facing base: the tenant's public URL
    # sits behind a cookie-authed proxy ElevenLabs can't pass.
    hosted = hosted_bridge()
    if hosted:
        bridge_base, bridge_headers = hosted
    else:
        bridge_headers = None
        public_base = public_base or settings.get("public_base")
        if not public_base:
            await probe.close()
            raise SetupError(
                "I don't know this Luna's public URL yet — open Settings → Voice "
                "once (any visit records it), then retry"
            )
        bridge_base = f"{public_base}/api/p/plugin-voice/v1"

    # Personality-matched setup: the brain names itself, writes its own
    # greeting, chooses its waiting words, and picks the voice that fits.
    # Every step degrades to neutral defaults; connect never fails on it.
    persona = await personality.fetch_persona(ctx)
    voice_id = settings.get("voice_id")  # an explicit owner choice wins
    if not voice_id and persona.get("voice_description"):
        try:
            voice_id = await personality.pick_voice(
                ctx, await probe.list_voices(), persona["voice_description"]
            )
        except ElevenLabsError:
            voice_id = None
    agent_label = f"{persona['name']} (plugin-voice)" if persona.get("name") else AGENT_NAME

    # Owner Voice Persona overrides (004) beat the fetched persona.
    ov = persona_config.overrides_of(settings)
    persona_kw = dict(
        first_message=ov.get("greeting") or persona.get("greeting"),
        fillers=ov.get("fillers") or persona.get("fillers"),
        voice_id=voice_id,
        request_headers=bridge_headers,
        overrides=persona_config.elevenlabs_overrides(settings),
    )
    try:
        agent_id = (agent_override or "").strip() or await probe.find_agent(agent_label) \
            or await probe.find_agent(AGENT_NAME)
        if agent_id:
            current = await probe.get_agent_bridge_url(agent_id)
            keep_current = (
                not hosted
                and is_local_host(public_base)
                and current
                and not is_local_host(current)
            )
            if not keep_current:
                await probe.update_agent_bridge(
                    agent_id, custom_llm_url=bridge_base, bridge_secret=secret, **persona_kw
                )
        else:
            agent_id = await probe.create_agent(
                agent_label, custom_llm_url=bridge_base, bridge_secret=secret, **persona_kw
            )
    except ElevenLabsError as exc:
        await probe.close()
        raise SetupError(f"Could not provision the ElevenLabs agent: {exc}", 502) from exc
    await vault.store_credential(VAULT_AGENT_ID, agent_id, kind="config")

    settings.update({
        "persona_name": persona.get("name"),
        "greeting": persona.get("greeting"),
        "fillers": persona.get("fillers"),
        "voice_id": voice_id,
        # Provisioned with the current config shape — /session's one-time
        # migration must not re-PATCH a fresh agent.
        "agent_config_v": ElevenLabsClient.AGENT_CONFIG_V,
    })
    await save_settings(ctx, settings)

    old = get_client()
    if old is not None:
        await old.close()
    set_client(probe)
    return await build_status(ctx)


async def resync_persona(
    ctx: Any,
    client: ElevenLabsClient,
    agent_id: str,
    *,
    preferred_base: str | None = None,
) -> dict:
    """Re-ask the agent who it is and re-PATCH the ElevenLabs agent.

    004: shared by the manual "Re-match" button and the automatic rename
    resync fired from ``/session``. Owner Voice Persona overrides always win
    over the freshly fetched persona; an explicit owner voice pick is kept.
    ``preferred_base`` is a browser-derived bridge base (``…/v1``) — used
    unless it's a localhost while the agent already has a working tunnel URL.
    """
    secret = await read(ctx, VAULT_BRIDGE_SECRET)
    if not secret:
        raise SetupError("Connect first")

    persona = await personality.fetch_persona(ctx)
    settings = await settings_of(ctx)
    voice_id = None
    if persona.get("voice_description"):
        try:
            voice_id = await personality.pick_voice(
                ctx, await client.list_voices(), persona["voice_description"]
            )
        except ElevenLabsError:
            voice_id = None

    hosted = hosted_bridge()
    if hosted:
        bridge_base, bridge_headers = hosted
    else:
        bridge_headers = None
        current = await client.get_agent_bridge_url(agent_id)
        bridge_base = preferred_base
        if bridge_base and is_local_host(bridge_base) and current and not is_local_host(current):
            bridge_base = current  # keep a working tunnel URL
        if not bridge_base:
            bridge_base = current
        if not bridge_base:
            public = settings.get("public_base")
            if not public:
                raise SetupError("No reachable bridge URL known — reconnect once", 400)
            bridge_base = f"{public}/api/p/plugin-voice/v1"

    ov = persona_config.overrides_of(settings)
    try:
        await client.update_agent_bridge(
            agent_id,
            custom_llm_url=bridge_base,
            bridge_secret=secret,
            first_message=ov.get("greeting") or persona.get("greeting"),
            fillers=ov.get("fillers") or persona.get("fillers"),
            voice_id=voice_id or settings.get("voice_id"),
            request_headers=bridge_headers,
            overrides=persona_config.elevenlabs_overrides(settings),
        )
    except ElevenLabsError as exc:
        raise SetupError(f"Could not update the agent: {exc}", 502) from exc

    settings.update({
        # Fall back to the live identity name so a failed persona fetch still
        # converges (otherwise every /session would retry the resync forever).
        "persona_name": persona.get("name") or await identity.live_name(ctx),
        "greeting": persona.get("greeting"),
        "fillers": persona.get("fillers"),
    })
    if voice_id:
        settings["voice_id"] = voice_id
    await save_settings(ctx, settings)
    return settings
