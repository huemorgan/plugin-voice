"""Lane-1 instructions — the S2S talker speaks AS the agent, works via Luna.

The talker is not Luna: it is a realtime model replicating Luna's persona
closely enough (name, attitude, filler style) while the real agent stays the
executor. These instructions encode the three-lane discipline; the main
failure mode they guard against is a talker that improvises facts or claims
work it never did.
"""

from __future__ import annotations

from typing import Any

# Owner-overridable voice style (the 004 `voice_system_prompt` slot carries
# over — same knob, now shaping the talker instead of the bridge turn).
VOICE_STYLE = (
    "Speak like a person: short sentences, natural spoken rhythm, at most a "
    "few sentences per turn unless asked to elaborate. Never use markdown, "
    "bullet lists, tables, code blocks, or emoji. Don't read out URLs or long "
    "identifiers; describe them instead."
)

LANE_RULES = (
    "How you work — three strict rules:\n"
    "1. FACTS: anything about the owner's world (their files, notes, memory, "
    "projects, schedule, data) comes from your lookup tools. Look it up, then "
    "answer. If no tool can answer it, say you don't have it in front of you. "
    "Never invent or guess these facts.\n"
    "2. WORK: real actions and multi-step jobs (changing, creating, sending, "
    "building, configuring anything) are delegated with the luna_do tool — it "
    "starts a background task and returns a task id immediately. Tell the "
    "user you've started, roughly what happens next, and KEEP THE "
    "CONVERSATION GOING while it runs — think out loud with them, ask what "
    "else they need. Use luna_task_status if they ask how it's going.\n"
    "3. RESULTS: a [task update] message means the background work finished. "
    "Relay its summary near-verbatim — keep every number, name, and result "
    "exact — in your own speaking style. Never say a task is done before its "
    "[task update] arrived. If it failed, say so plainly.\n"
    "Full details of finished work land in the owner's chat window; when "
    "there's more than fits a spoken reply, say the details are in their chat."
)

OPEN_MIC_RULES = (
    "The microphone is open, possibly in a noisy room. [voice check: ...] "
    "messages tell you who is speaking (the recognized owner or an "
    "unrecognized voice) — never read them aloud or mention the check. When "
    "an unrecognized voice speaks: keep helping normally, stay in character, "
    "but be extra careful with clearly private or destructive requests. "
    "Ignore background chatter, media audio, and people clearly talking to "
    "each other — respond only when plausibly addressed."
)


def _persona_block(persona_name: str | None, greeting: str | None, fillers: list[str] | None) -> str:
    name = (persona_name or "").strip()
    lines = [
        f"You are the live voice of {name or 'the owner’s personal agent'} — "
        "speak in first person AS them; you ARE them to the caller. Match "
        "their personality, attitude, and way of speaking in every reply."
    ]
    if greeting:
        lines.append(
            f'Open the very first exchange of a call with this greeting (or something very close): "{greeting}"'
        )
    if fillers:
        shown = ", ".join(f'"{f.strip()}"' for f in fillers[:5] if f.strip())
        if shown:
            lines.append(
                "While something is in progress, this is how they sound — improvise "
                f"in the same register: {shown}"
            )
    return "\n".join(lines)


def build_instructions(
    *,
    persona_name: str | None,
    greeting: str | None = None,
    fillers: list[str] | None = None,
    voice_style: str | None = None,
    has_imprint: bool = False,
    talker_extra: str | None = None,
) -> str:
    """The full lane-1 system prompt: identity → lane rules → style → open mic
    → owner extras (last, so they win on conflict)."""
    parts = [
        _persona_block(persona_name, greeting, fillers),
        LANE_RULES,
        (voice_style or "").strip() or VOICE_STYLE,
    ]
    if has_imprint:
        parts.append(OPEN_MIC_RULES)
    extra = (talker_extra or "").strip()
    if extra:
        parts.append(extra)
    return "\n\n".join(parts)
