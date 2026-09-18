# 007 — Replace OpenAI Realtime with Gemini 3.8 Live

Owner ask: replace plugin-voice's speech engine with Google's Gemini live voice
models ("3.8 or better"), using the existing `LUNA_GEMINI_API_KEY`. The agent
must hold a conversation in environments that are NOT silent, stay
interruptible, but not fall silent on every beep/background noise — natural
human turn-taking. Then ship to production (marketplace publish).

## Research findings (verified 2026-09-18, real key)

- Models: **`gemini-3.8-live`** (default; low-latency) and
  **`gemini-3.8-live-extended-thinking`** (background reasoning), both visible
  to `LUNA_GEMINI_API_KEY` on the Gemini API. Pricing ~$0.005/min audio in,
  ~$0.018/min out. ([model list probed live; docs](https://ai.google.dev/gemini-api/docs/live))
- Transport: **WebSocket** (`BidiGenerateContent`), not WebRTC. Audio in: raw
  16-bit PCM **16 kHz** mono little-endian; audio out: 24 kHz PCM.
- **Ephemeral tokens** (`auth_tokens.create`, v1alpha) mint server-side and let
  the browser connect directly to Google without exposing the real key — exact
  parity with today's OpenAI `client_secrets` flow. Verified working with our key.
- Noise/turn-taking knobs (all verified accepted on v1alpha in one live session):
  - `realtimeInputConfig.automaticActivityDetection`:
    `startOfSpeechSensitivity: START_SENSITIVITY_LOW` (don't trigger on beeps),
    `endOfSpeechSensitivity`, `prefixPaddingMs`, `silenceDurationMs` (500–800 ms
    recommended).
  - `proactivity: {proactiveAudio: true}` — the model *decides not to respond*
    to irrelevant audio instead of treating every sound as a turn.
  - Barge-in is server-side: `serverContent.interrupted` arrives when real
    speech interrupts; client must flush its playback queue.
  - Browser side: `getUserMedia({echoCancellation, noiseSuppression,
    autoGainControl})` so Luna's own voice + room noise don't feed back.
- Session limits: audio-only 15 min → lifted via
  `contextWindowCompression: {slidingWindow}` (verified accepted); `goAway` +
  `sessionResumption` handle reconnects.
- Function calling: `toolCall`/`toolResponse` messages over the same WS;
  `toolCallCancellation` on interruption. Schemas are OpenAPI-style
  (`{name, description, parameters}` with UPPERCASE types) vs OpenAI's
  `{type:"function", ...}` — needs a converter.
- Output transcription available (`outputAudioTranscription`) — free win for
  status display later; input transcription too.

## Current architecture (what stays, what goes)

Stays (untouched): three-lane design — broker lane-2 fast reads, `luna_do`
lane-3 dispatch via `/rt/tool`, `/rt/events` task nudges, `/live` imprint tee +
speaker gating, persona/talker instruction builder, settings & persona UIs,
Luna View pane (drives the same `LunaRT` API), widget UI shell.

Goes: `openai_realtime.py` (WebRTC SDP, client secrets, semantic_vad),
the WebRTC path in `rt-client.js`, OpenAI voice catalog, OpenAI key
setup/validation.

Key compatibility constraint: `LunaRT.start(opts)` keeps its exact public
surface (`onState/onWho/onRemoteStream/onEnd/onReact/onTask/onStatus`,
handle `{end, setMuted, muted, session, micStream}`) so `index.html` and
`view/index.html` keep working unchanged. Playback goes through
`MediaStreamAudioDestinationNode` so `onRemoteStream` still hands a
MediaStream to the existing `<audio>` + analyser waveform code.

Constraint: hosted installs never pip-install plugin deps → the plugin must
NOT import `google.genai`. All REST (token mint, key validation) via `httpx`,
exactly like `openai_realtime.py` does today.

## Phases

### Phase 0 — baseline + wire-format spike
- Record pytest baseline for plugin-voice.
- Raw-WS spike (python `websockets`, browser-shaped): connect to
  `wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContent`
  with an **ephemeral token**, send camelCase `setup` JSON, stream PCM in, get
  PCM out, exercise `toolCall` → `toolResponse` and `interrupted`. This
  validates exactly what the browser will do and pins the wire format.
- REST-mint spike: mint the ephemeral token via plain httpx POST (no SDK).
- Probe the 3.8-live voice list; pick an 8–10 voice catalog with descriptions
  for `personality.pick_voice`.

### Phase 1 — server: `gemini_live.py` + routes
- New `gemini_live.py`: key resolution (vault `plugin_voice.gemini_api_key` →
  env `LUNA_GEMINI_API_KEY`/`GEMINI_API_KEY`), httpx ephemeral-token minting,
  `setup_message()` builder (system instruction, converted tool schemas, VAD
  preset, proactivity, speech config/voice, transcription, compression),
  voice catalog, VAD presets mapped from persona `turn_eagerness`:
  - `patient` → START_LOW/END_LOW, silence 800 ms (noisy rooms)
  - `normal`  → START_LOW/END_HIGH... (exact enums per phase-0 findings), silence 650 ms
  - `eager`   → START_HIGH/END_HIGH, silence 450 ms
- `routes.py /rt/session` → returns `{access_token, ws_url, setup, model,
  voice, rt_token, live_token, persona_name, has_imprint, tool_names}`.
- `setup.py`: connect/validate against Gemini (`GET /v1beta/models` with
  `x-goog-api-key`), store under new vault key, migrate/clean legacy OpenAI
  vault keys on disconnect.
- Settings: unknown stored `rt_model`/`rt_voice` (OpenAI-era) silently fall
  back to Gemini defaults; `/voices` serves the new catalog + both 3.8 models.

### Phase 2 — client: rt-client.js WS rewrite
- Mic: `getUserMedia({audio: {echoCancellation:true, noiseSuppression:true,
  autoGainControl:true}})`; AudioWorklet capture → downsample to 16 kHz s16le →
  base64 `realtimeInput.audio` chunks (~100 ms). The SAME 16 kHz stream feeds
  the imprint tee (`/live`) — one capture path, ScriptProcessor gone.
- Playback: queue 24 kHz PCM into scheduled AudioBufferSources →
  `MediaStreamAudioDestinationNode` → `onRemoteStream(stream)`.
- Events: `setupComplete` → greeting nudge (clientContent turn) + state
  "listening"; `serverContent.modelTurn` audio → "speaking";
  **`interrupted` → flush playback queue instantly** (real barge-in);
  `turnComplete` → back to "listening"; `toolCall` → existing `/rt/tool`
  relay → `toolResponse` (luna_view_react still intercepted client-side);
  `toolCallCancellation` → drop pending relays; `goAway`/
  `sessionResumptionUpdate` → transparent reconnect with resumption handle.
- Task nudges: `[task update]`/`[voice check]` context via `clientContent`
  (`turnComplete: true` only when a spoken announcement is wanted; input-
  activity guard keeps nudges out of mid-utterance, as today).
- States for the widget: listening/thinking/speaking derived from activity +
  queue drain — same callbacks, same waveforms.

### Phase 3 — natural turn-taking polish
- Talker instructions addition: how to behave on interruptions and background
  noise (resume gracefully, don't restart the sentence, don't acknowledge
  noise; brief answers stay the rule).
- Persona settings tab: eagerness wording updated to describe the new
  VAD presets; model picker (3.8-live vs extended-thinking) in Settings.
- Soak: ≥16 min continuous session locally to prove compression keeps the
  session alive past the 15-min ceiling; scripted noise/interruption checks.

### Phase 4 — tests, QA, ship
- Tests: replace `test_live_openai.py` with `test_live_gemini.py` (key
  resolution precedence, setup_message shape incl. VAD presets + proactivity +
  schema conversion, /rt/session response contract, settings fallback for
  OpenAI-era values, connect/validate flows mocked). Full suite green.
- QA on local Luna (:8765): rsync managed copy, real browser session end-to-end
  (widget + Luna View), verify barge-in + noise behavior manually.
- Ship 0.8.0: three version stamps, commit+push, marketplace publish, catalog
  verify. Production notes: owner upgrades the plugin on hosted agents;
  hosted tenants need `LUNA_GEMINI_API_KEY` set (present in
  luna-service/.env.example:154 — actual Render env value is owner-set) or a
  pasted key in Settings → Voice.

## Risks
- v1alpha surface may shift (proactivity is alpha) — pin exact field names by
  phase-0 spike, degrade gracefully (drop proactivity if refused at setup).
- Ephemeral-token WS auth query param name must be confirmed in phase 0
  (`access_token=` vs `key=`).
- iOS Safari AudioWorklet/autoplay quirks — playback starts from a user
  gesture (call start button), same as today.
