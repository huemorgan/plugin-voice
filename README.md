# plugin-voice

Voice conversations with your Luna agent — that know **who is speaking** and
sound like **who your agent actually is**.

Built on the plugin-talk architecture (ElevenLabs Agents carries audio; your
agent's own loop stays the brain via an OpenAI-compatible SSE bridge), plus:

- **Personality-matched setup.** On connect, the *brain configures itself*: it
  states its name, writes its own greeting ("This is T-800. I am online. State
  your objective."), chooses its waiting fillers ("Running tactical
  assessment... ") used both as ElevenLabs soft-timeout speech and bridge
  keepalives, and picks the ElevenLabs voice that fits its personality — all
  automatic, all falling back to neutral defaults if the brain doesn't answer.
- **Owner voice imprint.** In Settings you read five phrases; the plugin builds
  a spectral voice profile (numpy-only, calibrated + cohort-z-normalized —
  see `tests/dojo/report.md`: ~6.7% EER on a 10-voice ElevenLabs matrix).
- **Live speaker check.** During a call the widget streams mic audio to your
  Luna (never further), scores it against your imprint each second, shows
  "● You" vs "● Unrecognized voice", and annotates the brain's prompt when the
  speaker doesn't sound like you — so the agent can be appropriately careful.
  Advisory by design: this is useful signal, not biometric security.

## Setup

1. Install (needs `plugin-vault`; voice recognition additionally needs `numpy`
   on the Luna machine — everything else works without it).
2. Settings → Voice: paste your ElevenLabs API key → Connect. The agent
   provisions itself, personality and all.
3. Record the five imprint phrases in the same tab.
4. Click the sidebar **Voice** widget and talk.

Self-hosted Lunas must be publicly reachable for ElevenLabs to call the bridge
(e.g. `cloudflared tunnel`); hosted tenants need nothing extra.

## Dojo

`EL_KEY=sk_... .venv/bin/python tests/dojo/run_dojo.py` — synthesizes a matrix
of ElevenLabs voices, enrolls each as owner in turn, sweeps the decision
threshold, regenerates `plugin_voice/dsp_calibration.py` (whitening + cohort +
tuned threshold), and writes the accuracy report.

## Tests

```bash
pip install -e ".[dev]" && pytest          # 54 unit + dojo-style route tests
```

Source: https://github.com/huemorgan/plugin-voice — MIT.
Sibling: [plugin-talk](https://github.com/huemorgan/plugin-talk) — the simple
version without recognition/personality.
