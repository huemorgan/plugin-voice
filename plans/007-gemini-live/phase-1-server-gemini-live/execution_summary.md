# Phase 1 — execution summary

## What shipped

The entire server half of plugin-voice now speaks Gemini Live instead of
OpenAI Realtime. No version bump or publish yet — phases 1+2 ship together as
0.8.0 (a phase-1 server with the old WebRTC client is not usable alone).
Nothing is committed yet either; the working tree holds the full change set
for a single 0.8.0 commit after phase 2.

Files:

- `plugin_voice/gemini_live.py` — NEW (~270 lines). Everything Gemini in one
  module: model/voice catalogs (`gemini-3.8-live` default,
  `gemini-3.8-live-extended-thinking`; 10 prebuilt voices, Aoede default),
  `resolve_gemini_key` (pasted vault key → `LUNA_GEMINI_API_KEY` /
  `GEMINI_API_KEY` env — no gateway lane, minting must authenticate directly
  against Google), VAD presets keyed by persona `turn_eagerness`
  (START_SENSITIVITY_LOW everywhere except eager, silence 800/650/450 ms),
  `setup_message()` building the full locked `{"setup": …}` (AUDIO modality,
  voice, systemInstruction, converted tools, VAD preset,
  `proactivity.proactiveAudio`, `outputAudioTranscription`, context-window
  compression 25600/12800, `sessionResumption:{}`),
  `convert_tools()` (OpenAI-flat → `functionDeclarations`, recursive UPPERCASE
  types, empty object schemas omit `parameters`), and `GeminiLiveClient`
  (httpx, `POST /v1alpha/auth_tokens` with `bidiGenerateContentSetup`
  constraint, uses=10, 4 h expiry, 2 min new-session window, safe
  RealtimeError messages for 401/403/429/4xx/no-name).
- `plugin_voice/openai_realtime.py` — DELETED (git rm).
- `plugin_voice/routes.py` — `/rt/session` mints a Gemini ephemeral token and
  returns `{access_token, expires_at, ws_url, setup, model, voice, rt_token,
  live_token, persona_name, has_imprint, tool_names}`; the browser must send
  `setup` verbatim as its first WS frame (it must match the token's locked
  constraint). Settings validation is strict-on-save against the new
  catalogs; stale OpenAI-era model/voice values fall back to defaults at mint
  time so the call button never dead-ends. Disconnect deletes the gemini key
  plus all legacy vault keys (openai + elevenlabs trio). `/voices` serves the
  Gemini catalogs.
- `plugin_voice/setup.py` — connect flow stores the pasted key under
  `plugin_voice.gemini_api_key` and validates it by minting a throwaway
  1-use token; status/messages reworded; stale `rt_voice` cleared before
  persona voice-pick.
- `plugin_voice/__init__.py` — `VAULT_GEMINI_KEY` constant; manifest +
  tool descriptions reworded to Gemini; `VAULT_OPENAI_KEY` kept only for
  disconnect cleanup.
- `plugin_voice/talker.py` — new `NOISE_RULES` block (always included):
  resume naturally after false interruptions, stop for real speech, never
  acknowledge background noise, never go quiet because the room is noisy.
- `plugin_voice/luna-plugin.toml` — description/tags/readme/tool wording
  synced to Gemini (manifest-agreement test forced this now, not phase 4).
- Tests — conftest `FakeLive` replaces `FakeRT` (patches
  `gemini_live.GeminiLiveClient`, records full minted setups);
  `test_realtime_core.py` rewritten (key precedence, mint HTTP contract via
  MockTransport, error mapping, setup shape, VAD pins, tool conversion,
  /rt/session contract); `test_live_gemini.py` replaces `test_live_openai.py`;
  broker/tasks/dojo/persona/features/routes tests migrated. New pin:
  `test_resolve_never_uses_the_gateway`.

## Verification

- Full suite in the plugin's own venv: **149 passed, 2 skipped** (baseline
  was 146/1; the extra skip is `test_js_field_list_matches_client_source`,
  which self-skips while rt-client.js still speaks WebRTC and re-arms
  automatically when phase 2 rewrites it).
- **Live probe** (scratchpad `probe_phase1.py`, run with the real
  `LUNA_GEMINI_API_KEY`): imported the real `gemini_live.py`, built a
  production-shaped `setup_message` (real tool schemas, patient VAD), minted
  through `GeminiLiveClient`, then connected
  `LIVE_WS_URL?access_token=…` exactly as the browser will and sent the setup
  verbatim. Result: `setupComplete` (Google accepted the full setup as a
  token constraint — including the three things phase 0 had NOT covered:
  `sessionResumption:{}`, 4 h `expireTime`, `uses:10`), 105 KB of
  `audio/pcm;rate=24000` model audio with transcription, a real
  `get_weather` toolCall answered via `toolResponse`, and a
  `sessionResumptionUpdate` frame. This proves the /rt/session payload is
  browser-sufficient.
- `tests/test_live_gemini.py` run with the real key: 1 passed (0.33 s — a
  real network mint; an earlier run silently used the conftest fake, fixed by
  binding the real client at module import time).

## Deviations from PHASE.md

1. **Key validation on connect**: PHASE.md said `GET /v1beta/models`; shipped
   a throwaway 1-use token mint instead — it exercises the exact call
   sessions depend on, so a key that passes cannot fail later at mint time.
2. **Token uses raised 3 → 10** — resumption/goAway reconnects reuse the same
   token; 3 was too tight for a long call with reconnects.
3. **NOISE_RULES unconditional** — not imprint-gated; every session benefits.
4. **luna-plugin.toml wording done now** (manifest-agreement test demanded
   consistency once the in-code manifest changed).
5. **mint response**: Google returns only `name` (no `expireTime` echo), so
   `mint_token` reports the expiry it requested — found by the live probe.
6. `validate_key` (list-models helper) was written then removed as dead code.

## Surprises / learnings

- Google's auth_tokens response body is just `{"name": …}` — don't expect the
  expiry back.
- The conftest autouse patch can silently defang "live" tests: any test that
  imports `GeminiLiveClient` inside the test function gets the fake. Live and
  MockTransport tests must bind the class at module import time.

## Reassessment of remaining phases

- **Phase 2 (browser client)** — unchanged, and de-risked: the probe pinned
  the exact browser sequence (connect with `?access_token=`, send `setup`
  verbatim, then setupComplete → clientContent/realtimeInput; audio out is
  24 kHz PCM inlineData; toolCall/toolResponse round trip works over the
  constrained socket). One addition: the greeting nudge should be a
  `clientContent` text turn — the probe shows the model answers it with audio
  immediately.
- **Phase 3 (turn-taking polish)** — unchanged. VAD presets + proactivity are
  already accepted by Google as locked constraints, so phase 3 is genuinely
  just UI wording + soak, no wire risk.
- **Phase 4 (QA + ship 0.8.0)** — unchanged; remember the two settings-UI
  assertions in test_voice_features.py still assert the OLD HTML ("OpenAI",
  gateway card) — phase 2 must flip them together with the HTML rewording.
