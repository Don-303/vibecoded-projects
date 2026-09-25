"""Terminus app: wires the database, scheduler and widgets together."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import ContentSwitcher, Footer, Input, OptionList, Static

from terminus.config import Config
from terminus.db import Database
from terminus.models import DayPlan, Task
from terminus.quickadd import ParsedTask, parse_quickadd
from terminus.scheduler import plan_day
from terminus.theme import GREEN, MUTED, RED, TERMINUS_THEME
from terminus.todoist import TodoistError, TodoistSync
from terminus.widgets import Sidebar, TaskModal, TasksView, TodayView
from terminus.widgets.timeline import fmt_duration

#: Actions that make no sense while a modal (e.g. the task form) is open.
MAIN_SCREEN_ACTIONS = {
    "show", "new_task", "toggle_done", "toggle_focus", "agent",
    "edit_task", "delete_task", "move_task", "quit", "sync", "do_today",
}  # fmt: skip


def placeholder(view_id: str, title: str, body: str) -> Vertical:
    return Vertical(
        Static(title, classes="view-title"),
        Static(body, classes="placeholder"),
        id=view_id,
    )


class TerminusApp(App):
    CSS_PATH = "styles.tcss"
    TITLE = "terminus"

    BINDINGS = [
        Binding("1", "show('today')", "Today", show=False),
        Binding("2", "show('tasks')", "Tasks", show=False),
        Binding("3", "show('stats')", "Stats", show=False),
        Binding("4", "show('agent')", "Agent", show=False),
        Binding("n", "new_task", "New"),
        Binding("space", "toggle_done", "Done"),
        Binding("f", "toggle_focus", "Focus"),
        Binding("F", "toggle_focus(True)", "Free focus", show=False),
        Binding("slash", "agent", "Agent"),
        Binding("r", "sync", "Sync"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(
        self,
        config: Config | None = None,
        db: Database | None = None,
        clock: Callable[[], datetime] = datetime.now,
        todoist: TodoistSync | None = None,
    ) -> None:
        super().__init__()
        self.register_theme(TERMINUS_THEME)  # before CSS loads: it defines $muted/$faint
        self.theme = "terminus"
        self.config = config or Config.from_env()
        self.db = db or Database(self.config.db_path)
        self.clock = clock
        self.plan = DayPlan([], [])
        self._last_minute: datetime | None = None
        self._day = self.now().date()
        self._pomos_notified = 0
        self._pending_delete: tuple[int, datetime] | None = None
        self.todoist = todoist
        if todoist is None and self.config.todoist_token:
            self.todoist = TodoistSync(
                self.db,
                self.config.todoist_token,
                project_name=self.config.todoist_project,
                pomodoro=self.config.pomodoro,
                clock=self.now,
            )
        self._syncing = False
        self._sync_again = False

    # ── layout ───────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        with Horizontal(id="main"):
            yield Sidebar(id="sidebar")
            with ContentSwitcher(initial="today", id="content"):
                yield TodayView(id="today")
                yield TasksView(id="tasks")
                yield placeholder(
                    "stats",
                    "Stats",
                    "Weekly deep-work hours and streak history arrive in v2.\n\n"
                    "Focus sessions are already being logged, so v2 will have your history.",
                )
                yield placeholder(
                    "agent",
                    "AI Agent",
                    "Chat with your remote Hermes agent here in v2.\n\n"
                    "It will see today's plan and can propose task changes —\n"
                    "nothing is applied until you confirm.",
                )
        yield Footer()

    def on_mount(self) -> None:
        moved = self.db.carry_over(self._day)
        if moved:
            self.notify(f"Carried {moved} unfinished task(s) over to today.")
        if active := self.db.active_session():  # resumed after a restart
            self._pomos_notified = int(active.elapsed(self.now()) / self.config.pomodoro)
        self.refresh_all()
        self.set_interval(1, self._tick)
        if self.todoist:
            self.set_interval(self.config.todoist_interval, self.request_sync)
            self.request_sync()

    # ── state ────────────────────────────────────────────────────────────

    def now(self) -> datetime:
        return self.clock().replace(microsecond=0)

    @property
    def today_view(self) -> TodayView:
        return self.query_one(TodayView)

    @property
    def tasks_view(self) -> TasksView:
        return self.query_one(TasksView)

    @property
    def current_view(self) -> str:
        return self.query_one(ContentSwitcher).current or "today"

    def refresh_all(self) -> None:
        """Re-read the database, re-plan the day and redraw everything."""
        now = self.now()
        cfg = self.config
        tasks = self.db.tasks_for_day(self._day)
        worked = self.db.worked_by_task([t.id for t in tasks], now)
        self.plan = plan_day(
            tasks,
            day=self._day,
            now=now,
            worked=worked,
            active=self.db.active_session(),
            day_start=cfg.day_start,
            day_end=cfg.day_end,
            pomodoro=cfg.pomodoro,
        )
        start = datetime.combine(self._day, cfg.day_start)
        end = datetime.combine(self._day, cfg.day_end)
        active = self.db.active_session()
        free = active.elapsed(now) if active and active.task_id is None else None
        self.today_view.update_plan(self.plan, now, start, end, free)
        self.tasks_view.update_tasks(tasks, self.plan, worked, cfg.pomodoro)
        self._update_status(now)

    def _update_status(self, now: datetime) -> None:
        active = self.db.active_session()
        task = self.db.get_task(active.task_id) if active and active.task_id else None
        left, number = None, 0
        if active:
            elapsed = active.elapsed(now)
            number = int(elapsed / self.config.pomodoro) + 1
            left = self.config.pomodoro - elapsed % self.config.pomodoro
        self.query_one(Sidebar).update_status(
            now=now,
            streak=self.db.streak(now.date()),
            focus_today=self.db.focus_time_on(now.date(), now),
            focus_task=(task.title if task else "Free focus") if active else None,
            pomodoro_left=left,
            pomodoro_number=number,
            sync_line=self._sync_line(),
        )

    def _sync_line(self) -> Text | None:
        sync = self.todoist
        if sync is None:
            return None
        line = Text("todoist  ", style=MUTED)
        pending = sync.pending()
        if self._syncing:
            line.append("syncing…", style=MUTED)
        elif sync.last_error:
            line.append("✗ offline" if "offline" in sync.last_error else "✗ error", style=RED)
        elif sync.last_synced:
            line.append(f"✓ {sync.last_synced:%H:%M}", style=GREEN)
        else:
            line.append("waiting", style=MUTED)
        if pending:
            line.append(f" · {pending} queued", style=MUTED)
        return line

    def _tick(self) -> None:
        """Runs every second: live timer, minute re-plan, midnight rollover."""
        now = self.now()
        if now.date() != self._day:
            self._day = now.date()
            self.db.carry_over(self._day)
        minute = now.replace(second=0)
        active = self.db.active_session()
        if minute != self._last_minute:
            self._last_minute = minute
            self.refresh_all()
        elif active:
            self._update_status(now)
        if active:
            completed = int(active.elapsed(now) / self.config.pomodoro)
            if completed > self._pomos_notified:
                self._pomos_notified = completed
                self.bell()
                self.notify(
                    f"Pomodoro {completed} done — take a short break (f to pause).",
                    title="Focus",
                    timeout=10,
                )

    def selected_task(self) -> Task | None:
        """The task an action applies to: table selection, or the timeline's focus."""
        if self.current_view == "tasks":
            task_id = self.tasks_view.selected_task_id()
        else:
            block = self.plan.running or self.plan.up_next
            task_id = block.task.id if block else None
        return self.db.get_task(task_id) if task_id is not None else None

    # ── todoist ──────────────────────────────────────────────────────────

    def request_sync(self) -> None:
        """Sync with Todoist in the background; coalesces overlapping requests."""
        if self.todoist is None:
            return
        if self._syncing:
            self._sync_again = True
            return
        self._syncing = True
        self.run_worker(self._run_sync(), group="todoist", exit_on_error=False)

    async def _run_sync(self) -> None:
        assert self.todoist is not None
        try:
            while True:
                self._sync_again = False
                self._update_status(self.now())
                try:
                    result = await self.todoist.sync()
                    for error in result.errors:
                        self.notify(f"Todoist: {error}", severity="warning")
                except TodoistError as exc:
                    self.log.warning(f"todoist sync failed: {exc}")
                except Exception as exc:  # never let a sync bug take the planner down
                    self.todoist.last_error = str(exc)
                    self.notify(f"Todoist sync crashed: {exc}", severity="error")
                    break
                if not self._sync_again:
                    break
        finally:
            self._syncing = False
            self.refresh_all()

    def action_sync(self) -> None:
        self.request_sync()

    def _pushed(self, queue: Callable[..., None], *args) -> None:
        """Queue a change for Todoist (if enabled) and sync it right away."""
        if self.todoist is not None:
            queue(self.todoist, *args)
            self.request_sync()

    # ── actions ──────────────────────────────────────────────────────────

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        if action in MAIN_SCREEN_ACTIONS and isinstance(self.screen, ModalScreen):
            return False
        if action == "sync" and self.todoist is None:
            return False
        return True

    def action_show(self, view: str) -> None:
        self.query_one(ContentSwitcher).current = view
        self.query_one(Sidebar).select(view)
        if view == "tasks":
            self.tasks_view.focus_table()
        elif view == "today":
            self.query_one("#timeline-scroll").focus()

    def action_agent(self) -> None:
        self.action_show("agent")

    def action_new_task(self) -> None:
        self.push_screen(TaskModal(), self._add_parsed)

    def _add_parsed(self, parsed: ParsedTask | None) -> None:
        if parsed is None:
            return
        task_id = self.db.add_task(
            parsed.title,
            self._day,
            estimate=parsed.estimate,
            tags=parsed.tags,
            fixed_start=parsed.fixed_start,
            now=self.now(),
        )
        self._pushed(TodoistSync.queue_add, task_id)
        self.refresh_all()
        if parsed.fixed_start is None and any(t.title == parsed.title for t in self.plan.overflow):
            self.notify(f"Added “{parsed.title}” — it won't fit today.", severity="warning")
        else:
            self.notify(f"Added “{parsed.title}”.")

    def action_edit_task(self) -> None:
        task = self.selected_task()
        if task is None:
            return

        def save(parsed: ParsedTask | None) -> None:
            if parsed is not None:
                self.db.update_task(
                    task.id,
                    title=parsed.title,
                    estimate=parsed.estimate,
                    tags=parsed.tags,
                    fixed_start=parsed.fixed_start,
                )
                if parsed.fixed_start and task.backlogged:
                    self.db.set_overdue(task.id, None)  # pinned to a time today → on the timeline
                self._pushed(TodoistSync.queue_update, task, self.db.get_task(task.id))
                self.refresh_all()

        self.push_screen(TaskModal(task), save)

    def action_delete_task(self) -> None:
        task = self.selected_task()
        if task is None:
            return
        now = self.now()
        pending = self._pending_delete
        if pending and pending[0] == task.id and now - pending[1] < timedelta(seconds=4):
            self._pending_delete = None
            active = self.db.active_session()
            if active and active.task_id == task.id:
                self.db.stop_session(now)  # its logged time is kept as free focus
            self._pushed(TodoistSync.queue_delete, task.id)  # before the row (and its id) goes
            self.db.delete_task(task.id)
            self.notify(f"Deleted “{task.title}”.")
            self.refresh_all()
        else:
            self._pending_delete = (task.id, now)
            self.notify(f"Press d again to delete “{task.title}”.", severity="warning", timeout=4)

    def action_do_today(self) -> None:
        """Pull an overdue/undated task onto today's timeline (due today in Todoist too)."""
        task = self.selected_task()
        if task is None or not task.backlogged:
            self.notify("Only overdue tasks need this — others are already on today.")
            return
        self.db.set_overdue(task.id, None)
        self._pushed(TodoistSync.queue_reschedule, task.id)
        self.notify(f"“{task.title}” is on today's plan.")
        self.refresh_all()

    def action_move_task(self, offset: int) -> None:
        if self.tasks_view.sort != "plan":
            self.notify("Switch sort back to 'plan' (o) to reorder.", severity="warning")
            return
        task = self.selected_task()
        if task is None or task.done or task.fixed:
            return
        # Swap with the nearest floating, unfinished neighbour so the move is visible.
        order = [t for t in self.db.tasks_for_day(self._day) if not t.done and not t.fixed]
        ids = [t.id for t in order]
        i = ids.index(task.id)
        j = i + offset
        if 0 <= j < len(ids):
            all_ids = [t.id for t in self.db.tasks_for_day(self._day)]
            self.db.move_task(task.id, all_ids.index(ids[j]) - all_ids.index(task.id))
            self.refresh_all()

    def action_toggle_done(self) -> None:
        task = self.selected_task()
        if task is None:
            return
        now = self.now()
        if task.done:
            self.db.set_done(task.id, False, now)
            self._pushed(TodoistSync.queue_done, task.id, False)
            self.notify(f"Reopened “{task.title}”.")
        else:
            active = self.db.active_session()
            if active and active.task_id == task.id:
                self.db.stop_session(now)
            self.db.set_done(task.id, True, now)
            self._pushed(TodoistSync.queue_done, task.id, True)
            self.notify(f"Done: “{task.title}”. Remaining blocks re-planned.")
        self.refresh_all()

    def action_toggle_focus(self, free: bool = False) -> None:
        """Pause a running session, or start one.

        ``f`` focuses the selected/next task, falling back to free focus when
        there is none; ``F`` always starts free focus (no task attached).
        """
        now = self.now()
        stopped = self.db.stop_session(now)
        if stopped:
            self.notify(f"Focus paused — {fmt_duration(stopped.elapsed(now))} logged.")
        else:
            task = None if free else self.selected_task()
            if task is not None and task.done:
                self.notify("That task is already done — F starts free focus.", severity="warning")
                return
            self.db.start_session(task.id if task else None, now)
            self._pomos_notified = 0
            self.notify(f"Focusing on “{task.title}”." if task else "Free focus started.", title="Focus")
        self.refresh_all()

    # ── events ───────────────────────────────────────────────────────────

    @on(OptionList.OptionSelected, "#nav")
    def _nav_selected(self, event: OptionList.OptionSelected) -> None:
        self.action_show(event.option.id)

    @on(Input.Submitted, "#quick-add")
    def _quick_add(self, event: Input.Submitted) -> None:
        if not event.value.strip():
            return
        try:
            parsed = parse_quickadd(event.value)
        except ValueError as exc:
            self.notify(str(exc), severity="error")
            return
        event.input.clear()
        self._add_parsed(parsed)


def main() -> None:
    TerminusApp().run()


if __name__ == "__main__":
    main()
