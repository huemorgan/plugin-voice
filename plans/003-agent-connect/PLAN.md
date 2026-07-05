# 003 — plugin-voice: Agent-Driven Connection + Gateway Slug Reality

**Produces version:** plugin-voice 0.3.0 (adds agent tools → manifest capability change)

## Context

On the hosted tenant the owner asked their agent (in chat) to wire the voice
plugin to the platform's ElevenLabs key. The agent connected the gateway key and
granted access — and the Voice settings still said "Not connected". Three gaps:

1. **Slug mismatch.** The hosted gateway registers the service as **`11labs`**
   (admin-entered; naming convention `{slug}_api_key`, `LUNA_11LABS_API_KEY` —
   see luna-service `cloud/gateway/registry.py::default_names`). Our 0.2.3
   resolution chain only asks `ctx.vault.connect("elevenlabs", ...)`.
2. **The agent has no handle.** Even with the key wired, nothing lets the brain
   *finish* the setup (persona + ElevenLabs agent provisioning). The owner
   shouldn't have to open Settings at all if they asked in chat.
3. **Confusing UI.** The key-paste input shows while a platform key may already
   exist, and nothing says plainly that "Voice" runs on ElevenLabs/11labs.

## Goals

1. Resolution chain tries **both** service slugs (`elevenlabs`, `11labs`) for
   `vault.connect` and both env-var spellings; source surfaced in status.
2. **Agent tools**: `voice_status` (read) and `voice_connect` (setup) so the
   brain can check and complete the connection from chat — the key value never
   passes through the agent; resolution happens server-side.
3. Settings UI: the key input stays **hidden** while the initial check runs and
   whenever a platform key is detected — paste is the fallback, not the front
   door ("paste my own key instead" reveals it). Branding says ElevenLabs (11labs).
4. The public bridge URL for tool-initiated connects: captured from any authed
   settings/status request (proxy headers) into plugin settings, so a chat-only
   setup still knows the tenant's public base.

## Approach

- **`setup.py`**: extract the connect/resolve flow out of route closures into
  ctx-taking functions (`resolve_key`, `do_connect`, `build_status`) shared by
  HTTP routes and agent tools. `SetupError` carries friendly messages.
- **`__init__.py`**: register `voice_status` (auto_approve/low) and
  `voice_connect` (ask/medium — it creates/updates an ElevenLabs agent).
- **toml**: `[requires] tools = 2` + `[[tools]]` entries (manifest law: tool
  changes are manifest changes) → version 0.3.0.
- **UI**: status check first, three states — detected key → "Use detected key"
  button only; no key → paste input; connected → cards visible (0.2.3 gating).
- Tests for slug fallback, tools, public-base capture; dojo untouched.

## Acceptance criteria

- [ ] With a vault/gateway credential named `11labs_api_key` (or slug `11labs`),
      status reports connected and `voice_connect` completes setup from a tool call.
- [ ] Settings never shows the paste input when a platform key is detected.
- [ ] Owner can type "connect the voice plugin" in chat and the agent does it.
- [ ] 70+ tests green; published to marketplace; execution summary written.
