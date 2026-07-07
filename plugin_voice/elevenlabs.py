"""ElevenLabs REST calls — all of them live here, nowhere else.

The API key is passed at construction (resolved from the vault by routes) and
only ever sent as the ``xi-api-key`` header. Never logged.
"""

from __future__ import annotations

from typing import Any

import httpx

DEFAULT_BASE_URL = "https://api.elevenlabs.io"


class ElevenLabsError(Exception):
    """A failed ElevenLabs call, with a safe (key-free) message."""


class ElevenLabsClient:
    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        headers: dict | None = None,
        params: dict | None = None,
    ) -> None:
        """Direct key (xi-api-key header) or pre-built auth from a vault
        Connection (gateway keys arrive with their own header/base_url)."""
        self._http = httpx.AsyncClient(
            base_url=base_url,
            headers=headers if headers is not None else {"xi-api-key": api_key or ""},
            params=params or None,
            timeout=httpx.Timeout(15.0),
        )

    async def close(self) -> None:
        await self._http.aclose()

    async def _req(self, method: str, path: str, *, params: dict | None = None, json: Any = None) -> dict[str, Any]:
        try:
            resp = await self._http.request(method, path, params=params, json=json)
        except httpx.HTTPError as exc:
            raise ElevenLabsError(f"ElevenLabs unreachable: {type(exc).__name__}") from exc
        if resp.status_code >= 400:
            raise ElevenLabsError(f"ElevenLabs {path} failed: HTTP {resp.status_code}")
        if resp.status_code == 204 or not resp.content:
            return {}
        return resp.json()

    async def _get(self, path: str, **params: Any) -> dict[str, Any]:
        return await self._req("GET", path, params=params or None)

    async def list_voices(self) -> list[dict[str, Any]]:
        """The account's voices, trimmed to what the picker needs."""
        data = await self._get("/v1/voices")
        return [
            {
                "voice_id": v.get("voice_id"),
                "name": v.get("name"),
                "category": v.get("category"),
                "preview_url": v.get("preview_url"),
                "labels": v.get("labels") or {},
            }
            for v in data.get("voices", [])
            if v.get("voice_id")
        ]

    async def conversation_token(self, agent_id: str) -> str | None:
        """Short-lived WebRTC conversation token for a (private) agent."""
        try:
            data = await self._get("/v1/convai/conversation/token", agent_id=agent_id)
        except ElevenLabsError:
            return None
        return data.get("token") or None

    async def signed_url(self, agent_id: str) -> str | None:
        """Signed WebSocket URL — fallback transport when token/WebRTC is unavailable."""
        try:
            data = await self._get("/v1/convai/conversation/get-signed-url", agent_id=agent_id)
        except ElevenLabsError:
            return None
        return data.get("signed_url") or None

    # ------------------------------------------------------- agent provisioning

    async def _ensure_secret(self, value: str) -> str:
        """A workspace secret holding the bridge token; returns its secret_id.

        ElevenLabs STRIPS a plain Authorization entry from custom request_headers
        (verified live: every call arrived as 401), so the token must be a
        workspace secret referenced as ``custom_llm.api_key`` — ElevenLabs then
        sends ``Authorization: Bearer <secret>`` itself. Secret values can't be
        read back, so the name embeds a hash of the value: same secret → reuse,
        rotated secret → new name.
        """
        import hashlib

        name = f"luna-talk-bridge-{hashlib.sha256(value.encode()).hexdigest()[:10]}"
        try:
            data = await self._get("/v1/convai/secrets")
            for s in data.get("secrets", []):
                if s.get("name") == name and s.get("secret_id"):
                    return s["secret_id"]
        except ElevenLabsError:
            pass
        data = await self._req(
            "POST", "/v1/convai/secrets", json={"type": "new", "name": name, "value": value}
        )
        secret_id = data.get("secret_id")
        if not secret_id:
            raise ElevenLabsError("secret create returned no secret_id")
        return secret_id

    # Bump when _agent_config changes shape; installs stamp it into settings
    # and /session re-PATCHes agents provisioned under an older shape once.
    # v2: barge-in disabled + transcribe_on_disabled_interruptions (open mic).
    AGENT_CONFIG_V = 2

    @staticmethod
    def _agent_config(
        custom_llm_url: str,
        secret_id: str,
        *,
        first_message: str | None = None,
        fillers: list[str] | None = None,
        voice_id: str | None = None,
        request_headers: dict[str, str] | None = None,
        overrides: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        # api_type "chat_completions": ElevenLabs appends /chat/completions to
        # the url, so we hand it the bridge base ending at .../v1 (verified
        # live against the API, 2026-07). Soft-timeout fillers + patient turns
        # keep slow tool-using brains alive in noisy rooms (001 findings); the
        # filler texts come from the brain's own personality when available.
        # ``overrides`` are the owner's Voice Persona tweaks (004): passthrough
        # prompt, soft-timeout seconds/count, turn eagerness. Absent → the
        # shipped values below, byte-identical to pre-004 configs.
        ov = overrides or {}
        fillers = [f.strip() for f in (fillers or []) if f and f.strip()][:5]
        primary = fillers[0] if fillers else "One moment, I'm checking that..."
        rest = fillers[1:] if len(fillers) > 1 else ["Still working on it...", "Almost there, hang on..."]
        config: dict[str, Any] = {
            # Barge-in OFF: in a noisy room every stray voice used to cut the
            # agent mid-sentence. "interruption" absent from client_events
            # disables it; what people say while the agent talks is still
            # transcribed and carried into the next turn, where the brain (plus
            # the bridge's fast triage) decides if it matters.
            "conversation": {
                "client_events": [
                    "conversation_initiation_metadata",
                    "asr_initiation_metadata",
                    "ping",
                    "audio",
                    "user_transcript",
                    "agent_response",
                    "agent_response_correction",
                    "vad_score",
                ],
            },
            "turn": {
                "turn_eagerness": ov.get("turn_eagerness") or "patient",
                "transcribe_on_disabled_interruptions": True,
                "soft_timeout_config": {
                    "timeout_seconds": float(ov.get("soft_timeout_seconds") or 5.0),
                    "message": primary,
                    "additional_soft_timeout_messages": rest,
                    "randomize_fillers": True,
                    "max_soft_timeouts_per_generation": (
                        ov["max_soft_timeouts"] if ov.get("max_soft_timeouts") is not None else 3
                    ),
                    "use_llm_generated_message": False,
                },
            },
            "agent": {
                # Neutral fallback: the brain's real name/personality arrives
                # via the persona fetch; this is only used if that failed.
                "first_message": first_message or "Hey, I'm listening — what can I do for you?",
                "prompt": {
                    "prompt": ov.get("passthrough_prompt") or (
                        "Every reply is produced by the connected custom LLM "
                        "(the agent's own loop, with its real name and "
                        "personality); pass conversation through faithfully."
                    ),
                    "llm": "custom-llm",
                    "custom_llm": {
                        "url": custom_llm_url,
                        "model_id": "luna",
                        "api_key": {"secret_id": secret_id},
                        # Non-auth routing headers only (e.g. Fly's
                        # fly-force-instance-id machine pin) — ElevenLabs strips
                        # a plain Authorization entry here, hence api_key above.
                        "request_headers": dict(request_headers or {}),
                    },
                },
            },
        }
        if voice_id:
            config["tts"] = {"voice_id": voice_id}
        return config

    async def find_agent(self, name: str) -> str | None:
        data = await self._get("/v1/convai/agents", page_size=100)
        for agent in data.get("agents", []):
            if agent.get("name") == name and agent.get("agent_id"):
                return agent["agent_id"]
        return None

    async def create_agent(
        self,
        name: str,
        *,
        custom_llm_url: str,
        bridge_secret: str,
        first_message: str | None = None,
        fillers: list[str] | None = None,
        voice_id: str | None = None,
        request_headers: dict[str, str] | None = None,
        overrides: dict[str, Any] | None = None,
    ) -> str:
        secret_id = await self._ensure_secret(bridge_secret)
        data = await self._req(
            "POST",
            "/v1/convai/agents/create",
            json={
                "name": name,
                "conversation_config": self._agent_config(
                    custom_llm_url, secret_id,
                    first_message=first_message, fillers=fillers, voice_id=voice_id,
                    request_headers=request_headers, overrides=overrides,
                ),
            },
        )
        agent_id = data.get("agent_id")
        if not agent_id:
            raise ElevenLabsError("agent create returned no agent_id")
        return agent_id

    async def update_agent_bridge(
        self,
        agent_id: str,
        *,
        custom_llm_url: str,
        bridge_secret: str,
        first_message: str | None = None,
        fillers: list[str] | None = None,
        voice_id: str | None = None,
        request_headers: dict[str, str] | None = None,
        overrides: dict[str, Any] | None = None,
    ) -> None:
        """Re-point an existing agent at the (possibly moved) bridge + fresh secret."""
        secret_id = await self._ensure_secret(bridge_secret)
        await self._req(
            "PATCH",
            f"/v1/convai/agents/{agent_id}",
            json={
                "conversation_config": self._agent_config(
                    custom_llm_url, secret_id,
                    first_message=first_message, fillers=fillers, voice_id=voice_id,
                    request_headers=request_headers, overrides=overrides,
                ),
            },
        )

    async def get_agent_bridge(self, agent_id: str) -> dict[str, Any] | None:
        """The agent's current custom-LLM config: ``{"url", "request_headers"}``
        (None if the agent or its custom_llm block is missing)."""
        try:
            data = await self._get(f"/v1/convai/agents/{agent_id}")
        except ElevenLabsError:
            return None
        prompt = ((data.get("conversation_config") or {}).get("agent") or {}).get("prompt") or {}
        custom = prompt.get("custom_llm") or {}
        if not custom.get("url"):
            return None
        return {"url": custom["url"], "request_headers": custom.get("request_headers") or {}}

    async def get_agent_bridge_url(self, agent_id: str) -> str | None:
        """The custom-LLM url currently configured on the agent (None if unset)."""
        cfg = await self.get_agent_bridge(agent_id)
        return cfg["url"] if cfg else None

    async def set_agent_voice(self, agent_id: str, voice_id: str | None) -> None:
        """Set the agent's TTS voice (agent default — no per-session override needed)."""
        if not voice_id:
            return
        await self._req(
            "PATCH",
            f"/v1/convai/agents/{agent_id}",
            json={"conversation_config": {"tts": {"voice_id": voice_id}}},
        )
