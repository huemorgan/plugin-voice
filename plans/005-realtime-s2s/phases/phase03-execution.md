# Phase 03 — execution summary

Landed as planned; suite fully green (157 passed, 2 skipped).

## What shipped

- `tasks.py` (new):
  - `TaskManager` — per-process singleton (`state.task_manager()`),
    strongly-referenced asyncio tasks, 3-concurrent cap with a speakable
    refusal, entries + events pruned 10 minutes after completion.
  - `dispatch` returns `{ok, task_id, accepted, note}` immediately;
    `owner_verified` is stamped **at dispatch time** from the live speaker
    verdict — the tool gate reflects who asked, not who is speaking when the
    task ends.
  - The voice task prompt (`TASK_RULES`) enforces the spoken_summary
    contract: ≤3 sentences, no markdown, numbers/names exact, long detail →
    `send_chat_message` + "details are in your chat". Unrecognized
    dispatchers are flagged in the prompt.
  - Tool gating reuses `bridge.voice_tool_allowlist` (import, not copy — the
    function moves here bodily at the phase-05 cutover when bridge dies).
  - Failures → `task_failed` event with a canned speakable summary; the real
    exception goes to the server log only (leak-tested).
  - Events: `{seq, type: task_started|task_done|task_failed, task_id,
    instruction, spoken_summary, elapsed_s, ts}`, seq monotonic, fan-out to
    every subscriber queue, `recent_events(since_seq)` for replay.
- `routes.py`:
  - `/rt/tool` now routes the lane-3 synthetics before the registry:
    `luna_do` (dispatch) and `luna_task_status` (one/all, elapsed included —
    the talker has no clock).
  - `WS /rt/events` — rt_token gate (4401), replay-then-live from the
    manager's stream, `?since=` resume.
  - `/rt/session` appends `tasks.synthetic_schemas()` to the minted tools.
- `state.py`: `task_manager()` / `reset_task_manager()`; conftest resets it
  around every app instance.

## Tests

`tests/test_tasks.py` — 12 tests: immediate dispatch + gated completion
(SlowAgent with an explicit gate), multi-subscriber fan-out, concurrency cap
and post-completion capacity return, empty-instruction refusal, failure path
(speakable, no internals leak), dispatch-time owner stamping (prompt_always
tools dropped; prompt says UNRECOGNIZED), synthetic schema shape, seq
monotonicity + since filtering, session tool wiring, `luna_do`/`luna_task_status`
via `/rt/tool`, events WS gate + live stream + replay of missed events.

## Deviations from plan

- Event types are explicit `task_started/task_done/task_failed` (the plan
  sketched `task_progress` for acceptance; an explicit started type is
  clearer for the widget's status UI). Mid-flight progress still lands in a
  future plan — `run_turn` is a black box, as noted.
- `voice_tool_allowlist` stayed in `bridge.py` for now (imported by tasks) —
  moving it mid-plan would have duplicated code while the ElevenLabs path is
  still live; it relocates in phase 05.
