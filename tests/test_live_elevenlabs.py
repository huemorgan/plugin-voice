"""Live smoke test against the real ElevenLabs API.

Opt-in only: set ``LUNA_TEST_ELEVENLABS_KEY`` in the environment. The key is
read from env at runtime and never written anywhere.
"""

from __future__ import annotations

import os

import pytest

KEY = os.environ.get("LUNA_TEST_ELEVENLABS_KEY", "")

pytestmark = pytest.mark.skipif(not KEY, reason="LUNA_TEST_ELEVENLABS_KEY not set")


async def test_list_voices_live():
    from plugin_voice.elevenlabs import ElevenLabsClient

    client = ElevenLabsClient(KEY)
    try:
        voices = await client.list_voices()
    finally:
        await client.close()

    assert voices, "account should expose at least one voice"
    sample = voices[0]
    assert sample["voice_id"] and sample["name"]
