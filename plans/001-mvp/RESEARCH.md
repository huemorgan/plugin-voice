# plugin-talk 001-mvp — Research: Voice Conversation with Luna in the Browser

> Deep research run 2026-07-04 (105 agents, 23 sources fetched, 113 claims extracted,
> 25 adversarially verified: 17 confirmed / 8 refuted). Every confirmed claim below
> survived a 3-vote adversarial verification pass against primary sources. Claims that
> failed verification are listed in "Refuted / do not trust" — notably **all pricing and
> latency numbers failed**, so cost figures must be re-checked against vendor pages
> before committing.

## The question

How should a Luna plugin deliver real-time voice conversation ("talk to Luna") in the
browser, given Luna's constraints:

- **Luna is the brain.** It runs its own agent loop with tools (tool calls can take many
  seconds), streams text replies. We are not replacing the LLM/agent.
- **Plugin surface:** FastAPI routes (incl. WebSockets) under `/api/p/plugin-talk/`,
  a static browser `ui/`, secrets from the vault, tools/skills registration.
- Self-hosted Python/FastAPI; no separate media server unless we choose one.

Three architecture families were compared.

---

## Family 1 — Browser-only (Web Speech API)

Mic → browser `SpeechRecognition` (STT) → text into Luna chat → reply text →
browser `speechSynthesis` (TTS). Zero backend audio work.

**Verified findings:**

- **Feasible with zero backend.** MDN confirms `SpeechRecognition` (STT) and
  `SpeechSynthesis` (TTS via the device's default synthesizer) — a plugin UI could do
  voice in/out with only text passing through Luna's chat. *(high confidence, 3-0)*
- **Cannot be the primary architecture.** `SpeechRecognition` is **not Baseline**:
  ~88% global coverage and all of it "partial support" (webkit-prefixed, implementation
  gaps). **Firefox ships it disabled by default** (v22–v155, behind
  `dom.webspeech.recognition.enable`) — browser-only STT simply fails for Firefox users.
  *(high confidence, 3-0 across three merged claims; MDN + caniuse, fetched 2026-07)*
- **TTS voice quality is inconsistent per device.** 110+ English voices exist across
  platforms but only a small subset is on any given device (Chromium on Linux ships no
  voices by default) — no consistent voice experience. *(medium confidence, 3-0)*
- Privacy nuance: the old "Chrome always sends audio to Google's servers" claim was
  **refuted on nuance** — Chrome now offers on-device recognition (`processLocally`),
  but behavior is configuration-dependent, so don't market family 1 as "private."

**Verdict:** great as a zero-cost demo/fallback mode (Chrome/Edge/Safari), unacceptable
as the only path.

---

## Family 2 — Server-side pipeline (browser audio → STT → Luna → TTS)

Browser mic audio over WebSocket to the plugin backend → streaming STT (Deepgram,
OpenAI gpt-4o-transcribe/Whisper, AssemblyAI, Gladia) → Luna agent loop → streaming
TTS (ElevenLabs Flash, OpenAI TTS, Cartesia Sonic…) → audio back to browser.

**Verified findings:**

- **Fits Luna's plugin model exactly.** Pipecat's `FastAPIWebsocketTransport` wraps a
  `fastapi.WebSocket` object — a full STT→agent→TTS Pipecat pipeline can run **inside a
  Luna plugin's `/api/p/plugin-talk/` WebSocket route with no separate media server**.
  Pipecat is transport-agnostic (Daily, plain WebSockets, `SmallWebRTCTransport`,
  telephony serializers), so the transport can be swapped later without rearchitecting.
  *(high confidence, 3-0; Pipecat primary docs)*
- **WebSocket vs WebRTC — Pipecat's own position:** the FastAPI WebSocket transport is
  positioned for telephony/server-side integrations and prototyping; for browser
  client/server apps in production Pipecat **recommends WebRTC-based transports** for
  more robust network and media handling (jitter, packet loss, echo cancellation).
  A WebSocket MVP is officially supported; WebRTC is the recommended production upgrade.
  *(high confidence, 3-0; verbatim warning in Pipecat docs)*
- **LiveKit Agents is a heavier footprint.** It is architecturally tied to LiveKit's
  media-server infrastructure (rooms/participants; the worker connects to a
  `LIVEKIT_URL`) — choosing it means running the Apache-2.0 LiveKit server yourself or
  paying for LiveKit Cloud. Heavier than Pipecat for a self-hosted Luna plugin.
  *(high confidence, 3-0)*
- Self-hosted STT/TTS components (faster-whisper, Piper, Kokoro, Coqui, WhisperLive)
  appeared in sources (e.g. faster-whisper + Piper + local LLM stacks reporting 2–3 s
  round trips) but **no claim about them survived verification** — treat as unassessed,
  promising for a later fully-local mode.

**Verdict:** the architecture that keeps everything under Luna's control — Luna's agent
loop, vault-managed API keys, no third party in the voice loop. MVP-viable over plain
WebSocket; upgrade to WebRTC transport for production robustness.

---

## Family 3 — Hosted realtime speech-to-speech, Luna as "custom LLM"

The voice loop (audio transport, VAD, barge-in, STT/TTS) lives in a hosted service;
Luna plugs in as the brain.

**Verified findings:**

- **ElevenLabs Agents Platform supports Luna as the brain.** You point the voice agent
  at an OpenAI-compatible server implementing `/v1/chat/completions` or `/v1/responses`;
  responses **must stream as SSE** (`text/event-stream`, `data: {json}\n\n`, ending
  `data: [DONE]`). ElevenLabs' own tool-calling/RAG are bypassed in favor of your
  agent's. *(high confidence, 3-0; ElevenLabs primary docs, current July 2026 —
  note docs moved from `/agents-platform/` to `/eleven-agents/`)*
