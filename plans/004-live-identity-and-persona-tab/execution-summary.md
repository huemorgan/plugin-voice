# 004 — Execution summary

Shipped in **0.4.0**; **0.4.1** folded the persona editor back into the single
"Voice" settings entry (owner feedback: one plugin, one left-pane item). The
shell declares ONE SettingsTab; a Setup/Persona tab switcher inside the page
navigates between `ui/settings/` and `ui/settings/persona/`, carrying the
shell's `?token=` across the switch.

## What changed

### A. Live identity — no more stale name
- `identity.py` (new): `live_identity(ctx)` / `live_name(ctx)` read the
  `identity` ConfigSection off `ctx.config_registry` (plugin-identity's
  sanctioned surface). Degrades to `None` anywhere the registry is absent.
- `/status` and `/session` report the live name, falling back to the stored
  snapshot only when no registry is available.
- `/session` compares live name vs `settings["persona_name"]` (the name the
  ElevenLabs greeting was generated for). On mismatch it schedules a
  **background** `setup.resync_persona(...)` — session minting never blocks on
  the ~30s persona LLM fetch. Single-flight guard + task ref live in
  `state.py`. Mid-call replies were never stale (run_turn is live); only the
  baked greeting needed the re-PATCH, and the *next* call picks it up.
- `resync_persona` stamps `persona_name` with `persona.name or live_name` so a
  degraded (NEUTRAL) persona fetch can't retrigger resync on every session.

### B. Voice Persona settings tab — nothing hardcoded anymore
- `persona_config.py` (new): three-layer merge **owner override >
  personality-auto > shipped default** with `apply_changes` validation.
  Owner-tweakable fields: `greeting`, `fillers`, `voice_system_prompt`,
  `triage_enabled`, `triage_system`, `passthrough_prompt`,
  `soft_timeout_seconds`, `max_soft_timeouts`, `turn_eagerness`
  (+ voice picker on the same page, saved via the existing `/settings`).
- Shipped defaults are byte-identical to the pre-004 hardcodes → untouched
  installs produce identical ElevenLabs configs → `AGENT_CONFIG_V` stays 2,
  no migration PATCH storm.
- `bridge.py` / `elevenlabs.py` parametrized (`system_prompt`, `system`,
  `overrides` kwargs); connect/heal/migrate/resync all pass
  `persona_config.elevenlabs_overrides(settings)`.
- Routes: `GET/PUT /persona-settings` (PUT re-PATCHes the ElevenLabs agent
  only when a voice-side field changed); manual `refresh-persona` now
  delegates to the same `setup.resync_persona`.
- New tab `voice-persona` → `ui/settings/persona/index.html`
  (tenant-prefix-safe API base, override-vs-auto field notes, reset-all).
- Fixed latent `_serve` bug: directory paths now resolve to their own
  `index.html` instead of falling back to the root settings page.

## Tests
`tests/test_persona_settings.py` (new, 15 tests): merge precedence,
validation, GET/PUT round-trip + agent re-PATCH, prompt-only PUT skips the
PATCH, custom prompts reach run_turn/run_llm, triage off-switch, live name in
/status, /session auto-resync (awaited via the held task ref) and no-op when
names match, resync preserves owner overrides. Manifest tests updated for the
second tab. **Suite: 110 passed, 1 skipped.**
