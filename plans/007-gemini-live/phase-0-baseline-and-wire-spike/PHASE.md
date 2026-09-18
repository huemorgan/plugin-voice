# Phase 0 — baseline + wire-format spike

## Scope
No plugin code changes. Establish the pre-change test baseline and pin the
exact Gemini Live wire format the browser client will speak, using throwaway
scripts in the session scratchpad (not committed).

## Deliverables
1. Pytest baseline for plugin-voice recorded (count, failures if any).
2. Ephemeral token minted via **plain httpx REST** (no google-genai import) —
   endpoint, body shape, response shape documented.
3. Raw-WS browser-shaped session against
   `.../v1alpha.GenerativeService.BidiGenerateContent` authenticated with that
   ephemeral token: confirmed auth mechanism (query param vs header), camelCase
   `setup` accepted (VAD low sensitivity + proactivity + compression +
   transcription + tools), PCM audio out received, `toolCall`/`toolResponse`
   round trip, greeting nudge via `clientContent`.
4. Voice catalog decision: probe which prebuilt voices `gemini-3.8-live`
   accepts; pick 8–10 with descriptions for personality.pick_voice.

## Verification criteria
- Baseline: full plugin-voice suite run, output recorded in the summary.
- Spike script transcript shows: token minted (masked), WS connected with
  token, `setupComplete` received, audio bytes > 0 for a text turn, a tool
  call issued by the model and answered, no key material printed.

## Notes carried in from research
- SDK path (v1alpha + proactivity + compression) already verified working.
- `auth_tokens.create` verified via SDK; REST equivalent must be pinned here
  because hosted plugins can't import google-genai.
