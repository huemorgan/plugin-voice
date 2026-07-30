# 005 — Realtime speech-to-speech: three lanes, Luna as the hands

## Problem (owner report, 2026-07-29/30)

The ElevenLabs pipeline (STT → bridge → `run_turn` → TTS) cannot give the
conversation feel we want:

1. **Latency is structural.** `run_turn` is slow (tools, memory) and
   non-streaming; the bridge papers over it with "Mm..." buffer words and
   7-second keepalive fillers. Dead air → the user talks over the pending
   reply → barge-in cancels the whole turn (observed on the first real call,
   2026-07-04).
2. **Interruption kills work, not just speech.** A barge-in aborts the SSE
   stream and the pending reply with it.
3. **TTS/STT quality.** Chunked synthesis (buffer words + sentence chunks)
   breaks prosody; STT on an open mic is noisy; the pipeline loses the
   paralinguistic channel entirely.

No tuning fixes this — the speaker and the executor must be different brains.

## Design — three lanes

The plugin moves to a **speech-to-speech realtime model** (OpenAI Realtime,
`gpt-realtime-2.1`) as the *talker*, with Luna demoted from "the speaker" to
"the executor". Full duplex and barge-in come native from the realtime stack.

| Lane | What | Latency | Carrier |
|---|---|---|---|
| 1. Talking | The S2S model holds the conversation, in Luna's replicated persona (name, attitude, greeting, filler style — close enough, not identical) | ~ms, native duplex | WebRTC: widget ↔ OpenAI |
| 2. Knowledge | Read-only info tools (memory, wiki, files…) exposed as native Realtime function calls, executed directly against Luna's tool registry — **no LLM turn** | sub-second | data channel → widget → `POST /rt/tool` |
| 3. Doing | Real agent work: `luna_do` dispatches a **background `run_turn`**; the talker keeps conversing; completion arrives as an *event* the talker narrates. Luna's text is never spoken verbatim — it returns a `spoken_summary` the talker relays, details go to web chat | seconds–minutes, async | `/rt/events` WS → widget → conversation item injection |

Hard rules baked into the talker's instructions (the main failure mode is a
talker that improvises facts or claims work it never did):

- Facts about the owner's world come from lane 2 tools — never invented.
- Task status comes only from lane 3 events — never say "done" unless Luna
  said done. Narrate honestly ("working on it, meanwhile…").
- Relay `spoken_summary` near-verbatim (numbers, names); "details are in your
  chat" for the rest.
- Open-mic discipline: speaker-verdict context items tell the talker who is
  speaking; ignore chatter not addressed to it (replaces the old triage LLM).

### Auth & topology

- Widget fetches `GET /rt/session` (cookie-auth; GET because hosted widget
  cookies are read-only) → plugin mints an **ephemeral client secret**
  (`POST api.openai.com/v1/realtime/client_secrets`, server-side key) plus a
  plugin-local `rt_token` for the relay/event/live endpoints.
- Widget opens WebRTC straight to OpenAI. **No public reachability needed**:
  ElevenLabs had to call into the bridge (cloudflared for self-hosted,
  Fly-direct hack for hosted); here everything Luna-bound rides the widget's
  own authenticated origin. `hosted_bridge()` and the bridge secret die.
- Key resolution reuses the 003 chain: pasted key → `ctx.vault.connect("openai")`
  → env `LUNA_OPENAI_API_KEY` / `OPENAI_API_KEY` (verified present in QA env).

### Safety

- Lane 2 executes registry handlers **directly** — this bypasses
  `pre_gate_check`/approval policy, so the broker holds its own conservative
  allowlist: `policy == "auto_approve"` AND `risk_level == "low"` AND not
  skill-gated AND name not in a deny set; owner can extend/trim via settings.
- Lane 3 keeps today's `voice_tool_allowlist` gates on the background
  `run_turn` (drop `risk_level="high"`; drop `prompt_always` while the live
  imprint says an unrecognized voice is speaking).
- Speaker verdict (owner imprint, unchanged DSP) still streams over `/live`;
  the server remains the authority (`live_state.recent_speaker()`) for gating,
  and the widget mirrors verdict changes to the talker as context items.

### What survives / what dies

- **Survives:** enrollment + live speaker check (dsp*, `/enroll*`, `/live`),
  `identity.py`, `personality.fetch_persona` (voice_description now picks
  from the fixed Realtime voice set), persona overrides tab (fields reworked),
  vault plumbing, widget slot/settings tab shells.
- **Dies at cutover (phase 05):** `elevenlabs.py`, `bridge.py`,
  `/v1/chat/completions`, buffer words / keepalives / triage LLM / soft
  timeouts, `hosted_bridge()`, `elevenlabs-client.js`, ElevenLabs tests.
  ElevenLabs users keep **plugin-talk** (the sibling stays as-is).

## Phases

Each phase lands green (`pytest`) with the old path still working until the
phase-05 cutover; each gets `phases/phaseNN-execution.md` when done.

1. [phase01-realtime-core](phases/phase01-realtime-core.md) — OpenAI key
   resolution, `openai_realtime.py` (client-secret minting, session config),
   talker instruction builder, `GET/POST /rt/session`.
2. [phase02-knowledge-lane](phases/phase02-knowledge-lane.md) — read-only
   tool broker over `ctx.tool_registry`, Realtime tool schemas,
   `POST /rt/tool` relay.
3. [phase03-doing-lane](phases/phase03-doing-lane.md) — background task
   manager around `run_turn`, `luna_do`/`luna_task_status` synthetic tools,
   `/rt/events` WebSocket, spoken_summary contract.
4. [phase04-widget](phases/phase04-widget.md) — `rt-client.js` (WebRTC +
   data channel, no SDK), widget rewrite: function-call relay, task-event
   injection, imprint mic tee, speaker context updates.
5. [phase05-settings-cutover](phases/phase05-settings-cutover.md) — settings
   UI for OpenAI connect + voice/model pick, persona-config field migration,
   ElevenLabs removal, README, version 0.5.0 (all three stamps), full suite,
   live verify on QA Luna.

## Risks

- **Gateway-minted keys:** if the vault gateway fronts OpenAI with a virtual
  key/base URL, `client_secrets` minting may not pass through it — try, and
  degrade with a clear "paste a real key" error. (QA/live use a real key.)
- **Realtime voices are a fixed set** (marin, cedar, alloy, …) — the persona
  voice-match degrades from "any ElevenLabs voice" to "closest of ~10"; a
  static description catalog feeds the same `pick_voice` flow.
- **Widget iframe permissions:** mic already granted for the current widget;
  WebRTC to `api.openai.com` needs no server CSP change we know of — verified
  live in phase 04 on QA Luna before cutover.
- **Cost:** realtime audio is priced per minute; `gpt-realtime-2.1-mini` is
  the owner-selectable economy option (default: full model).
