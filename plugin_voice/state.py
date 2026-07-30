"""Module-level live state — call tokens, last speaker, lane-3 task manager.

Same pattern as plugin-render's `state.py`: resolved at call time, never cached
on the plugin instance.
"""

from __future__ import annotations

import time

# live-check WS tokens: token -> expiry ts. The widget iframe has no bearer
# token, so /live and the rt relay authenticate with these.
_live_tokens: dict[str, float] = {}
LIVE_TOKEN_TTL = 300.0

# 005: rt tokens gate the realtime relay endpoints (/rt/tool, /rt/events) for
# a whole call — calls outlast the 5-minute live-token TTL by design.
RT_TOKEN_TTL = 4 * 3600.0

# most recent speaker verdict from the live check: (label, score, ts)
_last_speaker: tuple[str, float, float] | None = None


def mint_live_token(token: str, *, ttl: float = LIVE_TOKEN_TTL) -> None:
    now = time.time()
    for t in [t for t, exp in _live_tokens.items() if exp < now]:
        _live_tokens.pop(t, None)
    _live_tokens[token] = now + ttl


def live_token_valid(token: str) -> bool:
    exp = _live_tokens.get(token or "")
    return bool(exp and exp > time.time())


def set_last_speaker(label: str, score: float) -> None:
    global _last_speaker
    _last_speaker = (label, score, time.time())


def recent_speaker(max_age: float = 10.0) -> tuple[str, float] | None:
    if _last_speaker is None:
        return None
    label, score, ts = _last_speaker
    if time.time() - ts > max_age:
        return None
    return label, score


def reset_speaker() -> None:
    global _last_speaker
    _last_speaker = None


# 005: one TaskManager per process — lane-3 tasks and their event stream must
# survive across route calls and widget reconnects.
_task_manager = None


def task_manager():
    global _task_manager
    if _task_manager is None:
        from .tasks import TaskManager

        _task_manager = TaskManager()
    return _task_manager


def reset_task_manager() -> None:
    global _task_manager
    _task_manager = None
