"""Modal form for creating or editing a task."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static

from terminus.models import Task
from terminus.quickadd import ParsedTask, parse_time


class TaskModal(ModalScreen[ParsedTask | None]):
    """Returns a ParsedTask on save, or None on cancel."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, task: Task | None = None) -> None:
        super().__init__()
        self.editing = task

    def compose(self) -> ComposeResult:
        t = self.editing
        with Vertical(id="task-modal"):
            yield Static("Edit task" if t else "New task", classes="view-title")
            yield Label("Title")
            yield Input(t.title if t else "", placeholder="What needs doing?", id="m-title")
            yield Label("Tags (space separated)")
            yield Input(" ".join(t.tags) if t else "", placeholder="work deep", id="m-tags")
            with Horizontal(id="m-row"):
                with Vertical():
                    yield Label("Pomodoros")
                    yield Input(str(t.estimate) if t else "1", type="integer", id="m-estimate")
                with Vertical():
                    yield Label("Pinned start (optional)")
                    yield Input(
                        f"{t.fixed_start:%H:%M}" if t and t.fixed_start else "",
                        placeholder="14:30",
                        id="m-fixed",
                    )
            yield Static(id="m-error")
            with Horizontal(id="m-buttons"):
                yield Button("Save", variant="primary", id="m-save")
                yield Button("Cancel", id="m-cancel")

    def _value(self, id: str) -> str:
        return self.query_one(f"#{id}", Input).value.strip()

    @on(Button.Pressed, "#m-save")
    @on(Input.Submitted)
    def save(self) -> None:
        error = self.query_one("#m-error", Static)
        title = self._value("m-title")
        if not title:
            error.update("Title is required.")
            return
        try:
            fixed = parse_time(self._value("m-fixed")) if self._value("m-fixed") else None
        except ValueError as exc:
            error.update(str(exc))
            return
        estimate = max(1, int(self._value("m-estimate") or 1))
        tags = [t.lstrip("#").lower() for t in self._value("m-tags").replace(",", " ").split()]
        self.dismiss(ParsedTask(title=title, tags=tags, estimate=estimate, fixed_start=fixed))

    @on(Button.Pressed, "#m-cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)
