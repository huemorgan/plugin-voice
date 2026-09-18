# Phase 4 — execution summary

## What shipped

**plugin-voice 0.8.0** — the whole Gemini Live migration (phases 1–3) in one
release. Commit `bd1d98d` ("0.8.0: voice runs on Gemini Live (replaces
OpenAI Realtime)") on `main`, pushed to
`github.com/huemorgan/plugin-voice`, published to marketplace `official`
(catalog `latest_version` verified `0.8.0`).

Version stamps bumped in all three places: `plugin_voice/__init__.py`
(manifest), `plugin_voice/luna-plugin.toml`, `pyproject.toml`. The toml
`description` was also updated (it still said "OpenAI Realtime"; now
"Gemini Live speech-to-speech … noise-robust turn taking").

### Rebase surprise (handled)

The remote had moved while this plan ran: `61a9a05` shipped **v0.7.1**
("governor v3 — never trade resolution, leaky-bucket sensing" in the Luna
View pane) plus a docs commit. The 0.8.0 commit was rebased onto it. Only
the three version stamps conflicted (0.7.1 vs 0.8.0 — resolved to 0.8.0);
`ui/view/index.html` merged cleanly, and a post-rebase diff against 0.7.1
confirmed the governor v3 changes are fully preserved (the only remaining
delta in that file is a one-line comment reword, OpenAI→Gemini). Full suite
re-run after the rebase before pushing.

## Verification

- **Suite:** `plugins/plugin-voice/.venv/bin/python -m pytest tests -q` →
  **150 passed, 1 skipped** (the skip is `test_live_gemini` without an env
  key), run both before and after the rebase. `test_manifest.py`'s pinned
  version updated to 0.8.0.
- **No stray "openai" strings:** zero occurrences in the UI tree; the only
  Python mentions are the deliberate legacy vault-key cleanup
  (`VAULT_OPENAI_KEY`, deleted on disconnect) and historical comments.
- **QA Luna (:8765), real browser** with the packaged bytes rsynced to
  `~/.luna/managed_plugins/plugin_voice` and the server restarted:
  - `/api/plugins` reports `plugin-voice 0.8.0` enabled.
  - Settings page renders "Connected via the platform-provided Gemini key —
    ready to talk", Gemini wording, both models, the 10-voice catalog.
  - Voice widget: call started (mic stubbed with a synthesized silent
    MediaStream), reached **"Listening…"** against the real Gemini Live
    endpoint with **0 console errors**, then ended cleanly.
- **Marketplace:** publish returned `{"status": "published",
  "plugin": "official/plugin-voice", "version": "0.8.0"}`;
  `GET /api/catalog/official/plugin-voice` → `latest_version: "0.8.0"` with
  the new description.

## Owner follow-ups (not automatable from here)

1. **Upgrade plugin-voice on hosted agents** — publishing does not
   auto-upgrade installed agents.
2. **Hosted key:** ensure `LUNA_GEMINI_API_KEY` is set in the hosted
   (Render) environment, or paste a Google AI key in Settings → Voice per
   agent. Hosted installs never pip-install plugin deps — the plugin
   deliberately uses pure httpx (no `google.genai`), so no dependency
   change is needed.
3. **Real-microphone QA:** barge-in and noisy-room behavior with an actual
   human voice and an actual noisy room needs ears. The wire-level checks
   (phase 3: white noise ignored, real speech interrupts) are the automated
   stand-in, but a quick live call — talk over Luna mid-sentence, type
   loudly near the mic — is the true acceptance test.
4. The local QA Luna server was left running on port 8765 (log in the
   session scratchpad); kill it whenever. `scratchpad/qa_token.txt` is a
   local QA-only JWT.

## Reassessment of remaining phases

None remain — this was the final phase. Plan 007 is complete: Luna voice
runs entirely on Gemini Live (`gemini-3.8-live`, default voice Aoede), with
server-locked ephemeral tokens (the key never reaches the browser),
noise-robust per-persona turn-taking (VAD presets + proactiveAudio,
wire-pinned), barge-in, session resumption across Google's forced ~10-minute
drops (soak-tested to 16.6 min), and a settings/persona UI with no OpenAI
remnants.
