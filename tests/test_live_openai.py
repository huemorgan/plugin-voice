"""Live OpenAI Realtime checks — only run when a real key is present.

``LUNA_OPENAI_API_KEY=sk-... pytest tests/test_live_openai.py`` — skipped in
normal CI runs.
"""

from __future__ import annotations

import os

import pytest

KEY = (os.environ.get("LUNA_OPENAI_API_KEY") or "").strip()

pytestmark = pytest.mark.skipif(not KEY, reason="no LUNA_OPENAI_API_KEY in env")


async def test_live_mint_client_secret():
    from plugin_voice.openai_realtime import DEFAULT_MODEL, RealtimeClient, session_config

    rc = RealtimeClient(KEY)
    try:
        minted = await rc.mint_client_secret(
            session_config(instructions="Say hello.", voice="marin", model=DEFAULT_MODEL)
        )
    finally:
        await rc.close()
    assert minted["value"].startswith("ek_")
    assert minted["session"]["model"].startswith("gpt-realtime")
