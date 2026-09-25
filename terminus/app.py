"""Terminus app: wires the database, scheduler and widgets together."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.css.query import NoMatches
from textual.containers import Horizontal
from textual.screen import ModalScreen
from textual.widgets import ContentSwitcher, Footer, Input, Markdown, OptionList
from textual.widgets.markdown import MarkdownStream

from terminus import actions, brief, stats
from terminus.agent_client import GMAIL_HINT, HermesClient, HermesError, build_context
from terminus.config import Config
from terminus.db import Database
from terminus.models import DayPlan, Task
from terminus.quickadd import ParsedTask, parse_quickadd
from terminus.scheduler import plan_day
from terminus.theme import CYAN, GREEN, MUTED, RED, TERMINUS_THEME
from terminus.todoist import TodoistError, TodoistSync
from terminus.widgets import (
    AgentConsole,
    BriefView,
    JobModal,
    JobsView,
    StagePicker,
    Sidebar,
    StatsView,
    TaskModal,
    TasksView,
    TodayView,
)
from terminus.widgets.brief_view import EMPTY as BRIEF_EMPTY
from terminus.widgets.proposal_panel import ProposalPanel
from terminus.widgets.timeline import fmt_duration

#: Actions that make no sense while a modal (e.g. the task form) is open.
MAIN_SCREEN_ACTIONS = {
    "show", "new_task", "toggle_done", "toggle_focus", "agent",
    "edit_task", "delete_task", "move_task", "quit", "sync", "do_today", "new_chat",
    "make_brief", "add_job", "edit_job", "stage_job", "open_job_link", "delete_job",
    "scan_job_email",
}  # fmt: skip

#: Chat turns (user + assistant messages) sent to Hermes as history.
CHAT_HISTORY = 20


class TerminusApp(App):
    CSS_PATH = "styles.tcss"
    TITLE = "terminus"

    BINDINGS = [
        Binding("1", "show('today')", "Today", show=False),
        Binding("2", "show('tasks')", "Tasks", show=False),
        Binding("3", "show('stats')", "Stats", show=False),
        Binding("4", "show('agent')", "Agent", show=False),
        Binding("5", "show('brief')", "Brief", show=False),
        Binding("6", "show('jobs')", "Jobs", show=False),
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
        hermes: HermesClient | None = None,
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
        self.hermes = hermes or HermesClient(
            self.config.hermes_host, self.config.hermes_api_key, model=self.config.hermes_model
        )
        self._hermes_online: bool | None = None
        self._chat: list[dict] = []
        self._agent_busy = False
        self._briefing = False
        self._proposal: ProposalPanel | None = None  # awaiting y/n
        self._brief_seen: object = None  # the day whose brief the user has opened

    # ── layout ───────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        with Horizontal(id="main"):
            yield Sidebar(id="sidebar")
            with ContentSwitcher(initial="today", id="content"):
                yield TodayView(id="today")
                yield TasksView(id="tasks")
                yield StatsView(id="stats")
                yield AgentConsole(id="agent")
                yield BriefView(id="brief")
                yield JobsView(id="jobs")
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
        if self.hermes.api_key:
            self.set_interval(60, self.check_hermes)
            self.check_hermes()
        else:
            self.query_one(AgentConsole).show_setup()
        self._update_agent_status()
        self._load_brief()
        self.maybe_brief()

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
        if self.current_view == "stats":
            self.query_one(StatsView).update_stats(stats.compute(self.db, now))
        if self.current_view == "jobs":
            self.query_one(JobsView).update_jobs(self.db.list_jobs(), now)
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
            service_lines=[line for line in (self._sync_line(), self._hermes_line()) if line],
        )

    def _hermes_line(self) -> Text | None:
        if not self.hermes.api_key:
            return None
        line = Text("hermes   ", style=MUTED)
        if self._agent_busy:
            line.append("● thinking…", style=CYAN)
        elif self._hermes_online is None:
            line.append("● checking", style=MUTED)
        elif self._hermes_online:
            line.append("● online", style=GREEN)
        else:
            line.append("● offline", style=RED)
        return line

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
        try:
            self._tick_inner()
        except NoMatches:
            pass  # the UI is being torn down (quitting); nothing left to update

    def _tick_inner(self) -> None:
        now = self.now()
        if now.date() != self._day:
            self._day = now.date()
            self.db.carry_over(self._day)
            self._load_brief()
        minute = now.replace(second=0)
        active = self.db.active_session()
        if minute != self._last_minute:
            self._last_minute = minute
            self.refresh_all()
            self.maybe_brief()
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
        elif view == "stats":
            self.query_one(StatsView).update_stats(stats.compute(self.db, self.now()))
            self.query_one("#stats-scroll").focus()
        elif view == "jobs":
            jobs_view = self.query_one(JobsView)
            jobs_view.update_jobs(self.db.list_jobs(), self.now())
            jobs_view.focus_table()
        elif view == "brief":
            self._brief_seen = self._day
            self._update_brief_banner()
            self.query_one("#brief-scroll").focus()
        elif view == "agent":
            self.query_one(AgentConsole).focus_input()

    def action_agent(self) -> None:
        self.action_show("agent")

    # ── hermes agent ─────────────────────────────────────────────────────

    def check_hermes(self) -> None:
        self.run_worker(self._check_hermes(), group="hermes-health", exclusive=True)

    async def _check_hermes(self) -> None:
        self._hermes_online = await self.hermes.health()
        self._update_agent_status()
        self._update_status(self.now())

    def _update_agent_status(self) -> None:
        self.query_one(AgentConsole).set_status(
            configured=bool(self.hermes.api_key),
            online=self._hermes_online,
            host=self.hermes.base_url,
            busy=self._agent_busy,
        )

    def _agent_context(self, *, actions: bool = False) -> str:
        now = self.now()
        active = self.db.active_session()
        task = self.db.get_task(active.task_id) if active and active.task_id else None
        return build_context(
            self.plan,
            now=now,
            focus_today=self.db.focus_time_on(now.date(), now),
            streak=self.db.streak(now.date()),
            focus_task=(task.title if task else "free focus") if active else None,
            upcoming=self.db.upcoming_tasks(self._day),
            jobs=self.db.list_jobs(),
            actions=actions,
        )

    @on(Input.Submitted, "#agent-input")
    def _agent_submit(self, event: Input.Submitted) -> None:
        if self.send_to_agent(event.value):
            event.input.clear()

    def send_to_agent(self, prompt: str) -> bool:
        """Send a message to Hermes as if typed in the chat. False if not sent."""
        prompt = prompt.strip()
        if not prompt:
            return False
        if self._agent_busy:
            self.notify("Hermes is still answering…", severity="warning")
            return False
        self._clear_pending_proposal()
        self.query_one(AgentConsole).add_user(prompt)
        self._chat.append({"role": "user", "content": prompt})
        self._agent_busy = True
        self.run_worker(self._ask(), group="hermes-chat", exit_on_error=False)
        return True

    async def _ask(self) -> None:
        console = self.query_one(AgentConsole)
        self._update_agent_status()
        self._update_status(self.now())
        reply, activity = console.start_reply()
        # Opened on the first token: stopping a stream whose task never ran raises
        # CancelledError into this worker and skips the cleanup below.
        stream: MarkdownStream | None = None
        answer = ""
        hide = actions.VisibleFilter()  # the proposed-changes block isn't shown as text
        messages = [{"role": "system", "content": self._agent_context(actions=True)}]
        messages += self._chat[-CHAT_HISTORY:]
        try:
            async for event in self.hermes.stream_chat(messages):
                if event.kind == "token":
                    answer += event.text
                    if visible := hide.feed(event.text):
                        stream = stream or Markdown.get_stream(reply)
                        await stream.write(visible)
                else:
                    activity.update(Text(f"⚙ {event.text}", style=MUTED))
                console.log_area.scroll_end(animate=False)
            if rest := hide.flush():
                stream = stream or Markdown.get_stream(reply)
                await stream.write(rest)
            self._hermes_online = True
        except HermesError as exc:
            console.add_error(str(exc))
            if "reach" in str(exc) or "responding" in str(exc):
                self._hermes_online = False
        except Exception as exc:  # a client bug mustn't take the planner down
            console.add_error(f"Unexpected error talking to Hermes: {exc}")
        finally:
            if stream is not None:
                await stream.stop()
            self._agent_busy = False
            self._update_agent_status()
            self._update_status(self.now())
        if answer:
            self._chat.append({"role": "assistant", "content": answer})
            self._offer_changes(answer)
        else:
            self._chat.pop()  # unanswered: don't resend it as history next turn
            reply.remove()

    # ── proposed changes (v3) ────────────────────────────────────────────

    def _clear_pending_proposal(self) -> None:
        if self._proposal is not None:
            self._proposal.decided = True
            self._proposal.resolve("Superseded by your next message — nothing changed.", applied=False)
            self._proposal = None

    def _offer_changes(self, answer: str) -> None:
        """If the reply proposes task changes, check them and ask the user to confirm."""
        _, entries, error = actions.extract(answer)
        console = self.query_one(AgentConsole)
        if error:
            console.add_error(error)
        if not entries:
            return
        known = {t.id: t for t in self.db.tasks_for_day(self._day)}
        known.update({t.id: t for t in self.db.upcoming_tasks(self._day)})
        now = self.now()
        jobs = {j.id: j for j in self.db.list_jobs()}
        proposal = actions.validate(entries, known, today=self._day, now_time=now.time(),
                                    jobs=jobs)
        self._show_proposal(proposal)

    def _show_proposal(self, proposal: actions.Proposal) -> None:
        console = self.query_one(AgentConsole)
        panel = ProposalPanel(proposal)
        console.log_area.mount(panel)
        console.log_area.scroll_end(animate=False)
        if proposal.changes:
            self._proposal = panel
        else:
            panel.resolve("Hermes proposed changes, but none were valid — nothing to apply.",
                          applied=False)

    @on(ProposalPanel.Decided)
    def _changes_decided(self, event: ProposalPanel.Decided) -> None:
        self._proposal = None
        chosen = [event.panel.proposal.changes[i] for i in event.selected] if event.apply else []
        if not chosen:
            event.panel.resolve("Discarded — nothing changed.", applied=False)
            note = "(Terminus: the user discarded these proposed changes.)"
        else:
            done, failed = [], []
            for change in chosen:
                try:
                    self._apply_change(change)
                    done.append(change.summary)
                except LookupError as exc:
                    failed.append(f"{change.summary} ({exc})")
            skipped = len(event.panel.proposal.changes) - len(chosen)
            lines = [f"✓ {s}" for s in done] + [f"✗ {s}" for s in failed]
            if skipped:
                lines.append(f"({skipped} unticked, not applied)")
            event.panel.resolve("\n".join(lines), applied=bool(done))
            note = f"(Terminus: the user applied {len(done)} of {len(event.panel.proposal.changes)} "
            note += "proposed changes.)"
            self.refresh_all()
        if self._chat and self._chat[-1]["role"] == "assistant":
            self._chat[-1]["content"] += f"\n\n{note}"
        self.query_one(AgentConsole).focus_input()

    def _apply_change(self, c: actions.Change) -> None:
        """Apply one confirmed change through the same paths as the keybindings."""
        now = self.now()
        if c.op == "job_add":
            fields = dict(c.job_fields)
            self.db.add_job(fields.pop("company"), now=now, **fields)
            return
        if c.op == "job_update":
            if self.db.get_job(c.job_id) is None:
                raise LookupError("application no longer exists")
            self.db.update_job(c.job_id, now=now, **c.job_fields)
            return
        if c.op == "add":
            task_id = self.db.add_task(c.title, c.day, estimate=c.estimate, tags=c.tags,
                                       fixed_start=c.at, now=now)
            self._pushed(TodoistSync.queue_add, task_id)
            return
        task = self.db.get_task(c.task_id)
        if task is None:
            raise LookupError("task no longer exists")
        active = self.db.active_session()
        stop_focus = active is not None and active.task_id == task.id
        if c.op == "done":
            if stop_focus:
                self.db.stop_session(now)
            self.db.set_done(task.id, True, now)
            self._pushed(TodoistSync.queue_done, task.id, True)
        elif c.op == "delete":
            if stop_focus:
                self.db.stop_session(now)
            self._pushed(TodoistSync.queue_delete, task.id)  # before the row goes
            self.db.delete_task(task.id)
        elif c.op == "schedule":
            day = c.day or (self._day if task.backlogged else task.day)
            fixed = None if c.unpin else (c.at or task.fixed_start)
            self.db.update_task(task.id, title=task.title, estimate=task.estimate,
                                tags=task.tags, fixed_start=fixed)
            self.db.set_day(task.id, day)
            self.db.set_overdue(task.id, None)
            self._pushed(TodoistSync.queue_reschedule, task.id)
        elif c.op == "edit":
            self.db.update_task(
                task.id,
                title=c.title or task.title,
                estimate=c.estimate if c.estimate is not None else task.estimate,
                tags=c.tags if c.tags is not None else task.tags,
                fixed_start=task.fixed_start,
            )
            self._pushed(TodoistSync.queue_update, task, self.db.get_task(task.id))

    # ── job tracker ──────────────────────────────────────────────────────

    def _selected_job(self):
        job_id = self.query_one(JobsView).selected_job_id()
        return self.db.get_job(job_id) if job_id is not None else None

    def _refresh_jobs(self) -> None:
        self.query_one(JobsView).update_jobs(self.db.list_jobs(), self.now())

    def action_add_job(self) -> None:
        def save(fields: dict | None) -> None:
            if fields:
                self.db.add_job(fields.pop("company"), now=self.now(), **fields)
                self._refresh_jobs()

        self.push_screen(JobModal(today=self._day), save)

    def action_edit_job(self) -> None:
        job = self._selected_job()
        if job is None:
            return

        def save(fields: dict | None) -> None:
            if fields:
                self.db.update_job(job.id, now=self.now(), **fields)
                self._refresh_jobs()

        self.push_screen(JobModal(job, today=self._day), save)

    def action_stage_job(self) -> None:
        job = self._selected_job()
        if job is None:
            return

        def picked(stage: str | None) -> None:
            if stage and stage != job.stage:
                self.db.update_job(job.id, stage=stage, now=self.now())
                self.notify(f"{job.label}: {job.stage} → {stage}")
                self._refresh_jobs()

        self.push_screen(StagePicker(job), picked)

    def action_open_job_link(self) -> None:
        job = self._selected_job()
        if job is None:
            return
        if not job.link:
            self.notify("No link saved for this application (e to add one).")
            return
        import webbrowser
        webbrowser.open(job.link)

    def action_delete_job(self) -> None:
        job = self._selected_job()
        if job is None:
            return
        now = self.now()
        pending = self._pending_delete
        key = -job.id  # negative: a job, not a task
        if pending and pending[0] == key and now - pending[1] < timedelta(seconds=4):
            self._pending_delete = None
            self.db.delete_job(job.id)
            self.notify(f"Deleted {job.label}.")
            self._refresh_jobs()
        else:
            self._pending_delete = (key, now)
            self.notify(f"Press d again to delete {job.label}.", severity="warning", timeout=4)

    def action_scan_job_email(self) -> None:
        """Ask Hermes to check Gmail for job updates, then propose tracker changes.

        Two steps, because gpt-oss:20b fumbles doing it all at once: (1) Hermes runs a
        Gmail search and returns just a list of emails; (2) that list plus the tracker
        go back to Hermes as a normal chat turn, whose proposed changes the user confirms.
        """
        if not self.hermes.api_key:
            self.notify("Connect Hermes first ([4] AI Agent shows how).", severity="warning")
            return
        if self._agent_busy:
            self.notify("Hermes is still answering…", severity="warning")
            return
        last = self.db.get_state("jobs_last_scan")
        days = max(1, (self._day - date.fromisoformat(last)).days + 1) if last else 14
        self.action_show("agent")
        self._clear_pending_proposal()
        self.query_one(AgentConsole).add_user(f"Check my email for job updates (last {days} days)")
        self._agent_busy = True
        self.run_worker(self._scan_jobs(days), group="hermes-chat", exit_on_error=False)

    async def _scan_jobs(self, days: int) -> None:
        console = self.query_one(AgentConsole)
        self._update_agent_status()
        activity = console.add_activity("⚙ Hermes is searching your Gmail…")
        query = (f"newer_than:{days}d (application OR applying OR applied OR interview OR "
                 "assessment OR offer OR candidate OR recruiter OR hiring) "
                 "-category:promotions -category:social")
        prompt = (
            f"{GMAIL_HINT} Run search_emails with the query `{query}` (up to 40 results). "
            "Reply with ONLY a JSON array, one object per email, no prose: "
            '[{"id": "<gmail message id>", "from": "<sender name>", "subject": "…", '
            '"date": "YYYY-MM-DD", "company": "<employer the application is with>", '
            '"role": "<position, or empty if not named>", "stage": "<stage>", '
            '"summary": "<one line>"}]. '
            'stage is "none" unless the email is about one of MY OWN applications '
            "(job alerts, newsletters, marketing, courses = none). Otherwise: application "
            'sent / received / thanks for applying = "applied"; reminder to finish an '
            'application = "saved"; test, assessment or screening call = "screening"; '
            'interview invite = "interview"; offer = "offer"; unfortunately / not progressing '
            '= "rejected". Base every field only on that email. Use [] if nothing matches.'
        )
        try:
            for attempt in (1, 2):  # the model occasionally answers [] without looking
                text = ""
                async for event in self.hermes.stream_chat([{"role": "user", "content": prompt}]):
                    if event.kind == "token":
                        text += event.text
                    else:
                        activity.update(Text(f"⚙ {event.text}", style=MUTED))
                emails = actions.parse_email_list(text)
                if emails:
                    break
                activity.update(Text("⚙ Nothing found — asking Hermes to search once more…",
                                     style=MUTED))
        except (HermesError, ValueError) as exc:
            activity.update("")
            console.add_error(f"Couldn't get the email list from Hermes: {exc}")
            self._agent_busy = False
            self._update_agent_status()
            return

        self.db.set_state("jobs_last_scan", self._day.isoformat())
        jobs = {j.id: j for j in self.db.list_jobs()}
        entries = actions.job_entries_from_emails(emails, jobs)
        mine = [e for e in emails if str(e.get("stage", "none")).lower() not in ("", "none")]
        if not emails:
            summary = [f"No job-related emails in the last {days} days."]
        else:
            summary = [f"Checked {len(emails)} emails from the last {days} days — "
                       f"{len(mine)} about your applications."]
        summary += [f"- **{e.get('company') or e.get('from')}** · {e.get('stage')} · "
                    f"{e.get('subject')}" for e in mine]
        if mine and not entries:
            summary.append("\nYour tracker is already up to date.")
        activity.remove()
        console.add_reply("\n".join(summary))
        self._chat.append({"role": "user", "content": "Check my email for job updates."})
        self._chat.append({"role": "assistant", "content": "\n".join(summary)})
        self._agent_busy = False
        self._update_agent_status()
        if entries:
            self._show_proposal(actions.validate(entries, {}, today=self._day,
                                                 now_time=self.now().time(), jobs=jobs))

    # ── morning brief ────────────────────────────────────────────────────

    def maybe_brief(self) -> None:
        """Write today's brief once it's past the configured time, if there isn't one."""
        at = self.config.brief_time
        if (at is not None and not self._briefing and self.now().time() >= at
                and self.db.get_brief(self._day) is None):
            self.action_make_brief()

    def action_make_brief(self) -> None:
        if self._briefing:
            return
        self._briefing = True
        self._update_brief_banner()
        self.run_worker(self._make_brief(), group="brief", exit_on_error=False)

    async def _make_brief(self) -> None:
        view = self.query_one(BriefView)
        cfg = self.config
        day = self._day
        try:
            view.set_status("Fetching headlines…", busy=True)
            inputs = await brief.gather(now=self.now(), feeds=cfg.brief_feeds or brief.FINANCE_FEEDS)
            markdown, by_hermes, note = None, False, ""
            if self.hermes.api_key:
                view.show(brief.raw_digest(inputs))  # something to read while Hermes writes
                view.set_status("Hermes is checking your email and writing the brief…", busy=True)
                markdown, note = await self._write_brief(view, inputs)
                by_hermes = markdown is not None
            else:
                note = ("Headlines only — connect Hermes ([4] AI Agent) for the full brief "
                        "with your email.")
            if markdown is None:
                markdown = brief.raw_digest(inputs, note=note)
            self.db.save_brief(day, markdown, by_hermes, self.now())
        except Exception as exc:  # never let the brief take the planner down
            view.set_status(f"Couldn't make the brief: {exc}", error=True)
            self._briefing = False
            self._update_brief_banner()
            return
        self._briefing = False
        self._load_brief()

    async def _write_brief(self, view: BriefView, inputs: brief.BriefInputs) -> tuple[str | None, str]:
        """Stream Hermes' write-up into the view. Returns (markdown or None, note)."""
        messages = brief.build_messages(inputs, self._agent_context(), email=self.config.brief_email)
        stream: MarkdownStream | None = None
        text = ""
        try:
            async for event in self.hermes.stream_chat(messages):
                if event.kind != "token":
                    continue
                if stream is None:
                    view.body.update("")
                    stream = Markdown.get_stream(view.body)
                text += event.text
                await stream.write(event.text)
        except HermesError as exc:
            return None, f"Hermes couldn't write the brief ({exc}) — showing headlines only."
        finally:
            if stream is not None:
                await stream.stop()
        return (text, "") if text.strip() else (None, "Hermes sent an empty reply.")

    def _load_brief(self) -> None:
        view = self.query_one(BriefView)
        view.set_title(f"Morning brief · {self._day:%A %d %B}")
        stored = self.db.get_brief(self._day)
        if stored is None:
            view.show(BRIEF_EMPTY)
            at = self.config.brief_time
            when = f"Written automatically after {at:%H:%M} · " if at else ""
            view.set_status(f"{when}g to make one now")
        else:
            created, markdown, by_hermes = stored
            view.show(markdown)
            who = "by Hermes" if by_hermes else "headlines only"
            view.set_status(f"{who} · {created:%H:%M} · g to regenerate")
        self._update_brief_banner()

    def _update_brief_banner(self) -> None:
        if self._briefing:
            banner = Text("☀ preparing your morning brief…", style=f"italic {MUTED}")
        elif self._brief_seen != self._day and self.db.get_brief(self._day) is not None:
            banner = Text.assemble(("☀ Morning brief ready", f"bold {CYAN}"), ("  press 5", MUTED))
        else:
            banner = None
        self.today_view.set_brief_banner(banner)

    def action_new_chat(self) -> None:
        if self._agent_busy:
            return
        self._chat.clear()
        self.query_one(AgentConsole).clear()
        self.notify("New conversation.")

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