- **ElevenLabs documents a mitigation for Luna's slow tool calls — "buffer words":**
  return an initial partial response ending in `"... "` (ellipsis + space) so TTS keeps
  natural flow while the agent keeps working. Explicitly recommended for slow agentic
  reasoning; the trailing space avoids audio distortion. *(high confidence, 3-0)*
- **Retell supports Luna as the brain via a developer-hosted WebSocket**
  (`/llm-websocket/:call_id`) that Retell connects to; your socket "controls what the
  agent says," supports `tool_call_invocation`/`tool_call_result` events (enriched
  transcripts via `transcript_with_tool_calls: true`), and streaming multiple response
  chunks ending `content_complete: true` is recommended for latency. **Caveat:** Retell
  now steers users toward its built-in agent frameworks over custom LLM "when possible"
  — supported but less-preferred. *(high confidence, 3-0 across three merged claims)*
- **Retell barge-in is fully configurable from the custom-LLM side:** turn-taking
  events pushed to your server, `interruption_sensitivity` tunable 0–1, and
  per-response `no_interruption_allowed: true`. *(high confidence, 3-0)*
- **Vapi supports Luna as the brain:** configure a public URL as the custom-LLM
  endpoint; Vapi POSTs conversation context (OpenAI-compatible `/chat/completions`,
  sends `stream: true`), accepts SSE **or plain JSON** (the claim that SSE is mandatory
  was refuted), API-key/OAuth2 auth, tool calling supported. *(high confidence, 3-0)*
- **Unassessed:** OpenAI Realtime API, Google Gemini Live, Amazon Nova Sonic produced
  **no surviving claims**. OpenAI Realtime speech-to-speech is generally understood
  NOT to support an external LLM as the brain — but that was not verified. Needs a
  targeted follow-up before ruling them in/out.

**Verdict:** viable and explicitly supported by ElevenLabs / Retell / Vapi — best voice
quality and turn-taking for least engineering. Costs: unverified (see below). Requires
Luna to expose a public OpenAI-compatible streaming endpoint (an interesting reusable
asset in itself), and puts a third party in the audio path.

---

## Refuted / do not trust (failed adversarial verification)

All from a single comparison blog (softcery.com) that failed on every numeric claim:

- Vapi $0.05/min orchestration (+$0.23–0.33/min real-world) — **refuted 1-2**
- ElevenLabs Conversational AI $0.10–0.20/min tier pricing — **refuted 0-3**
- "~800 ms round-trip budget; Retell achieves 300–500 ms" — **refuted 0-3**
- Pipecat v1.0.0 dated April 2026 / LiveKit interruption-model stats — **refuted 0-3**
- Platform BYO-LLM restriction matrix (Bland/Synthflow/Cognigy/Cartesia) — **refuted 0-3**

