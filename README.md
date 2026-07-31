# plugin-voice

Full-duplex, interruptible voice conversations with your Luna agent — that
know **who is speaking** and sound like **who your agent actually is**.

Since 0.5.0 the audio path is **OpenAI Realtime speech-to-speech** (WebRTC,
browser ⇄ OpenAI directly). The plugin works in three lanes:

- **Talking.** The realtime model speaks *as* your agent: at connect time the
  plugin replicates the agent's live name, greeting, waiting phrases, and
  attitude into the talker's instructions. Not the same model — but the same
  persona, close enough that a call feels like the same character.
- **Knowledge.** Read-only info tools (memory, wiki, files, …) are brokered
  straight to the talker, so "what did we decide about X?" gets answered
  mid-sentence without waking the full agent. A positive read-allowlist plus a
  write-veto keeps this lane strictly look-don't-touch.
- **Doing.** Real work is dispatched to your actual Luna agent in the
  background (`luna_do`). The talker narrates ("working on it — let's keep
  thinking meanwhile…"), checks status, and speaks a short summary when the
  task lands. Full details go to your chat; the agent's raw text is never
  read aloud verbatim.

Plus the **owner voice imprint** (unchanged from 0.4.x): record a few phrases
in Settings; the plugin builds a spectral voice profile (numpy-only,
calibrated + cohort-z-normalized — see `tests/dojo/report.md`: ~6.7% EER on a
10-voice synthetic matrix) and shows "● You" vs "● Unrecognized voice" live
during a call. With the owner lock on, unrecognized speakers can talk but not
use tools. Advisory by design — useful signal, not biometric security.
Imprint scoring runs on your Luna; that audio never goes further.

## Setup

1. Install (needs `plugin-vault`; the voice imprint additionally needs `numpy`
   on the Luna machine — everything else works without it).
2. Settings → Voice: paste an **OpenAI API key** → Connect (or use a granted /
   gateway / `LUNA_OPENAI_API_KEY` env key — the plugin resolves in that
   order). The agent then provisions its own persona: greeting, waiting
   phrases, and the realtime voice that fits its personality.
3. Optionally record the imprint phrases in the same tab.
4. Click the sidebar **Voice** widget and talk. Interrupt freely — it stops.

No tunnel, no public URL: audio is WebRTC from your browser to OpenAI; your
Luna only mints short-lived client secrets and serves the in-call tools.

The Persona tab (Settings → Voice) tunes the talker: realtime voice,
greeting/waiting-phrase overrides, turn eagerness, speaking-style prompt, and
extra instructions.

## History

Versions ≤ 0.4.x rode ElevenLabs Agents with an OpenAI-compatible SSE bridge
into the agent's own loop (the plugin-talk architecture), which required the
Luna to be publicly reachable. That path was removed in 0.5.0 — no bridge, no
tunnel. [plugin-talk](https://github.com/huemorgan/plugin-talk) remains the
ElevenLabs sibling if you prefer that stack.

On hosted tenants the platform's gateway key cannot mint realtime sessions
(billing can't meter WebRTC audio, which flows browser ⇄ OpenAI directly), so
hosted voice needs your own pasted OpenAI key. Since 0.5.2 the Setup page
probes the detected key with a real mint and says exactly why voice can't
start, keeping the paste field available.

## Luna View

Since 0.6.0 the widget's expand button opens **Luna View** — a full pane where
you *see* Luna while talking: ~24k WebGL2 dots that idle as an orb, become an
articulated face in-call, tremble with the audio actually heard and spoken,
and fire reaction shapes (heart, thumbs up, rocket, strong arm, fireworks)
when Luna decides they fit. On cores with hidden-section support (≥ 0.54) the
pane has no sidebar link; older cores show a visible "Luna" link instead.

The head model (`plugin_voice/ui/view/head.bin`) is a point-cloud sample of
the "Face Cap" demo head by [Face Cap](https://www.bannaflak.com/face-cap),
as distributed with the [three.js](https://threejs.org) examples
(`models/gltf/facecap.glb`).

## Dojo

`EL_KEY=sk_... .venv/bin/python tests/dojo/run_dojo.py` — synthesizes a
matrix of test voices (ElevenLabs TTS, dev-only tooling), enrolls each as
owner in turn, sweeps the decision threshold, regenerates
`plugin_voice/dsp_calibration.py` (whitening + cohort + tuned threshold), and
writes the accuracy report. Run it after touching `dsp.py`.

## Tests

```bash
cd plugins/plugin-voice
.venv/bin/python -m pytest -q
```

`tests/test_live_openai.py` needs `LUNA_OPENAI_API_KEY` set — it mints a real
client secret against the Realtime API; skipped otherwise.

Source: https://github.com/huemorgan/plugin-voice — MIT.
