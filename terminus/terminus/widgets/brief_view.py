"""Brief view: the morning brief (email + finance news + today), as Markdown."""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Markdown, Static

from terminus.theme import CYAN, MUTED, RED

EMPTY = """\
_No brief yet today._ It's written automatically on the first launch after the
configured time, or press **g** to make one now.
"""


class BriefView(Vertical):
    BINDINGS = [Binding("g", "app.make_brief", "Regenerate")]

    def compose(self) -> ComposeResult:
        yield Static(id="brief-title", classes="view-title")
        yield Static(id="brief-status")
        with VerticalScroll(id="brief-scroll"):
            yield Markdown(EMPTY, id="brief-body")

    @property
    def body(self) -> Markdown:
        return self.query_one("#brief-body", Markdown)

    def set_title(self, text: str) -> None:
        self.query_one("#brief-title", Static).update(text)

    def set_status(self, message: str, *, busy: bool = False, error: bool = False) -> None:
        style = RED if error else (f"italic {CYAN}" if busy else MUTED)
        self.query_one("#brief-status", Static).update(Text(message, style=style))

    def show(self, markdown: str) -> None:
        self.body.update(markdown)
