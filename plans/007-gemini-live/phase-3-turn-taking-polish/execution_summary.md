# Phase 3 — execution summary

## What shipped

Phase 3 was mostly proof work; the only code changes were one wording edit
and one production bug fix that the soak itself uncovered.

1. **Persona tab wording** (`plugin_voice/ui/settings/persona/index.html`) —
   the "Turn taking" hint now says what the presets actually do on Gemini:
   "How eagerly the voice jumps in when you pause — 'patient' waits longest
   and is hardest to distract (background noise, beeps and typing won't open
   a turn), 'normal' balances the two, 'eager' answers fastest but is easier
   to trigger in a noisy room." Verified rendered on QA Luna (:8765) with the
   persona's saved eagerness ("patient") pre-selected.

2. **`newSessionExpireTime` fix** (`plugin_voice/gemini_live.py`) — found by
   the soak, see below. `NEW_SESSION_WINDOW` went from 2 minutes to
   `TOKEN_TTL` (4 hours), with an explanatory comment and a regression
   assertion in `tests/test_realtime_core.py::test_mint_happy_path`
   (`body["newSessionExpireTime"] == body["expireTime"]`).

No version was published this phase — phases 1–3 ship together as 0.8.0 in
phase 4 (nothing user-visible ships alone; the managed copy on QA Luna was
kept in sync by rsync throughout).

## Verification

All checks ran against the real Gemini API with the production key and the
production-shaped setup (patient preset — the default persona value).

### Noise is ignored; real speech barges in (`scratchpad/probe_noise.py`) — PASS

- **A (noise must not open a turn):** 6 seconds of white noise at
  speech-like volume (amplitude 6000, ~18% full scale) streamed as
  `realtimeInput.audio` in real-time 100 ms chunks into an idle session →
  zero reaction: `interrupted +0`, model audio `+0` bytes, empty transcript.
  START_SENSITIVITY_LOW + proactiveAudio are doing their job — this is the
  wire-level pin on the user's core requirement ("won't become silent on
  every single beep").
- **B (real speech must interrupt):** while the model was mid-turn counting
  to thirty (≥1 s of audio already streamed), ~3 seconds of actual speech
  (Luna's own generated greeting audio, 24 kHz downsampled to 16 kHz) was
  streamed over it → `interrupted +1`. Barge-in works at the wire.

### Long-call soak (`scratchpad/probe_soak.py`) — two runs

One continuous session, a short text turn every ~60 s, per-turn first-audio
latency logged, handle-reconnect on socket drop (exactly what rt-client.js
does).

- **Run 1 — FAIL at 10.3 min, and it was a real production bug.** Google
  force-drops a live session at ~10 minutes (this is normal; long calls are
  session chains stitched together by resumption handles). The reconnect
  with the handle was refused: WS close 1011
  `new_session_expire_time deadline exceeded`. Cause: `newSessionExpireTime`
  gates EVERY connect on the token — including resumption reconnects — and
  we minted it at now+2 minutes. Every production call would have died at
  the first forced drop (~10 min in). Fixed as described above; full suite
  re-run (150 passed, 1 skipped — the skip is `test_live_gemini` without an
  env key), managed copy re-synced.
- **Run 2 — PASS: 16.6 min, 17 turns, reconnects=1, goAways=0.** The one
  drop came at 10.4 min (same forced ~10-min ceiling); the handle-reconnect
  succeeded and the very next turn answered in 0.85 s with correct context
  ("Twelve." — the model remembered the count across the reconnect).
  Latency stayed flat the whole way: first turn 1.01 s, last turn 0.50 s,
  all turns 0.50–1.01 s. contextWindowCompression + sessionResumption keep
  a >16-minute session fully usable.

### Suite

`plugins/plugin-voice/.venv/bin/python -m pytest tests -q` → **150 passed,
1 skipped** after the fix.

## Deviations from PHASE.md

- PHASE.md expected drops to announce themselves via `goAway`; in practice
  Google closed the socket with no goAway frame at the ~10-min mark
  (`ConnectionClosedError`). rt-client.js already treats any close the same
  way (reconnect with handle, up to 2 attempts), so no client change was
  needed — but the token fix was.

## Surprises / learnings

- `newSessionExpireTime` is not "how long the browser has to start the
  call" — it bounds every session start on the token, resumption included.
  It must span the whole call, same as `expireTime`. Now comment- and
  test-pinned.
- The forced ~10-minute session drop arrives as a plain socket close, not a
  goAway. Model context genuinely survives the resumption handle (the count
  continued correctly).

## Reassessment of remaining phases

Phase 4 (tests, QA, ship 0.8.0) stands as planned, with these notes folded in:

- The test work listed in the plan's phase 4 (test_live_gemini.py, setup
  shape, /rt/session contract, settings fallback) already landed during
  phases 1–2; phase 4's test job is just a final full-suite run.
- Manual barge-in / noisy-room QA with a real microphone needs the owner —
  phase 4's summary must flag it as an owner follow-up; the wire-level
  checks above are the automated stand-in.
- Ship checklist unchanged: bump three stamps to 0.8.0 (in-code manifest in
  `plugin_voice/__init__.py`, `plugin_voice/luna-plugin.toml`,
  `pyproject.toml`), commit+push, package + publish to marketplace slug
  `official`, catalog verify, then remind the owner to upgrade hosted
  agents and ensure `LUNA_GEMINI_API_KEY` is set on hosted (hosted installs
  never pip-install plugin deps — the plugin deliberately avoids
  `google.genai` and uses pure httpx, so no dependency change is needed).
