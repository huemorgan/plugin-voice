"""Phase 02 (005): lane-2 broker selection, execution, /rt/tool relay."""

from __future__ import annotations

import asyncio
import json

import pytest

from plugin_voice import VAULT_OPENAI_KEY, broker
from plugin_voice.broker import execute, knowledge_tools, tool_schemas

from .conftest import _RegisteredTool


def _names(tools) -> set[str]:
    return {t.name for t in tools}


# ---------------------------------------------------------------- selection


def test_default_selection_is_conservative(ctx):
    picked = _names(knowledge_tools(ctx, {}))
    # auto_approve + low in; high/ask out; send_chat_message deny-listed
    assert picked == {"get_weather", "list_files"}


def test_unset_risk_and_skill_gated_are_out(ctx):
    ctx.tool_registry.tools += [
        _RegisteredTool("wiki_read", risk_level=None),
        _RegisteredTool("memory_search", skill_gated=True),
    ]
    picked = _names(knowledge_tools(ctx, {}))
    assert "wiki_read" not in picked
    assert "memory_search" not in picked


def test_name_heuristics_deny_mislabeled_writes(ctx):
    # all claim low/auto_approve — every one is a write. Mid-name verbs too:
    # a live Luna registry admitted item_upsert/wiki_create_wiki/playbook_run
    # under the old suffix-only check.
    ctx.tool_registry.tools += [
        _RegisteredTool("goal_delete"),
        _RegisteredTool("theme_set"),
        _RegisteredTool("page_create"),
        _RegisteredTool("wiki_create_wiki"),
        _RegisteredTool("item_upsert"),
        _RegisteredTool("playbook_run"),
        _RegisteredTool("update_self"),
    ]
    picked = _names(knowledge_tools(ctx, {}))
    assert picked == {"get_weather", "list_files"}


def test_no_read_verb_means_not_selected(ctx):
    # reads must SAY they read — "research" is slow/agentic (that's luna_do's
    # job), "hello_world" says nothing.
    ctx.tool_registry.tools += [
        _RegisteredTool("research"),
        _RegisteredTool("hello_world"),
        _RegisteredTool("wiki_toc"),  # read token, no write token → in
    ]
    picked = _names(knowledge_tools(ctx, {}))
    assert picked == {"get_weather", "list_files", "wiki_toc"}


def test_cap_prefers_knowledge_prefixes(ctx):
    ctx.tool_registry.tools += [
        _RegisteredTool(f"ff_list_thing_{i:02d}") for i in range(broker.MAX_TOOLS + 5)
    ] + [
        _RegisteredTool("memory_recall"),
        _RegisteredTool("wiki_search"),
    ]
    picked = _names(knowledge_tools(ctx, {}))
    assert len(picked) == broker.MAX_TOOLS
    # knowledge-first prefixes survive the cut ahead of the ff_* flood, which
    # alphabetically precedes (and crowds out) the generic tools
    assert {"memory_recall", "wiki_search"} <= picked
    assert "list_files" not in picked


def test_owner_allow_does_not_count_against_cap(ctx):
    ctx.tool_registry.tools += [
        _RegisteredTool(f"aa_list_thing_{i:02d}") for i in range(broker.MAX_TOOLS)
    ]
    picked = _names(knowledge_tools(ctx, {"rt_tools_allow": ["restart_service"]}))
    assert "restart_service" in picked
    assert len(picked) == broker.MAX_TOOLS + 1


def test_owner_allow_and_deny(ctx):
    settings = {
        "rt_tools_allow": ["restart_service", "delete_everything"],
        "rt_tools_deny": ["list_files"],
    }
    picked = _names(knowledge_tools(ctx, settings))
    assert "restart_service" in picked      # ask-policy admitted by explicit allow
    assert "delete_everything" not in picked  # allow can never admit high risk
    assert "list_files" not in picked       # owner deny trims
    assert "get_weather" in picked


def test_broken_registry_degrades_to_no_tools(ctx):
    ctx.tool_registry = None
    assert knowledge_tools(ctx, {}) == []


# ------------------------------------------------------------------ schemas


def test_schemas_shape_and_uniqueness(ctx):
    ctx.tool_registry.tools += [
        _RegisteredTool(
            "get_weather",  # duplicate name — hot-reload artifact
            description="x" * 3000,
            parameters={"type": "object", "properties": {"city": {"type": "string"}}},
        ),
    ]
    schemas = tool_schemas(knowledge_tools(ctx, {}))
    names = [s["name"] for s in schemas]
    assert sorted(names) == ["get_weather", "list_files"]
    for s in schemas:
        assert s["type"] == "function"
        assert len(s["description"]) <= 1024
        assert s["parameters"]["type"] == "object"


