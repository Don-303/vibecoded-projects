"""The calm dark palette, shared by TCSS (via the Textual theme) and Rich markup."""

from textual.theme import Theme

from terminus.models import BlockState

BG = "#17181b"
SURFACE = "#1e1f23"
PANEL = "#26282d"
FG = "#c8ccd2"
MUTED = "#6b717b"
FAINT = "#3a3d44"
GREEN = "#8fbc8f"
CYAN = "#7fb4ca"
BLUE = "#7e9cd8"
AMBER = "#d6b36a"
RED = "#c98b8b"

STATE_COLOR = {
    BlockState.DONE: GREEN,
    BlockState.RUNNING: CYAN,
    BlockState.NEXT: FG,
    BlockState.MISSED: RED,
}

STATE_ICON = {
    BlockState.DONE: "✓",
    BlockState.RUNNING: "▶",
    BlockState.NEXT: "·",
    BlockState.MISSED: "✗",
}

TERMINUS_THEME = Theme(
    name="terminus",
    primary=CYAN,
    secondary=BLUE,
    accent=CYAN,
    success=GREEN,
    warning=AMBER,
    error=RED,
    foreground=FG,
    background=BG,
    surface=SURFACE,
    panel=PANEL,
    dark=True,
    variables={
        "muted": MUTED,
        "faint": FAINT,
        "footer-key-foreground": CYAN,
        "input-selection-background": f"{CYAN} 30%",
    },
)
