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
    assert "delete_everything" not in allowed  # high risk stays out
    assert "send_chat_message" in allowed  # spoken reply + chat post are complementary


def test_allowlist_prompt_always_gated_on_owner_voice():
    ctx = FakeCtx()
    ctx.tool_registry = FakeToolRegistry(
        [_RegisteredTool("playbook_propose", policy="prompt_always", risk_level="medium"),
         _RegisteredTool("get_weather")]
    )
    trusted = bridge.voice_tool_allowlist(ctx, owner_verified=True)
    assert "playbook_propose" in trusted
    guarded = bridge.voice_tool_allowlist(ctx, owner_verified=False)
    assert "playbook_propose" not in guarded and "get_weather" in guarded


def test_allowlist_falls_back_to_none_without_registry():
    ctx = FakeCtx()
    ctx.tool_registry = None
    assert bridge.voice_tool_allowlist(ctx) is None


def test_allowlist_unverified_voice_drops_prompt_always_low_risk_too():
    ctx = FakeCtx()
    ctx.tool_registry = FakeToolRegistry(
        [_RegisteredTool("confirmy", policy="prompt_always", risk_level="low")]
    )
    assert bridge.voice_tool_allowlist(ctx, owner_verified=False) is None


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


# ------------------------------------------------- open-mic mode (no barge-in)


def test_build_prompt_labels_unrecognized_speaker():
    prompt = bridge.build_prompt(
        [{"role": "user", "content": "turn off the lights"}], speaker="other"
    )
    assert "UNRECOGNIZED voice" in prompt
    assert "turn off the lights" in prompt


def test_build_prompt_labels_owner_speaker():
    prompt = bridge.build_prompt([{"role": "user", "content": "hi"}], speaker="owner")
    assert "The owner just said" in prompt


def test_is_skip_variants():
    assert bridge.is_skip("SKIP")
    assert bridge.is_skip(" skip. ")
    assert bridge.is_skip('"SKIP"')
    assert not bridge.is_skip("skipping the meeting works for me")
    assert not bridge.is_skip("")


def test_stream_turn_skip_reply_speaks_nothing():
    async def run():
        return "SKIP"

    events = _collect(bridge.stream_turn(run, buffer_words=""))
    contents = [
        json.loads(e.removeprefix("data: "))["choices"][0]["delta"].get("content")
        for e in events
        if e.startswith("data: {")
    ]
    assert not any(contents)  # role delta + finish only — no spoken text
    assert events[-1] == bridge.SSE_DONE


def test_silent_stream_is_wellformed_and_empty():
    events = _collect(bridge.silent_stream())
    assert events[-1] == bridge.SSE_DONE
    payloads = [json.loads(e.removeprefix("data: ")) for e in events if e.startswith("data: {")]
    assert payloads[-1]["choices"][0]["finish_reason"] == "stop"
    assert not any(p["choices"][0]["delta"].get("content") for p in payloads)


def test_triage_respond_and_skip():
    async def say_skip(prompt, **kw):
        return ("SKIP", None)

    async def say_respond(prompt, **kw):
        return ("RESPOND", None)

    assert asyncio.run(bridge.triage_utterance(say_skip, "blah blah tv noise")) is False
    assert asyncio.run(bridge.triage_utterance(say_respond, "what time is it?")) is True


def test_triage_fails_open_on_error_and_timeout():
    async def boom(prompt, **kw):
        raise RuntimeError("no fast model on this Luna")

    async def hang(prompt, **kw):
        await asyncio.sleep(5)

    assert asyncio.run(bridge.triage_utterance(boom, "hello?")) is True
    assert asyncio.run(bridge.triage_utterance(hang, "hello?", timeout=0.05)) is True


def test_triage_skips_empty_utterance_without_llm_call():
    async def never(prompt, **kw):
        raise AssertionError("should not be called")

    assert asyncio.run(bridge.triage_utterance(never, "   ")) is False


def test_agent_config_disables_barge_in_but_transcribes():
    from plugin_voice.elevenlabs import ElevenLabsClient

    config = ElevenLabsClient._agent_config("https://x/v1", "sec_1")
    assert "interruption" not in config["conversation"]["client_events"]
    assert "audio" in config["conversation"]["client_events"]
    assert config["turn"]["transcribe_on_disabled_interruptions"] is True