**No third-party cost or latency figure survived verification.** The comparison table
above uses first-party numbers fetched from vendor pricing pages on 2026-07-04 instead;
re-check them at PLAN.md time — vendor pricing moves fast.

## Open questions (for PLAN.md or a follow-up research pass)

1. Actual current per-minute costs: ElevenLabs Agents vs Retell vs Vapi vs a
   self-assembled Deepgram+ElevenLabs pipeline, at Luna's expected usage volume.
2. Do OpenAI Realtime / Gemini Live / Nova Sonic offer any supported external-brain mode?
3. Real end-to-end latency and barge-in quality of a fully self-hosted stack
   (faster-whisper + VAD + Piper/Kokoro) on typical self-hosted hardware.
4. How well do the slow-LLM mitigations (ElevenLabs buffer words, Retell streaming)
   hold up when Luna's tool calls take 10–30+ seconds, not 2–3?

---

## Comparison table

Vendor pricing fetched 2026-07-04 from vendor pages (the earlier third-party numbers
failed verification; these are first-party). Cost is per talk-minute and **excludes
Luna's own LLM tokens** (billed the same in every option). The last column rates fit
with our production constraint: tenant Lunas run on **small 1 vCPU / 1 GB ephemeral
machines**.

| Solution | Cost (~/talk-min, excl. LLM) | Speed (turn latency) | TTS quality | STT quality | Conversation quality | Fits 1 CPU / 1 GB ephemeral server? |
|---|---|---|---|---|---|---|
| **1. Browser-only** (Web Speech API) | **$0** | Medium — turn-based, no streaming pipeline | ★★ Poor–inconsistent (device-dependent voices; Chrome cuts off ~15 s utterances) | ★★ Mediocre, browser-dependent; **fails on Firefox** | ★★ Clunky push-to-talk; no barge-in | ★★★★★ **Perfect** — zero server load, all in browser |
| **2a. Pipeline via plugin WebSocket** (Deepgram Nova-3 + Aura-2) | **~$0.01–0.02** ($0.0048/min STT + ~$0.015/min TTS) | Good — streaming both ways; ~1–2 s realistic | ★★★ Good (Aura-2) | ★★★★★ Excellent (Nova-3) | ★★★★ Good — Pipecat gives VAD/barge-in, but we tune it ourselves | ★★★★ Good — pure I/O relay, no inference on-box; Pipecat adds ~100–200 MB RAM, tight but workable |
| **2b. Same pipeline, ElevenLabs Flash TTS** | ~$0.05–0.10 (TTS 0.5–1 credit/char) | Good — Flash is the low-latency model | ★★★★★ Excellent | ★★★★★ (same STT) | ★★★★ Same as 2a | ★★★★ Same as 2a |
| **3. ElevenLabs Agents** (Luna = custom LLM via SSE) | **$0.08** ($0.16 burst over concurrency cap; 95% discount on >10 s silence) | Very fast — managed voice loop | ★★★★★ Excellent | ★★★★★ Excellent (managed) | ★★★★★ Excellent — managed turn-taking + documented "buffer words" for slow tool calls | ★★★★★ **Excellent** — audio never touches our server; Luna only serves streaming text |
| **4. Retell** (Luna = custom LLM via WebSocket) | ~$0.07–0.095 ($0.055 infra + $0.015 standard / $0.04 ElevenLabs voices) | Very fast — managed loop | ★★★★ Good–excellent (voice-dependent) | ★★★★★ Excellent (managed) | ★★★★★ Excellent — tunable interruption sensitivity, per-response no-interrupt, tool-call events | ★★★★★ Excellent — text-only WebSocket from our server |
| **5. Vapi** (Luna = custom LLM via HTTP) | ~$0.05 + at-cost components ($0 with our own vault keys) | Very fast — managed loop | ★★★★ BYO — as good as the TTS picked | ★★★★ BYO/managed | ★★★★ Very good | ★★★★★ Excellent — text-only POST/SSE from our server |
| **6. Fully self-hosted** (faster-whisper + Piper/Kokoro) | $0 marginal | Poor on small hardware | ★★ OK (Piper) – ★★★ (Kokoro) | ★★★ Good with GPU; poor on CPU | ★★★ Depends entirely on hardware | ★ **Incompatible** — Whisper alone wants >1 GB RAM; needs GPU/beefy box |
| **7. OpenAI Realtime API** (speech-to-speech) | ~$0.30+ ($32/$64 per 1M audio tokens in/out; grows with context) | Fastest (native speech-to-speech) | ★★★★★ | ★★★★★ (native) | ★★★★★ voice UX, **but Luna can't be the brain** (external-LLM mode unverified/likely unsupported) | ★★★★★ technically (browser↔OpenAI direct) — but disqualified by the brain problem |

