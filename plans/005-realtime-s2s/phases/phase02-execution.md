# Phase 02 — execution summary

Landed as planned; suite fully green (145 passed, 2 skipped).

## What shipped

- `broker.py` (new): lane-2 selection + execution.
  - `knowledge_tools(ctx, settings)`: `auto_approve` AND `risk_level=="low"`
    AND not skill-gated AND not name-denied (`send_chat_message` static +
    `_connect/_delete/_create/_write/_set` suffix heuristics for mislabeled
    writes). Owner `rt_tools_allow` admits by exact name (never a high-risk
    tool); `rt_tools_deny` trims. Registry trouble degrades to **no tools**,
    never to all (opposite bias from `voice_tool_allowlist`, deliberately —
    direct handler calls bypass approval policy, so this list IS the gate).
  - `tool_schemas`: Realtime function-tool JSON, unique by name,
    descriptions capped at 1024 chars, parameter-less tools get an empty
    object schema.
  - `execute`: call-time membership re-check (registry/owner lists may have
    changed since mint), 8 s timeout, all failures return **speakable**
    errors (`ERR_*` constants) — never an exception, never internals that
    could be read aloud; un-JSON-able results stringified.
- `routes.py`:
  - `POST /rt/tool` — rt_token auth (not cookies; the relay is driven by the
    widget's data-channel handler). Server-authoritative owner lock:
    `rt_lock_tools_to_owner` + live verdict `other` → speakable refusal
    before any execution.
  - `/rt/session` now mints the session with `tools=tool_schemas(...)` and
    returns `tool_names`.
- `tests/conftest.py`: fake registry grew `description/parameters/
  skill_gated/handler` fields and `get(name)` — mirroring the real
  `RegisteredTool`/`ToolRegistry` surface the broker uses.

## Tests

`tests/test_broker.py` — 16 tests: default conservative selection, unset-risk
and skill-gated exclusion, write-suffix heuristics, owner allow/deny (allow
cannot admit high risk), broken-registry degradation, schema shape +
dedup, execute happy/unknown/unselected/timeout/crash (no internals leak)/
unjsonable/call-time deny, `/rt/tool` 401 + execute + owner lock,
`/rt/session` tool_names contract.

## Deviations from plan

- `_RtToolReq` had to live at module level in `routes.py` — a
  closure-local Pydantic model can't be resolved under
  `from __future__ import annotations` (FastAPI saw it as a query param;
  422s in the first test run caught it).
- Speakable-refusal errors return HTTP 200 with `{ok: false, error}` (the
  talker should hear them); only auth (401) and malformed requests (400) are
  HTTP errors.
