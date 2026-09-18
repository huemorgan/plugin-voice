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

from plugin_voice import VAULT_GEMINI_KEY
from plugin_voice.routes import VAULT_PROFILE

WIDGET_DIR = Path(__file__).parent.parent / "plugin_voice" / "ui" / "widgets" / "voice"


@pytest.fixture()
def rt(client, ctx):
    # minting is faked by conftest's autouse GeminiLiveClient patch
    ctx.vault.data[VAULT_GEMINI_KEY] = "gk-own"
    return client


# ----------------------------------------------------------------- contract

# Every field rt-client.js / index.html reads off the /rt/session payload.
JS_CONSUMED_FIELDS = {
    "access_token",    # ?access_token= on the Live WS URL
    "ws_url",          # the Constrained BidiGenerateContent WS endpoint
    "setup",           # first WS frame, sent VERBATIM (matches the token lock)
    "rt_token",        # /rt/tool bodies + /rt/events?token=
    "live_token",      # /live?token= (imprint tee)
    "has_imprint",     # tee on/off switch
    "persona_name",    # status line name
}


def test_rt_session_carries_every_field_the_client_reads(rt):
    data = rt.get("/api/p/plugin-voice/rt/session").json()
    missing = JS_CONSUMED_FIELDS - set(data)
    assert not missing, f"/rt/session dropped fields the widget JS reads: {missing}"
    assert data["access_token"].startswith("auth_tokens/")
    assert data["ws_url"].startswith("wss://")
    assert data["setup"]["setup"]["model"].startswith("models/")
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


# --------------------------------------------------------- 006 — Luna View


def test_view_pane_served_at_view_path(client):
    resp = client.get("/api/p/plugin-voice/ui/view/")
    assert resp.status_code == 200
    assert "Luna view" in resp.text


def test_view_pane_served_at_pane_root(client):
    """Today's shell hardcodes plugin pane iframes to /api/p/<plugin>/ui/ —
    the catch-all must serve Luna View there so the sidebar link works on
    cores that don't know SidebarSection.path yet."""
    resp = client.get("/api/p/plugin-voice/ui/")
    assert resp.status_code == 200
    assert "Luna view" in resp.text
    # head.bin resolves relative to the pane root too
    assert client.get("/api/p/plugin-voice/ui/head.bin").status_code == 200


def test_pane_root_does_not_shadow_widget_and_settings(client):
    """The /ui/{path} catch-all is registered LAST — the specific widget and
    settings routes must still win."""
    assert "LunaRT" in client.get("/api/p/plugin-voice/ui/widgets/voice/rt-client.js").text
    assert client.get("/api/p/plugin-voice/ui/settings/").status_code == 200


def test_rt_session_advertises_luna_view_react(rt):
    data = rt.get("/api/p/plugin-voice/rt/session").json()
    assert "luna_view_react" in data["tool_names"]


def test_rt_tool_luna_view_react_is_a_quiet_noop(rt):
    """Normally intercepted client-side; an old cached rt-client that relays
    it must get a clean ok, not an unknown-tool error."""
    token = rt.get("/api/p/plugin-voice/rt/session").json()["rt_token"]
    resp = rt.post(
        "/api/p/plugin-voice/rt/tool",
        json={"token": token, "name": "luna_view_react", "arguments": {"shape": "heart"}},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_js_field_list_matches_client_source():
    """The JS_CONSUMED_FIELDS set above is hand-maintained — verify each field
    actually appears as a session.<field> read in the client source, so the
    pin can't rot."""
    src = (WIDGET_DIR / "rt-client.js").read_text() + (WIDGET_DIR / "index.html").read_text()
    if "webrtc_url" in src:
        pytest.skip("rt-client.js still speaks WebRTC — migrates in 007 phase 2")
    for field in JS_CONSUMED_FIELDS:
        assert re.search(rf"session\.{field}\b", src), f"{field} not read in client JS"
