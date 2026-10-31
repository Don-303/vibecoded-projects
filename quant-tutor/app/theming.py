"""Match the app's Textual theme to the desktop's current theme.

Detection order:
1. Omarchy's active theme (~/.config/omarchy/current/theme.name): use the
   same-named Textual builtin if one exists, else a close relative from
   RENAMES, else fall back to plain light/dark using Omarchy's light.mode
   marker file.
2. No Omarchy: ask gsettings for the desktop color-scheme preference.
3. Anything failing: textual-dark.

The app re-runs this every few seconds, so switching the system theme
restyles the tutor live.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

OMARCHY_THEME_NAME = Path.home() / ".config" / "omarchy" / "current" / "theme.name"
OMARCHY_LIGHT_MARKER = Path.home() / ".config" / "omarchy" / "current" / "theme" / "light.mode"

# Omarchy theme names -> closest Textual builtin, where the names differ.
# Same-named themes (nord, gruvbox, dracula, tokyo-night, ...) need no entry.
RENAMES = {
    "catppuccin": "catppuccin-mocha",
    "catppuccin-dark": "catppuccin-mocha",
    "everforest": "gruvbox",
    "kanagawa": "tokyo-night",
    "monokai-dark": "monokai",
    "one-dark-pro": "atom-one-dark",
    "ristretto": "monokai",
    "rose-pine": "rose-pine-dawn",  # omarchy's rose-pine is the light variant
    "solarized": "solarized-dark",
}


def system_theme(builtin: set[str]) -> str:
    """The Textual theme name that best matches the current desktop theme."""
    name = _omarchy_theme_name()
    if name:
        if name in builtin:
            return name
        renamed = RENAMES.get(name)
        if renamed in builtin:
            return renamed
        return "textual-light" if OMARCHY_LIGHT_MARKER.exists() else "textual-dark"
    return "textual-light" if _gsettings_prefers_light() else "textual-dark"


def _omarchy_theme_name() -> str | None:
    try:
        return OMARCHY_THEME_NAME.read_text().strip() or None
    except OSError:
        return None


def _gsettings_prefers_light() -> bool:
    try:
        out = subprocess.run(
            ["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
            capture_output=True,
            text=True,
            timeout=2,
        ).stdout
    except Exception:
        return False
    # GNOME's 'default' means no dark preference, i.e. light.
    return "prefer-dark" not in out