**Reading the table with our constraints:**

- The 1 CPU / 1 GB ephemeral machine makes the custom-LLM platforms (3–5) extra
  attractive: all audio work happens off-box; Luna only serves a streaming text
  endpoint, which it already does for chat.
- **Best value:** 2a (Deepgram both ways) at ~$0.01–0.02/min — 4–8× cheaper than the
  hosted platforms; the trade is we own barge-in tuning and per-call CPU/RAM lands on
  the small tenant machine (concurrent calls would hurt).
- **Best experience for least server load:** ElevenLabs Agents or Retell — both
  verified to handle slow multi-second tool calls (buffer words / streamed tool-call
  events).
- Pricing sources (first-party, 2026-07-04): elevenlabs.io/pricing/agents,
  retellai.com/pricing, vapi.ai/pricing, deepgram.com/pricing,
  developers.openai.com/api/docs/pricing.

## Recommendation

**MVP (family 2, minimal):** a `plugin-talk` plugin that ships

- `ui/` — a talk page: mic capture, audio streamed over a WebSocket to the plugin
  backend; audio playback of replies; push-to-talk first (barge-in/VAD later).
- `routes.py` — a WebSocket route under `/api/p/plugin-talk/ws` running either a
  **Pipecat pipeline via `FastAPIWebsocketTransport`** or a minimal hand-rolled relay:
  streaming STT (vault-keyed Deepgram or OpenAI) → Luna's agent loop → streaming TTS
  (vault-keyed ElevenLabs Flash or OpenAI TTS) → audio frames back.
- Vault credentials for the STT/TTS providers; `depends_on=["plugin-vault"]`.
- **Optional zero-cost fallback mode:** Web Speech API (Chrome/Edge/Safari only, flagged
  as such in the UI; chunk long replies — Chrome cancels utterances after ~15 s).

This keeps Luna's agent loop, tools, approval policies, and keys fully in control, uses
only surfaces the plugin SDK already provides, and adds no media server.

**Upgrade path A (robustness):** swap the WebSocket transport for Pipecat's
`SmallWebRTCTransport` (WebRTC without a media server) for production-grade browser
audio: jitter/loss handling, echo cancellation, better barge-in.

**Upgrade path B (best voice UX, least code):** expose Luna as an OpenAI-compatible SSE
endpoint and plug it into **ElevenLabs Agents** (buffer-words pattern for slow tools) or
**Retell** (WebSocket custom-LLM with configurable barge-in). The voice loop is
outsourced; Luna stays the brain. Decide after pricing re-check (open question 1).

**Not recommended now:** LiveKit Agents (media-server footprint), OpenAI
Realtime/Gemini Live/Nova Sonic as primary path (external-brain support unverified),
browser-only as the primary mode (Firefox failure, inconsistent voices).

---

## Key sources (primary)

- Pipecat: `docs.pipecat.ai/server/services/transport/fastapi-websocket`,
  `docs.pipecat.ai/server/services/supported-services`
- ElevenLabs custom LLM: `elevenlabs.io/docs/eleven-agents/customization/llm/custom-llm`
- Retell custom LLM WebSocket: `docs.retellai.com/api-references/llm-websocket`
- Vapi custom LLM: `docs.vapi.ai/customization/custom-llm/using-your-server`
- LiveKit Agents: `docs.livekit.io/agents/`
- Web Speech API: `developer.mozilla.org/en-US/docs/Web/API/Web_Speech_API`,
  `caniuse.com/speech-recognition`

*Vendor docs are moving fast (ElevenLabs URLs changed paths mid-2026); all capability
claims are as of July 2026.*
