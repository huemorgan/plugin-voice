# Phase 01 — execution summary

Landed as planned; ElevenLabs path untouched, suite fully green.

## What shipped

- `openai_realtime.py` (new): `resolve_openai_key` (pasted `VAULT_OPENAI_KEY`
  → `vault.connect("openai")` with Bearer AuthSpec → `LUNA_OPENAI_API_KEY` /
  bare `OPENAI_API_KEY`); `RealtimeClient.mint_client_secret` with speakable
  errors (401 "rejected the key", 403/404 "endpoint may not support Realtime"
  for gateway proxies); static `VOICES` catalog (10 fixed Realtime voices,
  description labels shaped for `personality.pick_voice`); `session_config`
  (semantic VAD, eager/normal/patient → high/auto/low, unknown voice/model
  fall back to marin / gpt-realtime-2.1).
- `talker.py` (new): `build_instructions` — persona block (first-person AS
  the agent, greeting + fillers as style examples), the three lane rules
  (facts from tools / work via `luna_do` + keep talking / relay `[task
  update]` near-verbatim, never claim done early), voice style (owner
  `voice_system_prompt` override replaces the shipped text; the bridge-era
  default deliberately does NOT leak into the talker), open-mic block only
  when an imprint exists, `talker_extra` appended last.
- `routes.py`: `GET|POST /rt/session` (cookie-auth, GET because hosted widget
  cookies are read-only) — resolves key, builds instructions from stored
  persona + live identity name (NO blocking persona fetch; test pins
  `ctx.agent.calls == []`), mints the client secret server-side, arms one
  plugin `rt_token` (4 h TTL, `state.RT_TOKEN_TTL`) for `/rt/tool`,
  `/rt/events` and `/live`, returns
  `{client_secret, webrtc_url, model, voice, rt_token, live_token,
  persona_name, has_imprint, tool_names}`. The server key never appears in
  the payload (asserted).
- `state.py`: `mint_live_token(token, ttl=...)` + `RT_TOKEN_TTL = 4h`.
- `__init__.py`: `VAULT_OPENAI_KEY = "plugin_voice.openai_api_key"`.

## Tests

- `tests/test_realtime_core.py` — 19 tests: key-chain precedence, mock-
  transport minting (200/401/404/500/missing-value), session-config shape +
  fallbacks, instruction content (persona, lane rules, open-mic gating,
  override precedence), `/rt/session` route (no-key 400, happy GET+POST,
  imprint arming, 502 on mint failure + client closed, no persona fetch,
  key-leak guard).
- `tests/test_live_openai.py` — env-gated real mint; ran live with the QA key:
  `ek_…` secret minted for `gpt-realtime-2.1`. Skips cleanly without a key.
- Full suite: **129 passed, 2 skipped** (the 2 = live ElevenLabs, no key).

## Deviations from plan

None material. `live_token` is the same value as `rt_token` (one token arms
all three relay surfaces) rather than a second mint — fewer moving parts,
same auth story.