def main(argv: list[str] | None = None) -> None:
    import argparse
    import sys
    from pathlib import Path

    parser = argparse.ArgumentParser(prog="terminus", description="Terminal day planner.")
    sub = parser.add_subparsers(dest="command")
    imp = sub.add_parser("import-jobs", help="import job applications from a CSV file")
    imp.add_argument("csv", type=Path, help="e.g. Google Sheets → File → Download → CSV")
    imp.add_argument("--dry-run", action="store_true", help="show what would be imported")
    args = parser.parse_args(argv)

    if args.command == "import-jobs":
        from terminus.jobs_import import import_csv

        config = Config.from_env()
        db = Database(config.db_path)
        try:
            result = import_csv(db, args.csv, now=datetime.now().replace(microsecond=0),
                                dry_run=args.dry_run)
        except (OSError, ValueError) as exc:
            sys.exit(f"import-jobs: {exc}")
        print("Columns: " + ", ".join(f"{theirs} → {ours}" for ours, theirs in result.columns.items()))
        verb = "Would add" if args.dry_run else "Added"
        print(f"{verb} {len(result.added)}:")
        for line in result.added:
            print(f"  + {line}")
        if result.skipped:
            print(f"Skipped {len(result.skipped)}:")
            for line in result.skipped:
                print(f"  - {line}")
        return
    TerminusApp().run()


if __name__ == "__main__":
    main()
