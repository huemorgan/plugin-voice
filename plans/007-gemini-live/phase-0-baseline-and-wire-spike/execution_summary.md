# Phase 0 — execution summary (2026-09-18)

No plugin code changed. All spikes ran against the real Gemini API with the
owner's `LUNA_GEMINI_API_KEY` (never printed).

## Baseline
- plugin-voice test suite via its own `.venv`: **146 passed, 1 skipped**.
- NOTE: the luna core venv can't run this suite (no numpy) — the plugin has its
  own `.venv` with numpy; use `plugins/plugin-voice/.venv/bin/python -m pytest`.

## Wire format — pinned (everything verified live)
- **Token mint (plain REST, no SDK)**: `POST https://generativelanguage.googleapis.com/v1alpha/auth_tokens`
  with header `x-goog-api-key: <real key>`, body
  `{"uses": 1, "expireTime": <ISO, +30min>, "newSessionExpireTime": <ISO, +2min>,
  "bidiGenerateContentSetup": {…full camelCase setup…}}` → 200
  `{"name": "auth_tokens/…"}`. The first attempt with the docs' field name
  `liveConnectConstraints` was refused ("Cannot find field") — the real field is
  **`bidiGenerateContentSetup`** and it takes the *entire* setup message, which
  locks system instruction, tools and VAD server-side (the browser can't tamper).
- **Browser WS**: `wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContentConstrained?access_token=<auth_tokens/…>`.
  Plain `BidiGenerateContent` refuses tokens ("unregistered callers") with both
  `access_token=` and `Authorization: Token` — the **Constrained** method name is
  required (found in google-genai SDK source, live.py:959).
- Setup accepted on v1alpha with all our knobs at once:
  `generationConfig.responseModalities:["AUDIO"]` + `speechConfig`, `systemInstruction`,
  `tools[].functionDeclarations` (UPPERCASE types), `realtimeInputConfig.automaticActivityDetection`
  (START_SENSITIVITY_LOW / END_SENSITIVITY_LOW / prefixPaddingMs 60 / silenceDurationMs 700),
  `proactivity:{proactiveAudio:true}` (v1alpha-only — v1beta refuses the field),
  `outputAudioTranscription:{}`, `contextWindowCompression:{triggerTokens,slidingWindow}`.
- Session behavior verified: `setupComplete` → clientContent turn → audio out
  (`audio/pcm;rate=24000`, 96 KB for one sentence), output transcription text,
  `toolCall` {functionCalls:[{id,name,args}]} → `toolResponse`
  {functionResponses:[{id,name,response}]} round trip, `turnComplete` per turn.

## Voice catalog
All 18 candidate prebuilt voices accepted by `gemini-3.8-live` (Puck, Charon,
Kore, Fenrir, Aoede, Leda, Orus, Zephyr, Autonoe, Callirrhoe, Despina, Erinome,
Algenib, Rasalgethi, Achernar, Sulafat, Vindemiatrix, Sadachbia). Phase 1 ships
a curated 10-voice catalog with descriptions for personality.pick_voice;
default **Aoede** (closest to the old "marin" warm-female default).

## Deviations from PHASE.md
- None in substance. Two documented-vs-real corrections (constraint field name,
  Constrained WS method) — exactly what the spike was for.

## Reassessment of remaining phases
- Phase 1 unchanged, plus two pins: mint tokens on **v1alpha** with
  `bidiGenerateContentSetup` as the constraint (full setup lock), and hand the
  browser the Constrained WS URL. `/rt/session` should return the full setup
  JSON for the client to send verbatim (it must match the token's locked setup).
- Phase 2 unchanged; wire shapes now exact. `uses` should be >1 (docs: session
  resumption reconnects reuse the token every ~10 min) — set `uses: 3` and
  `newSessionExpireTime` ~+2 min.
- Phases 3–4 unchanged.
