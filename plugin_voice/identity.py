"""Live agent identity — read it, never copy it.

plugin-identity registers an ``identity`` config section on core's registry
(name, emoji, mission, …). Reading it through ``ctx.config_registry`` is the
sanctioned, import-free way for a marketplace plugin to know the agent's REAL
current name — the persona snapshot this plugin stores at connect time goes
stale the moment the owner renames the agent (004: "it keeps saying their old
name").
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("plugin-voice.identity")


async def live_identity(ctx: Any) -> dict | None:
    """The agent's current identity fields, or None when unavailable
    (older core, bare test context, section not registered)."""
    registry = getattr(ctx, "config_registry", None)
    get = getattr(registry, "get", None)
    if not callable(get):
        return None
    try:
        section = get("identity")
        reader = getattr(section, "reader", None)
        if not callable(reader):
            return None
        data = await reader()
    except Exception as exc:  # noqa: BLE001 — never break a route on identity read
        log.debug("plugin-voice: live identity read failed: %s", exc)
        return None
    return data if isinstance(data, dict) else None


async def live_name(ctx: Any) -> str | None:
    data = await live_identity(ctx)
    name = str((data or {}).get("name") or "").strip()
    return name or None
