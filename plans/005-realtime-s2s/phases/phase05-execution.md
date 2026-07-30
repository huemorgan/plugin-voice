# Phase 05 — execution summary

Landed: OpenAI cutover complete, ElevenLabs path deleted, version **0.5.0**
(all three stamps), suite fully green, live QA on a real Luna passed.

## What changed

**Deleted** (git rm): `plugin_voice/bridge.py`, `plugin_voice/elevenlabs.py`,
`plugin_voice/ui/widgets/voice/elevenlabs-client.js`, `tests/test_bridge.py`,
`tests/test_hosted_bridge.py`, `tests/test_live_elevenlabs.py`.

**Rewritten**
- `routes.py` — OpenAI-only surface. `/connect` (pasted key → validate →
  vault), `/disconnect` (also purges the three legacy 0.4.x ElevenLabs vault
  keys), `/status`, `/voices` (static catalog + models), `/settings`
  (`rt_voice` / `rt_model` / `rt_lock_tools_to_owner` / `rt_tools_allow` /
  `rt_tools_deny`, per-field apply, None clears), `/rt/session`, `/rt/tool`
  (incl. `luna_do`/`luna_task_status` synthetics + owner lock), `/rt/events`,
  imprint routes unchanged, `/refresh-persona`, `GET/PUT /persona-settings`
  (no more upstream PATCH — nothing to PATCH anymore). Static `_serve` now
  404s missing assets (dotted names) instead of SPA-falling-back — the old
  behavior served `index.html` for `elevenlabs-client.js`.
- `state.py` — dropped the shared ElevenLabs client + resync-task machinery;
  keeps live-token store (LIVE_TOKEN_TTL 300 s, RT_TOKEN_TTL 4 h), speaker
  state, task manager.
- `__init__.py` — version **0.5.0** (authoritative stamp), two vault
  constants only (`openai_api_key`, `settings`), tools `voice_status` /
  `voice_connect` rewritten for OpenAI, no `on_unload`.
- `ui/settings/index.html` — OpenAI wording, paste-block hidden when
  connected, gateway-connect button, voice + **model** selects from the
  static catalog (no preview audio), imprint recorder unchanged.
- `ui/settings/persona/index.html` — dropped Timing (soft timeouts), triage,
  passthrough-prompt cards and audio preview; now: voice picker (saves
  `{rt_voice}`), greeting/fillers auto-vs-override, turn eagerness,
  speaking-style prompt (`voice_system_prompt`), **extra instructions**
  (`talker_extra`), re-match-personality button, reset-all.
- `README.md` — three-lane architecture, OpenAI setup, no-tunnel note,
  ElevenLabs history note (plugin-talk stays the sibling).
- `luna-plugin.toml` + `pyproject.toml` — 0.5.0, descriptions in sync
  (test-enforced byte equality with the manifest).

**Tests** — `conftest.py` FakeEL → `FakeRT` (patches
`openai_realtime.RealtimeClient`, records minted session configs, delenvs
`LUNA_OPENAI_API_KEY`/`OPENAI_API_KEY` so a dev's real key can't leak into
tests); `test_persona_settings.py`, `test_routes_dojo.py`,
`test_voice_features.py` rewritten for the 0.5.0 surface;
`test_manifest.py` pins 0.5.0 + rt-client.js shipped + ElevenLabs bundle
gone.

## Fixes found while landing

- `anyio.run` doesn't forward kwargs — `_prewire(ctx, overrides=…)` call
  sites became positional.
- Static `_serve` SPA fallback answered 200 for any missing file; restricted
  the fallback to extension-less paths.
- Stale ElevenLabs comments reworded in `personality.py` /
  `openai_realtime.py` (code was already generic).

## Gates

- Suite: **130 passed, 1 skipped** (skip = live OpenAI mint, needs
  `LUNA_OPENAI_API_KEY`) from the plugin venv.
- Grep gate `elevenlabs|11labs|bridge_secret`: remaining hits only in
  `plans/` (history), `tests/dojo/` (the calibration tool legitimately
  synthesizes test voices via ElevenLabs TTS), the README history note,
  tests' negative assertions + the legacy-vault-purge test, and
  `routes.LEGACY_VAULT_KEYS` (the purge list itself).

## Live QA (QA Luna :8766, plugin 0.5.0 via managed_plugins)

- Fresh `/connect` with pasted real key → `key_source: "own"`, key value
  absent from every response. `/disconnect` → falls back to env key
  (resolution chain, expected).
- `/settings` `rt_voice: cedar` round trip → `/rt/session` mints a real
  `ek_…` secret with voice cedar, model gpt-realtime-2.1, 34 tools
  (knowledge lane + `luna_do`/`luna_task_status`).
- Rename agent Atlas → Vega via plugin-identity → next `/rt/session` says
  Vega (live-name, not snapshot). Restored.
- `/rt/tool` knowledge call (`wiki_list_wikis`) → real wiki data; bad token
  → 401.
- `luna_do` dispatch → accepted, task id, `task_started` on `/rt/events`
  replay WS. The background turn itself failed with **Anthropic "credit
  balance too low"** — an account/billing issue, not plugin code; the
  graceful-failure path (status `failed` + apologetic `spoken_summary`)
  worked as designed. Lane 3 was verified end-to-end with real turns in
  phase 03 QA.
- `PUT /persona-settings` save + clear round trip; persona/settings/widget
  pages 200; `elevenlabs-client.js` 404.

## Note

`~/.luna/managed_plugins/plugin_voice` uses the FLAT layout (package
contents at the top level). Re-sync with
`rsync -a --delete plugin_voice/ ~/.luna/managed_plugins/plugin_voice/`
and clear `__pycache__`.
