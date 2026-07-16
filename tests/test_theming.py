"""Tests for desktop-theme detection."""
from app import theming

BUILTIN = {
    "textual-dark",
    "textual-light",
    "nord",
    "gruvbox",
    "tokyo-night",
    "catppuccin-mocha",
    "catppuccin-latte",
    "rose-pine-dawn",
    "monokai",
}


def _set_omarchy(monkeypatch, tmp_path, name: str | None, light: bool = False):
    theme_name = tmp_path / "theme.name"
    marker = tmp_path / "light.mode"
    if name is not None:
        theme_name.write_text(name + "\n")
    if light:
        marker.write_text("")
    monkeypatch.setattr(theming, "OMARCHY_THEME_NAME", theme_name)
    monkeypatch.setattr(theming, "OMARCHY_LIGHT_MARKER", marker)


def test_same_named_omarchy_theme_used_directly(monkeypatch, tmp_path):
    _set_omarchy(monkeypatch, tmp_path, "nord")
    assert theming.system_theme(BUILTIN) == "nord"


def test_renamed_omarchy_theme_maps_to_relative(monkeypatch, tmp_path):
    _set_omarchy(monkeypatch, tmp_path, "catppuccin")
    assert theming.system_theme(BUILTIN) == "catppuccin-mocha"


def test_unknown_dark_omarchy_theme_falls_back_to_dark(monkeypatch, tmp_path):
    _set_omarchy(monkeypatch, tmp_path, "nes")
    assert theming.system_theme(BUILTIN) == "textual-dark"


def test_unknown_light_omarchy_theme_falls_back_to_light(monkeypatch, tmp_path):
    _set_omarchy(monkeypatch, tmp_path, "white", light=True)
    assert theming.system_theme(BUILTIN) == "textual-light"


def test_no_omarchy_uses_color_scheme_preference(monkeypatch, tmp_path):
    _set_omarchy(monkeypatch, tmp_path, None)
    monkeypatch.setattr(theming, "_gsettings_prefers_light", lambda: True)
    assert theming.system_theme(BUILTIN) == "textual-light"
    monkeypatch.setattr(theming, "_gsettings_prefers_light", lambda: False)
    assert theming.system_theme(BUILTIN) == "textual-dark"
