# Phase 2 — execution summary

## What shipped

The browser half now speaks Gemini Live over a plain WebSocket; WebRTC and
the OpenAI data channel are gone. Still version 0.7.0 in the stamps — 0.8.0
ships in phase 4 after phase-3 polish and full QA (per plan, phases 1+2 have
no solo publish). Nothing committed yet; the tree carries phases 1+2 for one
commit.

Files:

- `plugin_voice/ui/widgets/voice/rt-client.js` — rewritten (~460 lines).
  `LunaRT.start(opts)` public surface unchanged (same callbacks, same handle),
  so `index.html`/Luna View needed no logic changes. Inside:
  - **Capture**: `getUserMedia({echoCancellation, noiseSuppression,
    autoGainControl})` (the browser half of the noise story) → ONE
    AudioWorklet path (inline blob module; ScriptProcessor fallback) →
    16 kHz s16le → ~100 ms base64 `realtimeInput.audio` chunks. The same 16 k
    frames feed the `/live` imprint tee (its dedicated ScriptProcessor is
    gone). Mute still just disables the mic track.
  - **Playback**: 24 kHz PCM chunks → scheduled `AudioBufferSource`s →
    `MediaStreamAudioDestinationNode`; `onRemoteStream` still delivers a
    plain MediaStream. `serverContent.interrupted` → every queued source
    stopped instantly (real barge-in), playhead reset.
  - **States**: setupComplete → listening + one greeting `clientContent` turn
    (proactiveAudio doesn't speak into silence — probe-confirmed the model
    answers the nudge with audio); first queued audio → speaking; queue
    drained/turnComplete → listening; tool relay → thinking. `userSpeaking`
    is now a local RMS energy gate (threshold 0.02, 700 ms hangover) used
    only to keep task nudges out of mid-utterance.
  - **Tools**: `toolCall` → existing `POST /rt/tool` → `toolResponse`
    (`functionResponses[{id, name, response}]`); `luna_view_react` still
    intercepted client-side with an immediate `{ok: true}` response;
    `toolCallCancellation.ids` drop late relays.
  - **Reconnect**: `goAway`/unexpected close → up to 2 reconnects to the same
    `ws_url?access_token=` (token uses=10), resending the setup verbatim plus
    `sessionResumption: {handle}` when one has arrived; counter resets on
    setupComplete; then `end("Connection lost")`.
  - **Nudges**: `[task update]`/`[voice check]` ride as `clientContent` turns
    — `turnComplete: true` only for spoken announcements (task done/failed).
- `plugin_voice/ui/settings/index.html` — reworded to Gemini: hint paragraph,
  "Google AI (Gemini) API key" label with `AIza…` placeholder, SOURCE_LABEL
  reduced to own/env, "Checking for a Gemini key…", error strings. The
  "Use detected key" one-click button is KEPT (see deviations).
- `plugin_voice/ui/widgets/voice/index.html`, `ui/view/index.html` — comment
  wording only.
- `tests/test_voice_features.py` — settings-HTML pin flipped to
  `"Gemini" in html and "OpenAI" not in html`.

## Verification

- Full suite in the plugin venv: **150 passed, 1 skipped** — the
  `test_js_field_list_matches_client_source` guard re-armed itself (no more
  `webrtc_url` in the client) and passes: the JS reads exactly
  `session.{access_token, ws_url, setup, rt_token, live_token, has_imprint,
  persona_name}`. The only skip left is the live-key test (runs when
  `LUNA_GEMINI_API_KEY` is exported; passed against real Google, 0.33 s).
- **Resumption probe** (scratchpad `probe_resume.py`, real key): the
  Constrained endpoint accepts BOTH a second connection on the same token and
  a setup carrying `sessionResumption.handle` alongside the locked
  constraint. So reconnect is purely client-side — the `?resume_handle=`
  server fallback in PHASE.md was not needed.
- **Real QA Luna** (`luna serve --port 8765`, managed copy rsynced, env
  Gemini key): `/status` → connected/ready via env key (a REAL mint probe);
  `/rt/session` → real token + full setup with 34 live registry tools
  converted; a script then connected that exact payload browser-style and
  Luna spoke her configured greeting ("Hey, I'm listening — what can I do
  for you?", 150 KB of 24 kHz PCM).
- **Real browser** (Playwright, mic stubbed with a synthesized silent
  MediaStream since headless has no mic): call button → `Connecting… →
  Listening…`, audio element playing with a live track, ZERO console
  errors/warnings; killed the open Gemini socket from the page →
  `Reconnecting… → Listening…` with a fresh socket OPEN and `/rt/events`
  untouched; End → clean return to "Start talking". Settings page against
  real Luna renders "Connected via the platform-provided Gemini key — ready
  to talk" with both models and the 10-voice catalog in the pickers.

## Deviations from PHASE.md

1. **"Use detected key" button kept** (PHASE.md said remove the gateway
   card): with the gateway lane gone the button is still the one-click bless
   flow for platform/env keys — exactly the hosted install path. All
   "gateway" wording removed from user-visible text; internal element ids
   (`connect-gateway`) kept so shell tests/testids don't churn.
2. **No `?resume_handle=` server lane** — the resumption probe proved the
   handle rides along fine with the locked setup, so routes.py stays as
   phase 1 left it.
3. Human barge-in (speak over Luna mid-sentence) can't be exercised with a
   synthetic silent mic; the `interrupted` frame handling is probe-pinned and
   the flush is unit-simple, but the real-mic interrupt test lands in
   phase 4 QA where the owner can talk.

## Reassessment of remaining phases

- **Phase 3 (turn-taking polish)** — still worth doing but smaller than
  planned: VAD presets + proactivity already ship and the settings page
  already exposes the model picker. What's left: an "eagerness" control in
  settings persona/setup wording (if the persona tab doesn't already expose
  turn_eagerness — check), plus the ≥16-min compression/resumption soak call.
- **Phase 4 (QA + ship 0.8.0)** — unchanged: full owner QA on real mic
  (greeting, barge-in, noisy-room behavior, tool call spoken end-to-end),
  bump the three version stamps to 0.8.0, commit + push, marketplace publish,
  catalog verify, remind about hosted upgrade + `LUNA_GEMINI_API_KEY` on
  hosted agents.
