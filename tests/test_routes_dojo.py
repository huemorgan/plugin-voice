"""Dojo-style tests through the real HTTP surface (FastAPI TestClient).

These answer the two questions the plan cares about most:
1. **Is the widget there?** — the manifest declares it AND the declared URL
   actually serves the visualization page with its controls.
2. **Is it configurable?** — connect stores the Gemini key in the VAULT
   (never anywhere else), the voice/model pickers round-trip through
   /settings, and the selected voice reaches the session the widget consumes.
"""

from __future__ import annotations

import json

import pytest

from plugin_voice import VAULT_GEMINI_KEY, VAULT_SETTINGS
from plugin_voice import gemini_live

from tests.conftest import FakeLive  # noqa: E402 — shared fake, autouse-patched

API = "/api/p/plugin-voice"


def _connect(client, **extra):
    resp = client.post(f"{API}/connect", json={"api_key": "gk_test_not_real", **extra})
    assert resp.status_code == 200, resp.text
    return resp.json()


# ------------------------------------------------------- 1. the widget is there


def test_widget_page_is_served_with_visualization_and_button(client):
    resp = client.get(f"{API}/ui/widgets/voice/")
    assert resp.status_code == 200
    html = resp.text
    assert 'data-testid="voice-viz"' in html          # the vibrating-voice canvas
    assert 'data-testid="voice-button"' in html       # talk/hang-up control
    assert 'data-testid="voice-status"' in html
    # both sides visualized from real streams (mic + WebRTC remote track)
    assert "createAnalyser" in html and "onRemoteStream" in html


