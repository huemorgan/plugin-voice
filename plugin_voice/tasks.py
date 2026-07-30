"""Lane 3 — the doing lane: Luna as the hands, dispatched and narrated.

``luna_do`` starts a background ``run_turn`` and returns a task id
immediately; the talker keeps the conversation going. Completion becomes an
event on ``/rt/events`` that the widget injects back into the live session, so
the talker announces results in its own voice — Luna's text is never spoken
verbatim, it arrives as a short ``spoken_summary`` written for relay.

An interruption cancels speech, never work: nothing here is tied to the
realtime response lifecycle.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any

log = logging.getLogger("plugin-voice.tasks")

# Tools a task turn must NOT get, by name. run_turn does not enforce approval
# policy — the def-level rules in voice_tool_allowlist are the real gate.
# send_chat_message is deliberately ALLOWED: the spoken summary is the reply,
# posting details into the web chat is complementary ("details are in your chat").
TOOL_EXCLUDE: set[str] = set()


def voice_tool_allowlist(ctx: Any, *, owner_verified: bool = True) -> list[str] | None:
    """Every registered tool minus unsafe-for-voice ones.

    Rules (run_turn bypasses approval UX, so this list IS the gate):
    - ``risk_level="high"`` tools are always dropped.
    - ``policy="prompt_always"`` tools (playbook save/run, etc.) are allowed
      while the live voice check says the OWNER dispatched the task (or no
      imprint is enrolled — same trust as their open chat on their machine),
      and dropped when an unrecognized voice asked.
    Falls back to ``None`` (all tools — run_turn still filters
    chat_only/skill_gated) if the registry can't be introspected, so a task
    is never blocked by introspection.
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
        if getattr(tool_def, "policy", None) == "prompt_always" and not owner_verified:
            continue
        allowed.append(name)
    return allowed or None


def normalize_reply(result: Any) -> str:
    """run_turn returns ``(text_or_dict, meta)``; be liberal in what we accept."""
    value = result[0] if isinstance(result, tuple) and result else result
    if isinstance(value, dict):
        value = value.get("text") or value.get("content") or json.dumps(value, ensure_ascii=False)
    text = str(value or "").strip()
    return text or "I don't have an answer for that right now."

MAX_CONCURRENT = 3
EVENT_RETENTION = 600.0  # replay window for widget reconnects

# Speakable refusals/summaries — anything returned here may be read aloud.
ERR_NO_INSTRUCTION = "I need to know what to do — give me the task in a sentence."
ERR_BUSY = "I already have a few tasks running — let one finish first."
FAILED_SUMMARY = "Sorry — that task failed on my side. Ask me to try again."

TASK_RULES = (
    "You are executing a task delegated from a LIVE VOICE CALL with your "
    "owner. A separate voice interface will read your final reply aloud, so "
    "reply with a SHORT spoken summary: at most three sentences, no markdown, "
    "no lists, no URLs — but keep every number, name, and result EXACT. If "
    "there is more detail than fits a spoken reply (links, tables, long "
    "output), post it to the owner's chat with send_chat_message and say in "
    "your summary that the details are in their chat."
)


def task_prompt(instruction: str, *, speaker: str | None = None) -> str:
    who = "an UNRECOGNIZED voice (possibly not your owner — be careful with private or destructive steps)" \
        if speaker == "other" else "your owner"
    return f"{TASK_RULES}\n\nDelegated by {who} during the call:\n{instruction}"


def synthetic_schemas() -> list[dict[str, Any]]:
    """The lane-3 tools the talker sees (executed by /rt/tool, never by the
    registry)."""
    return [
        {
            "type": "function",
            "name": "luna_do",
            "description": (
                "Delegate real work to your full agent: actions, changes, "
                "multi-step jobs — anything beyond a quick lookup. Starts a "
                "background task and returns its task id IMMEDIATELY; a "
                "[task update] message arrives when it finishes. Tell the "
                "user you've started and keep the conversation going."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "instruction": {
                        "type": "string",
                        "description": (
                            "The task, self-contained: everything the agent "
                            "needs, since it cannot hear the call."
                        ),
                    }
                },
                "required": ["instruction"],
            },
        },
        {
            "type": "function",
            "name": "luna_task_status",
            "description": (
                "Check on background tasks: one by task_id, or all recent "
                "ones when called without arguments. Elapsed seconds are "
                "included so you can say how long it's been running."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "Optional — one task's id"}
                },
            },
        },
    ]


