# Phase 1 — server: gemini_live.py + routes + setup

## Scope
Replace the OpenAI server half with a Gemini one. No client (JS) changes yet —
phase 1 and 2 ship together as 0.8.0 (nothing user-visible works with a phase-1
server and a phase-2-less client, so no solo publish).

## Deliverables
1. `plugin_voice/gemini_live.py` (new; `openai_realtime.py` deleted):
   - `SLUG="gemini"`, `ENV_VARS=("LUNA_GEMINI_API_KEY","GEMINI_API_KEY")`,
     `DEFAULT_MODEL="gemini-3.8-live"`, `MODELS=(…, "gemini-3.8-live-extended-thinking")`,
     `DEFAULT_VOICE="Aoede"`, 10-voice catalog with descriptions.
   - `resolve_gemini_key(ctx, vault_key)`: pasted vault key → env vars. No
     vault.connect/gateway lane: token minting authenticates directly against
     Google, a gateway virtual key can't (same reason the OpenAI path refused
     gateway keys with a 402).
   - VAD presets from persona turn_eagerness:
     patient → START_LOW/END_LOW, silence 800 ms, prefix 60 ms
     normal  → START_LOW/END_LOW, silence 650 ms, prefix 40 ms
     eager   → START_HIGH/END_HIGH, silence 450 ms, prefix 20 ms
     (LOW start everywhere except eager: background noise must not open turns.)
   - `setup_message(...)` → full camelCase `{"setup": …}` incl. proactiveAudio,
     transcriptions, compression, converted tool schemas.
   - `convert_tools(openai_style_list)` → `[{"functionDeclarations":[…]}]`,
     UPPERCASE parameter types, dropping OpenAI-only fields.
   - `GeminiLiveClient.mint_token(setup, uses=3)` via httpx (v1alpha,
     bidiGenerateContentSetup constraint) with the same safe RealtimeError
     messages; `LIVE_WS_URL` (Constrained) constant.
2. `routes.py`: `/rt/session` returns `{access_token, ws_url, setup, model,
   voice, rt_token, live_token, persona_name, has_imprint, tool_names}`;
   settings validation against the new catalogs; unknown legacy values fall
   back to defaults instead of 400 on mint (validation stays strict on save).
3. `setup.py`: connect flow validates a pasted key against
   `GET /v1beta/models` (x-goog-api-key), stores `plugin_voice.gemini_api_key`;
   disconnect deletes gemini + legacy openai + elevenlabs keys; status wording.
4. `__init__.py` vault-key constant `VAULT_GEMINI_KEY`; talker instructions get
   a noise/interruption demeanor block (moved up from phase 3 — it's one string
   in the same file family).
5. Tests: `test_live_gemini.py` replaces `test_live_openai.py` — key precedence,
   setup_message shape (VAD presets, proactivity, compression, transcription),
   tool conversion, mint error mapping, /rt/session contract, settings
   fallback; manifest/persona tests updated where they referenced OpenAI names.

## Verification
- Full plugin-voice suite green in its own .venv.
- Live probe: call the new mint path with the real env key from a script,
  connect the returned {ws_url, access_token, setup} exactly as the browser
  would, get audio — proving /rt/session output is browser-sufficient.
- QA-Luna route check happens in phase 2 (needs the client to exercise it).
