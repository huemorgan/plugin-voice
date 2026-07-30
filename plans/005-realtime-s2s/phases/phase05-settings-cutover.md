# Phase 05 — Settings, persona migration, ElevenLabs removal, ship

## Goal

Owner-facing setup moves to OpenAI; the ElevenLabs path is deleted; the
plugin ships as 0.5.0, suite green, verified on a real Luna.

## Settings / setup rework

- `setup.py`: `do_connect` now = validate OpenAI key (mint a throwaway client
  secret as the probe) → store pasted key (`VAULT_OPENAI_KEY`) → fetch
  persona (unchanged `personality.fetch_persona`) → `pick_voice` against the
  static Realtime voice catalog → stamp settings
  (`persona_name/greeting/fillers/rt_voice`). No agent provisioning, no
  bridge secret, no public base capture.
- `build_status`: `connected` (key resolvable + source), `rt_voice`,
  `rt_model`, `persona_name` (live-first), `imprint_ready`. Drop
  `agent_ready/agent_id/bridge_*`.
- Chat tools `voice_status`/`voice_connect` (`__init__.py`): descriptions
  reworded for OpenAI Realtime; same server-side key-resolution promise.
- `ui/settings/index.html`: paste-key flow relabeled (OpenAI), voice dropdown
  from the static catalog, model select (`gpt-realtime-2.1` /
  `gpt-realtime-2.1-mini`); remove the tunnel/public-URL hints (obsolete).
- `persona_config.py`: keep `greeting`, `fillers` (style examples now),
  `voice_system_prompt`; add `talker_extra`, `rt_voice`, `rt_model`,
  `rt_lock_tools_to_owner`, `rt_tools_allow`, `rt_tools_deny`,
  `turn_eagerness` (maps to semantic_vad eagerness). Drop `triage_*`,
  `passthrough_prompt`, `soft_timeout_seconds`, `max_soft_timeouts`. Stored
  overrides for dropped fields are ignored harmlessly (merge only knows
  current DEFAULTS).
- `ui/settings/persona/index.html`: fields follow suit.

## Deletions

- `elevenlabs.py`, `bridge.py`, `/v1/chat/completions` route, hosted-bridge
  heal/migrate helpers, `hosted_bridge()`, bridge-secret vault key (delete on
  sight, plus in `disconnect`), `ui/widgets/voice/elevenlabs-client.js`.
- Tests: `test_bridge.py`, `test_hosted_bridge.py`, `test_live_elevenlabs.py`,
  ElevenLabs cases in `test_routes_dojo.py`/`test_voice_features.py` replaced
  by rt equivalents.
- README rewritten (three lanes, OpenAI key, no tunnel requirement;
  plugin-talk stays the ElevenLabs sibling).

## Version — all three stamps (memory: toml-only bumps look unapplied)

`PluginManifest.version = "0.5.0"` (authoritative), `pyproject.toml`,
`plugin_voice/luna-plugin.toml`.

## Acceptance

1. Full `pytest` green from a clean venv (`pip install -e ".[dev]"`).
2. Grep gates: no `elevenlabs` / `11labs` / `bridge_secret` references left
   outside plans/ and the README history note.
3. Live QA (memory: verify on a real running Luna): fresh connect via pasted
   key from Settings, widget call end-to-end (phase-04 checklist), rename
   agent → next `/rt/session` reflects the live name.
4. Ship: push repo (huemorgan2 auth), then publish to marketplaces.com.ai
   (standing instruction), sync `~/.luna/managed_plugins` if present.
