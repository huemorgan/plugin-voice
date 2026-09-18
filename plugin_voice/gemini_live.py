"""Gemini Live REST calls + key resolution — all of them live here.

The plugin's server key never reaches the browser: this module mints an
ephemeral auth token (``POST /v1alpha/auth_tokens``) whose
``bidiGenerateContentSetup`` constraint locks the ENTIRE session setup
(system instruction, tools, VAD, voice) server-side, and the widget uses THAT
token to open a WebSocket straight to Google's Constrained live endpoint.
Key resolution: pasted key → env vars.

Wire facts (pinned by plans/007 phase 0 against the real API):
- constraint field is ``bidiGenerateContentSetup`` (docs say
  liveConnectConstraints; the API refuses that name),
- token connects ONLY to ``…GenerativeService.BidiGenerateContentConstrained``
  with ``?access_token=`` (plain BidiGenerateContent refuses tokens),
- ``proactivity`` is v1alpha-only,
- audio out is ``audio/pcm;rate=24000``, audio in must be 16 kHz s16le mono.
"""

from __future__ import annotations

import datetime
import logging
import os
from typing import Any

import httpx

log = logging.getLogger("plugin-voice.gemini_live")

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com"
API_VERSION = "v1alpha"  # proactivity (proactive audio) exists only here
DEFAULT_MODEL = "gemini-3.8-live"
MODELS = ("gemini-3.8-live", "gemini-3.8-live-extended-thinking")
DEFAULT_VOICE = "Aoede"
LIVE_WS_URL = (
    "wss://generativelanguage.googleapis.com/ws/"
    f"google.ai.generativelanguage.{API_VERSION}.GenerativeService."
    "BidiGenerateContentConstrained"
)

SLUG = "gemini"
ENV_VARS = ("LUNA_GEMINI_API_KEY", "GEMINI_API_KEY")

# Curated from the 18 prebuilt voices gemini-3.8-live accepts (phase-0 probe) —
# described so personality.pick_voice can match the persona's voice_description
# against it (generic catalog shape: voice_id / name / labels.description).
VOICES: list[dict[str, Any]] = [
    {"voice_id": "Aoede", "name": "Aoede", "labels": {"description": "female, warm and breezy, natural pace"}},
    {"voice_id": "Despina", "name": "Despina", "labels": {"description": "female, smooth and calm, composed"}},
    {"voice_id": "Kore", "name": "Kore", "labels": {"description": "female, firm and confident"}},
    {"voice_id": "Leda", "name": "Leda", "labels": {"description": "female, light and youthful"}},
    {"voice_id": "Zephyr", "name": "Zephyr", "labels": {"description": "female, bright and cheerful, upbeat"}},
    {"voice_id": "Callirrhoe", "name": "Callirrhoe", "labels": {"description": "female, easy-going and relaxed"}},
    {"voice_id": "Puck", "name": "Puck", "labels": {"description": "male, upbeat and lively"}},
    {"voice_id": "Charon", "name": "Charon", "labels": {"description": "male, informative and steady, low register"}},
    {"voice_id": "Orus", "name": "Orus", "labels": {"description": "male, firm and grounded"}},
    {"voice_id": "Fenrir", "name": "Fenrir", "labels": {"description": "male, excitable and energetic"}},
]

VOICE_IDS = frozenset(v["voice_id"] for v in VOICES)

