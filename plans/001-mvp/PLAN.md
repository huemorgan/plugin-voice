# 001 — plugin-talk MVP: Talk to Luna (ElevenLabs Agents path)

**Produces version:** plugin-talk 0.1.0
**Research:** [RESEARCH.md](RESEARCH.md) — path B chosen (ElevenLabs Agents, Luna as custom LLM)

## Context

We want to talk to Luna by voice in the browser. Research (001-mvp/RESEARCH.md) compared
browser-only Web Speech, a self-run STT→Luna→TTS pipeline, and hosted voice platforms.
Decision: **ElevenLabs Agents with Luna as the brain** — best conversation quality
(managed turn-taking, barge-in, documented "buffer words" pattern for slow tool calls)
and the best fit for our 1 vCPU / 1 GB ephemeral tenant machines, because audio never
touches the Luna server: ElevenLabs handles mic/WebRTC/STT/TTS, and Luna only serves
streaming text from an OpenAI-compatible endpoint it hosts as a plugin route.

Key SDK fact discovered during research: plugins drive the agent via
`ctx.agent.run_turn(prompt, tools=[...], conversation_id=...)` (see
`luna/luna/plugins/agent_facade.py`, used in production by plugin-whatsapp). It is
**headless and non-streaming** — it returns the finished reply. Two consequences:

1. The bridge cannot stream tokens as Luna generates them; it streams the finished
   reply as SSE chunks. First-audio latency ≈ one full agent turn → the ElevenLabs
   **buffer words** pattern (send `"... "`-terminated filler immediately) is mandatory.
2. `run_turn` does **not** enforce tool approval policy (known caveat from
   plugin-whatsapp) — the voice bridge must pass a curated tool allowlist.

## Goals

1. A "Talk" page served by the plugin: click, grant mic, converse with Luna by voice
   with natural interruption/turn-taking.
2. Luna stays the brain: replies come from Luna's agent loop with (curated) tools and
   memory; a voice session binds to a Luna conversation so context persists.
3. Plugin-only implementation, authored against `luna_sdk` (no `import luna.*`).
4. Secrets (ElevenLabs API key, agent id, bridge shared secret) in the vault;
   `depends_on=["plugin-vault"]`.
5. Near-zero load on the tenant machine (text-only bridge).
6. A **left-pane sidebar widget** (like plugin-brain's) hosting the talk session: a
   vibrating voice visualization for **both** Luna's speech and the user's mic input —
   a permanent, visible place where the conversation "lives".
7. A **Talk settings area** where the user picks Luna's voice (and future options:
   language, first message, interruption sensitivity).

## Non-Goals

- Pipeline path (Pipecat/WebRTC on our server) — that's the fallback plan, not MVP.
- Browser-only Web Speech fallback mode (possible 002).
- Telephony, multi-user rooms, wake words, voice cloning management UI.
- Multi-tenant fleet provisioning of ElevenLabs agents (one Luna = one agent for MVP;
  fleet automation is a luna-service concern, out of scope here).
- Streaming tokens out of `run_turn` (requires a luna-core change; recorded as a
  future change request, not attempted in this plan).

## Approach

### Phase 1 — ElevenLabs agent setup (manual, documented in `docs/SETUP.md`)

