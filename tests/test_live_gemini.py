"""Live Gemini checks — only run when a real key is present.

``LUNA_GEMINI_API_KEY=... pytest tests/test_live_gemini.py`` — skipped in
normal CI runs. Mints a real ephemeral token with the full production-shaped
setup, proving the key works for exactly what /rt/session does.
"""

from __future__ import annotations

import os

import pytest

# Module-top import binds the REAL client before conftest's autouse patch
# swaps the module attribute for FakeLive — this file must hit Google.
from plugin_voice.gemini_live import DEFAULT_MODEL, GeminiLiveClient, setup_message

KEY = (os.environ.get("LUNA_GEMINI_API_KEY") or "").strip()

pytestmark = pytest.mark.skipif(not KEY, reason="no LUNA_GEMINI_API_KEY in env")


async def test_live_mint_ephemeral_token():
    setup = setup_message(
        instructions="Say hello.",
        voice="Aoede",
        model=DEFAULT_MODEL,
        tools=[{
            "type": "function", "name": "get_weather", "description": "d",
            "parameters": {"type": "object",
                           "properties": {"city": {"type": "string"}},
                           "required": ["city"]},
        }],
        turn_eagerness="patient",
    )
    gc = GeminiLiveClient(KEY)
    try:
        minted = await gc.mint_token(setup, uses=1)
    finally:
        await gc.close()
    # Google accepted the FULL setup (VAD preset, proactivity, compression,
    # resumption, converted tools) as a token constraint.
    assert minted["name"].startswith("auth_tokens/")
    assert minted["expire_time"]