# Persona turn-taking patience → server VAD preset. START sensitivity stays
# LOW except on "eager": a low start threshold is what keeps beeps, keyboard
# clatter and background chatter from opening turns (or cutting Luna off) in
# rooms that aren't silent. silenceDurationMs is how long a pause ends the
# owner's turn — patient people get to think mid-sentence.
VAD_PRESETS: dict[str, dict[str, Any]] = {
    "patient": {
        "startOfSpeechSensitivity": "START_SENSITIVITY_LOW",
        "endOfSpeechSensitivity": "END_SENSITIVITY_LOW",
        "prefixPaddingMs": 60,
        "silenceDurationMs": 800,
    },
    "normal": {
        "startOfSpeechSensitivity": "START_SENSITIVITY_LOW",
        "endOfSpeechSensitivity": "END_SENSITIVITY_LOW",
        "prefixPaddingMs": 40,
        "silenceDurationMs": 650,
    },
    "eager": {
        "startOfSpeechSensitivity": "START_SENSITIVITY_HIGH",
        "endOfSpeechSensitivity": "END_SENSITIVITY_HIGH",
        "prefixPaddingMs": 20,
        "silenceDurationMs": 450,
    },
}


class RealtimeError(Exception):
    """A failed Gemini Live call, with a safe (key-free) message."""