1. Create an ElevenLabs Agent (dashboard). Voice + first message + language.
2. Set **Custom LLM**: URL `https://<luna-host>/api/p/plugin-talk/v1/chat/completions`,
   with an extra header `Authorization: Bearer <bridge-secret>`. Disable ElevenLabs'
   own tools/RAG (Luna's agent loop replaces them).
3. Keep the agent **private**; browser sessions authenticate via conversation tokens
   minted by our backend (Phase 3).
4. Record in vault: `plugin_talk.elevenlabs_api_key`, `plugin_talk.agent_id`,
   `plugin_talk.bridge_secret`.

### Phase 2 — Plugin skeleton

```
plugin_talk/
  __init__.py          # LunaPlugin subclass; manifest name=plugin-talk 0.1.0
  luna-plugin.toml     # mirror manifest; routes_module="routes"; depends_on plugin-vault
  routes.py            # FastAPI router (bridge + session + voices + settings + static ui)
  bridge.py            # OpenAI-compat ⇄ run_turn adapter (pure logic, unit-testable)
  elevenlabs.py        # all ElevenLabs REST calls (token mint, voices list) in one module
  ui/
    widgets/talk/      # sidebar widget: talk session + voice visualization
    settings/          # settings-tab iframe: voice picker + status
```

Manifest declares the sidebar widget the same way plugin-brain does:

```python
widgets=[WidgetSlot(id="talk", slot="sidebar.bottom", label="Talk to Luna", height=180)]
```

**SDK gap found:** `PluginManifest.widgets` exists (`luna/plugins/base.py:200`) and the
shell renders it, but `WidgetSlot` is **not re-exported from `luna_sdk`** (only
`SettingsTab` is). File a one-line change request to luna to export it; until it lands,
pass a plain dict (`widgets=[{"id": "talk", ...}]` — pydantic validates it).

### Phase 3 — The two backend routes (`routes.py`)

**A. `POST /api/p/plugin-talk/v1/chat/completions`** — the custom-LLM bridge.
- Auth: constant-time compare of `Authorization: Bearer` against vault
  `plugin_talk.bridge_secret`. 401 otherwise. This route is unauthenticated-by-Luna
  (ElevenLabs calls it), so the secret is the only gate — never log bodies.
- Parse OpenAI Chat Completions request (`messages`, `stream: true`).
- Immediately open the SSE response and send a buffer-words chunk (e.g. `"Hmm... "`)
  so TTS starts while Luna thinks.
- Build the prompt: system voice-style preamble (short spoken sentences, no markdown,
  no lists, no URLs read aloud) + the latest user message. ElevenLabs maintains the
  transcript and sends full `messages` history each call; we forward a trimmed window
  (last N turns) inside the prompt for continuity beyond the bound conversation.
- Call `ctx.agent.run_turn(prompt, tools=ALLOWLIST, conversation_id=<voice-conv>)`.
  - `ALLOWLIST`: read-mostly tools; exclude `send_chat_message` (double-post),
    everything `risk_level="high"`, and `prompt_always` tools — `run_turn` bypasses
    approval UX (same policy stance as plugin-whatsapp's exclude list).
  - Bind all requests of one voice session to one Luna conversation (created lazily,
    titled "Voice call <date>") so the chat UI shows the transcript afterwards.
- Stream the reply text as OpenAI `chat.completion.chunk` SSE events (sentence-sized
  chunks are fine), then `data: [DONE]`.

**B. `POST /api/p/plugin-talk/session`** — start a voice session (Luna-authenticated
via SDK `get_current_user`).
- Calls ElevenLabs `POST /v1/convai/conversation/token` (or current endpoint) with the
  vault API key + agent id; returns the short-lived conversation token to the browser.
- The browser never sees the ElevenLabs API key.

### Phase 4 — Sidebar talk widget (`ui/widgets/talk/`)

The talk session lives **in the left-pane widget** (always visible, like plugin-brain):

- Served from `/api/p/plugin-talk/ui/widgets/talk/` (same pattern as plugin-brain's
  widget route: path-traversal-guarded static serving with no-cache headers).
- Talk/hang-up button + status (idle / listening / Luna thinking / Luna speaking).
- **Vibrating voice visualization for both sides**: Web Audio `AnalyserNode` taps —
  one on the user's mic stream (`getUserMedia` track the ElevenLabs SDK uses), one on
  Luna's playback audio. Render as a live waveform/orb on `<canvas>`: user input pulses
  in one color, Luna's voice in another, so you *see* the conversation happening.
  (The `@elevenlabs/client` SDK exposes input/output volume/frequency hooks; use them
  if sufficient, else raw AnalyserNode.)
- Uses the ElevenLabs browser SDK (`@elevenlabs/client`, vendored into the widget as a
  static bundle) → `startSession({conversationToken})` from route B; audio flows
  browser ⇄ ElevenLabs over WebRTC/WSS. No audio touches Luna's server.
- Buffer-words filler and "thinking" state map to a distinct calm-pulse animation so
  slow tool calls read as "Luna is working", not "it's broken".

### Phase 4b — Talk settings (voice picker)

- **Settings tab** (`SettingsTab(id="talk", label="Talk", icon="mic")`) with iframe
  from `ui/settings/`: setup status (vault keys present? agent reachable?), **voice
  selector** with preview playback, and placeholders for future options (language,
  first message, interruption sensitivity).
- Backend:
  - `GET /api/p/plugin-talk/voices` — proxies ElevenLabs' voices list using the vault
    API key (Luna-authenticated route; browser never sees the key).
  - `GET/POST /api/p/plugin-talk/settings` — persists `{voice_id, ...}` via the
    plugin config surface (`ctx.register_config_section` / plugin storage — decide at
    implementation; NOT the vault, these aren't secrets).
- Applying the voice: pass the chosen `voice_id` as a conversation override when the
  widget starts a session (ElevenLabs `startSession` supports config overrides —
  verify exact field, likely `overrides.tts.voice_id`); fall back to updating the
  agent via REST `PATCH` if per-session override is unavailable.

### Phase 5 — Tests & verification

- Unit (`tests/`): manifest/toml sync; bridge auth (401 wrong secret, timing-safe);
  OpenAI request→prompt mapping; SSE chunk framing (`data:` lines, `[DONE]`,
  buffer-words first chunk); tool allowlist excludes high-risk/prompt_always.
- Contract test: golden SSE transcript for a canned `run_turn` stub.
- Live E2E (manual, per devprocess): scenario files under `plans/001-mvp/e2e/` —
  full voice round-trip, barge-in mid-reply, a tool-using query ("what plugins are
  installed?"), transcript visible in Luna chat, session survives 15+ s tool call.

## Data / API contract

- Bridge request/response: OpenAI Chat Completions (SSE), per ElevenLabs custom-LLM
  spec (`text/event-stream`, `data: {chunk-json}\n\n`, terminator `data: [DONE]`).
- `POST /session` → `{"token": "<conversation-token>", "agent_id": "...",
  "voice_id": "<selected or null>"}`.
- `GET /voices` → `{"voices": [{"voice_id", "name", "preview_url"}]}` (proxied).
- `GET/POST /settings` → `{"voice_id": "...", ...}` (non-secret plugin config).
- Vault keys: `plugin_talk.elevenlabs_api_key`, `plugin_talk.agent_id`,
  `plugin_talk.bridge_secret`.
- Widget slot: `{"id": "talk", "slot": "sidebar.bottom", "label": "Talk to Luna",
  "height": 180}` served at `/api/p/plugin-talk/ui/widgets/talk/`.
- Tool policy of plugin's own surface: none registered in MVP (no new agent tools).

## Risks

| Risk | Mitigation |
|---|---|
| First-audio latency = full agent turn (run_turn non-streaming) | Buffer-words filler immediately; voice system prompt demands short replies; trim history window. Future: streaming turn API in luna core (change request). |
| Long tool calls (10–30 s) may hit ElevenLabs custom-LLM timeouts | Keep SSE open + periodic buffer chunks; verify in E2E (research open question 4); if hard-capped, reply "still working on it" and deliver via chat. |
| `run_turn` bypasses approval policy | Curated allowlist: no high-risk, no prompt_always, no send_chat_message. |
| Bridge route is publicly reachable | Bearer secret, constant-time compare, no body logging, 429 rate-limit per IP. |
| ElevenLabs pricing/docs drift ($0.08/min, endpoints moved mid-2026) | Re-verify endpoint + price at implementation start; isolate EL API calls in one module. |
| Per-tenant agent provisioning doesn't scale by hand | Accepted for MVP (one agent per Luna); fleet automation deferred to luna-service. |

## Acceptance criteria

- [ ] From a fresh Luna with plugin-talk installed + SETUP.md followed: the Talk widget
      appears in the left sidebar; click talk, speak, hear Luna answer in <5 s for a
      no-tool question.
- [ ] The widget's voice visualization vibrates with the user's mic input and with
      Luna's speech, in visibly distinct styles, and shows a "thinking" state during
      tool calls.
- [ ] Voice selected in the Talk settings tab is audibly applied to the next session
      and survives reload.
- [ ] Barge-in works (interrupting stops Luna's audio and she listens).
- [ ] A tool-using voice query returns a correct spoken answer.
- [ ] Voice transcript appears in a Luna conversation.
- [ ] Wrong/missing bridge secret → 401; no secrets in logs.
- [ ] All unit tests pass; manifest checklist (PLUGIN-ARCHITECTURE.md §10) satisfied.
- [ ] `luna-plugin.toml` and `PluginManifest` agree (name plugin-talk, 0.1.0).

## Verification

```bash
cd plugins/plugin-talk
python -m pytest tests/ -q                       # unit + contract
python ../../scripts/check_no_raw_fs.py .        # repo checks
python ../../scripts/check_no_cached_capability.py .
# package + local publish smoke:
python ../../scripts/package_plugin.py .
```
Then the live E2E scenarios in `plans/001-mvp/e2e/` (real browser, real ElevenLabs
agent), per the devprocess skill — and write `plans/001-mvp/execution-summary.md`
when done (what was accomplished / discovered / future considerations).
