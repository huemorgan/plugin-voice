"""OpenAI-compatible bridge between ElevenLabs Agents and Luna's agent loop.

ElevenLabs "Custom LLM" POSTs a Chat Completions request here and expects an
SSE stream back (``data: {chunk}\n\n`` … ``data: [DONE]``). Luna's sanctioned
plugin surface, ``ctx.agent.run_turn``, is headless and NON-streaming — it
returns the finished reply. So the stream opens with a short "buffer words"
chunk (ElevenLabs' documented pattern for slow custom LLMs: text ending in
``"... "`` keeps TTS flowing naturally) and then streams the finished reply in
sentence-sized chunks.

Pure logic only — no FastAPI, no vault — so every piece is unit-testable.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

# ElevenLabs buffer-words pattern: ellipsis + trailing space avoids TTS
# distortion while Luna thinks (docs: eleven-agents custom-llm "Buffer words").
# Varied so back-to-back replies don't all open with the same phrase; picked by
# reply length hash (deterministic — no random in tests).
BUFFER_WORDS = "Mm... "
_BUFFER_VARIANTS = ("Mm... ", "Hmm... ", "So... ", "Right... ", "Okay... ")


def pick_buffer_words(seed: str, variants: list[str] | tuple[str, ...] | None = None) -> str:
    pool = list(variants) if variants else list(_BUFFER_VARIANTS)
    return pool[sum(seed.encode()) % len(pool)] if seed else pool[0]

# How many trailing conversation messages to forward into the prompt. ElevenLabs
# resends the full transcript each call; Luna's own memory covers the long tail.
HISTORY_WINDOW = 8

VOICE_SYSTEM_PROMPT = (
    "You are having a real-time VOICE conversation — your reply will be read "
    "aloud by text-to-speech. Speak like a person: short sentences, natural "
    "spoken rhythm, at most a few sentences per turn unless asked to elaborate. "
    "Never use markdown, bullet lists, tables, code blocks, or emoji. Don't "
    "read out URLs or long identifiers; describe them instead. If a task will "
    "take a while, say so briefly and give the short version first."
)

# Tools a voice turn must NOT get. run_turn does not enforce approval policy
# (same caveat plugin-whatsapp documents), so anything that would normally
# prompt the user has no gate here — exclude by name AND by def policy/risk.
TOOL_EXCLUDE = {"send_chat_message"}


def voice_tool_allowlist(ctx: Any) -> list[str] | None:
    """Every registered tool minus unsafe-for-voice ones.

    Excludes by def when the registry exposes defs: ``risk_level="high"`` and
    ``policy="prompt_always"`` tools are dropped (no approval UX exists on a
    voice turn). Falls back to name-only exclusion, then to ``None`` (all
    tools — run_turn still filters chat_only/skill_gated) if the registry
    can't be introspected, so a reply is never blocked by introspection.
    """
    reg = getattr(ctx, "tool_registry", None)
    items: list[Any] | None = None
    for attr in ("all", "names", "tool_names"):
        fn = getattr(reg, attr, None)
        if callable(fn):
            try:
                got = list(fn())
            except Exception:  # noqa: BLE001
                continue
            if got:
                items = got
                break
    if not items:
        return None

    allowed: list[str] = []
    for it in items:
        tool_def = getattr(it, "definition", None) or it
        name = getattr(tool_def, "name", None) or (it if isinstance(it, str) else None)
        if not isinstance(name, str) or name in TOOL_EXCLUDE:
            continue
        if getattr(tool_def, "risk_level", None) == "high":
            continue
        if getattr(tool_def, "policy", None) == "prompt_always":
            continue
        allowed.append(name)
    return allowed or None


def build_prompt(messages: list[dict[str, Any]], *, window: int = HISTORY_WINDOW) -> str:
    """Fold the OpenAI ``messages`` array into a single run_turn prompt.

    The voice style preamble leads; a trimmed transcript window follows; the
    latest user utterance closes the prompt as the thing to answer.
    """
    convo = [m for m in messages if m.get("role") in ("user", "assistant") and _text(m)]
    tail = convo[-window:]
    current = ""
    if tail and tail[-1].get("role") == "user":
        current = _text(tail[-1])
        tail = tail[:-1]

    parts = [VOICE_SYSTEM_PROMPT]
    if tail:
        lines = [f"{'You' if m['role'] == 'assistant' else 'Owner'}: {_text(m)}" for m in tail]
        parts.append("[Recent voice conversation]\n" + "\n".join(lines))
    parts.append(f"The owner just said (respond to this, aloud):\n{current or '(silence)'}")
    return "\n\n".join(parts)


def _text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):  # OpenAI content-parts form
        return " ".join(
            p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"
        ).strip()
    return ""


def split_speech_chunks(text: str, *, max_len: int = 240) -> list[str]:
    """Sentence-ish chunks sized for TTS streaming."""
    text = (text or "").strip()
    if not text:
        return []
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks: list[str] = []
    buf = ""
    for s in sentences:
        candidate = f"{buf} {s}".strip() if buf else s
        if len(candidate) > max_len and buf:
            chunks.append(buf + " ")
            buf = s
        else:
            buf = candidate
    if buf:
        chunks.append(buf)
    return chunks


def _chunk_payload(
    completion_id: str,
    created: int,
    *,
    delta: dict[str, Any],
    finish_reason: str | None = None,
) -> dict[str, Any]:
    return {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": "luna",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }


def sse_event(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


SSE_DONE = "data: [DONE]\n\n"


# Spoken while a slow (tool-using) turn is still running, so the line never
# goes dead — dead air makes the user talk over the pending reply, and the
# barge-in then cancels it (observed in the first real call, 2026-07-04).
KEEPALIVE_WORDS = ("Still on it... ", "One sec... ", "Almost there... ", "Working on it... ")
KEEPALIVE_INTERVAL = 7.0


async def stream_turn(
    run: Callable[[], Awaitable[str]],
    *,
    buffer_words: str = BUFFER_WORDS,
    keepalive_interval: float = KEEPALIVE_INTERVAL,
    keepalive_words: tuple[str, ...] | list[str] | None = None,
) -> AsyncIterator[str]:
    """Yield SSE events for one voice turn.

    ``run`` is the deferred agent call (returns final reply text). The buffer
    chunk goes out immediately so ElevenLabs starts TTS while Luna works, a
    keepalive phrase covers every further ``keepalive_interval`` seconds of a
    slow tool-using turn, and if the turn fails we still speak a graceful
    error and close the stream correctly — a hung/500 response would stall the
    call mid-sentence.
    """
    import asyncio

    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())

    def content(text: str) -> str:
        return sse_event(_chunk_payload(completion_id, created, delta={"content": text}))

    yield sse_event(_chunk_payload(completion_id, created, delta={"role": "assistant"}))
    if buffer_words:
        yield content(buffer_words)

    task = asyncio.ensure_future(run())
    lively = list(keepalive_words or KEEPALIVE_WORDS)
    beat = 0
    while True:
        try:
            reply = await asyncio.wait_for(asyncio.shield(task), timeout=keepalive_interval)
            break
        except asyncio.TimeoutError:
            yield content(lively[beat % len(lively)])
            beat += 1
        except Exception:  # noqa: BLE001 — never leak internals into spoken audio
            reply = "Sorry, something went wrong on my side. Ask me again in a moment."
            break

    for chunk in split_speech_chunks(reply):
        yield content(chunk)

    yield sse_event(_chunk_payload(completion_id, created, delta={}, finish_reason="stop"))
    yield SSE_DONE


def completion_json(reply: str) -> dict[str, Any]:
    """Non-streaming Chat Completions response (Vapi-style clients accept plain JSON)."""
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": "luna",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": reply},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def normalize_reply(result: Any) -> str:
    """run_turn returns ``(text_or_dict, meta)``; be liberal in what we accept."""
    value = result[0] if isinstance(result, tuple) and result else result
    if isinstance(value, dict):
        value = value.get("text") or value.get("content") or json.dumps(value, ensure_ascii=False)
    text = str(value or "").strip()
    return text or "I don't have an answer for that right now."
