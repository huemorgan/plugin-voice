"""Phase 03 (005): lane-3 task manager, events, dispatch tools, /rt/events."""

from __future__ import annotations

import asyncio
import json

import pytest

from plugin_voice import VAULT_OPENAI_KEY, tasks
from plugin_voice.tasks import TaskManager


class SlowAgent:
    """run_turn that waits on an explicit gate — the test controls when the
    'work' finishes."""

    def __init__(self, reply: str = "Done. The playbook has three steps."):
        self.reply = reply
        self.gate = asyncio.Event()
        self.calls: list[dict] = []

    async def run_turn(self, prompt: str, **kw):
        self.calls.append({"prompt": prompt, **kw})
        await self.gate.wait()
        return (self.reply, None)


async def _drain(entryish: TaskManager) -> None:
    for e in list(entryish._entries.values()):
        task = e.get("task")
        if task is not None:
            await task


# ---------------------------------------------------------------- lifecycle


async def test_dispatch_returns_immediately_then_completes(ctx):
    ctx.agent = SlowAgent()
    tm = TaskManager()
    q = tm.subscribe()

    out = tm.dispatch(ctx, "build the playbook", owner_verified=True, settings={})
    assert out["ok"] and out["task_id"] and out["accepted"]
    started = await asyncio.wait_for(q.get(), 1)
    assert started["type"] == "task_started"

    st = tm.status(out["task_id"])
    assert st["task"]["status"] in ("queued", "running")

    ctx.agent.gate.set()
    done = await asyncio.wait_for(q.get(), 1)
    assert done["type"] == "task_done"
    assert done["task_id"] == out["task_id"]
    assert done["spoken_summary"] == "Done. The playbook has three steps."
    assert isinstance(done["elapsed_s"], int)
    assert tm.status(out["task_id"])["task"]["status"] == "done"
    await _drain(tm)


async def test_dispatch_binds_conversation_id(ctx):
    # A delegated turn must be bound to the owner's chat so send_chat_message
    # and approval cards resolve there (the doing-lane fix).
    ctx.agent = SlowAgent()
    tm = TaskManager()
    tm.dispatch(ctx, "do it", owner_verified=True, settings={}, conversation_id="conv-123")
    ctx.agent.gate.set()
    await _drain(tm)
    assert ctx.agent.calls[0]["conversation_id"] == "conv-123"


async def test_all_subscribers_receive_events(ctx):
    ctx.agent = SlowAgent()
    tm = TaskManager()
    q1, q2 = tm.subscribe(), tm.subscribe()
    tm.dispatch(ctx, "x", owner_verified=True, settings={})
    ctx.agent.gate.set()
    await _drain(tm)
    for q in (q1, q2):
        types = []
        while not q.empty():
            types.append(q.get_nowait()["type"])
        assert types == ["task_started", "task_done"]


async def test_concurrency_cap_speakable(ctx):
    ctx.agent = SlowAgent()
    tm = TaskManager()
    for i in range(tasks.MAX_CONCURRENT):
        assert tm.dispatch(ctx, f"job {i}", owner_verified=True, settings={})["ok"]
    refused = tm.dispatch(ctx, "one too many", owner_verified=True, settings={})
    assert refused == {"ok": False, "error": tasks.ERR_BUSY}
    ctx.agent.gate.set()
    await _drain(tm)
    # capacity returns after completion
    assert tm.dispatch(ctx, "next", owner_verified=True, settings={})["ok"]
    ctx.agent.gate.set()
    await _drain(tm)


async def test_empty_instruction_refused(ctx):
    tm = TaskManager()
    assert tm.dispatch(ctx, "   ", owner_verified=True, settings={}) == {
        "ok": False, "error": tasks.ERR_NO_INSTRUCTION,
    }


async def test_failure_becomes_speakable_event(ctx):
    class FailingAgent:
        async def run_turn(self, prompt, **kw):
            raise RuntimeError("internal path /var/secret")

    ctx.agent = FailingAgent()
    tm = TaskManager()
    q = tm.subscribe()
    out = tm.dispatch(ctx, "explode", owner_verified=True, settings={})
    await _drain(tm)
    events = [q.get_nowait() for _ in range(q.qsize())]
    assert events[-1]["type"] == "task_failed"
    assert events[-1]["spoken_summary"] == tasks.FAILED_SUMMARY
    assert "secret" not in json.dumps(events)
    assert tm.status(out["task_id"])["task"]["status"] == "failed"


# ----------------------------------------------------- gating + task prompt


