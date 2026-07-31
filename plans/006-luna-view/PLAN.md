# 006 — Luna View: a visual presence for the voice

## Goal

From the sidebar Voice widget, an expand button opens a full **Luna View** pane where you *see* Luna while talking to her: a cloud of ~24k glowing WebGL2 dots that idles as an orb, becomes a real articulated face when the conversation starts, trembles with the audio actually heard and spoken, and fires reaction shapes (heart, thumbs up, rocket, strong arm, fireworks) when Luna decides they fit.

The pane opens **like a left-pane page but with no left-pane link** — it exists only through the widget's expand button (deep-linkable, not listed in nav).

## Status

- Mock (`mock/index.html` + `mock/head.bin` + `assets/extract_head.mjs`) is done and validated: 60fps at 24k points, real scanned head with baked `jawOpen` articulation, all shapes morphing seamlessly. The mock still has manual shape chips and rain-style confetti — both are removed/replaced below.
- **Phases 1, 3, 4 executed** (commits 981ee56, 2f0fb4c, cf6b159 — v0.6.0 on branch `006-luna-view`). E2E scenario 01 passed; 03 passed except the human-mic duplex call (live walkthrough pending); 02 blocked on Phase 2.
- **Phase 2 (luna core: `hidden` + `luna-open-section`) waits on a user-assigned luna plan number.** Until it lands, the pane is reachable via a visible "Luna" sidebar link — fully functional thanks to the plugin's `/ui/` catch-all route.
- Local test loop: `~/.luna/managed_plugins/plugin_voice/` runs 0.6.0 on the :3000 Luna (0.5.2 backup at `/tmp/plugin_voice.bak-052`).

## Product decisions (this revision)

1. **No shape picker.** The shape chips in the mock are a dev harness — the pane ships zero shape buttons. The avatar drives itself: orb when idle, face when in a call, reaction shapes only when Luna (or a call event) triggers them, each auto-reverting.
2. **Functional controls only**, bottom-center, in this order:
   - **Talk / End** — start or end the call from the pane (same session as the widget, never a second one).
   - **Mute mic** — toggles the local mic track (`track.enabled = false`); icon swaps to muted state; the feed marks `muted` so both widget and pane render it.
   - **Set up microphone** — shown *only* when mic permission is not granted (`navigator.permissions.query({name:"microphone"})` ≠ `"granted"`, with a safe fallback when the API is missing). One click runs a one-shot `getUserMedia` to raise the browser prompt, then the button disappears.
3. **Fireworks, not confetti.** The rain mode is cut. Celebration = fireworks: 3–5 sequential bursts — a small fraction of the cloud (~3k points, not all 24k) launches as rockets and explodes into expanding spherical shells with rainbow color and fast fade, whole show ≈ 2.5s, then auto-revert to the previous shape. Snappy, not the slow drift the confetti rain had.
4. **Transient state labels.** Because the call is full duplex, states overlap and flip fast — so the state is written as a short label ("Listening", "Speaking", "Thinking", "Idle") that fades in on every change (~150ms), holds ~1s, and fades out. The dot colors remain the persistent state signal (teal = you, violet = Luna); the text is a fading annotation, never a fixture.
5. **Open without a nav link.** Preferred path (we own luna core): hidden sidebar sections. Fallback if that's rejected: a visible "Luna" nav link and the expand button navigates to it.

## Architecture

### Who owns the audio

