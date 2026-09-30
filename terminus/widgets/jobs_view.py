"""Jobs view: a table of job applications, plus the add/edit form and stage picker."""

from __future__ import annotations

from datetime import date, datetime

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Input, Label, OptionList, Select, Static
from textual.widgets.option_list import Option

from terminus.actions import parse_day
from terminus.models import STAGES, Job, JobEmail
from terminus.theme import AMBER, BLUE, CYAN, FG, GREEN, MUTED, RED

STAGE_STYLE = {
    "saved": MUTED,
    "applied": FG,
    "screening": BLUE,
    "interview": f"bold {CYAN}",
    "offer": f"bold {GREEN}",
    "rejected": RED,
    "withdrawn": MUTED,
}
#: Sort order among open applications: furthest along first.
STAGE_RANK = {"offer": 0, "interview": 1, "screening": 2, "applied": 3, "saved": 4,
              "rejected": 5, "withdrawn": 6}


def ago(then: datetime | None, now: datetime) -> str:
    if then is None:
        return ""
    days = (now.date() - then.date()).days
    return "today" if days <= 0 else "yesterday" if days == 1 else f"{days}d ago"


def sort_key(job: Job):
    return (job.closed, job.next_date or date.max, STAGE_RANK[job.stage], job.company.lower())


