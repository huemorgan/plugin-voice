"""Lane 2 — read-only tool broker: sub-second lookups, no LLM turn.

The talker calls Luna tools as native Realtime functions; this module decides
WHICH tools are safe to expose and executes them directly against the
registry. Direct handler calls bypass ``pre_gate_check`` and approval policy,
so the selection here IS the gate — conservative by construction:

    auto_approve  AND  risk_level == "low"  AND  not skill_gated
    AND a read-verb in the name  AND  no write-verb in the name

Positive selection, not a deny-list: a real Luna registers hundreds of tools,
and plugins routinely stamp writes as auto_approve/low (``item_upsert``,
``wiki_create_wiki``, ``playbook_run`` were all admitted by an earlier
suffix-deny heuristic). A tool gets in only when its name *says* it reads.

The owner can extend (``rt_tools_allow``) or trim (``rt_tools_deny``) by exact
name in settings; an extension never admits a high-risk tool.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

log = logging.getLogger("plugin-voice.broker")

# send_chat_message is a write — lane 3 posts details there itself.
DENY: frozenset[str] = frozenset({"send_chat_message"})

# A name token that promises a read. "wiki_read" ✓, "ff_portfolio_summary" ✓,
# "research" ✗ (slow — that's what luna_do is for), "goal_update" ✗.
READ_TOKENS: frozenset[str] = frozenset({
    "get", "list", "read", "search", "recall", "status", "summary",
    "toc", "cite", "fetch", "context", "detail", "brief", "lookup",
})
# A name token that smells like a mutation — vetoes even when a read token is
# present ("get_or_create_page" style names).
WRITE_TOKENS: frozenset[str] = frozenset({
    "connect", "delete", "create", "write", "set", "update", "add", "remove",
    "patch", "edit", "run", "upsert", "save", "log", "record", "advance",
    "complete", "cancel", "pause", "resume", "start", "open", "close",
    "send", "act", "draft", "revise", "propose", "nudge",
})

# Cap on heuristic picks — a voice session with hundreds of function schemas
# is slow to mint and degrades the talker. Knowledge-first prefixes win the
# cut; owner rt_tools_allow entries never count against the cap.
MAX_TOOLS = 32
PREFERRED_PREFIXES = ("memory", "wiki", "read", "web", "recall", "file", "goal", "tasks")

EXEC_TIMEOUT = 8.0

# Speakable errors — whatever we return may be read aloud by the talker.
ERR_UNAVAILABLE = "That tool isn't available on this call."
ERR_TIMEOUT = "That lookup took too long — try asking me to do it as a task."
ERR_FAILED = "That lookup failed on my side."
ERR_OWNER_ONLY = "I can only look things up for my owner, and this doesn't sound like them."


def _def_of(item: Any) -> Any:
    return getattr(item, "definition", None) or item


def _effective_policy(tool_def: Any) -> str | None:
    """Policy with the legacy ``gated=True`` flag resolved (falls back to the
    raw ``.policy`` field on older ToolDefs without the method)."""
    fn = getattr(tool_def, "effective_policy", None)
    if callable(fn):
        try:
            return fn()
        except Exception:  # noqa: BLE001
            pass
    return getattr(tool_def, "policy", None)


def _name_of(item: Any) -> str | None:
    name = getattr(_def_of(item), "name", None)
    return name if isinstance(name, str) and name else None


def _reads_by_name(name: str) -> bool:
    tokens = set(name.lower().split("_"))
    return bool(tokens & READ_TOKENS) and not (tokens & WRITE_TOKENS)


def _allow_deny(settings: dict) -> tuple[set[str], set[str]]:
    allow = {str(n) for n in settings.get("rt_tools_allow") or [] if str(n).strip()}
    deny = {str(n) for n in settings.get("rt_tools_deny") or [] if str(n).strip()}
    return allow, deny


def knowledge_tools(ctx: Any, settings: dict) -> list[Any]:
    """The registry tools the talker may call directly. Empty on any registry
    trouble — lane 2 degrades to 'no lookups', never to 'all tools'."""
    reg = getattr(ctx, "tool_registry", None)
    all_fn = getattr(reg, "all", None)
    if not callable(all_fn):
        return []
    try:
        items = list(all_fn())
    except Exception:  # noqa: BLE001
        return []

    allow, deny = _allow_deny(settings)
    owner_picked: list[Any] = []
    heuristic: list[tuple[tuple[int, str], Any]] = []
    for item in items:
        name = _name_of(item)
        if not name or name in deny:
            continue
        tool_def = _def_of(item)
        risk = getattr(tool_def, "risk_level", None)
        if name in allow:
            if risk != "high":
                owner_picked.append(item)
            continue
        if name in DENY:
            continue
        # effective_policy resolves the legacy gated=True flag — a gated tool
        # left at default auto_approve must NOT read as safe-for-voice.
        if _effective_policy(tool_def) != "auto_approve" or risk != "low":
            continue
        if getattr(item, "skill_gated", False) or getattr(tool_def, "skill_gated", False):
            continue
        if not _reads_by_name(name):
            continue
        preferred = 0 if name.lower().startswith(PREFERRED_PREFIXES) else 1
        heuristic.append(((preferred, name), item))

    heuristic.sort(key=lambda pair: pair[0])
    if len(heuristic) > MAX_TOOLS:
        dropped = [n for (_, n), _ in heuristic[MAX_TOOLS:]]
        log.info(
            "plugin-voice: lane-2 capped at %d tools, dropped %d: %s",
            MAX_TOOLS, len(dropped), ", ".join(dropped),
        )
    return owner_picked + [item for _, item in heuristic[:MAX_TOOLS]]


def tool_schemas(tools: list[Any]) -> list[dict[str, Any]]:
    """Realtime function-tool schemas, unique by name."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in tools:
        name = _name_of(item)
        if not name or name in seen:
            continue
        seen.add(name)
        tool_def = _def_of(item)
        out.append({
            "type": "function",
            "name": name,
            "description": str(getattr(tool_def, "description", "") or "")[:1024],
            "parameters": getattr(tool_def, "parameters", None)
            or {"type": "object", "properties": {}},
        })
    return out


def _jsonable(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)


async def execute(
    ctx: Any,
    name: str,
    arguments: dict[str, Any] | None,
    settings: dict,
    *,
    timeout: float = EXEC_TIMEOUT,
) -> dict[str, Any]:
    """Run one lane-2 call. Never raises — errors come back speakable.

    Membership is re-checked at call time: the registry (or the owner's
    allow/deny) may have changed since the session was minted.
    """
    allowed = {_name_of(t) for t in knowledge_tools(ctx, settings)}
    if name not in allowed:
        return {"ok": False, "error": ERR_UNAVAILABLE}
    try:
        handler = getattr(ctx.tool_registry.get(name), "handler", None)
    except KeyError:
        return {"ok": False, "error": ERR_UNAVAILABLE}
    if not callable(handler):
        return {"ok": False, "error": ERR_UNAVAILABLE}
    kwargs = arguments if isinstance(arguments, dict) else {}
    try:
        result = await asyncio.wait_for(handler(**kwargs), timeout=timeout)
    except asyncio.TimeoutError:
        return {"ok": False, "error": ERR_TIMEOUT}
    except Exception:  # noqa: BLE001 — never leak internals into spoken audio
        log.exception("plugin-voice: lane-2 tool %s failed", name)
        return {"ok": False, "error": ERR_FAILED}
    return {"ok": True, "result": _jsonable(result)}