The WebRTC session (mic + Luna's remote stream) lives in the **widget iframe** and must not restart when the pane opens. `MediaStream`s can't cross iframes, so the pane never touches audio directly:

- Widget broadcasts a viz feed on `BroadcastChannel("plugin-voice-viz")` at ~30Hz: `{ state, userLevel, lunaLevel, muted, owner }` — trivially cheap. `state ∈ idle | connecting | listening | thinking | speaking` (connecting drives the swarm choreography).
- The pane is a pure renderer of that feed and sends commands back on the same channel: `{cmd:"start"}`, `{cmd:"end"}`, `{cmd:"mute", on}`.
- If no session owner responds to a `{cmd:"ping"}` within ~300ms, the pane starts the session itself (it ships the same `rt-client.js`) and becomes the feed owner. Single-owner rule: widget wins if both are alive (same pattern as the existing `enroll-start` pause message).

### How the pane opens (luna core, small)

1. `SidebarSection` (in `luna/plugins/base.py`) gains `hidden: bool = False`. Hidden sections stay in the shell's section registry (so `plugin:<id>` renders) but are filtered out of the desktop nav and mobile bottom-nav lists. `plugin_webui` passes the flag through.
2. `Shell.tsx` gets one more global message case next to the existing `luna-plugin-changed`: `{type:"luna-open-section", section:"luna-view"}` from any plugin iframe → `setSection("plugin:luna-view")`. Generic — any plugin widget can then open its own pane.
3. The voice widget gets the expand affordance: a scale icon (⤢, YouTube miniplayer pattern) fading in on hover in its top-right corner, posting `luna-open-section` to the parent.

Core changes ship with a normal luna release (image bump); the plugin degrades gracefully on older cores — the expand button posts a message nobody handles, and the section, being hidden, simply isn't reachable. No crash either way.

### The pane (`ui/view/index.html`)

Built from the validated mock renderer (single file + `head.bin` fetched from the plugin's own static route; zero dependencies, no build step). Removed relative to the mock: shape/state/audio chip rows, FPS counter (kept behind `?debug=1`). Added: controls above, transient state label, feed/command channel, auto choreography.

### Shape choreography (automatic)

| trigger | behavior |
|---|---|
| no session | orb, dim violet/blue, slow drift |
| call connecting | swarm (torus-knot ribbon) — "the flock assembles" |
| call live | face; `jawOpen` scaled by Luna's speaking level; teal tint while you speak, violet while she does |
| thinking (task lane busy) | face holds, slow pulse |
| call ends | disperse → orb |
| reaction event | gesture shape ~3s (fireworks ≈ 2.5s show), then revert to face/orb |

Reactions arrive as feed events: `{event:"react", shape:"heart"|"thumbs"|"rocket"|"arm"|"fireworks"}`. In this plan the emitter is the session's existing data-channel event lane in `rt-client.js` (a `luna_view_react` in-call tool the talker can invoke when relevant — heart for warmth, fireworks for a win, etc.). The pane just renders whatever reaction events appear on the channel.

## Phases

### Phase 1 — Mock revision (validate before product code) — DONE

In `mock/index.html`: remove shape chips, add auto choreography + transient state labels, replace confetti rain with the fireworks burst system (~3k points, shells + fade, 2.5s). Keep the dev state/audio chips behind `?debug=1`.

**Exit criteria**: fireworks read as fireworks and finish snappy; labels fade correctly during rapid full-duplex state flips; still 60fps. ✓ verified in browser (commit 981ee56).

> retro(phase 1): learnings folded into phases 3–4 —
> - Fireworks is a **shader overlay**, not a shape morph: `uFw` (ramp) + `uFwT` (burst clock), sparks = `fract(seed*7.31) < 0.125`, 3 bursts staggered 0.34s, rise 0.40s, expansion `1-exp(-2.8t)`, droop `0.16t²`, fade `smoothstep(0.9,1.5,et)` with twinkle. First cut used 4 bursts + 0.40 droop and read as falling dust — keep the tuned constants.
> - The feed contract needs a **"connecting"** state (drives the swarm choreography between Talk and live) — it's a real session phase, not just a mock artifact.
> - `person` and `cloud` targets are dropped from the product pane (exploration shapes; the pane ships orb/swarm/face + 4 gestures + fireworks overlay).
> - `setShape` early-returns on same-name calls — reaction revert relies on it; keep in the pane port.

### Phase 2 — luna core (separate repo, small PR)

- `SidebarSection.hidden` flag + `plugin_webui` pass-through + Shell nav filtering (desktop and mobile trees).
- Generic `luna-open-section` message handler in `Shell.tsx`.
- Unit test: hidden section renders via `setSection`, absent from both nav lists.

> retro(phase 3): scope check against the LIVE core (my-projects/luna, v0.53) — it already has
> `SidebarSection.path` and the webui passes it through (newer than the luna-service submodule
> snapshot). Phase 2 therefore shrinks to exactly: `hidden` field + pass-through + nav filtering
> (sections must STAY in the pluginSections registry so `setSection('plugin:luna-view')` still
> resolves — filter only the nav lists), + the `luna-open-section` window-message handler.
> Blocked on the user assigning a luna plan number (luna-submodule-changes skill).

### Phase 3 — plugin-voice 0.6.0

- `ui/view/index.html` pane from the revised mock + `head.bin` + shared `rt-client.js`.
- `rt-client.js`: add `handle.setMuted(on)` (toggle mic track enabled) and the `luna_view_react` event pass-through.
- Widget: viz feed broadcast, expand button, mute state rendering.
- Manifest: `sidebar_sections=[{id:"luna-view", label:"Luna", icon:"sparkles", path:"ui/view/", hidden:true}]`.
- Version bump **0.5.2 → 0.6.0** in BOTH `luna-plugin.toml` and `PluginManifest`; `[requires]`/tools unchanged unless `luna_view_react` lands as a registered tool.
- Unit loop per repo rules (uv + 3.12, manifest-sync tests, no `luna.*` imports); then live browser test: widget → expand → pane, talk, mute, reaction, close.

> retro(phase 3): DONE (commit 2f0fb4c, v0.6.0, 142 tests green) — learnings for phases 2/4:
> - Today's shells hardcode pane iframes to `/api/p/<plugin>/ui/`, so routes.py grew a `/ui/`
>   **catch-all serving the view** (registered last; widget/settings routes still win). This makes
>   the visible-"Luna"-link fallback fully functional on current cores — Phase 2 is now purely
>   cosmetic/UX (hide the link, wire the expand button), not a functionality gate.
> - `luna_view_react` ships as a mint-time schema only (`tool_names` includes it; `/rt/tool`
>   answers a quiet `{ok:true}` for old cached clients). `[requires] tools` stays 2.
> - E2E verified in a real local Luna (port 3000): pane at 60 FPS/24k points, heart morph +
>   fireworks overlay, error surfacing (mic blocked, setup needed) all good. **A real duplex call
>   cannot be automated**: CDP `grantPermissions` satisfies `permissions.query` but not actual
>   capture in headed Chrome — the talk/mute/handoff loop is live-walkthrough territory.
> - Local test loop: rsync the package over `~/.luna/managed_plugins/plugin_voice/` (0.5.2 backup
>   at `/tmp/plugin_voice.bak-052`) and restart `my-projects/luna` serve on :3000.

### Phase 4 — Polish

- Connect/disconnect choreography (assemble/disperse), error = brief red shiver.
- Reduced-motion fallback (static dim orb) and WebGL-unavailable fallback (keep the widget's wave canvas as the pane body).
- Auto point-count/DPR drop if frame time exceeds budget 60 frames straight.

## Performance budget

- 60fps at 24k points; JS per frame = uniforms only (≤ 0.5ms); zero cost when hidden (`visibilitychange`-gated rAF).
- Pane ≤ 40KB code + 672KB `head.bin` (cached, served by the plugin's own route).

## Out of scope

- Voice-commanded shapes ("Luna, become a cloud") beyond the reaction event lane.
- More blendshapes (blink/smile/visemes) — same baking path, later.
- Mobile layout tuning.

## Risks

- **Head model licensing** — `facecap.glb` is from the three.js examples. Before publishing 0.6.0, confirm the license or re-run `extract_head.mjs` on an owned/CC0 scan (works on any glTF head with ARKit blendshapes).
- **Two iframes, one session** — single-owner ping rule above; widget wins ties.
- **Old core + new plugin** — hidden section unreachable, expand button inert; no breakage. Acceptable because we control the hosted fleet's core version.
- **Mic permission UX** — the pane iframe must inherit mic permission like the widget iframe does today (no sandbox attribute on plugin iframes); verified in Shell.tsx.