class JobsView(Vertical):
    BINDINGS = [
        Binding("a", "app.add_job", "Add"),
        Binding("e", "app.edit_job", "Edit"),
        Binding("enter", "app.job_details", "Details", show=False),
        Binding("s", "app.stage_job", "Stage"),
        Binding("o", "app.open_job_link", "Open link"),
        Binding("h", "toggle_closed", "Show closed"),
        Binding("d", "app.delete_job", "Delete"),
        Binding("u", "app.scan_job_email", "Check email"),
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.show_closed = False
        self._jobs: list[Job] = []

    def compose(self) -> ComposeResult:
        yield Static(id="jobs-title", classes="view-title")
        table = DataTable(id="jobs-table", cursor_type="row")
        table.add_column("Stage", key="stage", width=10)
        table.add_column("Company", key="company", width=16)
        table.add_column("Role", key="role", width=18)
        table.add_column("Status", key="status", width=26)
        table.add_column("✉", key="emails", width=3)
        table.add_column("Next step", key="next", width=22)
        table.add_column("Applied", key="applied", width=7)
        yield table
        yield Static(
            Text("u asks Hermes to check your email for job updates · "
                 "import a sheet: terminus import-jobs tracker.csv", style=MUTED),
            id="jobs-hint",
        )

    def update_jobs(self, jobs: list[Job], now: datetime,
                    email_counts: dict[int, int] | None = None) -> None:
        self._jobs = jobs
        if email_counts is not None:
            self._email_counts = email_counts
        counts = getattr(self, "_email_counts", {})
        table = self.query_one(DataTable)
        keep = self.selected_job_id()
        table.clear()
        today = now.date()
        shown = sorted((j for j in jobs if self.show_closed or not j.closed), key=sort_key)
        for j in shown:
            dim = MUTED if j.closed else ""
            nxt = Text()
            if j.next_date:
                style = RED if j.next_date < today else AMBER if j.next_date == today else CYAN
                if j.closed:
                    style = MUTED
                nxt.append(f"{j.next_date:%a %d %b} ", style=style)
            nxt.append(j.next_step or ("" if j.next_date else "—"), style=dim or MUTED)
            n = counts.get(j.id, 0)
            table.add_row(
                Text(j.stage.capitalize(), style=STAGE_STYLE[j.stage]),
                Text(j.company, style=dim or "bold", overflow="ellipsis", no_wrap=True),
                Text(j.role, style=dim, overflow="ellipsis", no_wrap=True),
                Text(j.status or "", style=dim or FG, overflow="ellipsis", no_wrap=True),
                Text(str(n) if n else "", style=CYAN if n and not j.closed else MUTED),
                nxt,
                Text(f"{j.applied_on:%d %b}" if j.applied_on else "", style=MUTED),
                key=str(j.id),
            )
        if keep is not None:
            try:
                table.move_cursor(row=table.get_row_index(str(keep)), animate=False)
            except Exception:
                pass
        active = sum(not j.closed for j in jobs)
        closed = len(jobs) - active
        title = Text.assemble(("Jobs", "bold"), (f"   {active} active", MUTED))
        if closed:
            title.append(f" · {closed} closed ({'h hides' if self.show_closed else 'h shows'})",
                         style=MUTED)
        self.query_one("#jobs-title", Static).update(title)

    def selected_job_id(self) -> int | None:
        table = self.query_one(DataTable)
        if table.row_count == 0:
            return None
        row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
        return int(row_key.value)

    @on(DataTable.RowSelected, "#jobs-table")
    def _row_selected(self) -> None:  # enter (or click) on a row
        self.app.action_job_details()

    def action_toggle_closed(self) -> None:
        self.show_closed = not self.show_closed
        self.update_jobs(self._jobs, self.app.now())

    def focus_table(self) -> None:
        self.query_one(DataTable).focus()


class JobModal(ModalScreen[dict | None]):
    """Add or edit a job application. Returns the fields, or None on cancel."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, job: Job | None = None, today: date | None = None) -> None:
        super().__init__()
        self.job = job
        self.today = today or date.today()

    def compose(self) -> ComposeResult:
        j = self.job
        with Vertical(id="job-modal"):
            yield Static("Edit application" if j else "New application", classes="view-title")
            with Horizontal(classes="modal-row"):
                with Vertical():
                    yield Label("Company")
                    yield Input(j.company if j else "", id="j-company")
                with Vertical():
                    yield Label("Role")
                    yield Input(j.role if j else "", id="j-role")
            with Horizontal(classes="modal-row"):
                with Vertical():
                    yield Label("Stage")
                    yield Select([(s.capitalize(), s) for s in STAGES],
                                 value=j.stage if j else "applied", allow_blank=False, id="j-stage")
                with Vertical():
                    yield Label("Applied on")
                    yield Input(j.applied_on.isoformat() if j and j.applied_on else
                                ("" if j else "today"), placeholder="today / 2026-09-12",
                                id="j-applied")
            with Horizontal(classes="modal-row"):
                with Vertical():
                    yield Label("Next step")
                    yield Input(j.next_step if j else "", placeholder="Interview prep", id="j-next")
                with Vertical():
                    yield Label("Next date")
                    yield Input(j.next_date.isoformat() if j and j.next_date else "",
                                placeholder="tomorrow / friday / 2026-09-30", id="j-next-date")
            yield Label("Status")
            yield Input(j.status if j else "", placeholder="What's happening now, e.g. "
                        "Online assessment due Fri", id="j-status")
            yield Label("Link")
            yield Input(j.link if j else "", placeholder="https://…", id="j-link")
            yield Label("Notes")
            yield Input(j.notes if j else "", id="j-notes")
            yield Static(id="j-error")
            with Horizontal(id="j-buttons"):
                yield Button("Save", variant="primary", id="j-save")
                yield Button("Cancel", id="j-cancel")

    def _value(self, id: str) -> str:
        return self.query_one(f"#{id}", Input).value.strip()

    def _date(self, id: str) -> date | None:
        text = self._value(id)
        return parse_day(text, self.today) if text else None

    @on(Button.Pressed, "#j-save")
    @on(Input.Submitted)
    def save(self) -> None:
        error = self.query_one("#j-error", Static)
        company = self._value("j-company")
        if not company:
            error.update("Company is required.")
            return
        link = self._value("j-link")
        if link and not link.startswith(("http://", "https://")):
            error.update("Link must start with http:// or https://")
            return
        try:
            applied, next_date = self._date("j-applied"), self._date("j-next-date")
        except ValueError as exc:
            error.update(f"{exc} — use today, tomorrow, a weekday or YYYY-MM-DD")
            return
        self.dismiss({
            "company": company,
            "role": self._value("j-role"),
            "stage": self.query_one("#j-stage", Select).value,
            "applied_on": applied,
            "next_step": self._value("j-next"),
            "next_date": next_date,
            "link": link,
            "notes": self._value("j-notes"),
            "status": self._value("j-status"),
        })

    @on(Button.Pressed, "#j-cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)


class StagePicker(ModalScreen[str | None]):
    """Quick stage change: pick from the list (enter), or esc to cancel."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, job: Job) -> None:
        super().__init__()
        self.job = job

    def compose(self) -> ComposeResult:
        with Vertical(id="stage-picker"):
            yield Static(Text.assemble(("Stage · ", "bold"), (self.job.label, CYAN)),
                         classes="view-title")
            yield OptionList(
                *(Option(Text(s.capitalize(), style=STAGE_STYLE[s]), id=s) for s in STAGES),
                id="stage-options",
            )

    def on_mount(self) -> None:
        options = self.query_one(OptionList)
        options.highlighted = STAGES.index(self.job.stage)
        options.focus()

    @on(OptionList.OptionSelected)
    def _picked(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        self.dismiss(None)


class JobDetail(ModalScreen[str | None]):
    """One application: its fields, stage history and linked emails.

    Returns "edit" if the user asked to edit it, else None.
    """

    BINDINGS = [
        Binding("escape,q", "close", "Close"),
        Binding("o", "open_email", "Open email in Gmail"),
        Binding("l", "open_link", "Open job link"),
        Binding("e", "edit", "Edit"),
    ]

    def __init__(self, job: Job, history: list[tuple[str, datetime]],
                 emails: list[JobEmail]) -> None:
        super().__init__()
        self.job = job
        self.history = history
        self.emails = emails

    def compose(self) -> ComposeResult:
        j = self.job
        head = Text()
        head.append(j.company, style="bold")
        if j.role:
            head.append(f" — {j.role}")
        head.append(f"   {j.stage.capitalize()}", style=STAGE_STYLE[j.stage])
        info = Text()
        rows = [
            ("Status", j.status or "—"),
            ("Next step", " ".join(filter(None, [f"{j.next_date:%a %d %b}" if j.next_date else "",
                                                 j.next_step])) or "—"),
            ("Applied", f"{j.applied_on:%d %b %Y}" if j.applied_on else "—"),
            ("Link", j.link or "—"),
            ("Notes", j.notes or "—"),
        ]
        for label, value in rows:
            info.append(f"{label:<10}", style=MUTED)
            info.append(f"{value}\n")
        history = Text("History   ", style=MUTED)
        history.append("  →  ".join(f"{s.capitalize()} {at:%d %b}" for s, at in self.history) or "—")
        with Vertical(id="job-detail"):
            yield Static(head, classes="view-title")
            yield Static(info)
            yield Static(history, id="jd-history")
            yield Static(Text.assemble((f"Emails ({len(self.emails)})", "bold"),
                                       ("   o opens the highlighted one in Gmail", MUTED)),
                         id="jd-emails-title")
            if self.emails:
                yield OptionList(*(Option(self._email_line(m), id=str(i))
                                   for i, m in enumerate(self.emails)), id="jd-emails")
            else:
                yield Static(Text("No linked emails yet — u on the Jobs page checks your inbox.",
                                  style=MUTED))
            yield Static(Text("e edit · l job link · esc close", style=MUTED), id="jd-hint")

    @staticmethod
    def _email_line(m: JobEmail) -> Text:
        line = Text()
        line.append(f"{m.sent_on:%d %b}  " if m.sent_on else "        ", style=MUTED)
        if m.stage:
            line.append(f"{m.stage:<10}", style=STAGE_STYLE.get(m.stage, MUTED))
        line.append(m.subject or "(no subject)", style="bold")
        sender = m.sender.split("<")[0].strip().strip('"') or m.sender
        if sender:
            line.append(f"  · {sender}", style=MUTED)
        if m.summary and m.summary != m.subject:
            line.append(f"\n              {m.summary}", style=MUTED)
        return line

    def on_mount(self) -> None:
        if self.emails:
            self.query_one("#jd-emails", OptionList).focus()

    @on(OptionList.OptionSelected, "#jd-emails")
    def _selected(self) -> None:
        self.action_open_email()

    def action_open_email(self) -> None:
        if not self.emails:
            return
        index = self.query_one("#jd-emails", OptionList).highlighted or 0
        import webbrowser
        webbrowser.open(self.emails[index].gmail_url)

    def action_open_link(self) -> None:
        if self.job.link:
            import webbrowser
            webbrowser.open(self.job.link)
        else:
            self.notify("No link saved for this application.")

    def action_edit(self) -> None:
        self.dismiss("edit")

    def action_close(self) -> None:
        self.dismiss(None)
