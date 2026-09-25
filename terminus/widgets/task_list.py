"""Tasks view: filterable, sortable table with a quick-add bar."""

from __future__ import annotations

from datetime import timedelta

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import DataTable, Input, Static

from terminus.models import BlockState, DayPlan, Task
from terminus.theme import AMBER, CYAN, GREEN, MUTED, STATE_COLOR, STATE_ICON

SORTS = ["plan", "title", "estimate", "tag"]


def pips(worked: timedelta, estimate: int, pomodoro: timedelta) -> Text:
    done = min(int(worked / pomodoro), estimate)
    return Text.assemble(("●" * done, GREEN), ("○" * (estimate - done), MUTED))


class TasksView(Vertical):
    BINDINGS = [
        Binding("e", "app.edit_task", "Edit"),
        Binding("d", "app.delete_task", "Delete"),
        Binding("shift+up,K", "app.move_task(-1)", "Move up", show=False),
        Binding("shift+down,J", "app.move_task(1)", "Move down", show=False),
        Binding("o", "cycle_sort", "Sort"),
        Binding("s", "focus_filter", "Search"),
        Binding("a", "focus_quickadd", "Quick add"),
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.sort = "plan"
        self._tasks: list[Task] = []
        self._plan = DayPlan([], [])
        self._worked: dict[int, timedelta] = {}
        self._pomodoro = timedelta(minutes=25)

    def compose(self) -> ComposeResult:
        yield Static("Tasks", id="tasks-title", classes="view-title")
        yield Input(placeholder="search  (title or #tag)   — esc to leave", id="task-filter")
        table = DataTable(id="task-table", cursor_type="row", zebra_stripes=False)
        table.add_column(" ", key="state", width=2)
        table.add_column("Task", key="title")
        table.add_column("Tags", key="tags")
        table.add_column("Pomodoros", key="pomos")
        table.add_column("Slot", key="slot")
        yield table
        yield Input(
            placeholder="quick add:  Write report #work ~2 @14:30   (enter to add)",
            id="quick-add",
        )

    # ── data ─────────────────────────────────────────────────────────────

    def update_tasks(
        self,
        tasks: list[Task],
        plan: DayPlan,
        worked: dict[int, timedelta],
        pomodoro: timedelta,
    ) -> None:
        self._tasks, self._plan, self._worked, self._pomodoro = tasks, plan, worked, pomodoro
        self._rebuild()

    def selected_task_id(self) -> int | None:
        table = self.query_one(DataTable)
        if table.row_count == 0:
            return None
        row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
        return int(row_key.value)

    def _visible(self) -> list[Task]:
        query = self.query_one("#task-filter", Input).value.strip().lower()
        tasks = self._tasks
        if query:
            if query.startswith("#"):
                tasks = [t for t in tasks if any(tag.startswith(query[1:]) for tag in t.tags)]
            else:
                tasks = [t for t in tasks if query in t.title.lower() or query in t.tags]
        return sorted(tasks, key=self._sort_key())

    def _sort_key(self):
        if self.sort == "title":
            return lambda t: (t.done, t.title.lower())
        if self.sort == "estimate":
            return lambda t: (t.done, -t.estimate, t.position)
        if self.sort == "tag":
            return lambda t: (t.done, t.tags[0] if t.tags else "~", t.position)
        # plan: done first (they happened), then timeline order, then overflow.
        order = {b.task.id: i for i, b in enumerate(self._plan.blocks)}
        return lambda t: order.get(t.id, len(order) + t.position)

    def _rebuild(self) -> None:
        table = self.query_one(DataTable)
        keep = self.selected_task_id()
        table.clear()
        overflow = {t.id for t in self._plan.overflow}
        for t in self._visible():
            block = self._plan.block_for(t.id)
            if block:
                color = STATE_COLOR[block.state]
                icon = Text(STATE_ICON[block.state], style=color)
                slot = Text(f"{block.start:%H:%M}–{block.end:%H:%M}", style=MUTED)
                if block.state is BlockState.RUNNING:
                    slot.stylize(CYAN)
            elif t.id in overflow:
                color, icon, slot = AMBER, Text("»", style=AMBER), Text("tomorrow", style=AMBER)
            else:
                color, icon, slot = MUTED, Text(" "), Text("")
            title = Text(t.title, style=f"strike {MUTED}" if t.done else color)
            if t.fixed:
                title.append(f"  @{t.fixed_start:%H:%M}", style=MUTED)
            tags = Text(" ".join(f"#{tag}" for tag in t.tags), style=MUTED)
            pomos = pips(self._worked.get(t.id, timedelta()), t.estimate, self._pomodoro)
            table.add_row(icon, title, tags, pomos, slot, key=str(t.id))
        if keep is not None:
            try:
                table.move_cursor(row=table.get_row_index(str(keep)), animate=False)
            except Exception:  # row filtered out or deleted
                pass
        self.query_one("#tasks-title", Static).update(
            Text.assemble(("Tasks", "bold"), (f"   sorted by {self.sort}", MUTED))
        )

    # ── interaction ──────────────────────────────────────────────────────

    def action_cycle_sort(self) -> None:
        self.sort = SORTS[(SORTS.index(self.sort) + 1) % len(SORTS)]
        self._rebuild()

    def action_focus_filter(self) -> None:
        self.query_one("#task-filter", Input).focus()

    def action_focus_quickadd(self) -> None:
        self.query_one("#quick-add", Input).focus()

    def focus_table(self) -> None:
        self.query_one(DataTable).focus()

    @on(Input.Changed, "#task-filter")
    def _filter_changed(self) -> None:
        self._rebuild()

    @on(Input.Submitted, "#task-filter")
    def _filter_done(self) -> None:
        self.focus_table()

    def on_key(self, event) -> None:
        if event.key == "escape" and isinstance(self.app.focused, Input):
            event.stop()
            self.focus_table()
