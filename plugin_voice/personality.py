"""Personality-matched setup — the brain configures its own voice presence.

At connect time we ask the agent (via the sanctioned ``run_turn`` surface) who
it is: its name, a greeting in its own voice, waiting fillers that sound like
IT (a Terminator waits differently than a butler), and a description of the
voice that would fit. A second call picks the best ElevenLabs voice from the
account's list. Every step degrades gracefully to neutral defaults — connect
must never fail because personality fetch failed.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("plugin-voice.personality")

PERSONA_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "Your name, as you call yourself"},
        "greeting": {
            "type": "string",
            "description": (
                "One short spoken greeting to open a voice call, in your own "
                "voice and personality. MUST include your name. No markdown."
            ),
        },
        "fillers": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Exactly 5 short phrases (2-6 words each) you would naturally "
                "say while working on something during a conversation — your "
                "personality's way of saying 'one moment'. Spoken text only."
            ),
        },
        "voice_description": {
            "type": "string",
            "description": (
                "One sentence describing the text-to-speech voice that fits "
                "you: gender, age feel, tone, pace, accent."
            ),
        },
    },
    "required": ["name", "greeting", "fillers", "voice_description"],
}

PERSONA_PROMPT = (
    "You are being connected to a real-time VOICE interface. Answer with JSON "
    "describing how you should sound. Stay fully in character — your actual "
    "name and personality, not a generic assistant."
)

NEUTRAL = {
    "name": None,
    "greeting": "Hey, I'm listening — what can I do for you?",
    "fillers": ["One moment, I'm checking that...", "Still working on it...", "Almost there, hang on..."],
    "voice_description": None,
}


def _clean_persona(raw: Any) -> dict | None:
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "").strip()[:60]
    greeting = str(raw.get("greeting") or "").strip()[:300]
    fillers = [str(f).strip()[:80] for f in (raw.get("fillers") or []) if str(f).strip()][:5]
    voice_desc = str(raw.get("voice_description") or "").strip()[:300]
    if not name or not greeting:
        return None
    # TTS-safe fillers: ellipsis + trailing space (ElevenLabs buffer-words shape)
    fillers = [f.rstrip(".… ") + "... " for f in fillers if f]
    return {
        "name": name,
        "greeting": greeting,
        "fillers": fillers or list(NEUTRAL["fillers"]),
        "voice_description": voice_desc or None,
    }


async def fetch_persona(ctx: Any, *, timeout: float = 30.0) -> dict:
    """The brain's self-description, or neutral defaults on any failure."""
    import asyncio

    agent = getattr(ctx, "agent", None)
    if agent is None:
        return dict(NEUTRAL)
    try:
        result = await asyncio.wait_for(
            agent.run_turn(
                PERSONA_PROMPT,
                output_schema=PERSONA_SCHEMA,
                tools=[],  # identity question — no tools needed, keeps it fast
                memory_write=False,
            ),
            timeout=timeout,
        )
        raw = result[0] if isinstance(result, tuple) else result
        persona = _clean_persona(raw)
        if persona:
            return persona
        log.warning("plugin-voice: persona reply unusable, using neutral defaults")
    except Exception as exc:  # noqa: BLE001
        log.warning("plugin-voice: persona fetch failed (%s), using neutral defaults", exc)
    return dict(NEUTRAL)


VOICE_PICK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"voice_id": {"type": "string", "description": "The chosen voice_id, verbatim from the list"}},
    "required": ["voice_id"],
}


async def pick_voice(ctx: Any, voices: list[dict], voice_description: str | None, *, timeout: float = 20.0) -> str | None:
    """Let the brain choose the ElevenLabs voice that fits its personality."""
    import asyncio
    import json

    if not voices or not voice_description:
        return None
    agent = getattr(ctx, "agent", None)
    if agent is None:
        return None

    catalog = [
        {"voice_id": v.get("voice_id"), "name": v.get("name"), "labels": v.get("labels") or {}}
        for v in voices
        if v.get("voice_id")
    ][:40]
    prompt = (
        "Pick the ONE text-to-speech voice that best matches this description "
        f"of how you should sound: \"{voice_description}\".\n\n"
        "Available voices (JSON):\n" + json.dumps(catalog, ensure_ascii=False) +
        "\n\nReturn the voice_id of your choice."
    )
    try:
        result = await asyncio.wait_for(
            agent.run_turn(prompt, output_schema=VOICE_PICK_SCHEMA, tools=[], memory_write=False),
            timeout=timeout,
        )
        raw = result[0] if isinstance(result, tuple) else result
        vid = str((raw or {}).get("voice_id") or "").strip() if isinstance(raw, dict) else ""
        if any(v["voice_id"] == vid for v in catalog):
            return vid
        log.warning("plugin-voice: picked voice_id not in catalog, ignoring")
    except Exception as exc:  # noqa: BLE001
        log.warning("plugin-voice: voice pick failed (%s)", exc)
    return None
