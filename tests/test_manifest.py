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
    assert toml["version"] == manifest.version == "0.7.0"
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
    """ONE shell tab; the Setup/Persona switch is inside the page (004.1)."""
    tabs = _manifest().settings_tabs
    assert len(tabs) == 1
    assert tabs[0].id == "voice"
    assert tabs[0].iframe_src == "/api/p/plugin-voice/ui/settings/"


def test_widget_files_shipped():
    """The widget's static files exist inside the package (survive packaging)."""
    widget_dir = PKG / "ui" / "widgets" / "voice"
    assert (widget_dir / "index.html").is_file()
    assert (widget_dir / "rt-client.js").is_file()
    assert not (widget_dir / "elevenlabs-client.js").exists()
    assert (PKG / "ui" / "settings" / "index.html").is_file()
    assert (PKG / "ui" / "settings" / "persona" / "index.html").is_file()


def test_luna_view_section_declared():
    """006: the Luna View pane is a hidden sidebar section (dict — the SDK
    doesn't re-export SidebarSection; pydantic validates dicts fine and old
    cores that lack `hidden`/`path` fields simply ignore them)."""
    sections = _manifest().sidebar_sections
    assert len(sections) == 1
    s = sections[0]
    get = s.get if isinstance(s, dict) else lambda k, d=None: getattr(s, k, d)
    assert get("id") == "luna-view"
    assert get("label")
    assert get("path") == "ui/view/"
    assert get("hidden") is True


def test_view_files_shipped():
    """006: the Luna View pane's static files exist inside the package."""
    view_dir = PKG / "ui" / "view"
    assert (view_dir / "index.html").is_file()
    # The 24k-point head model the avatar morphs to (float32 layout, N*7 values).
    assert (view_dir / "head.bin").stat().st_size == 24000 * 7 * 4


def test_view_uses_shared_rt_client():
    """006: the pane must reuse the widget's rt-client.js, not carry a copy."""
    html = (PKG / "ui" / "view" / "index.html").read_text(encoding="utf-8")
    assert '/api/p/plugin-voice/ui/widgets/voice/rt-client.js' in html
    assert not (PKG / "ui" / "view" / "rt-client.js").exists()


def test_no_luna_core_imports():
    """SDK-only rule: no `import luna.` anywhere in the package."""
    for py in PKG.rglob("*.py"):
        source = py.read_text(encoding="utf-8")
        assert "import luna." not in source and "from luna." not in source, py
