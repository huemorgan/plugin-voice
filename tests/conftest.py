"""Test stub for `luna_sdk` + fakes for vault / agent / tool registry.

`luna_sdk` is provided by the Luna runtime, not PyPI. The stub carries just the
names plugin_voice imports; the real contract is exercised inside Luna.
"""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass, field
from typing import Any

import pytest


def _install_luna_sdk_stub() -> None:
    if "luna_sdk" in sys.modules:
        return

    mod = types.ModuleType("luna_sdk")

    @dataclass
    class ToolDef:
        name: str
        description: str = ""
        parameters: dict | None = None
        policy: str = "auto_approve"
        risk_level: str = "low"
        sensitive_args: list = field(default_factory=list)
        skill_gated: bool = False

    @dataclass
    class SettingsTab:
        id: str
        label: str
        icon: str = "settings"
        sort_order: int = 50
        iframe_src: str = ""

    class PluginManifest:
        def __init__(self, **kw: Any) -> None:
            for k, v in kw.items():
                setattr(self, k, v)
            self.name = kw.get("name", "")
            self.version = kw.get("version", "")
            self.description = kw.get("description", "")
            self.widgets = kw.get("widgets", [])
            self.settings_tabs = kw.get("settings_tabs", [])
            self.depends_on = kw.get("depends_on", [])
            self.routes_module = kw.get("routes_module")

    class PluginContext:  # pragma: no cover — structural stand-in
        pass

    class LunaPlugin:  # pragma: no cover — structural stand-in
        manifest: PluginManifest

        async def on_load(self, ctx: Any) -> None: ...
        async def on_unload(self) -> None: ...

    def get_current_user():  # overridden per-app in tests when needed
        return {"id": "test-owner"}

    @dataclass
    class AuthSpec:
        location: str = "header"
        name: str = "Authorization"
        scheme: str | None = None

    @dataclass
    class Connection:
        base_url: str
        secret: str
        auth: "AuthSpec" = field(default_factory=lambda: AuthSpec())
        source: str = "real"
        billed: bool = False

        def apply(self, headers, params):
            if self.auth.location == "query":
                params[self.auth.name] = self.secret
                return
            headers[self.auth.name] = (
                f"{self.auth.scheme} {self.secret}" if self.auth.scheme else self.secret
            )

    mod.AuthSpec = AuthSpec
    mod.Connection = Connection

    mod.ToolDef = ToolDef
    mod.SettingsTab = SettingsTab
    mod.PluginManifest = PluginManifest
    mod.PluginContext = PluginContext
    mod.LunaPlugin = LunaPlugin
    mod.get_current_user = get_current_user
    sys.modules["luna_sdk"] = mod


_install_luna_sdk_stub()


# ---------------------------------------------------------------------- fakes


class _Cred:
    def __init__(self, value: str) -> None:
        self.value = value


class FakeVault:
    """Dict-backed stand-in for the vault provider."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.gateway_connection = None  # set by tests to simulate a granted/gateway key

    async def connect(self, slug: str, *, upstream_default: str, auth=None, credential_name=None):
        return self.gateway_connection

    async def get_credential(self, key: str) -> _Cred:
        if key not in self.data:
            raise KeyError(key)
        return _Cred(self.data[key])

    async def store_credential(self, key: str, value: str, *, kind: str = "api_key") -> None:
        self.data[key] = value

    async def delete_credential(self, key: str) -> None:
        if key not in self.data:
            raise KeyError(key)
        del self.data[key]


class FakeAgent:
    """Records run_turn calls; returns a canned two-sentence reply."""

    def __init__(self, reply: str = "Hello there. All systems look good!") -> None:
        self.reply = reply
        self.calls: list[dict[str, Any]] = []

    async def run_turn(self, prompt: str, **kw: Any):
        self.calls.append({"prompt": prompt, **kw})
        return (self.reply, None)


@dataclass
class _RegisteredTool:
    name: str
    policy: str = "auto_approve"
    risk_level: str = "low"
    description: str = ""
    parameters: dict | None = None
    skill_gated: bool = False
    handler: Any = None


class FakeToolRegistry:
    def __init__(self, tools: list[_RegisteredTool] | None = None) -> None:
        self.tools = tools if tools is not None else [
            _RegisteredTool("get_weather"),
            _RegisteredTool("list_files"),
            _RegisteredTool("delete_everything", policy="prompt_always", risk_level="high"),
            _RegisteredTool("restart_service", policy="ask", risk_level="medium"),
            _RegisteredTool("send_chat_message"),
        ]

    def all(self) -> list[_RegisteredTool]:
        return list(self.tools)

    def get(self, name: str) -> _RegisteredTool:
        for t in self.tools:
            if t.name == name:
                return t
        raise KeyError(name)


class FakeCtx:
    def __init__(self) -> None:
        self.plugin_name = "plugin-voice"
        self.vault = FakeVault()
        self.agent = FakeAgent()
        self.tool_registry = FakeToolRegistry()


@pytest.fixture()
def ctx() -> FakeCtx:
    return FakeCtx()


@pytest.fixture()
def app(ctx, monkeypatch):
    """A real FastAPI app with plugin-voice's routes mounted — the dojo surface."""
    from fastapi import FastAPI

    from plugin_voice.state import reset_task_manager

    from plugin_voice import routes as routes_module

    reset_task_manager()
    application = FastAPI()
    routes_module.register_routes(application, ctx)
    yield application
    reset_task_manager()


@pytest.fixture()
def client(app):
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        yield c


class FakeRT:
    """Stands in for openai_realtime.RealtimeClient — no network.

    Both setup (key probe) and routes (/rt/session mint) construct the client
    via the `openai_realtime` module attribute, so one patch covers both.
    """

    instances: list["FakeRT"] = []
    fail_mint = False           # raise RealtimeError from mint_client_secret
    minted: list[dict] = []     # recorded session configs

    def __init__(self, api_key: str | None = None, **kw):
        self.api_key = api_key
        self.kwargs = kw
        self.closed = False
        FakeRT.instances.append(self)

    async def mint_client_secret(self, session: dict):
        if FakeRT.fail_mint:
            from plugin_voice.openai_realtime import RealtimeError

            raise RealtimeError("OpenAI rejected the key (HTTP 401) — check it in Settings → Voice")
        FakeRT.minted.append(session)
        return {"value": "ek_test_secret", "expires_at": 4102444800, "session": session}

    async def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def _patch_realtime(monkeypatch):
    from plugin_voice import openai_realtime as rt_module

    FakeRT.instances = []
    FakeRT.fail_mint = False
    FakeRT.minted = []
    monkeypatch.setattr(rt_module, "RealtimeClient", FakeRT)
    # a developer's real key must never leak into the resolve chain under test
    monkeypatch.delenv("LUNA_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
