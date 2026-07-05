# 003 — Agent-Driven Connection + Gateway Slug — Execution Summary

**Version shipped:** plugin-voice 0.3.0 · **Tests:** 71 green

## What was accomplished

- **Root cause of the "agent connected the key but Voice says Not connected"
  screenshot:** the hosted gateway registers ElevenLabs as service slug
  **`11labs`** (admin-entered; the seeded registry derives `11labs_api_key` /
  `LUNA_11LABS_API_KEY` from the slug — luna-service
  `cloud/gateway/registry.py::default_names`), while the 0.2.3 resolution chain
  only asked `ctx.vault.connect("elevenlabs", ...)`. The chain now tries **both
  slugs** and both env-var spellings.
- **Agent tools** (`voice_status` auto_approve/low, `voice_connect` ask/medium):
  the brain can check and complete the whole setup from chat — resolve the key
  (never seeing its value), run the personality setup, provision the ElevenLabs
  agent. `bridge_secret` is stripped from tool outputs. Manifest updated
  (`[requires] tools = 2` + `[[tools]]`) → 0.3.0.
- **Public-base capture**: any authed `/status`/`/connect` visit records the
  tenant's public URL (proxy headers) into settings, so a chat-only
  `voice_connect` knows where the bridge lives; friendly error if it doesn't yet.
- **Settings UI**: the key-paste input is hidden by default — it appears only
  after the status check finds NO key anywhere. Detected platform key → a
  single "Use detected key" button. Branding now says **ElevenLabs (11labs)**
  everywhere so it's obvious which key powers Voice.
- **`setup.py` refactor**: connect/resolve/status flow extracted from route
  closures into ctx-taking functions shared by HTTP routes and agent tools
  (`SetupError` carries friendly messages + status codes).

## What we discovered along the way

- Gateway service slugs are DATA, not convention — "11labs" ≠ "elevenlabs".
  Anything resolving gateway keys must try the aliases (or read the inventory).
- **Concurrent-editing collision:** while this plan was in flight, a parallel
  session committed `0.2.4: settings cards hidden until connected` — it
  reverted the in-progress `__init__.py` edit (silently: string-replace
  "succeeded" against stale expectations) and the working tree ended half-merged.
  Recovered by diffing `git show` of the surprise commit and re-applying. Rule:
  re-read files before editing after any pause, and check `git log` for commits
  you didn't make.
- FastAPI + `from __future__ import annotations`: request models MUST be
  module-level (function-local classes silently become query params) — hit
  twice now (0.2.1, 0.2.3-refactor); worth a lint.
- Monkeypatched fakes must target every module that imports the symbol
  (`routes.ElevenLabsClient` AND `setup.ElevenLabsClient`).

## Things to consider in the future

- The tenant's Luna needs the marketplace upgrade + restart to get 0.3.0; the
  fleet's upgrade tray handles surfacing it.
- `voice_connect` uses the captured public base; a first-ever chat-only setup
  on a fresh tenant (no settings visit at all) still needs one Settings visit —
  could be removed if the SDK exposes the tenant's public URL.
- Consider reading the gateway inventory (`list_available_gateway_keys`
  equivalent server-side) instead of slug guessing.
- The parallel-session hazard: consider a lightweight lock/notice when two
  agents edit one plugin repo.
