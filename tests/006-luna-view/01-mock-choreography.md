# Scenario 01 — Mock: automatic choreography, labels, fireworks

Target: `plans/006-luna-view/mock/index.html` served over HTTP (Playwright can't open file://).

## Steps

1. Open `http://localhost:8641/` (serve the mock folder with `python3 -m http.server 8641`).
2. Screenshot + snapshot the initial view.
   - **Expect**: orb of dots, dim violet/blue, NO shape chip row visible, NO state chip row visible. Controls visible: Talk, Mute (disabled until call), and "Set up microphone" only if permission isn't granted. A small "Idle" label may be fading out after load.
3. Open `http://localhost:8641/?debug=1`.
   - **Expect**: dev chips (states, audio sim, reactions) and the FPS counter appear.
4. In debug mode, click "Simulate voice" then the state "Speaking".
   - **Expect**: dots morph into the FACE (auto choreography follows the simulated call), violet tint while Luna speaks, mouth moves with the envelope. A "Speaking" label fades in then out within ~1.5s — it must NOT stay on screen permanently.
5. Switch state rapidly: Listening → Speaking → Listening.
   - **Expect**: label swaps each time, fades quickly; colors track teal (listening) / violet (speaking). No stuck labels, no flicker storm.
6. Click reaction "Fireworks" (debug chip).
   - **Expect**: a burst show — rockets rise and explode into expanding rainbow shells, clearly fireworks not falling rain; whole show over in ~2.5–3s; avatar returns to the previous shape automatically. Only a minority of dots participate (scene stays legible).
7. Click reactions heart / thumbs / rocket / arm.
   - **Expect**: each forms for ~3s, then auto-reverts. No manual revert needed.
8. Check the FPS counter during fireworks and face+voice.
   - **Expect**: ≥ 55 fps sustained on this machine.
9. Set state "Idle" / end the simulated call.
   - **Expect**: dots disperse back to the orb.

## Pass

All expectations hold with screenshot evidence for steps 2, 4, 6, and 8.
