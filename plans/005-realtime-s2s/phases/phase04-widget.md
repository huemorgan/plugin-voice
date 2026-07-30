# Phase 04 — Widget: WebRTC client, relays, imprint tee

## Goal

The sidebar Voice widget talks over WebRTC to OpenAI Realtime with native
barge-in; function calls and task events flow through it; the voice imprint
keeps working. No SDK bundle — plain `RTCPeerConnection` (~300 lines replaces
the 24k-line `elevenlabs-client.js`).

## `ui/widgets/voice/rt-client.js`

- `startCall()`:
  1. `GET rt/session` (same-origin, cookie) → secret, model, voice, rt_token.
  2. `getUserMedia({audio})` once; the SAME stream feeds WebRTC and the
     imprint tee.
  3. `RTCPeerConnection`: `addTrack(mic)`, `ontrack → <audio>.srcObject`,
     `createDataChannel("oai-events")`.
  4. SDP offer → `POST {webrtc_url}?model=…` with
     `Authorization: Bearer {client_secret}`, `Content-Type: application/sdp`
     → setRemoteDescription(answer).
- Data channel handling:
  - `response.done` / `response.output_item.done` with `function_call` items
    → `POST rt/tool {token, name, arguments}` → send
    `conversation.item.create {type: function_call_output, call_id, output}`
    then `response.create`.
  - surface talker state (listening / speaking / tool call) to the UI.
- `rt/events` WS: each task event → `conversation.item.create` (system-style
  message: `[task update] …spoken_summary…`) + `response.create` so the
  talker announces it; if the user is mid-utterance (`input_audio_buffer`
  speech active), hold the nudge until turn end (queue, flush on
  `input_audio_buffer.speech_stopped` + short delay).
- Imprint tee (only when `has_imprint`): AudioWorklet/ScriptProcessor →
  16 kHz s16le PCM chunks → `/live?token=` WS (existing protocol). On verdict
  change (owner↔other↔unknown, debounced): send context item
  `[voice check: the current speaker is …]`. Server stays authoritative for
  gating; this only informs the talker's behavior.
- Barge-in: native (semantic VAD) — no client work; on `response.canceled`
  nothing to clean up server-side (lane 3 work is independent by design).

## `ui/widgets/voice/index.html` rewrite

Keep the current look (call button, status line, "● You / ● Unrecognized
voice" chip); wire to `rt-client.js`; drop the ElevenLabs script tag and
signed-url/conversation-token paths. Error states: no key configured (point
to Settings → Voice), mint failure, WebRTC failure.

## Server side

- Static serving already generic (`/ui/widgets/voice/{path}`) — no route work
  beyond what phases 01–03 added.
- `/rt/session` gains `has_imprint` + `live_token` (reuse existing minting)
  so ONE call arms both sockets.

## Tests

- Route-level (`tests/test_routes_rt.py` extended): `/rt/session` payload
  carries everything the client needs (contract test pinning field names used
  by `rt-client.js`); static widget route serves the new files;
  `elevenlabs-client.js` gone from the widget dir (in phase 05 cutover).
- JS has no test harness in this repo — logic worth testing lives server-side
  by construction; the browser path is verified live:
- **Live verify (memory: real running Luna, not just unit tests):** QA Luna
  on :8766 with the real OpenAI key (`env -u ANTHROPIC_API_KEY`), CDP browser
  session — call connects, talker answers in persona, a knowledge lookup
  round-trips, `luna_do` dispatch + spoken completion, barge-in interrupts
  speech while the task still lands.
