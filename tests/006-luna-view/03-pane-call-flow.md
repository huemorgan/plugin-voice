# Scenario 03 — Pane: controls, feed, mute, reactions (plugin-voice 0.6.0)

Target: a real Luna (local or tenant) with plugin-voice 0.6.0 installed and a
working OpenAI key (Settings → Voice shows ready).

## Steps

1. Hover the sidebar Voice widget.
   - **Expect**: a scale (⤢) icon fades in at the widget's top-right.
2. Click the icon.
   - **Expect**: the main pane switches to Luna View: full-size dot avatar (orb), controls bottom-center: Talk; Mute hidden or disabled (no call yet). "Set up microphone" visible ONLY if mic permission was never granted.
3. Click "Set up microphone" (only if shown) and grant.
   - **Expect**: browser prompt appears; after granting, the button disappears.
4. Click Talk. Speak a short sentence ("what time is it?").
   - **Expect**: swarm assembles → face forms. While you speak: teal tint + "Listening" label fading in/out. When Luna answers: violet tint, jaw moves with her voice, "Speaking" label fades in/out. Audio is audible.
5. While Luna talks, click Mute, then speak.
   - **Expect**: mute icon switches state; Luna does not react to your (muted) speech; the widget ALSO shows the muted state (feed sync). Unmute restores.
6. Open the sidebar widget view while the pane call is running.
   - **Expect**: ONE session only — widget waves move with the same audio; no second call, no double audio.
7. Ask Luna something celebratory ("we just won the contract!") or trigger a reaction via the debug channel.
   - **Expect**: fireworks (or another gesture) plays ~2.5s and reverts to the face.
8. Click End.
   - **Expect**: dots disperse to orb; "Idle" label fades; call actually closed (no residual audio, widget idle too).

## Pass

All expectations hold; screenshots at steps 2, 4, 5, 7.
