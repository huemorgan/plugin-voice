# 002 — plugin-voice: Multi-Voice Recognition + Personality-Matched Setup

**Produces version:** plugin-voice 0.2.0
**Base:** duplicated from plugin-talk v0.1.7 (001-mvp heritage kept in `plans/001-mvp/`).
plugin-talk remains untouched and continues to exist as the simple sibling.

## Context

plugin-talk proved the voice loop: ElevenLabs carries audio, Luna's own agent loop
stays the brain via an OpenAI-compatible SSE bridge, and calls genuinely converse
(001 execution summary documents the road there). Two things it cannot do:

1. **It doesn't know who is speaking.** Everything on the mic is "the user" — with
   owner-level tool permissions. Background speakers barge in as if they were the
   owner (observed live: room chatter cancelled replies and could have issued
   commands).
2. **It has no personality.** The greeting says a hard-coded name, the waiting
   fillers are generic ("One moment..."), and the TTS voice is whatever the agent
   default happens to be — even when the brain is "Athena" or a Terminator.

ElevenLabs' live loop offers no diarization or speaker ID (research 001 +
open-question follow-up), so speaker recognition must be ours. The plugin already
holds the mic stream in the widget — we can analyze it in parallel to the call.

## Goals

1. **Owner voice imprint (enrollment).** A Settings section has the owner read
   several phrases; the plugin builds a voice profile (lightweight spectral
   features, no ML deps beyond numpy) stored in the vault. Part of plugin setup.
2. **Live speaker check.** During a call, mic audio is scored against the imprint
   in ~1s windows on the Luna machine. The widget shows who's talking (you vs
   unrecognized voice); the bridge tells the brain when the current speaker does
   NOT match the owner, so Luna can respond appropriately and treat requests
   with suspicion.
3. **Personality-matched setup (automatic, at connect).** Ask the brain itself —
   via `run_turn` with a JSON schema — for: its **name**, a **greeting in its own
   voice** ("start with their name"), **5 waiting/filler phrases in its
   personality** (a Terminator gives different fillers than a butler), and a
   **description of the voice that fits it**. Then: greeting → agent
   `first_message`; fillers → ElevenLabs soft-timeout messages AND bridge
   keepalive words; voice → auto-picked from the account's ElevenLabs voices by
   matching labels (gender/age/accent/descriptive) against the brain's
   description, chosen by the brain itself from the candidate list.
4. **Voice dojo.** A repeatable harness (in `dojo/`) that synthesizes many
   different speakers via ElevenLabs TTS, enrolls one as owner, and measures
   recognition: true-accept / false-accept rates, threshold sweep, EER. Tune the
   recognizer until it shines; commit the tuned threshold + a dojo report.
5. All of plugin-talk's behavior (bridge, buffer/keepalive, widget, voice picker,
   localhost guard, secret-based EL auth) carried over and kept green.

## Non-Goals

- Real biometric security. Spectral-statistics speaker verification on clean
  speech is useful signal, not authentication. The bridge annotates; it does not
  block. (A future plan can swap in a proper embedding model server-side.)
- Multi-speaker *transcript labeling* (who said which words) — that needs the
  family-2 pipeline (Pipecat + diarizing STT) per 001 research. This plan only
  answers "is the current speaker the owner?"
- Changing plugin-talk in any way.

## Approach

### Phase 1 — Duplicate + rename (mechanical)
plugin-talk → plugin-voice: package `plugin_voice`, routes `/api/p/plugin-voice`,
vault keys `plugin_voice.*`, widget id `voice`, v0.2.0, new repo
`github.com/huemorgan/plugin-voice`.

### Phase 2 — Personality setup (`personality.py`)
On `/connect` (after key validation, before agent provisioning):
- `ctx.agent.run_turn(PERSONA_PROMPT, output_schema=PERSONA_SCHEMA, tools=[])` →
  `{name, greeting, fillers[5], voice_description}`. Greeting must contain the
  name; fillers must be short (≤6 words) and end naturally for TTS.
- Fetch account voices (`/v1/voices` returns labels: gender, age, accent,
  description). Second `run_turn` (or `run_llm`) picks the best `voice_id` from
  the candidate list given `voice_description`.
- Provision the ElevenLabs agent with: `name = "<Name> (plugin-voice)"`,
  `first_message = greeting`, `tts.voice_id = match`, soft-timeout messages =
  fillers. Store `{persona_name, fillers, voice_id}` in `plugin_voice.settings`.
- The bridge's keepalive words come from stored fillers (fall back to defaults).
- Graceful degradation: if the brain call fails, fall back to plugin-talk's
  neutral defaults — connect must never break because personality fetch broke.

### Phase 3 — Voice imprint (`dsp.py` + routes)
- `dsp.py` (numpy only): 16k s16le PCM → 25ms/10ms frames → hamming → FFT → 24
  log-mel band energies; voiced-frame filter by energy; utterance embedding =
  concat(mean, std) of bands + band-ratio shape vector, L2-normalized.
  Profile = mean of enrollment embeddings. Score = cosine similarity.
