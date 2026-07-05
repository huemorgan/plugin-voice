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


## Amendment — the HTTP 500 investigation (v0.3.1 round, 2026-07-05)

**Symptom:** "Use detected key" on the hosted tenant → `ElevenLabs /v1/voices
failed: HTTP 500`.

**Root cause (confirmed in prod logs):** the gateway's 11labs service row was
mis-entered — `upstream_url` contains the ElevenLabs API KEY (`sk_808b…`)
instead of `https://api.elevenlabs.io`, so the proxy builds
`sk_…/v1/voices` and crashes (`ValueError: unknown url type`). The key value
is therefore also leaked in Render request logs — **rotate it** when convenient.
`auth_style` is `header:Authorization:Bearer` but ElevenLabs uses `xi-api-key`
(the resolver handles either, but consistency matters).

**Fix state:** the row was updated via Render one-off jobs *through the app's
own session* (verified by a read-back job) — but the LIVE web instance still
serves the old values, and a redeploy-to-refresh **failed to boot**
(`ConnectionRefusedError` connecting to a DB in lifespan). Conclusion: the
running instance's env snapshot and the freshly-resolved env point at
**different databases**, and the freshly-resolved path is unreachable from new
containers. This is pre-existing platform drift, not caused by this work — the
next luna-service deploy by anyone would fail the same way. Old instance left
untouched and healthy.

**The 30-second proper fix (owner):** the fleet admin UI writes through the
LIVE app's own DB connection — edit gateway service `11labs`:
`upstream_url = https://api.elevenlabs.io`, `auth_style = header:xi-api-key`.
Effective immediately, no deploy needed.

**Debug techniques that worked:** Render logs API with `text=` filters found
the traceback; `/proxy/anthropic` control probe validated the mental model
(passthrough OK → fault isolated to the 11labs row); one-off jobs with
exit-code semantics beat phone-home (trycloudflare interstitials eat
server-to-server POSTs silently).
