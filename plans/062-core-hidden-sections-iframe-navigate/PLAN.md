# 062 — Hidden sidebar sections + iframe → shell navigation

> Mirror copy for the voice plugin's records. This is a **Luna core** plan — its
> home is `luna/plans/062-hidden-sections-iframe-navigate/` in
> [huemorgan/luna](https://github.com/huemorgan/luna) (shipped in core 0.54.001).
> It exists because Luna View (plan 006) needed a nav-hidden pane and a general
> iframe → shell navigation message; plugin-voice ≥ 0.6.0 is its first consumer.

## Goal

Two small, general shell capabilities that plugin-voice 0.6.0 (Luna View) needs and any plugin can use:

1. **`SidebarSection.hidden`** — a section that renders like any pane but is not listed in the sidebar nav. It stays deep-linkable (`/p/<id>`), stays addressable by the agent's `navigate_to` tool, and stays in the shell's section registry — only the nav lists skip it. Use case: plugin-voice's "Luna View" pane opens from the voice widget's expand button, not from a nav link.

2. **`luna-navigate` window message** — plugin iframes (widgets and panes) can steer the shell the same way the agent's `navigate_to` tool does. One navigation brain (`handleUiEvent`), three feeds: agent tool via SSE (exists), nav clicks (exists), and now iframe postMessage. Closes a real capability gap — iframes today are navigation dead ends (the only iframe→shell message is `luna-plugin-changed`).

## Changes

### `luna/plugins/base.py`

- `SidebarSection` gains `hidden: bool = False`. Older plugins never set it; pydantic default keeps every existing manifest valid.

### `plugins/plugin_webui/routes.py`

- `/api/ui/plugins` sidebar entries carry `"hidden": getattr(s, "hidden", False)` (getattr-guarded like `path`, so a plugin shipping its own older SidebarSection model can't 500 the registry).
- `ui_tools.valid_sections()` needs **no change** — it reads manifests, not nav lists, so hidden sections remain agent-navigable by design.

### `ui/src/views/Shell.tsx` + `ui/src/lib/api.ts`

- `PluginSidebarEntry` gains `hidden?: boolean`.
- One module-level `navPluginSections(sections, builtinIds)` helper used by all three nav-list builders (mobile bottom nav, desktop sidebar, collapsed sidebar) — replaces the triplicated `.filter(p => !builtinIds.has(p.id))` and adds the `!p.hidden` condition in exactly one place.
- A `message` listener that maps `{type:'luna-navigate', section, target?}` (same-origin only, section validated against builtins + known plugin sections) onto the existing `handleUiEvent({type:'ui.navigate', ...})`. Everything else — approvals mapping, `plugin:` prefixing, in-section target delivery through the iframe bridge — comes for free.

## Non-goals

- No changes to `navigate_to` (already general, discovers sections dynamically).
- No permission model for iframe navigation — plugin iframes are same-origin and unsandboxed today; they're already trusted with more than navigation.

## Tests

- `tests/062-hidden-sections-iframe-navigate/unit/test_hidden_sections.py` — manifest default, webui payload pass-through, hidden section still in `valid_sections()`.
- Browser walkthrough (dojo-style) against the local server with plugin-voice 0.6.0 installed: "Luna" absent from nav, `/p/luna-view` renders, widget expand button opens the pane, `navigate_to` still opens playbooks / brain / luna-view from chat.

## Consumers

- plugin-voice 0.6.0 ships `sidebar_sections=[{id:"luna-view", ..., hidden:true}]` and its widget posts `{type:"luna-navigate", section:"luna-view"}` on expand. On cores without 062 the field is ignored (visible link fallback) and the message is inert — no breakage either direction.
