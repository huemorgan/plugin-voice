# Phase 3 — natural turn-taking polish

## Scope

Most of the planned phase 3 already shipped earlier: the talker
noise/interruption demeanor block landed in phase 1 (NOISE_RULES,
unconditional) and the model picker already exists in Settings and was
verified against real Luna in phase 2. What remains is wording + proof:

## Deliverables

1. **Persona tab wording** — the "Turn taking" hint now describes what the
   presets actually do on Gemini (patient/normal ignore background sound and
   wait longer; eager trades noise-robustness for speed). One paragraph, no
   behavior change.
2. **Noise/interruption wire checks** (scripted, real key):
   - Feed bursts of synthetic noise (white noise at speech-like volume) via
     `realtimeInput.audio` into an idle session → assert NO `interrupted`
     and no model turn opens (START_SENSITIVITY_LOW + proactiveAudio doing
     their job).
   - While the model is mid-turn (audio streaming), feed real speech-shaped
     audio (Luna's own recorded 24 k greeting, downsampled to 16 k — actual
     speech as far as VAD is concerned) → assert `interrupted` arrives
     (barge-in works at the wire level).
3. **Compression soak** — one continuous session ≥16 minutes (past the
   15-minute audio-session ceiling), kept alive with a short text turn every
   ~60 s; contextWindowCompression + sessionResumption must keep it usable
   the whole way (a goAway answered by a successful handle-reconnect counts
   as alive — that's exactly what the client does). Record turn latency at
   the start vs. the end.

## Verification

- Both wire checks pass with the production-shaped setup (patient preset —
  the default persona value).
- Soak reaches ≥16 min with the final text turn still answered; result and
  any goAway/reconnect events recorded in the execution summary.
- Full suite still green; settings persona page renders the new wording on
  QA Luna.