async def resolve_gemini_key(ctx: Any, *, vault_key: str) -> dict | None:
    """``{"api_key": …, "source": "own"|"env"}`` or None.

    Precedence: the owner's own pasted key → env vars (``LUNA_GEMINI_API_KEY``
    via ctx.get_env, bare ``GEMINI_API_KEY`` from the process env — ctx.get_env
    only allows LUNA_* names). No gateway/vault.connect lane: token minting
    authenticates directly against Google, which a gateway virtual key can't —
    the same reason the old OpenAI path refused gateway keys (audio never
    traverses the gateway, so platform billing can't meter it).

    Hosted machines carry ``LUNA_GEMINI_API_KEY``/``GEMINI_API_KEY`` set to the
    platform's lsv1- gateway token (the chat-proxy pair) — that token can never
    mint against Google, so lsv1- values are skipped here rather than forwarded
    to Google to die as an opaque HTTP 400 "API key not valid".
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

    get_env = getattr(ctx, "get_env", None)
    for var in ENV_VARS:
        value = ""
        if callable(get_env) and var.startswith("LUNA_"):
            value = (get_env(var) or "").strip()
        if not value or value.startswith("lsv1-"):
            value = (os.environ.get(var) or "").strip()
        if value and not value.startswith("lsv1-"):
            return {"api_key": value, "source": "env"}
    return None


def _upper_types(schema: Any) -> Any:
    """JSON-schema types → the OpenAPI-style UPPERCASE the Live API wants,
    recursively; unknown keys pass through untouched."""
    if isinstance(schema, dict):
        out = {}
        for k, v in schema.items():
            if k == "type" and isinstance(v, str):
                out[k] = v.upper()
            elif k in ("properties",):
                out[k] = {pk: _upper_types(pv) for pk, pv in (v or {}).items()}
            elif k == "items":
                out[k] = _upper_types(v)
            else:
                out[k] = v
        return out
    return schema


def convert_tools(tools: list[dict] | None) -> list[dict]:
    """OpenAI-flat tool schemas (``{"type":"function","name",…}``) → one
    Gemini ``{"functionDeclarations": […]}`` tool entry."""
    decls = []
    for t in tools or []:
        decl: dict[str, Any] = {
            "name": t.get("name"),
            "description": t.get("description") or "",
        }
        params = t.get("parameters")
        # Gemini refuses empty OBJECT schemas with no properties — omit instead.
        if params and params.get("properties"):
            decl["parameters"] = _upper_types(params)
        decls.append(decl)
    return [{"functionDeclarations": decls}] if decls else []


def setup_message(
    *,
    instructions: str,
    voice: str = DEFAULT_VOICE,
    model: str = DEFAULT_MODEL,
    tools: list[dict] | None = None,
    turn_eagerness: str = "normal",
) -> dict[str, Any]:
    """The full camelCase ``{"setup": …}`` message. It is BOTH the token's
    ``bidiGenerateContentSetup`` constraint and the first frame the browser
    sends — they must match, so routes hand the browser this exact object.

    - proactiveAudio: the model itself decides that background noise, media
      audio and side chatter deserve no reply — the main "don't go silent or
      answer every beep" lever beyond the VAD preset.
    - contextWindowCompression lifts the 15-minute audio session ceiling.
    - sessionResumption lets the client reconnect a dropped socket into the
      same conversation (goAway / sessionResumptionUpdate handling).
    """
    return {
        "setup": {
            "model": f"models/{model if model in MODELS else DEFAULT_MODEL}",
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {
                        "prebuiltVoiceConfig": {
                            "voiceName": voice if voice in VOICE_IDS else DEFAULT_VOICE
                        }
                    }
                },
            },
            "systemInstruction": {"parts": [{"text": instructions}]},
            "tools": convert_tools(tools),
            "realtimeInputConfig": {
                "automaticActivityDetection": dict(
                    VAD_PRESETS.get(turn_eagerness) or VAD_PRESETS["normal"]
                ),
            },
            "proactivity": {"proactiveAudio": True},
            "outputAudioTranscription": {},
            "contextWindowCompression": {
                "triggerTokens": "25600",
                "slidingWindow": {"targetTokens": "12800"},
            },
            "sessionResumption": {},
        }
    }


# Token lifetime: a voice call can run long; resumption reconnects re-present
# the same token, so it must outlive the socket. newSessionExpireTime gates
# EVERY connect — including the resumption reconnects Google forces every
# ~10 minutes on a live call (the phase-3 soak died at 10.3 min with
# "new_session_expire_time deadline exceeded" when this was 2 minutes) — so
# it must span the whole call, same as the token itself.
TOKEN_TTL = datetime.timedelta(hours=4)
NEW_SESSION_WINDOW = TOKEN_TTL
TOKEN_USES = 10


class GeminiLiveClient:
    def __init__(self, api_key: str, *, base_url: str = DEFAULT_BASE_URL) -> None:
        self._http = httpx.AsyncClient(
            base_url=base_url,
            headers={"x-goog-api-key": api_key},
            timeout=httpx.Timeout(15.0),
        )

    async def close(self) -> None:
        await self._http.aclose()

    async def mint_token(self, setup: dict[str, Any], *, uses: int = TOKEN_USES) -> dict[str, Any]:
        """Ephemeral auth token for a browser-side Live session.

        ``setup`` is the full ``{"setup": …}`` message from
        :func:`setup_message`; its inner object becomes the token's locked
        constraint. Returns ``{"name": "auth_tokens/…", "expire_time": iso}``.
        """
        now = datetime.datetime.now(datetime.timezone.utc)
        expire_time = (now + TOKEN_TTL).strftime("%Y-%m-%dT%H:%M:%SZ")
        body = {
            "uses": uses,
            "expireTime": expire_time,
            "newSessionExpireTime": (now + NEW_SESSION_WINDOW).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "bidiGenerateContentSetup": setup["setup"],
        }
        try:
            resp = await self._http.post(f"/{API_VERSION}/auth_tokens", json=body)
        except httpx.HTTPError as exc:
            raise RealtimeError(f"Google unreachable: {type(exc).__name__}") from exc
        if resp.status_code in (401, 403):
            raise RealtimeError(
                f"Google rejected the key (HTTP {resp.status_code}) — check it in Settings → Voice"
            )
        if resp.status_code == 429:
            raise RealtimeError("Gemini rate limit hit — try again in a moment")
        if resp.status_code >= 400:
            detail = ""
            try:
                detail = (resp.json().get("error") or {}).get("message") or ""
            except ValueError:
                pass
            raise RealtimeError(
                f"Gemini token minting failed: HTTP {resp.status_code}"
                + (f" — {detail[:200]}" if detail else "")
            )
        data = resp.json()
        if not data.get("name"):
            raise RealtimeError("Google returned no session token")
        # Google's mint response carries only ``name`` — the expiry is the one
        # we requested, so report that.
        return {"name": data["name"], "expire_time": data.get("expireTime") or expire_time}
