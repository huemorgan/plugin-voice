# 004 — Live identity + "Voice Persona" settings tab

## Problems (owner report, 2026-07-05)

1. **Stale identity.** `setup.do_connect` snapshots the agent's persona
   (name, greeting, fillers, voice pick) into vault settings and bakes it
   into the ElevenLabs agent config (`first_message`, soft-timeout fillers).
   When the owner renames the agent in plugin-identity, nothing re-reads it —
   the call still opens with the old name. The only fix today is the manual
   "Re-match voice & words" button.
2. **Hardcoded, untweakable voice behavior.** Everything that shapes how the
   agent sounds is a Python constant: `bridge.VOICE_SYSTEM_PROMPT`,
   `bridge.TRIAGE_SYSTEM`, the ElevenLabs passthrough prompt, the neutral
   greeting, the default fillers, soft-timeout seconds / max count, turn
   eagerness. The owner cannot control or tweak any of it.

## Audit — persona data duplicated from the agent

| Stored in plugin settings | Real source | Verdict |
|---|---|---|
| `persona_name` | plugin-identity `name` | **Stop trusting the snapshot.** Read live via `ctx.config_registry.get("identity").reader()` (registered by plugin-identity; sanctioned surface, no core imports). Keep the stored value only as the "generated-for" marker to detect renames. |
| `greeting` | agent's own personality (LLM fetch) | Keep the auto-fetched value, but auto-REFRESH on rename (below) and let the owner override it in the new tab. |
| `fillers` | agent's own personality (LLM fetch) | Same as greeting. |
| `voice_id` | owner choice / LLM pick | Already owner-controlled; surfaces in the new tab too. |

Mid-call replies were never stale — `run_turn` composes the system prompt
with live identity. Only the baked greeting/config and the status line were.

## Design

### A. Live identity + auto re-sync (fixes the stale name)

- New `identity.py`: `live_identity(ctx) -> dict | None` reading the
  `identity` config section registered by plugin-identity. Graceful `None`
  on any failure (older core, bare test ctx).
- `build_status` / `/session` report `persona_name` = live name when
  available, falling back to the stored snapshot.
- On `/session` (every call start): if the live name differs from
  `settings["persona_name"]`, schedule a background
  `setup.resync_persona(ctx, client, agent_id)` — the same flow as
  refresh-persona (fetch persona → keep owner voice pick → PATCH agent →
  re-stamp settings). Background so session minting never waits ~30s on an
  LLM call; the *next* call after a rename greets with the new name, this
  one already answers as the new name (replies are live). A module-level
  in-flight guard prevents stampedes.

### B. "Voice Persona" settings tab — every hardcoded knob editable

New per-field **overrides** dict stored inside the existing
`plugin_voice.settings` vault JSON under `"persona_overrides"`. Unset field
→ auto value (fetched persona) → shipped default. New module
`persona_config.py` owns `DEFAULTS`, `effective(settings)` merge and PUT
validation.

| Field | Default (today's hardcode) | Applied to |
|---|---|---|
| `greeting` | auto persona greeting → neutral | ElevenLabs `first_message` |
| `fillers` (list ≤5) | auto persona fillers → neutral trio | buffer words, keepalives, soft-timeout messages |
| `voice_system_prompt` | `bridge.VOICE_SYSTEM_PROMPT` | every bridge turn |
| `triage_enabled` (bool) | `True` | open-mic gate on/off |
| `triage_system` | `bridge.TRIAGE_SYSTEM` | triage LLM call |
| `passthrough_prompt` | elevenlabs.py bridge prompt text | ElevenLabs agent prompt |
| `soft_timeout_seconds` (1–30) | `5.0` | ElevenLabs turn config |
| `max_soft_timeouts` (0–10) | `3` | ElevenLabs turn config |
| `turn_eagerness` | `patient` (`eager`/`normal`/`patient`) | ElevenLabs turn config |

Routes (owner-authed):
- `GET /persona-settings` → `{values, overrides, defaults, persona_name}`.
- `PUT /persona-settings` → partial body; `null` clears an override back to
  auto/default; validation errors are 400. If an ElevenLabs-side field
  changed and an agent is provisioned, re-PATCH the agent immediately.

Plumbing: `_agent_config`/`create_agent`/`update_agent_bridge` gain an
`overrides` kwarg (soft-timeout, eagerness, passthrough prompt). All
existing `update_agent_bridge` call sites (connect, heal, migrate,
refresh-persona) pass the current overrides so a PATCH never silently
resets an owner tweak. `bridge.build_prompt`/`triage_utterance` accept an
optional system-prompt override. Defaults produce a byte-identical
ElevenLabs config → no `AGENT_CONFIG_V` bump, no migration PATCH storm.

Manifest: second `SettingsTab` (`id="voice-persona"`, label "Voice Persona")
→ new static page `ui/settings/persona/index.html` (served by the existing
`/ui/settings/{path}` route). UI: voice picker, greeting + fillers editors,
timing knobs, collapsible "Advanced prompts" textareas, per-field
auto/default hints, "Reset all to defaults", BASE derived from
`location.pathname` (hosted-tenant safe).

### C. Tests

- `persona_config`: merge precedence (override > auto > default), validation.
- Routes: GET shape; PUT persists + re-PATCHes the agent with overrides;
  invalid values 400; PUT of prompt-only fields does not PATCH.
- Bridge: custom system prompt lands in `run_turn` prompt; custom triage
  prompt in `run_llm`; `triage_enabled=false` skips triage.
- Identity: `/session` reports the live name; name mismatch schedules a
  resync; resync PATCHes the agent and re-stamps settings.
- Manifest: two settings tabs; persona UI page served.
- Existing 89 tests stay green (FakeCtx has no config_registry → live read
  degrades to stored snapshot; no behavior change for old paths).

## Ship

- Version 0.4.0 (toml, pyproject, `__init__`, manifest test pin).
- `pytest` green → rsync to `~/.luna/managed_plugins/plugin_voice/` →
  git commit + push (standalone repo) → `package_plugin.py` →
  `publish_plugin.sh … official` → verify marketplace index shows 0.4.0.
