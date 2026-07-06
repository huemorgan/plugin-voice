"""Manifest sanity: toml ⇄ PluginManifest sync, widget + settings tab declared."""

from __future__ import annotations

import tomllib
from pathlib import Path

PKG = Path(__file__).parent.parent / "plugin_voice"


def _toml() -> dict:
    with open(PKG / "luna-plugin.toml", "rb") as f:
        return tomllib.load(f)


def _manifest():
    from plugin_voice import VoicePlugin

    return VoicePlugin.manifest


def test_toml_and_manifest_agree():
    toml, manifest = _toml(), _manifest()
    assert toml["name"] == manifest.name == "plugin-voice"
    assert toml["version"] == manifest.version == "0.3.4"
    assert toml["entry"] == "plugin_voice"
    assert toml["description"] == manifest.description


def test_vault_dependency_declared_everywhere():
    toml, manifest = _toml(), _manifest()
    assert "plugin-vault" in toml["requires"]["depends_on"]
    assert "plugin-vault" in manifest.depends_on


def test_tools_declared_in_toml():
    toml = _toml()
    assert toml["requires"]["tools"] == 2
    assert {t["name"] for t in toml["tools"]} == {"voice_status", "voice_connect"}
    by_name = {t["name"]: t for t in toml["tools"]}
    assert by_name["voice_status"]["policy"] == "auto_approve"
    assert by_name["voice_connect"]["policy"] == "ask"


def test_widget_declared():
    """The sidebar talk widget is declared in the manifest."""
    widgets = _manifest().widgets
    assert len(widgets) == 1
    w = widgets[0]
    get = w.get if isinstance(w, dict) else lambda k, d=None: getattr(w, k, d)
    assert get("id") == "voice"
    assert get("slot") == "sidebar.bottom"
    assert get("label")
    assert get("height", 0) > 0


def test_settings_tab_declared():
    tabs = _manifest().settings_tabs
    assert len(tabs) == 1
    assert tabs[0].id == "voice"
    assert tabs[0].iframe_src == "/api/p/plugin-voice/ui/settings/"


def test_widget_files_shipped():
    """The widget's static files exist inside the package (survive packaging)."""
    widget_dir = PKG / "ui" / "widgets" / "voice"
    assert (widget_dir / "index.html").is_file()
    assert (widget_dir / "elevenlabs-client.js").is_file()
    assert (PKG / "ui" / "settings" / "index.html").is_file()


def test_no_luna_core_imports():
    """SDK-only rule: no `import luna.` anywhere in the package."""
    for py in PKG.rglob("*.py"):
        source = py.read_text(encoding="utf-8")
        assert "import luna." not in source and "from luna." not in source, py
