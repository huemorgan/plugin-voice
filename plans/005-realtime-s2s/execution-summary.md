# 005 — realtime speech-to-speech: execution summary

**Shipped:** plugin-voice **0.5.0** — full-duplex, interruptible voice on
OpenAI Realtime, in the agent's own persona, three lanes.
**Tests:** 130 passed, 1 skipped (live OpenAI mint, key-gated) ·
**Live QA:** real Luna (:8766), real OpenAI key — connect, mint, tools,
rename-follows-live-name all verified.

## The three lanes (as built)

1. **Talking** — the Realtime model IS the voice. `talker.build_instructions`
   replicates the agent's persona (live name first, snapshot fallback,
   greeting, fillers, attitude) + lane rules + spoken-style rules + open-mic
   rules (when an imprint exists) + owner `talker_extra` last. Browser ⇄
   OpenAI over WebRTC; Luna only mints ephemeral `ek_…` client secrets
   (`POST /v1/realtime/client_secrets`) — the server key never reaches the
   browser (test-asserted).
2. **Knowledge** — `broker.py` exposes read-only tools straight to the
   talker: positive READ_TOKENS selection + WRITE_TOKENS veto over the
   registry, 32-tool cap, owner allow/deny lists, owner-lock (`/rt/tool`
   refuses non-owner speakers when `rt_lock_tools_to_owner` and the imprint
   says "other").
3. **Doing** — `tasks.TaskManager` dispatches real work to the actual agent
   (`run_turn`) in the background via the synthetic `luna_do` tool; the
   talker narrates and keeps going. Task updates arrive over `/rt/events`
   (WS, seq-replay) as `[task update]` inputs — the agent's raw text is never
   spoken; `spoken_summary` + details-to-chat.

Owner voice imprint (0.2.x DSP) rides along unchanged and now feeds both the
open-mic prompt rules and the tool owner-lock.

## Phases

| Phase | What landed | Summary |
|---|---|---|
| 01 | `openai_realtime.py` (key chain, catalog, session config, mint), `talker.py`, `/rt/session` | [phase01-execution.md](phases/phase01-execution.md) |
| 02 | `broker.py` knowledge lane, `/rt/tool` relay, rt_token auth | [phase02-execution.md](phases/phase02-execution.md) |
| 03 | `tasks.py` doing lane, `/rt/events` WS, `luna_do`/`luna_task_status` | [phase03-execution.md](phases/phase03-execution.md) |
| 04 | `rt-client.js` WebRTC widget: data-channel events, function-call relay, task events, imprint tee | [phase04-execution.md](phases/phase04-execution.md) |
| 05 | Settings cutover, persona page, ElevenLabs deletion, 0.5.0, live QA, ship | [phase05-execution.md](phases/phase05-execution.md) |

## Surface (0.5.0)

- Key: pasted → vault/gateway (`connect("openai")`) → `LUNA_OPENAI_API_KEY` /
  `OPENAI_API_KEY` env. Vault: `plugin_voice.openai_api_key` + `.settings`
  only; legacy 0.4.x ElevenLabs keys purged on `/disconnect`.
- Models `gpt-realtime-2.1` (default) / `-mini`; 10 fixed voices, marin
  default, persona-matched at connect.
- Settings: `rt_voice`, `rt_model`, `rt_lock_tools_to_owner`,
  `rt_tools_allow`, `rt_tools_deny`; persona overrides: greeting, fillers,
  `voice_system_prompt`, `talker_extra`, `turn_eagerness`
  (semantic_vad eagerness high/auto/low).
- No tunnel, no bridge, no public URL requirement — the 0.4.x ElevenLabs
  path is gone; plugin-talk remains the ElevenLabs sibling.

## Known limits

- Voice match degrades from "any ElevenLabs voice" to "closest of 10".
- The talker is OpenAI's LLM, not the agent — persona is replicated, not
  shared; deep context arrives via knowledge tools, not memory.
- Lane-3 turns cost normal agent-turn tokens; QA hit an Anthropic
  "credit balance too low" on the QA account (graceful-failure path
  verified; real turns verified in phase 03).
