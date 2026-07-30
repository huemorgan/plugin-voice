# Scenario 02 — luna core: hidden section + luna-open-section

Target: local luna dev UI (luna-service/luna) with a plugin exposing a
`hidden: true` sidebar section (plugin-voice 0.6.0 or a fixture).

## Steps

1. Open the Luna shell in the browser and read the left nav (desktop).
   - **Expect**: the hidden section ("Luna" / luna-view) is NOT listed in the left nav. All previously visible sections still are.
2. Resize to a mobile viewport (or emulate) and read the bottom nav.
   - **Expect**: hidden section absent there too.
3. From the browser console (or the voice widget's expand button), post the open message to the shell window: `window.postMessage({type:"luna-open-section", section:"luna-view"}, "*")`.
   - **Expect**: the shell switches to the Luna View pane — the pane iframe loads `/api/p/plugin-voice/ui/view/` and renders the dot avatar.
4. Navigate to another section (Chat) and back via the message again.
   - **Expect**: works repeatedly; no console errors.
5. Reload the app while the section is open (deep link/route restore).
   - **Expect**: either the pane restores or the app lands on chat gracefully — no crash, no blank shell.

## Pass

Hidden from both navs, opens via message, no regressions in existing nav.
