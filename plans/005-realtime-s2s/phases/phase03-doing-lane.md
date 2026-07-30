# Phase 03 — Doing lane: background `run_turn`, events, dispatch tools

## Goal

The talker dispatches real agent work and keeps talking; results come back as
events it narrates. An interruption cancels speech, never work.

## New module `tasks.py`

- `TaskManager` (module singleton in `state.py`, survives route calls;
  spawned tasks strongly referenced — asyncio weak-ref pitfall):
  - `dispatch(ctx, instruction, *, speaker, settings) -> {task_id, accepted}`
    — cap concurrent tasks (3; speakable refusal beyond), build the **voice
    task prompt**, spawn `asyncio.create_task`.
  - `status(task_id)` / `list_active()` — `queued|running|done|failed` +
    elapsed seconds (server-computed; the talker has no clock).
  - completion pushes an event dict onto every subscribed queue
    (`subscribe() -> asyncio.Queue`, `unsubscribe`), retained 10 minutes for
    reconnects (`recent_events(since_seq)`).
- **Voice task prompt** (replaces `bridge.build_prompt` for lane 3): the
  dispatched instruction + rules: you are executing a task delegated from a
  live voice call; when done, reply with a SHORT spoken summary (≤3
  sentences, no markdown, numbers/names exact); post details/links to web
  chat via `send_chat_message` and say you did.
- Tool gating: today's `voice_tool_allowlist(ctx, owner_verified)` moves here
  from `bridge.py` unchanged (drop high-risk always; drop `prompt_always`
  when the dispatching speaker was unrecognized). `owner_verified` is stamped
  AT DISPATCH TIME from `live_state.recent_speaker()`.
- Failure → event with a speakable apology summary; the exception goes to the
  server log (same discipline as the bridge's `run()`).

## Synthetic Realtime tools (appended to broker schemas)

- `luna_do(instruction)` — "delegate real work (actions, changes, multi-step
  jobs, anything beyond a quick lookup) to {name}'s full agent. Returns a
  task id immediately; a task event will arrive when it finishes. Tell the
  user you've started and roughly what happens next; keep conversing."
- `luna_task_status(task_id?)` — status of one/all active tasks, elapsed
  seconds included so the talker can say "about a minute in".

Both execute in `/rt/tool` (routed by name before registry lookup).

## Route `WS /rt/events`

- Auth: `?token=` rt_token (same as `/live` pattern).
- On connect: replay `recent_events(since)` (client sends `{since_seq}` first
  or as query param), then live-stream from a subscription queue.
- Event shape: `{seq, type: "task_done"|"task_failed"|"task_progress",
  task_id, instruction_echo, spoken_summary, elapsed_s}`.
- Widget (phase 04) turns each into a conversation context item + a
  `response.create` nudge so the talker speaks it naturally.
- Progress events v1: emitted on dispatch acceptance and completion only —
  `run_turn` is a black box mid-flight; a later plan can tap turn telemetry.

## Tests (`tests/test_tasks.py`)

- dispatch → immediate task_id; slow fake `run_turn` → status running → done
  event with spoken summary; queue subscribers all receive it.
- concurrency cap: 4th dispatch refused speakably.
- `owner_verified` stamped at dispatch: unrecognized speaker → allowlist
  passed to `run_turn` excludes `prompt_always` tools (fake registry).
- failure path: raising `run_turn` → `task_failed` event, no unhandled task
  exception, error logged.
- events WS route: token gate; replay-then-live ordering; seq monotonic.
- `luna_do`/`luna_task_status` dispatch through `/rt/tool`.