def test_widget_manifest_declaration_matches_served_url(client):
    from plugin_voice import VoicePlugin

    w = VoicePlugin.manifest.widgets[0]
    widget_id = w["id"] if isinstance(w, dict) else w.id
    resp = client.get(f"{API}/ui/widgets/{widget_id}/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_widget_ships_its_voice_engine(client):
    resp = client.get(f"{API}/ui/widgets/voice/rt-client.js")
    assert resp.status_code == 200
    assert "LunaRT" in resp.text[:2000]
    # the ElevenLabs bundle is gone for good
    assert client.get(f"{API}/ui/widgets/voice/elevenlabs-client.js").status_code == 404


def test_settings_page_is_served(client):
    resp = client.get(f"{API}/ui/settings/")
    assert resp.status_code == 200
    assert 'data-testid="voice-voice-select"' in resp.text
    assert 'data-testid="voice-connect"' in resp.text
    # writes need the shell's bearer token (cookie auth is read-only)
    assert "luna-auth" in resp.text and "Authorization" in resp.text
    # OpenAI era: no ElevenLabs remnants in the page
    assert "elevenlabs" not in resp.text.lower() and "11labs" not in resp.text


def test_widget_uses_get_for_session(client):
    # minting lives in rt-client.js (005) — cookie-auth GET, same reason:
    # widget iframes are read-only on hosted, and minting writes nothing.
    js = client.get(f"{API}/ui/widgets/voice/rt-client.js").text
    assert '"/rt/session", {' in js and 'method: "GET"' in js
    assert 'credentials: "include"' in js


def test_static_serving_blocks_path_traversal(client):
    resp = client.get(f"{API}/ui/widgets/voice/%2e%2e/%2e%2e/__init__.py")
    assert resp.status_code in (403, 404)
    assert "LunaPlugin" not in resp.text


# --------------------------------------------------- 2. things are configurable


def test_connect_stores_key_in_vault_only(client, ctx):
    """One pasted Gemini key is all the owner provides."""
    status = _connect(client)
    assert status["connected"] is True
    assert status["key_source"] == "own"
    assert ctx.vault.data[VAULT_GEMINI_KEY] == "gk_test_not_real"
    # the probe minted a throwaway ephemeral token with the pasted key
    assert FakeLive.instances and FakeLive.instances[0].api_key == "gk_test_not_real"
    # and the key value never appears in the response body
    assert "gk_test_not_real" not in json.dumps(status)


def test_connect_rejects_bad_key(client, ctx):
    FakeLive.fail_mint = True
    resp = client.post(f"{API}/connect", json={"api_key": "gk_bad"})
    assert resp.status_code == 400
    assert VAULT_GEMINI_KEY not in ctx.vault.data  # nothing stored on failure


def test_connect_blank_key_yields_string_error_not_422(client):
    """Field-level 422s render as [object Object] in browsers — never emit them."""
    for body in ({}, {"api_key": ""}, {"api_key": "   "}):
        resp = client.post(f"{API}/connect", json=body)
        assert resp.status_code == 400
        assert isinstance(resp.json()["detail"], str)


def test_voice_settings_round_trip(client, ctx):
    _connect(client)
    base = client.get(f"{API}/settings").json()

    resp = client.post(f"{API}/settings", json={"rt_voice": "Kore"})
    assert resp.status_code == 200 and resp.json()["rt_voice"] == "Kore"

    # persisted (vault-backed KV), visible on re-read
    assert client.get(f"{API}/settings").json()["rt_voice"] == "Kore"
    assert json.loads(ctx.vault.data[VAULT_SETTINGS])["rt_voice"] == "Kore"

    # clearing works
    client.post(f"{API}/settings", json={"rt_voice": None})
    assert client.get(f"{API}/settings").json()["rt_voice"] is None


def test_settings_reject_unknown_voice_and_model(client):
    assert client.post(f"{API}/settings", json={"rt_voice": "rachel"}).status_code == 400
    assert client.post(f"{API}/settings", json={"rt_model": "gpt-5o"}).status_code == 400


def test_model_and_broker_knobs_round_trip(client, ctx):
    client.post(f"{API}/settings", json={
        "rt_model": "gemini-3.8-live-extended-thinking",
        "rt_lock_tools_to_owner": True,
        "rt_tools_allow": ["goal_list", " ", ""],
    })
    stored = json.loads(ctx.vault.data[VAULT_SETTINGS])
    assert stored["rt_model"] == "gemini-3.8-live-extended-thinking"
    assert stored["rt_lock_tools_to_owner"] is True
    assert stored["rt_tools_allow"] == ["goal_list"]  # blanks dropped


def test_voices_endpoint_is_the_static_catalog(client):
    data = client.get(f"{API}/voices").json()
    assert {v["voice_id"] for v in data["voices"]} == set(gemini_live.VOICE_IDS)
    assert set(data["models"]) == set(gemini_live.MODELS)


def test_selected_voice_reaches_the_session_the_widget_consumes(client, ctx):
    _connect(client)
    client.post(f"{API}/settings", json={"rt_voice": "Kore"})
    # GET: the widget iframe only has cookie (read-only) auth
    session = client.get(f"{API}/rt/session").json()
    assert session["voice"] == "Kore"
    minted_voice = FakeLive.minted[-1]["setup"]["generationConfig"][
        "speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"]
    assert minted_voice == "Kore"


def test_session_requires_setup(client):
    resp = client.get(f"{API}/rt/session")
    assert resp.status_code == 400  # no key resolvable yet


def test_status_reflects_disconnect(client, ctx):
    _connect(client)
    client.post(f"{API}/disconnect")
    status = client.get(f"{API}/status").json()
    assert status["connected"] is False
    assert VAULT_GEMINI_KEY not in ctx.vault.data


def test_disconnect_purges_legacy_elevenlabs_vault_keys(client, ctx):
    """A 0.4.x install leaves ElevenLabs credentials behind — disconnect is
    the cleanup point."""
    for legacy in (
        "plugin_voice.elevenlabs_api_key",
        "plugin_voice.agent_id",
        "plugin_voice.bridge_secret",
    ):
        ctx.vault.data[legacy] = "stale"
    client.post(f"{API}/disconnect")
    assert not any(k.startswith("plugin_voice.") and ctx.vault.data.get(k) == "stale"
                   for k in list(ctx.vault.data))


def test_widget_requests_mic_permission_explicitly(client):
    html = client.get(f"{API}/ui/widgets/voice/").text
    js = client.get(f"{API}/ui/widgets/voice/rt-client.js").text
    assert "getUserMedia" in js                         # explicit permission ask
    assert "NotAllowedError" in js                      # denied → clear re-ask hint
    assert "createAnalyser" in html                     # own analysers drive the viz


def test_widget_layout_matches_owner_spec(client):
    """No legend, no Talk button; CTA over the waves; ghost End button."""
    html = client.get(f"{API}/ui/widgets/voice/").text
    assert 'data-testid="voice-cta"' in html and "Start talking" in html
    assert 'data-testid="voice-button"' in html and ">End<" in html
    assert "Click Talk to start" not in html
    assert "legend" not in html.lower()
    assert "overflow: hidden" in html  # nothing scrolls in the 180px slot