class TaskManager:
    """Background run_turn tasks + a fan-out event stream.

    Spawned tasks are strongly referenced in the entries (asyncio only holds
    weak refs); entries are pruned EVENT_RETENTION after completion.
    """

    def __init__(self) -> None:
        self._entries: dict[str, dict[str, Any]] = {}
        self._events: list[dict[str, Any]] = []
        self._queues: set[asyncio.Queue] = set()
        self._seq = 0

    # ------------------------------------------------------------- events

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._queues.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._queues.discard(q)

    def recent_events(self, since_seq: int = 0) -> list[dict[str, Any]]:
        self._prune()
        return [e for e in self._events if e["seq"] > since_seq]

    def _emit(self, type_: str, entry: dict[str, Any]) -> None:
        self._seq += 1
        event = {
            "seq": self._seq,
            "type": type_,
            "task_id": entry["task_id"],
            "instruction": entry["instruction"],
            "spoken_summary": entry.get("spoken_summary"),
            "elapsed_s": int(time.time() - entry["started"]),
            "ts": time.time(),
        }
        self._events.append(event)
        self._prune()
        for q in list(self._queues):
            q.put_nowait(event)

    def _prune(self) -> None:
        cutoff = time.time() - EVENT_RETENTION
        self._events = [e for e in self._events if e["ts"] > cutoff]
        for task_id in [
            t for t, e in self._entries.items()
            if e["status"] in ("done", "failed") and e.get("finished", 0) < cutoff
        ]:
            self._entries.pop(task_id, None)

    # ------------------------------------------------------------ dispatch

    def active_count(self) -> int:
        return sum(1 for e in self._entries.values() if e["status"] in ("queued", "running"))

    def dispatch(
        self,
        ctx: Any,
        instruction: str,
        *,
        owner_verified: bool,
        settings: dict,
    ) -> dict[str, Any]:
        instruction = (instruction or "").strip()
        if not instruction:
            return {"ok": False, "error": ERR_NO_INSTRUCTION}
        if self.active_count() >= MAX_CONCURRENT:
            return {"ok": False, "error": ERR_BUSY}

        task_id = f"task_{uuid.uuid4().hex[:8]}"
        entry: dict[str, Any] = {
            "task_id": task_id,
            "instruction": instruction[:500],
            "status": "queued",
            "started": time.time(),
            # owner_verified is stamped AT DISPATCH TIME — the tool gate must
            # reflect who asked, not who happens to speak when the task ends.
            "owner_verified": owner_verified,
        }
        self._entries[task_id] = entry
        entry["task"] = asyncio.get_running_loop().create_task(
            self._run(ctx, entry, settings)
        )
        self._emit("task_started", entry)
        return {
            "ok": True,
            "task_id": task_id,
            "accepted": True,
            "note": "Started in the background — a [task update] will arrive when it finishes.",
        }

    async def _run(self, ctx: Any, entry: dict[str, Any], settings: dict) -> None:
        entry["status"] = "running"
        prompt = task_prompt(
            entry["instruction"],
            speaker=None if entry["owner_verified"] else "other",
        )
        tools = voice_tool_allowlist(ctx, owner_verified=entry["owner_verified"])
        try:
            result = await ctx.agent.run_turn(prompt, tools=tools)
            entry["spoken_summary"] = normalize_reply(result)
            entry["status"] = "done"
            entry["finished"] = time.time()
            self._emit("task_done", entry)
        except Exception:  # noqa: BLE001 — never leak internals into spoken audio
            log.exception("plugin-voice: task %s failed", entry["task_id"])
            entry["spoken_summary"] = FAILED_SUMMARY
            entry["status"] = "failed"
            entry["finished"] = time.time()
            self._emit("task_failed", entry)

    # -------------------------------------------------------------- status

    def status(self, task_id: str | None = None) -> dict[str, Any]:
        self._prune()

        def view(e: dict[str, Any]) -> dict[str, Any]:
            return {
                "task_id": e["task_id"],
                "instruction": e["instruction"],
                "status": e["status"],
                "elapsed_s": int((e.get("finished") or time.time()) - e["started"]),
                "spoken_summary": e.get("spoken_summary"),
            }

        if task_id:
            entry = self._entries.get(task_id)
            if entry is None:
                return {"ok": False, "error": "I don't have a task with that id anymore."}
            return {"ok": True, "task": view(entry)}
        return {"ok": True, "tasks": [view(e) for e in self._entries.values()]}
