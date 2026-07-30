"""Voice-persona settings — the talker's owner-editable knobs.

Three layers, first hit wins:

1. **overrides** — what the owner saved in the Voice Persona tab, stored
   under ``persona_overrides`` inside the plugin's settings JSON;
2. **auto** — what the agent's own personality produced at connect /
   re-match time (``greeting`` / ``fillers`` in settings);
3. **defaults** — the shipped values below. ``voice_system_prompt`` and
   ``talker_extra`` default to None: the talker's built-in style prompt
   (talker.VOICE_STYLE) applies unless the owner overrides it.

Engine knobs (``rt_voice``, ``rt_model``, ``rt_lock_tools_to_owner``,
``rt_tools_allow``, ``rt_tools_deny``) live at the settings top level — they
configure the session, not the persona — and are edited via ``POST /settings``.
Stored overrides for fields dropped in 0.5.0 (triage, timeouts, passthrough)
are ignored harmlessly: the merge only knows the current DEFAULTS.
"""

from __future__ import annotations

from typing import Any

OVERRIDES_KEY = "persona_overrides"

NEUTRAL_GREETING = "Hey, I'm listening — what can I do for you?"
NEUTRAL_FILLERS = [
    "One moment, I'm checking that...",
    "Still working on it...",
    "Almost there, hang on...",
]

TURN_EAGERNESS_VALUES = ("eager", "normal", "patient")

# greeting/fillers default to None: "auto" — use the personality-fetched
# value from settings, else the neutral fallbacks above.
# voice_system_prompt/talker_extra None → the talker's shipped style.
DEFAULTS: dict[str, Any] = {
    "greeting": None,
    "fillers": None,
    "voice_system_prompt": None,
    "talker_extra": None,
    "turn_eagerness": "patient",
}


class PersonaConfigError(ValueError):
    """A rejected override value, with an owner-readable message."""


def overrides_of(settings: dict) -> dict:
    raw = settings.get(OVERRIDES_KEY)
    return dict(raw) if isinstance(raw, dict) else {}


def effective(settings: dict) -> dict:
    """The resolved persona config: overrides > auto (fetched persona) > defaults."""
    ov = overrides_of(settings)
    out = dict(DEFAULTS)
    out["greeting"] = (
        ov.get("greeting")
        or (settings.get("greeting") or "").strip()
        or NEUTRAL_GREETING
    )
    fillers = ov.get("fillers") or settings.get("fillers")
    out["fillers"] = [str(f) for f in fillers if str(f).strip()] if fillers else list(NEUTRAL_FILLERS)
    for key in DEFAULTS:
        if key in ("greeting", "fillers"):
            continue
        if key in ov:
            out[key] = ov[key]
    return out


def _clean_str(value: Any, field: str, *, max_len: int = 4000) -> str:
    text = str(value).strip()
    if not text:
        raise PersonaConfigError(f"{field} cannot be empty — send null to reset it")
    if len(text) > max_len:
        raise PersonaConfigError(f"{field} is too long (max {max_len} characters)")
    return text


def apply_changes(settings: dict, changes: dict) -> tuple[dict, set[str]]:
    """Validate ``changes`` and fold them into ``settings[OVERRIDES_KEY]``.

    ``None`` clears a field back to auto/default. Returns the mutated
    settings dict and the set of fields that actually changed.
    """
    ov = overrides_of(settings)
    changed: set[str] = set()
    for field, value in changes.items():
        if field not in DEFAULTS:
            raise PersonaConfigError(f"Unknown field '{field}'")
        if value is None:
            if field in ov:
                ov.pop(field)
                changed.add(field)
            continue
        if field == "fillers":
            if not isinstance(value, list):
                raise PersonaConfigError("fillers must be a list of phrases")
            cleaned = [str(f).strip()[:120] for f in value if str(f).strip()]
            if not cleaned:
                raise PersonaConfigError("fillers cannot be empty — send null to reset")
            if len(cleaned) > 5:
                raise PersonaConfigError("at most 5 filler phrases")
            new = cleaned
        elif field == "turn_eagerness":
            new = str(value).strip().lower()
            if new not in TURN_EAGERNESS_VALUES:
                raise PersonaConfigError(
                    f"turn_eagerness must be one of {list(TURN_EAGERNESS_VALUES)}"
                )
        elif field == "greeting":
            new = _clean_str(value, field, max_len=300)
        else:  # the prompt textareas: voice_system_prompt / talker_extra
            new = _clean_str(value, field)
        if ov.get(field) != new:
            ov[field] = new
            changed.add(field)
    settings[OVERRIDES_KEY] = ov
    return settings, changed
