"""OpenAI Realtime REST calls + key resolution — all of them live here.

The plugin's server key never reaches the browser: this module mints an
ephemeral client secret (``POST /v1/realtime/client_secrets``) and the widget
uses THAT to open WebRTC straight to OpenAI. Key resolution: pasted key →
vault/gateway connect → env vars.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

log = logging.getLogger("plugin-voice.openai_realtime")

DEFAULT_BASE_URL = "https://api.openai.com"
DEFAULT_MODEL = "gpt-realtime-2.1"
MODELS = ("gpt-realtime-2.1", "gpt-realtime-2.1-mini")
DEFAULT_VOICE = "marin"
WEBRTC_CALLS_URL = "https://api.openai.com/v1/realtime/calls"

SLUG = "openai"
ENV_VARS = ("LUNA_OPENAI_API_KEY", "OPENAI_API_KEY")

# The Realtime voice set is fixed — described so personality.pick_voice can
# match the persona's voice_description against it (generic catalog shape:
# voice_id / name / labels.description).
VOICES: list[dict[str, Any]] = [
    {"voice_id": "marin", "name": "Marin", "labels": {"description": "female, warm and composed, natural pace"}},
    {"voice_id": "cedar", "name": "Cedar", "labels": {"description": "male, calm and grounded, low register"}},
    {"voice_id": "alloy", "name": "Alloy", "labels": {"description": "neutral, balanced and clear"}},
    {"voice_id": "ash", "name": "Ash", "labels": {"description": "male, direct and energetic"}},
    {"voice_id": "ballad", "name": "Ballad", "labels": {"description": "male, soft-spoken and gentle, unhurried"}},
    {"voice_id": "coral", "name": "Coral", "labels": {"description": "female, bright and friendly, upbeat"}},
    {"voice_id": "echo", "name": "Echo", "labels": {"description": "male, crisp and confident"}},
    {"voice_id": "sage", "name": "Sage", "labels": {"description": "female, light and youthful"}},
    {"voice_id": "shimmer", "name": "Shimmer", "labels": {"description": "female, expressive and animated"}},
    {"voice_id": "verse", "name": "Verse", "labels": {"description": "male, versatile storyteller, mid register"}},
]

VOICE_IDS = frozenset(v["voice_id"] for v in VOICES)

TURN_EAGERNESS_MAP = {"eager": "high", "normal": "auto", "patient": "low"}


class RealtimeError(Exception):
    """A failed OpenAI Realtime call, with a safe (key-free) message."""


async def resolve_openai_key(ctx: Any, *, vault_key: str) -> dict | None:
    """Client-construction kwargs + a ``source`` label, or None.

    Precedence: the owner's own pasted key → a granted vault credential or
    gateway virtual key via ``ctx.vault.connect("openai", ...)`` → env vars
    (``LUNA_OPENAI_API_KEY`` via ctx.get_env, bare ``OPENAI_API_KEY`` from the
    process env — ctx.get_env only allows LUNA_* names).
    """
    vault = getattr(ctx, "vault", None)
    if vault is not None:
        try:
            cred = await vault.get_credential(vault_key)
            own = (getattr(cred, "value", None) or "").strip()
        except KeyError:
            own = ""
        if own:
            return {"api_key": own, "source": "own"}

    connect_fn = getattr(vault, "connect", None)
    if callable(connect_fn):
        try:
            from luna_sdk import AuthSpec

            conn = await connect_fn(
                SLUG,
                upstream_default=DEFAULT_BASE_URL,
                auth=AuthSpec(location="header", name="Authorization", scheme="Bearer"),
            )
        except Exception as exc:  # noqa: BLE001 — older SDK / no gateway
            log.debug("plugin-voice: vault.connect(%s) unavailable: %s", SLUG, exc)
            conn = None
        if conn is not None:
            headers: dict = {}
            params: dict = {}
            conn.apply(headers, params)
            return {
                "headers": headers,
                "params": params,
                "base_url": getattr(conn, "base_url", None) or DEFAULT_BASE_URL,
                "source": "gateway" if getattr(conn, "source", "real") == "virtual" else "vault",
            }

    get_env = getattr(ctx, "get_env", None)
    for var in ENV_VARS:
        value = ""
        if callable(get_env) and var.startswith("LUNA_"):
            value = (get_env(var) or "").strip()
        if not value:
            value = (os.environ.get(var) or "").strip()
        if value:
            return {"api_key": value, "source": "env"}
    return None


class RealtimeClient:
    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        headers: dict | None = None,
        params: dict | None = None,
    ) -> None:
        """Direct key (Authorization: Bearer) or pre-built auth from a vault
        Connection (gateway keys arrive with their own header/base_url)."""
        self._http = httpx.AsyncClient(
            base_url=base_url,
            headers=headers if headers is not None else {"Authorization": f"Bearer {api_key or ''}"},
            params=params or None,
            timeout=httpx.Timeout(15.0),
        )

    async def close(self) -> None:
        await self._http.aclose()

    async def mint_client_secret(self, session: dict[str, Any]) -> dict[str, Any]:
        """Ephemeral client secret for a browser-side WebRTC session.

        Returns the raw response: ``{"value": "ek_...", "expires_at": ...,
        "session": {...}}``.
        """
        try:
            resp = await self._http.post("/v1/realtime/client_secrets", json={"session": session})
        except httpx.HTTPError as exc:
            raise RealtimeError(f"OpenAI unreachable: {type(exc).__name__}") from exc
        if resp.status_code == 401:
            raise RealtimeError("OpenAI rejected the key (HTTP 401) — check it in Settings → Voice")
        if resp.status_code in (403, 404):
            # A gateway/proxy base_url that doesn't pass Realtime through.
            raise RealtimeError(
                f"Realtime session minting failed (HTTP {resp.status_code}) — "
                "this key's endpoint may not support Realtime; paste a real "
                "OpenAI key in Settings → Voice"
            )
        if resp.status_code >= 400:
            raise RealtimeError(f"OpenAI client_secrets failed: HTTP {resp.status_code}")
        data = resp.json()
        if not data.get("value"):
            raise RealtimeError("OpenAI returned no client secret")
        return data


def session_config(
    *,
    instructions: str,
    voice: str = DEFAULT_VOICE,
    model: str = DEFAULT_MODEL,
    tools: list[dict] | None = None,
    turn_eagerness: str = "normal",
) -> dict[str, Any]:
    """The ``session`` body for client-secret minting.

    Semantic VAD carries the persona's turn-taking patience (mapped from
    the persona-settings eager/normal/patient values).
    """
    return {
        "type": "realtime",
        "model": model if model in MODELS else DEFAULT_MODEL,
        "instructions": instructions,
        "tools": tools or [],
        "audio": {
            "input": {
                "turn_detection": {
                    "type": "semantic_vad",
                    "eagerness": TURN_EAGERNESS_MAP.get(turn_eagerness, "auto"),
                },
            },
            "output": {
                "voice": voice if voice in VOICE_IDS else DEFAULT_VOICE,
            },
        },
    }
