"""Unit tests for the OpenAI-compat ⇄ run_turn bridge logic (pure, no HTTP)."""

from __future__ import annotations

import asyncio
import json

from plugin_voice import bridge
from tests.conftest import FakeCtx, FakeToolRegistry, _RegisteredTool


def _collect(agen):
    async def run():
        return [event async for event in agen]

    return asyncio.run(run())


# ---------------------------------------------------------------- build_prompt


def test_build_prompt_has_voice_style_and_current_message():
    prompt = bridge.build_prompt(
        [
            {"role": "system", "content": "ignored elevenlabs system prompt"},
            {"role": "user", "content": "hello luna"},
        ]
    )
    assert bridge.VOICE_SYSTEM_PROMPT in prompt
    assert "hello luna" in prompt
    assert "ignored elevenlabs" not in prompt  # their system prompt is not ours


def test_build_prompt_trims_history_window():
    messages = [{"role": "user", "content": f"msg {i}"} for i in range(30)]
    prompt = bridge.build_prompt(messages, window=4)
    assert "msg 29" in prompt  # the current utterance
    assert "msg 26" in prompt  # inside the window
    assert "msg 3" not in prompt  # far past is trimmed (Luna's memory covers it)


def test_build_prompt_handles_content_parts():
    prompt = bridge.build_prompt(
        [{"role": "user", "content": [{"type": "text", "text": "spoken words"}]}]
    )
    assert "spoken words" in prompt


# ------------------------------------------------------------------- allowlist


def test_allowlist_excludes_unsafe_tools():
    ctx = FakeCtx()
    allowed = bridge.voice_tool_allowlist(ctx)
    assert "get_weather" in allowed
    assert "restart_service" in allowed  # ask/medium is fine
    assert "delete_everything" not in allowed  # high risk
    assert "send_chat_message" not in allowed  # would double-post


def test_allowlist_falls_back_to_none_without_registry():
    ctx = FakeCtx()
    ctx.tool_registry = None
    assert bridge.voice_tool_allowlist(ctx) is None


def test_allowlist_excludes_prompt_always_even_low_risk():
    ctx = FakeCtx()
    ctx.tool_registry = FakeToolRegistry(
        [_RegisteredTool("confirmy", policy="prompt_always", risk_level="low")]
    )
    assert bridge.voice_tool_allowlist(ctx) is None  # nothing left → None fallback


# ----------------------------------------------------------------- stream_turn


def test_stream_turn_buffer_words_first_then_reply_then_done():
    async def run():
        return "First sentence. Second sentence!"

    events = _collect(bridge.stream_turn(run))
    assert events[-1] == bridge.SSE_DONE
    payloads = [json.loads(e[len("data: "):]) for e in events[:-1]]

    assert payloads[0]["choices"][0]["delta"].get("role") == "assistant"
    assert payloads[1]["choices"][0]["delta"]["content"] in bridge._BUFFER_VARIANTS
    assert bridge.BUFFER_WORDS.endswith("... ")  # ElevenLabs buffer-words shape

    spoken = "".join(p["choices"][0]["delta"].get("content", "") for p in payloads[1:])
    assert "First sentence." in spoken and "Second sentence!" in spoken
    assert payloads[-1]["choices"][0]["finish_reason"] == "stop"
    assert all(p["object"] == "chat.completion.chunk" for p in payloads)


def test_stream_turn_survives_agent_failure():
    async def run():
        raise RuntimeError("tool exploded")

    events = _collect(bridge.stream_turn(run))
    assert events[-1] == bridge.SSE_DONE
    text = "".join(events)
    assert "tool exploded" not in text  # internals never reach the caller
    assert "Sorry" in text


# ----------------------------------------------------------------------- misc


def test_split_speech_chunks_sentences():
    chunks = bridge.split_speech_chunks("One. Two! Three?")
    assert "".join(chunks).replace(" ", "") == "One.Two!Three?".replace(" ", "")


def test_normalize_reply_variants():
    assert bridge.normalize_reply(("hi", None)) == "hi"
    assert bridge.normalize_reply(({"text": "structured"}, None)) == "structured"
    assert bridge.normalize_reply(("", None))  # never empty → fallback phrase


def test_completion_json_shape():
    body = bridge.completion_json("hello")
    assert body["object"] == "chat.completion"
    assert body["choices"][0]["message"]["content"] == "hello"
    assert body["choices"][0]["finish_reason"] == "stop"


def test_stream_turn_keepalive_during_slow_tool_turns():
    """Dead air on long tool calls invites barge-in that cancels the reply —
    the stream must keep speaking while the agent works."""
    import asyncio as aio

    async def slow_run():
        await aio.sleep(0.12)
        return "Done at last."

    events = _collect(bridge.stream_turn(slow_run, keepalive_interval=0.03))
    text = "".join(events)
    assert any(k.strip() in text for k in bridge.KEEPALIVE_WORDS)
    assert "Done at last." in text
    assert events[-1] == bridge.SSE_DONE
