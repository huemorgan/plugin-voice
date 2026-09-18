# Phase 2 — client: rt-client.js Gemini WS rewrite

## Scope

Replace the WebRTC/OpenAI browser client with a Gemini Live WebSocket client.
`LunaRT.start(opts)` keeps its exact public surface (callbacks `onState`,
`onWho`, `onRemoteStream`, `onEnd`, `onReact`, `onTask`, `onStatus`; handle
`{end, setMuted, muted, session, micStream}`) so voice.js/widgets need no
changes. Also rewords the settings HTML (OpenAI → Gemini, gateway card gone)
and flips the two tests that pinned the old wording. Phases 1+2 ship together
as 0.8.0.

Wire facts pinned by the phase-1 probe: connect
`ws_url?access_token=<token>`, send `session.setup` VERBATIM as the first
frame (locked token constraint), wait for `setupComplete`; model audio arrives
as `serverContent.modelTurn.parts[].inlineData` base64 `audio/pcm;rate=24000`
with `outputTranscription`; `toolCall.functionCalls[]` answered by
`toolResponse.functionResponses[{id,name,response}]`; `turnComplete`,
`interrupted`, `toolCallCancellation`, `goAway`, `sessionResumptionUpdate`
frames as documented.

## Deliverables

1. **Capture** — `getUserMedia({audio: {echoCancellation: true,
   noiseSuppression: true, autoGainControl: true}})` (the browser-side half of
   the noise story). One AudioWorklet capture path (inline-blob module;
   ScriptProcessor fallback for old browsers) → downsample to 16 kHz s16le →
   ~100 ms base64 chunks → `realtimeInput.audio {data, mimeType
   "audio/pcm;rate=16000"}`. The SAME 16 kHz frames feed the `/live` imprint
   tee (dedicated ScriptProcessor deleted). Mute keeps working by disabling
   the mic track (worklet then captures silence).
2. **Playback** — `AudioContext({sampleRate: 24000})` →
   scheduled `AudioBufferSource`s → `MediaStreamAudioDestinationNode`, whose
   `.stream` goes to `onRemoteStream` (widget code unchanged).
   `serverContent.interrupted` → stop every scheduled source and reset the
   queue clock instantly (real barge-in).
3. **Events / states** — `setupComplete` → greeting nudge (a `clientContent`
   text turn — proactiveAudio may not speak on silence; probe confirmed the
   model answers it with audio) + "listening". First queued audio →
   "speaking"; queue drained (+`turnComplete`) → "listening"; toolCall or
   nudge-with-turnComplete → "thinking". `userSpeaking` (was OpenAI
   speech_started/stopped) is now a local energy gate on the 16 kHz capture
   frames (threshold + ~600 ms hangover) — used only to keep task nudges out
   of mid-utterance, same as today.
4. **Tools** — `toolCall` → existing `POST /rt/tool` relay → `toolResponse`
   (`response` = the relay's JSON result); `luna_view_react` still intercepted
   client-side (onReact + immediate `{ok:true}` toolResponse, no extra turn);
   `toolCallCancellation.ids` → drop in-flight relays (late results not sent).
5. **Reconnect** — on `goAway` or unexpected socket close: reconnect to the
   same `ws_url?access_token=` (token has uses=10) and resend the SAME setup
   verbatim. Track the latest `sessionResumptionUpdate.newHandle`; a live
   probe decides whether the constrained endpoint accepts `sessionResumption:
   {handle}` alongside the locked setup — if not (likely, verbatim-match), add
   `?resume_handle=` to GET `/rt/session` so routes.py bakes the handle into a
   freshly minted token's locked setup, and the client re-fetches the session
   to resume. Cap: 2 attempts, then `end("Connection lost")`.
6. **Task nudges / voice check** — `[task update]` and `[voice check]` lines
   become `clientContent` turns (`turnComplete: true` only when a spoken
   announcement is wanted: task_done/task_failed yes, task_started and voice
   checks no). Flush guard unchanged.
7. **Settings HTML + tests** — settings page reworded to Gemini (key label
   "Google AI API key", gateway connect card removed); flip the two
   `test_voice_features.py` assertions that pinned "OpenAI"/gateway HTML;
   `test_js_field_list_matches_client_source` re-arms itself once
   `webrtc_url` disappears from rt-client.js — JS must consume exactly
   `{access_token, ws_url, setup, rt_token, live_token, has_imprint,
   persona_name}`.

## Verification

- Full plugin-voice suite green in its own venv (149+; the JS field-list skip
  re-armed and passing).
- Live browser check on local QA Luna (`luna serve --port 8765`, rsync the
  managed copy): start a real call from the widget, hear a greeting, say
  something, get an answer; interrupt mid-sentence → audio stops instantly;
  trigger a tool (weather) → spoken result; kill the WS from devtools →
  transparent reconnect. Console clean of errors.
- Reconnect probe (script) for `sessionResumption.handle` vs. locked setup —
  result recorded in the execution summary and the chosen lane implemented.
