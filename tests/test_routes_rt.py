"""Phase 04 (005): the /rt/session ↔ rt-client.js contract, widget statics.

rt-client.js has no test harness in this repo — these tests pin the server
payload field names the JS destructures, so a rename breaks HERE instead of
silently in the browser.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from plugin_voice import VAULT_OPENAI_KEY
from plugin_voice.routes import VAULT_PROFILE

WIDGET_DIR = Path(__file__).parent.parent / "plugin_voice" / "ui" / "widgets" / "voice"


class FakeRT:
    def __init__(self, api_key=None, **kw): ...

    async def mint_client_secret(self, session):
        return {"value": "ek_test", "expires_at": 42, "session": session}

    async def close(self): ...


@pytest.fixture()
def rt(client, ctx, monkeypatch):
    from plugin_voice import openai_realtime

    monkeypatch.setattr(openai_realtime, "RealtimeClient", FakeRT)
    ctx.vault.data[VAULT_OPENAI_KEY] = "sk-own"
    return client


# ----------------------------------------------------------------- contract

# Every field rt-client.js / index.html reads off the /rt/session payload.
JS_CONSUMED_FIELDS = {
    "client_secret",   # Authorization: Bearer … on the SDP POST
    "webrtc_url",      # SDP POST target
    "model",           # ?model= query param on the SDP POST
    "rt_token",        # /rt/tool bodies + /rt/events?token=
    "live_token",      # /live?token= (imprint tee)
    "has_imprint",     # tee on/off switch
    "persona_name",    # status line name
}


def test_rt_session_carries_every_field_the_client_reads(rt):
    data = rt.get("/api/p/plugin-voice/rt/session").json()
    missing = JS_CONSUMED_FIELDS - set(data)
    assert not missing, f"/rt/session dropped fields the widget JS reads: {missing}"
    assert data["client_secret"] == "ek_test"
    assert data["webrtc_url"].startswith("https://")
    assert data["model"]
    assert data["rt_token"]


def test_rt_session_no_imprint_means_no_live_token(rt):
    data = rt.get("/api/p/plugin-voice/rt/session").json()
    assert data["has_imprint"] is False
    assert data["live_token"] is None


def test_rt_session_with_imprint_arms_live_with_same_token(rt, ctx):
    ctx.vault.data[VAULT_PROFILE] = json.dumps({"v": 1, "embedding": [0.1]})
    data = rt.get("/api/p/plugin-voice/rt/session").json()
    assert data["has_imprint"] is True
    assert data["live_token"] == data["rt_token"]  # one token arms every socket


# ------------------------------------------------------------------ statics


def test_widget_serves_rt_client_js(client):
    resp = client.get("/api/p/plugin-voice/ui/widgets/voice/rt-client.js")
    assert resp.status_code == 200
    assert "LunaRT" in resp.text


def test_widget_index_wires_rt_client_not_elevenlabs(client):
    resp = client.get("/api/p/plugin-voice/ui/widgets/voice/")
    assert resp.status_code == 200
    assert '<script src="rt-client.js">' in resp.text
    assert "elevenlabs-client.js" not in resp.text


def test_js_field_list_matches_client_source():
    """The JS_CONSUMED_FIELDS set above is hand-maintained — verify each field
    actually appears as a session.<field> read in the client source, so the
    pin can't rot."""
    src = (WIDGET_DIR / "rt-client.js").read_text() + (WIDGET_DIR / "index.html").read_text()
    for field in JS_CONSUMED_FIELDS:
        assert re.search(rf"session\.{field}\b", src), f"{field} not read in client JS"