async def test_owner_verified_stamped_at_dispatch(ctx):
    tm = TaskManager()
    tm.dispatch(ctx, "run the playbook", owner_verified=False, settings={})
    await _drain(tm)
    call = ctx.agent.calls[0]
    # unrecognized speaker → prompt_always tools dropped from the allowlist
    assert "delete_everything" not in (call["tools"] or [])
    assert "UNRECOGNIZED voice" in call["prompt"]
    assert tasks.TASK_RULES.splitlines()[0] in call["prompt"]

    tm2 = TaskManager()
    tm2.dispatch(ctx, "run the playbook", owner_verified=True, settings={})
    await _drain(tm2)
    assert "your owner" in ctx.agent.calls[1]["prompt"]


def test_synthetic_schemas():
    schemas = tasks.synthetic_schemas()
    by_name = {s["name"]: s for s in schemas}
    assert set(by_name) == {"luna_do", "luna_task_status"}
    assert by_name["luna_do"]["parameters"]["required"] == ["instruction"]
    assert "task id" in by_name["luna_do"]["description"].lower() or "task_id" in json.dumps(schemas)


def test_events_replay_and_seq_monotonic(ctx):
    async def run():
        ctx.agent = SlowAgent()
        tm = TaskManager()
        tm.dispatch(ctx, "a", owner_verified=True, settings={})
        ctx.agent.gate.set()
        await _drain(tm)
        events = tm.recent_events(0)
        seqs = [e["seq"] for e in events]
        assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
        # since_seq filters
        assert [e["seq"] for e in tm.recent_events(seqs[0])] == seqs[1:]

    asyncio.run(run())


# ------------------------------------------------------------------ routes


class FakeRT:
    def __init__(self, api_key=None, **kw): ...

    async def mint_client_secret(self, session):
        FakeRT.last_session = session
        return {"value": "ek_test", "expires_at": 42, "session": session}

    async def close(self): ...


@pytest.fixture()
def rt_token(client, ctx, monkeypatch):
    from plugin_voice import openai_realtime

    monkeypatch.setattr(openai_realtime, "RealtimeClient", FakeRT)
    ctx.vault.data[VAULT_OPENAI_KEY] = "sk-own"
    return client.get("/api/p/plugin-voice/rt/session").json()["rt_token"]


def test_session_includes_synthetic_tools(client, ctx, rt_token):
    data = client.get("/api/p/plugin-voice/rt/session").json()
    assert "luna_do" in data["tool_names"] and "luna_task_status" in data["tool_names"]
    names = [t["name"] for t in FakeRT.last_session["tools"]]
    assert "luna_do" in names and "get_weather" in names


def test_luna_do_and_status_via_rt_tool(client, ctx, rt_token):
    resp = client.post(
        "/api/p/plugin-voice/rt/tool",
        json={"token": rt_token, "name": "luna_do", "arguments": {"instruction": "check the goal board"}},
    )
    body = resp.json()
    assert body["ok"] and body["task_id"]

    status = client.post(
        "/api/p/plugin-voice/rt/tool",
        json={"token": rt_token, "name": "luna_task_status", "arguments": {"task_id": body["task_id"]}},
    ).json()
    assert status["ok"] and status["task"]["task_id"] == body["task_id"]

    all_status = client.post(
        "/api/p/plugin-voice/rt/tool",
        json={"token": rt_token, "name": "luna_task_status", "arguments": {}},
    ).json()
    assert all_status["ok"] and len(all_status["tasks"]) == 1


def test_rt_events_ws_token_gate_and_stream(client, ctx, rt_token):
    from starlette.websockets import WebSocketDisconnect as WSDisconnect

    with pytest.raises(WSDisconnect):
        with client.websocket_connect("/api/p/plugin-voice/rt/events?token=bad"):
            pass

    with client.websocket_connect(f"/api/p/plugin-voice/rt/events?token={rt_token}") as ws:
        client.post(
            "/api/p/plugin-voice/rt/tool",
            json={"token": rt_token, "name": "luna_do", "arguments": {"instruction": "quick job"}},
        )
        started = ws.receive_json()
        assert started["type"] == "task_started"
        done = ws.receive_json()
        assert done["type"] == "task_done"
        assert done["spoken_summary"]  # FakeAgent's canned reply


def test_rt_events_ws_replays_missed_events(client, ctx, rt_token):
    client.post(
        "/api/p/plugin-voice/rt/tool",
        json={"token": rt_token, "name": "luna_do", "arguments": {"instruction": "quick job"}},
    )
    import time

    deadline = time.time() + 2
    from plugin_voice import state as live_state

    while time.time() < deadline and live_state.task_manager().active_count():
        time.sleep(0.02)
    with client.websocket_connect(f"/api/p/plugin-voice/rt/events?token={rt_token}") as ws:
        types = [ws.receive_json()["type"], ws.receive_json()["type"]]
    assert types == ["task_started", "task_done"]
