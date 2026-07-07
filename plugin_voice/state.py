"""Module-level live state — ElevenLabs client, live-check tokens, last speaker.

Same pattern as plugin-render's `state.py`: resolved at call time, never cached
on the plugin instance.
"""

from __future__ import annotations

import time
from typing import Any

_client: Any = None

# live-check WS tokens: token -> expiry ts. Minted by /session (owner-authed);
# the widget iframe has no bearer token, so the WS authenticates with these.
_live_tokens: dict[str, float] = {}
LIVE_TOKEN_TTL = 300.0

# most recent speaker verdict from the live check: (label, score, ts)
_last_speaker: tuple[str, float, float] | None = None


def get_client() -> Any:
    return _client


def set_client(client: Any) -> None:
    global _client
    _client = client


async def close_client() -> None:
    global _client
    if _client is not None:
        try:
            await _client.close()
        finally:
            _client = None


def mint_live_token(token: str) -> None:
    now = time.time()
    for t in [t for t, exp in _live_tokens.items() if exp < now]:
        _live_tokens.pop(t, None)
    _live_tokens[token] = now + LIVE_TOKEN_TTL


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


# 004: one background persona resync at a time — /session fires it when the
# agent's live name no longer matches what the greeting was generated for.
# The task reference is kept so it can't be garbage-collected mid-flight
# (asyncio only holds a weak ref to tasks) and so tests can await it.
_resync_inflight = False
_resync_task = None


def try_begin_resync() -> bool:
    global _resync_inflight
    if _resync_inflight:
        return False
    _resync_inflight = True
    return True


def end_resync() -> None:
    global _resync_inflight
    _resync_inflight = False


def set_resync_task(task) -> None:
    global _resync_task
    _resync_task = task


def resync_task():
    return _resync_task
