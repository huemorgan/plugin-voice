# Phase 04 — execution summary

Landed as planned, plus one live-QA-driven broker redesign. Suite fully green
(166 passed, 2 skipped).

## What shipped

- `ui/widgets/voice/rt-client.js` (new, ~330 lines — replaces the 24k-line
  ElevenLabs bundle):
  - `LunaRT.start(opts)` → GET `rt/session` (cookie/bearer) → `getUserMedia`
    once (the same stream feeds WebRTC and the imprint tee) →
    `RTCPeerConnection` + data channel `oai-events` → SDP POST to
    `webrtc_url?model=…` with `Bearer client_secret`.
  - Function calls (`response.output_item.done`) → POST `rt/tool` →
    `function_call_output` + `response.create`. Relay failures come back as a
    speakable error string, never an exception.
  - `rt/events` WS → `[task update]` system items. Completions/failures nudge
    `response.create`; a nudge never lands mid-utterance (queued, flushed
    600 ms after `speech_stopped` / audio end). Reconnects with `?since=` so
    missed completions replay.
  - Imprint tee (only when `has_imprint`): 16 kHz s16le PCM → `/live` WS
    (protocol unchanged); verdict → who-chip callback + debounced (1.5 s)
    `[voice check: …]` context item. Server verdict stays the gate.
  - Mode surface: listening / thinking / speaking driven by data-channel
    events (`speech_started`, `response.created`,
    `output_audio_buffer.started/stopped`), not volume inference.
  - On `dc.onopen` sends one `response.create` so the talker opens with the
    greeting instead of waiting to be spoken to.
- `ui/widgets/voice/index.html` rewritten on the same look (waves, CTA, who
  chip, status row). Both waves now vibrate from real streams — mic analyser
  + WebRTC remote-track analyser — no SDK volume APIs, no synthetic pulse.
  ElevenLabs script tag gone (the bundle file itself dies in phase 05).
- `broker.py` REDESIGNED after live QA (see below): positive read-verb
  selection replaces the write-suffix deny heuristic.
  - `READ_TOKENS` (get/list/read/search/recall/status/…): a tool is only
    admitted when a name token *promises* a read, and `WRITE_TOKENS`
    (update/upsert/run/edit/add/…) veto mixed names.
  - `MAX_TOOLS = 32` cap, knowledge-first prefixes (`memory`, `wiki`, `web`,
    `read`, …) win the cut; dropped names are logged, never silent. Owner
    `rt_tools_allow` picks ride above the cap.

## Live QA (real Luna, real OpenAI — memory rule)

QA Luna on :8766 (`env -u ANTHROPIC_API_KEY`, key from `luna/.env`), plugin
synced into `~/.luna/managed_plugins/plugin_voice`:

- `/rt/session` minted a real `ek_…` secret; persona "Atlas", `has_imprint`
  true, `live_token == rt_token`.
- **The mint exposed a broker hole**: the write-suffix heuristic admitted
  ~140 tools including mislabeled writes (`wiki_create_wiki`, `item_upsert`,
  `goal_update`, `playbook_run`, `mcp_add_server` — all shipped as
  auto_approve/low by their plugins). Hence the read-verb redesign; the
  re-mint now exposes 32 read-only tools + the 2 synthetics.
- `/rt/tool` live: `get_settings` returned real identity data; `item_upsert`
  refused ("That tool isn't available on this call."); `luna_task_status`
  returned `{ok, tasks: []}`.
- `/rt/events` live: bad token rejected at handshake; good token streams.
- **Full browser call** (headless Firefox, fake mic, Playwright): status
  walked Connecting… → Listening… → "Atlas is thinking…" → "Atlas is
  speaking — interrupt anytime" → Listening…, and the imprint tee classified
  the fake-tone mic as "● Unrecognized voice" — greeting, WebRTC audio
  round-trip, and the /live DSP path all verified on a real server. Zero
  console errors.

## Tests

- `tests/test_routes_rt.py` (new, 7): the `/rt/session` ↔ JS contract —
  every field the client reads is pinned, and a meta-test greps the client
  source so the pin list can't rot; imprint on/off → `live_token` behavior;
  widget statics serve `rt-client.js` and no ElevenLabs tag.
- `tests/test_broker.py` (+4, updated): mid-name write verbs denied
  (`wiki_create_wiki`, `item_upsert`, `playbook_run`), read-verb requirement
  (`research`/`hello_world` out, `wiki_toc` in), cap with knowledge-prefix
  priority, owner allows above the cap.
- `tests/test_routes_dojo.py`: three widget-source pins updated from the
  ElevenLabs client to `rt-client.js` (GET minting, mic permission handling,
  real-stream analysers).

## Deviations from plan

- Chrome headless couldn't produce a fake mic (`NotSupportedError` from
  getUserMedia on macOS headless); live browser QA ran on Playwright Firefox
  with `media.navigator.streams.fake`. Human-voice checks (barge-in feel,
  spoken lane-2/lane-3 round-trip quality) remain manual QA in phase 05.
- `task_started` events are context-only (no `response.create`): the talker
  already acknowledged the dispatch when `luna_do` returned; nudging again
  made it narrate the same fact twice. Completions and failures do nudge.
- Broker redesign (above) was not in the phase plan — live QA finding.