- `POST /enroll` (owner-authed): body `{phrase_index, pcm_b64}`; accumulates
  enrollment utterances in the vault; ≥4 phrases → profile computed + stored
  (`plugin_voice.voice_profile`). `DELETE /enroll` resets. `GET /enroll` status.
- `WS /live?token=...`: widget streams 500ms PCM frames during a call
  (ScriptProcessor tap on the same mic stream). Server buffers 1s windows,
  scores vs profile, replies `{speaker: "owner"|"other"|"unknown", score}`.
  Short-lived `live_token` minted by `/session` (widget iframes have no bearer).
- Bridge integration: `state.py` keeps the last speaker verdicts; when the
  latest window within 10s of a bridge turn says "other", the prompt gains:
  `[Voice check: the current speaker does not sound like the owner]`.

### Phase 4 — UI
- Settings: "Voice imprint" card — 5 scripted phrases, record/re-record each
  (ScriptProcessor PCM capture), progress, enrolled state, reset. Shows the
  personality summary (name, chosen voice, fillers) after connect.
- Widget: unchanged look; status line shows 🟢 "You" vs 🟠 "Unrecognized voice"
  from the live check.

### Phase 5 — Voice dojo (`dojo/`)
- `dojo/run_dojo.py` (needs `EL_KEY` env): synthesizes enrollment + test phrases
  for N≥8 ElevenLabs premade voices; enrolls one voice as owner; scores all
  voices' test utterances; sweeps thresholds → EER; writes `dojo/report.md`
  with the matrix and the tuned threshold; asserts owner-vs-others separation.
- Tuning loop: adjust features/threshold until EER on clean TTS ≤ ~10% and the
  default threshold sits at the sweet spot. Committed threshold becomes the
  plugin default (`dsp.DEFAULT_THRESHOLD`).
- Live-loop scenarios (from the talk dojo): clean question + background-chatter
  barge-in, runnable against a provisioned agent.

### Phase 6 — Tests, publish, execution summary
Port plugin-talk's 42 tests (renamed), add: dsp unit tests (embedding shape,
self-similarity > cross-similarity on synthetic signals), enrollment flow,
personality fallback, live-token gating, bridge speaker annotation. Publish to
GitHub + official marketplace; write `plans/002-multi-voice/execution-summary.md`.

## Data / API contract

- Vault: `plugin_voice.elevenlabs_api_key`, `.agent_id`, `.bridge_secret`,
  `.settings` (JSON: voice_id, persona_name, fillers, threshold),
  `.voice_profile` (JSON: embedding floats + enrollment metadata).
- `POST /enroll` `{phrase_index:int, pcm_b64:str}` → `{enrolled:int, ready:bool}`
- `GET /enroll` → `{enrolled:int, ready:bool, phrases:[str]}`
- `DELETE /enroll` → `{enrolled:0}`
- `GET/POST /session` → adds `{live_token}` when a profile exists.
- `WS /live?token=` in: `{pcm_b64}` frames; out: `{speaker, score}` per window.
- Persona schema: `{name:str, greeting:str, fillers:[str], voice_description:str}`

## Risks

| Risk | Mitigation |
|---|---|
| Spectral recognizer too weak on real mics (trained on clean TTS) | Dojo reports honest numbers; verdict is advisory ("other" only annotates); threshold re-tunable in settings JSON; future plan: real embedding model. |
| Personality run_turn returns junk / slow at connect | Strict JSON schema; length caps; 20s budget; full fallback to neutral defaults. |
| ScriptProcessor deprecated | Works in all targets today; AudioWorklet is the noted upgrade. |
| WS auth from tokenless widget iframe | Short-lived single-use live_token from authed /session; expires in 5 min. |
| 1 vCPU: DSP while brain computes | 1s windows at 16k with numpy ≈ trivial; live check pauses while agent speaks (output suppression already handled by EL echo cancel). |

## Acceptance criteria

- [ ] Fresh install: connect with only an API key → agent exists with the brain's
      real name in the greeting, personality fillers, personality-matched voice.
- [ ] Settings shows the imprint recorder; after ~5 phrases enrollment is "ready".
- [ ] During a call, widget shows "You" for the owner and "Unrecognized voice"
      for a different speaker; bridge prompt carries the annotation.
- [ ] Dojo: owner voice accepted, ≥8 other voices rejected at the committed
      threshold; EER ≤ ~10% on the TTS matrix; report committed.
- [ ] All unit tests green; hygiene scripts pass; published to marketplace.

## Verification

```bash
cd plugins/plugin-voice
.venv/bin/python -m pytest tests/ -q
EL_KEY=... .venv/bin/python tests/dojo/run_dojo.py          # accuracy matrix + report
python ../../scripts/check_no_raw_fs.py . && python ../../scripts/check_no_cached_capability.py .
```
Then live: connect on a real Luna, verify the personality agent + a bridge turn,
and write `plans/002-multi-voice/execution-summary.md`.
