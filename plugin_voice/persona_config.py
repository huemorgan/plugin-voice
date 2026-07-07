"""Voice-persona settings — every previously hardcoded knob, owner-editable.

Three layers, first hit wins:

1. **overrides** — what the owner saved in the Voice Persona tab, stored
   under ``persona_overrides`` inside the plugin's settings JSON;
2. **auto** — what the agent's own personality produced at connect /
   refresh time (``greeting`` / ``fillers`` in settings);
3. **defaults** — the shipped values below (identical to the constants that
   used to live in bridge.py / elevenlabs.py, so an untouched install
   behaves exactly as before).
"""

from __future__ import annotations

from typing import Any

from . import bridge

OVERRIDES_KEY = "persona_overrides"

# ElevenLabs passthrough prompt (the agent-level prompt; every real reply
# comes from the custom LLM bridge, this only frames it).
PASSTHROUGH_PROMPT = (
    "Every reply is produced by the connected custom LLM "
    "(the agent's own loop, with its real name and "
    "personality); pass conversation through faithfully."
)

NEUTRAL_GREETING = "Hey, I'm listening — what can I do for you?"
NEUTRAL_FILLERS = [
    "One moment, I'm checking that...",
    "Still working on it...",
    "Almost there, hang on...",
]

TURN_EAGERNESS_VALUES = ("eager", "normal", "patient")

# greeting/fillers default to None: "auto" — use the personality-fetched
# value from settings, else the neutral fallbacks above.
DEFAULTS: dict[str, Any] = {
    "greeting": None,
    "fillers": None,
    "voice_system_prompt": bridge.VOICE_SYSTEM_PROMPT,
    "triage_enabled": True,
    "triage_system": bridge.TRIAGE_SYSTEM,
    "passthrough_prompt": PASSTHROUGH_PROMPT,
    "soft_timeout_seconds": 5.0,
    "max_soft_timeouts": 3,
    "turn_eagerness": "patient",
}

# Fields that live in the ElevenLabs agent config — changing one requires a
# re-PATCH of the agent; the rest apply on the next bridge turn.
ELEVENLABS_FIELDS = frozenset(
    {"greeting", "fillers", "passthrough_prompt", "soft_timeout_seconds",
     "max_soft_timeouts", "turn_eagerness"}
)


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


def elevenlabs_overrides(settings: dict) -> dict:
    """The kwargs handed to ElevenLabs agent create/update calls, resolved
    from the effective config (so PATCHes never reset an owner tweak)."""
    eff = effective(settings)
    return {
        "passthrough_prompt": eff["passthrough_prompt"],
        "soft_timeout_seconds": eff["soft_timeout_seconds"],
        "max_soft_timeouts": eff["max_soft_timeouts"],
        "turn_eagerness": eff["turn_eagerness"],
    }


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
        elif field == "triage_enabled":
            if not isinstance(value, bool):
                raise PersonaConfigError("triage_enabled must be true or false")
            new = value
        elif field == "soft_timeout_seconds":
            try:
                new = float(value)
            except (TypeError, ValueError):
                raise PersonaConfigError("soft_timeout_seconds must be a number") from None
            if not 1.0 <= new <= 30.0:
                raise PersonaConfigError("soft_timeout_seconds must be between 1 and 30")
        elif field == "max_soft_timeouts":
            if isinstance(value, bool) or not isinstance(value, int):
                raise PersonaConfigError("max_soft_timeouts must be a whole number")
            if not 0 <= value <= 10:
                raise PersonaConfigError("max_soft_timeouts must be between 0 and 10")
            new = value
        elif field == "turn_eagerness":
            new = str(value).strip().lower()
            if new not in TURN_EAGERNESS_VALUES:
                raise PersonaConfigError(
                    f"turn_eagerness must be one of {list(TURN_EAGERNESS_VALUES)}"
                )
        elif field == "greeting":
            new = _clean_str(value, field, max_len=300)
        else:  # the prompt textareas
            new = _clean_str(value, field)
        if ov.get(field) != new:
            ov[field] = new
            changed.add(field)
    settings[OVERRIDES_KEY] = ov
    return settings, changed
