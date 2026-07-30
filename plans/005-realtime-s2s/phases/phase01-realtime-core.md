# Phase 01 — Realtime core: key, session minting, talker instructions

## Goal

A cookie-authed widget can fetch everything it needs to open a WebRTC call to
OpenAI Realtime as Luna's talker. No widget/UI work yet; ElevenLabs path
untouched.

## New module `openai_realtime.py`

- `resolve_openai_key(ctx) -> dict | None` — same chain as `setup.resolve_key`:
  1. pasted key (`VAULT_OPENAI_KEY = "plugin_voice.openai_api_key"`),
  2. `ctx.vault.connect("openai", upstream_default="https://api.openai.com",
     auth=AuthSpec(header "Authorization", scheme "Bearer"))`,
  3. env `LUNA_OPENAI_API_KEY` then `OPENAI_API_KEY` via `ctx.get_env`
     (LUNA_-only) falling back to `os.environ` for the bare name.
- `RealtimeClient` (httpx, mirrors `ElevenLabsClient` shape):
  - `mint_client_secret(session_config) -> dict` → POST `/v1/realtime/client_secrets`,
    returns `{value, expires_at, session}`; `RealtimeError` on non-2xx with a
    friendly message (401 → "OpenAI rejected the key", 403/404 on a proxy →
    "your key's gateway does not support Realtime").
  - `close()`.
- `VOICES`: static catalog `[{voice_id, description}]` for the fixed Realtime
  set (marin, cedar, alloy, ash, ballad, coral, echo, sage, shimmer, verse)
  with one-line voice descriptions — feeds `personality.pick_voice` unchanged
  (it already takes a generic catalog).
- `session_config(instructions, voice, model, tools) -> dict` — the
  `{"session": {...}}` body: type realtime, audio in/out (pcm16 in via WebRTC
  defaults), `turn_detection: semantic_vad` with configured eagerness,
  `tools` (from later phases; empty now), `instructions`.

## Talker instructions builder — `talker.py`

`build_instructions(persona, effective_settings, *, tool_names, speaker_aware)`
returns the lane-1 system prompt:

1. **Identity block** — "You are the live voice of {name}." + persona
   greeting/fillers as *style examples* (the talker improvises in that
   register; nothing baked).
2. **Three-lane rules** (the hard rules from PLAN.md): facts → knowledge
   tools; work → `luna_do`, never claim completion without a task event,
   narrate honestly and keep the conversation moving; relay `spoken_summary`
   near-verbatim; details live in web chat.
3. **Voice style** — the existing `voice_system_prompt` override slot carries
   over as the owner-editable style section (short sentences, no markdown…).
4. **Open-mic block** (only when an imprint exists) — meaning of
   `[voice check: …]` context items; when the speaker is unrecognized, stay
   in character, no refusals, extra caution on private/destructive asks;
   ignore chatter not addressed to you (replaces the triage LLM).
5. Owner-editable via a new `talker_extra` persona override appended last.

## Route `GET|POST /api/p/plugin-voice/rt/session`

Cookie-authed (`get_current_user`); GET allowed because hosted widget cookies
are read-only and minting writes nothing durable in Luna. Flow:

1. `resolve_openai_key` → 400 "add your OpenAI key in Settings → Voice" if none.
2. Persona: reuse stored settings + `identity.live_name` (no blocking persona
   fetch here — connect/refresh stamps it, same as today).
3. Voice: `settings["rt_voice"]` else default `marin`; model:
   `settings["rt_model"]` else `gpt-realtime-2.1`.
4. Mint client secret; mint plugin `rt_token` (extend `live_state` minting
   with a 4-hour TTL variant — calls outlast the 300 s live-token TTL);
   `reset_speaker()`.
5. Return `{client_secret, expires_at, model, voice, rt_token,
   persona_name, has_imprint, webrtc_url: "https://api.openai.com/v1/realtime/calls"}`.

Session config sent to OpenAI includes instructions + (later) tool schemas, so
the widget never sees the server key and never composes the prompt.

## Tests (`tests/test_realtime_core.py`)

- key resolution precedence + miss → None (fake ctx patterned on conftest).
- `mint_client_secret` happy/401/proxy-404 via a fake httpx transport.
- `session_config` shape: semantic_vad, voice, instructions threaded.
- `build_instructions`: persona name present; hard rules present; open-mic
  block only with imprint; `talker_extra` appended; owner
  `voice_system_prompt` override wins over shipped style text.
- `/rt/session` route: 400 without key; happy path returns rt_token + secret
  (RealtimeClient monkeypatched); GET and POST both work; no persona fetch
  triggered.
- Live (env-gated, `tests/test_live_openai.py`): real mint against
  `LUNA_OPENAI_API_KEY` when present — skipped otherwise.
