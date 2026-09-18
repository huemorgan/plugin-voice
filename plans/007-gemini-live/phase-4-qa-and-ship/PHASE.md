# Phase 4 — final QA and ship 0.8.0

## Scope

Everything functional already landed in phases 1–3 (server Gemini Live core,
full WS client rewrite, turn-taking polish + the newSessionExpireTime fix).
This phase ships it: one release, 0.8.0, carrying all of it.

The plan's original phase-4 test work (test_live_gemini.py, setup-shape
tests, /rt/session contract, settings fallback) already landed during
phases 1–2 — here it's only a final full-suite run.

## Deliverables

1. **Version bump to 0.8.0** in all three stamps:
   - `plugin_voice/__init__.py` (in-code manifest)
   - `plugin_voice/luna-plugin.toml`
   - `pyproject.toml`
2. **Final verification** (see below) on the suite and on QA Luna.
3. **Commit + push** to main (attribution line per repo convention).
4. **Package + publish**: `scripts/package_plugin.py` → publish to
   marketplace slug `official` via `publish_plugin.sh` with LUNA_MP_TOKEN2;
   verify `latest_version` == 0.8.0 in the catalog.
5. **Execution summary** including owner follow-ups:
   - upgrade plugin-voice on hosted agents (publish doesn't auto-upgrade),
   - ensure `LUNA_GEMINI_API_KEY` is set on hosted (or paste a key in
     Settings → Voice),
   - real-microphone QA (barge-in + noisy room) needs a human — the
     wire-level checks in phase 3 are the automated stand-in.

## Verification

- Full suite in the plugin venv: expect 150 passed, 1 skipped (the skip is
  test_live_gemini without an env key; with the key exported it runs live).
- rsync the managed copy to QA Luna (:8765), restart if needed, then a real
  browser spot-check: settings page reports the env key ready
  ("Connected via the platform-provided Gemini key"), voice call reaches
  Listening with 0 console errors.
- Catalog check: `curl -s
  https://marketplaces.com.ai/api/catalog/official/plugin-voice` →
  `.latest_version == "0.8.0"`.
- No stray "openai" strings in the shipped package (UI + server).