# ---------------------------------------------------------------- execution


async def test_execute_happy_path(ctx):
    async def handler(city: str = "?"):
        return {"city": city, "temp": 21}

    ctx.tool_registry.get("get_weather").handler = handler
    out = await execute(ctx, "get_weather", {"city": "Haifa"}, {})
    assert out == {"ok": True, "result": {"city": "Haifa", "temp": 21}}


async def test_execute_unknown_and_unselected_tools_refused(ctx):
    out = await execute(ctx, "no_such_tool", {}, {})
    assert out["ok"] is False and out["error"] == broker.ERR_UNAVAILABLE
    # exists in the registry but not in the lane-2 set
    out = await execute(ctx, "restart_service", {}, {})
    assert out["ok"] is False and out["error"] == broker.ERR_UNAVAILABLE


async def test_execute_timeout_speakable(ctx):
    async def handler():
        await asyncio.sleep(5)

    ctx.tool_registry.get("get_weather").handler = handler
    out = await execute(ctx, "get_weather", {}, {}, timeout=0.05)
    assert out == {"ok": False, "error": broker.ERR_TIMEOUT}


async def test_execute_handler_crash_speakable(ctx):
    async def handler():
        raise RuntimeError("secret internal path /etc/x")

    ctx.tool_registry.get("get_weather").handler = handler
    out = await execute(ctx, "get_weather", {}, {})
    assert out == {"ok": False, "error": broker.ERR_FAILED}
    assert "secret" not in json.dumps(out)


async def test_execute_unjsonable_result_stringified(ctx):
    class Odd:
        def __str__(self):
            return "odd-result"

    async def handler():
        return Odd()

    ctx.tool_registry.get("get_weather").handler = handler
    out = await execute(ctx, "get_weather", {}, {})
    assert out == {"ok": True, "result": "odd-result"}


async def test_owner_deny_applies_at_call_time(ctx):
    async def handler():
        return "ok"

    ctx.tool_registry.get("get_weather").handler = handler
    out = await execute(ctx, "get_weather", {}, {"rt_tools_deny": ["get_weather"]})
    assert out["ok"] is False


# ---------------------------------------------------------------- /rt/tool


class FakeRT:
    def __init__(self, api_key=None, **kw): ...

    async def mint_client_secret(self, session):
        return {"value": "ek_test", "expires_at": 42, "session": session}

    async def close(self): ...


@pytest.fixture()
def rt_token(client, ctx, monkeypatch):
    from plugin_voice import openai_realtime

    monkeypatch.setattr(openai_realtime, "RealtimeClient", FakeRT)
    ctx.vault.data[VAULT_OPENAI_KEY] = "sk-own"
    return client.get("/api/p/plugin-voice/rt/session").json()["rt_token"]


def test_rt_tool_bad_token_401(client):
    resp = client.post(
        "/api/p/plugin-voice/rt/tool",
        json={"token": "nope", "name": "get_weather", "arguments": {}},
    )
    assert resp.status_code == 401


def test_rt_tool_executes(client, ctx, rt_token):
    async def handler(city: str = "?"):
        return f"Sunny in {city}"

    ctx.tool_registry.get("get_weather").handler = handler
    resp = client.post(
        "/api/p/plugin-voice/rt/tool",
        json={"token": rt_token, "name": "get_weather", "arguments": {"city": "Haifa"}},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "result": "Sunny in Haifa"}


def test_rt_tool_owner_lock(client, ctx, rt_token):
    from plugin_voice import state as live_state

    ctx.vault.data["plugin_voice.settings"] = json.dumps({"rt_lock_tools_to_owner": True})
    live_state.set_last_speaker("other", 0.1)
    resp = client.post(
        "/api/p/plugin-voice/rt/tool",
        json={"token": rt_token, "name": "get_weather", "arguments": {}},
    )
    assert resp.json() == {"ok": False, "error": broker.ERR_OWNER_ONLY}
    live_state.reset_speaker()


def test_rt_session_lists_lane2_tools(client, ctx, rt_token):
    data = client.get("/api/p/plugin-voice/rt/session").json()
    # lane-2 selection + the lane-3 synthetics (phase 03) + the UI-only
    # Luna View reaction tool (006)
    assert set(data["tool_names"]) == {
        "get_weather", "list_files", "luna_do", "luna_task_status", "luna_view_react",
    }
