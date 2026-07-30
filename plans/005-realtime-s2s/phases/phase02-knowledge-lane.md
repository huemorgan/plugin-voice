# Phase 02 — Knowledge lane: read-only tool broker + `/rt/tool`

## Goal

The talker can call Luna's read-only tools as native Realtime functions with
sub-second latency and no LLM turn. Direct handler execution bypasses
`pre_gate_check`/approval policy, so the broker's allowlist IS the gate —
conservative by construction.

## New module `broker.py` (pure logic, ctx injected)

- `knowledge_tools(ctx, settings) -> list[RegisteredTool]`:
  - candidates: `ctx.tool_registry.all()` where `policy == "auto_approve"`
    AND `risk_level == "low"` (default when unset: excluded) AND not
    `skill_gated` AND name not in `DENY` (static: `send_chat_message` — a
    write; anything matching `_connect`/`_delete`/`_create`/`_write`/`_set`
    suffix heuristics stays out even if mislabeled low-risk).
  - owner settings may **extend** (`rt_tools_allow`) or **trim**
    (`rt_tools_deny`) by exact name; extension only admits tools that exist
    and are not `risk_level == "high"`.
- `tool_schemas(tools) -> list[dict]` — Realtime function-tool schema
  (`{type: "function", name, description, parameters}`), descriptions
  truncated sanely; plus the lane-3 synthetic tools appended in phase 03.
- `execute(ctx, name, arguments, *, timeout=8.0) -> dict` —
  `registry.get(name)`, re-check it still passes `knowledge_tools` rules
  (registry may have changed since session mint), run handler with kwargs,
  `asyncio.wait_for` timeout. Returns `{ok, result}` or
  `{ok: False, error}` with a *speakable* error string; never raises into
  the route.

## Route `POST /rt/tool`

- Auth: `rt_token` (body field, constant-time via `live_state`), NOT cookie —
  the data-channel relay must work however the widget iframe is embedded.
- Body: `{token, name, arguments}` (arguments = parsed JSON object from the
  function-call event).
- Speaker gate, server-authoritative: if `live_state.recent_speaker()` says
  `other`, tools tagged `prompt_always` are already excluded by construction;
  additionally a settings flag `rt_lock_tools_to_owner` (default false)
  refuses all lane-2 calls with a speakable error.
- Response: `{ok, result | error}`; result JSON-serialized for
  `function_call_output` (stringified by the widget).

## Session wiring

`/rt/session` (phase 01) now includes `tools: tool_schemas(...)` in the minted
session config and returns `tool_names` so the instructions builder can list
what the talker can look up.

## Tests (`tests/test_broker.py`)

- selection: auto_approve+low in; ask/high/skill_gated/unset-risk out; deny
  heuristics (`x_delete`) out; owner allow admits an `ask`-policy low tool;
  owner deny trims; allow cannot admit `risk_level="high"`.
- `execute`: happy kwargs call; unknown tool → speakable error; timeout →
  speakable error; handler raising → `{ok: False}` not an exception;
  re-check drops a tool that turned high-risk after mint.
- route: bad token 401; good token executes; `rt_lock_tools_to_owner` +
  speaker=other refuses.
- schemas: valid function-tool JSON, names unique.
