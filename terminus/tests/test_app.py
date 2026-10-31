"""Drive the real app headlessly with Textual's Pilot."""

from datetime import time

import pytest

from textual.screen import ModalScreen

from terminus.app import TerminusApp
from terminus.widgets import JobsView
from terminus.db import Database
from terminus.models import BlockState
from terminus.widgets import TaskModal


def make_app(config, clock, db=None):
    return TerminusApp(config=config, db=db or Database(":memory:"), clock=clock)


async def test_quick_add_and_complete_reflows(config, clock):
    app = make_app(config, clock)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("2")
        assert app.current_view == "tasks"
        await pilot.press("a")
        await pilot.press(*"Deep work ~2 #code", "enter")
        await pilot.press(*"Email", "enter")
        titles = [t.title for t in app.db.tasks_for_day(clock.now.date())]
        assert titles == ["Deep work", "Email"]
        assert app.plan.block_for(2).start.strftime("%H:%M") == "10:50"

        await pilot.press("escape")  # back to table, cursor on first row
        clock.advance(minutes=20)
        await pilot.press("space")
        assert app.db.get_task(1).done
        assert app.plan.block_for(2).start.strftime("%H:%M") == "10:20"


async def test_typing_in_inputs_does_not_trigger_hotkeys(config, clock):
    app = make_app(config, clock)
    async with app.run_test() as pilot:
        await pilot.press("2", "a")
        await pilot.press(*"q1 nf/ x", "enter")
        assert app.is_running
        assert app.current_view == "tasks"
        assert [t.title for t in app.db.tasks_for_day(clock.now.date())] == ["q1 nf/ x"]


async def test_new_task_modal(config, clock):
    app = make_app(config, clock)
    async with app.run_test() as pilot:
        await pilot.press("n")
        assert isinstance(app.screen, TaskModal)
        await pilot.press("q")  # typed into the title, must not quit
        await pilot.press(*"uick sync")
        app.screen.query_one("#m-fixed").value = "15:00"
        await pilot.press("enter")
        assert not isinstance(app.screen, TaskModal)
        task = app.db.tasks_for_day(clock.now.date())[0]
        assert (task.title, task.fixed_start) == ("quick sync", time(15))


async def test_focus_session_and_pomodoro(config, clock):
    app = make_app(config, clock)
    app.db.add_task("Write", clock.now.date(), estimate=2)
    async with app.run_test() as pilot:
        await pilot.press("f")  # today view: focuses the up-next task
        assert app.db.active_session().task_id == 1
        assert app.plan.running.state is BlockState.RUNNING
        clock.advance(minutes=26)
        app._tick()
        assert app._pomos_notified == 1
        await pilot.press("f")
        assert app.db.active_session() is None
        assert app.db.focus_time_on(clock.now.date(), clock.now).total_seconds() == 26 * 60


async def test_delete_needs_double_press(config, clock):
    app = make_app(config, clock)
    app.db.add_task("Temp", clock.now.date())
    async with app.run_test() as pilot:
        await pilot.press("2", "d")
        assert app.db.get_task(1) is not None
        await pilot.press("d")
        assert app.db.get_task(1) is None


async def test_reorder(config, clock):
    app = make_app(config, clock)
    for name in ("A", "B", "C"):
        app.db.add_task(name, clock.now.date())
    async with app.run_test() as pilot:
        await pilot.press("2", "down", "shift+down")
        assert [t.title for t in app.db.tasks_for_day(clock.now.date())] == ["A", "C", "B"]


async def test_nav_keys(config, clock):
    app = make_app(config, clock)
    async with app.run_test() as pilot:
        for key, view in [("3", "stats"), ("slash", "agent"), ("1", "agent"),
                          ("escape", "agent"), ("1", "today")]:
            await pilot.press(key)  # "/" focuses the prompt, so "1" is typed until esc
            assert app.current_view == view


async def test_free_focus_without_tasks(config, clock):
    app = make_app(config, clock)
    async with app.run_test() as pilot:
        await pilot.press("f")  # no tasks at all
        active = app.db.active_session()
        assert active is not None and active.task_id is None
        clock.advance(minutes=26)
        app._tick()
        assert app._pomos_notified == 1
        await pilot.press("f")
        assert app.db.active_session() is None
        assert app.db.focus_time_on(clock.now.date(), clock.now).total_seconds() == 26 * 60


async def test_shift_f_is_free_focus_even_with_tasks(config, clock):
    app = make_app(config, clock)
    app.db.add_task("Write", clock.now.date())
    async with app.run_test() as pilot:
        await pilot.press("F")
        assert app.db.active_session().task_id is None
        await pilot.press("F")
        assert app.db.active_session() is None


# ── Todoist ──

def make_synced_app(config, clock):
    from fake_todoist import FakeTodoist
    from terminus.todoist import TodoistSync
    fake = FakeTodoist()
    db = Database(":memory:")
    sync = TodoistSync(db, "secret", clock=clock, transport=fake.transport)
    return TerminusApp(config=config, db=db, clock=clock, todoist=sync), fake


async def test_todoist_round_trip_through_ui(config, clock):
    app, fake = make_synced_app(config, clock)
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()  # initial sync creates the project
        assert any(p["name"] == "Terminus" for p in fake.projects.values())

        # phone → terminus, pulled with `r`
        iid = fake.phone_add("From phone ~2")
        await pilot.press("r")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert [t.title for t in app.db.tasks_for_day(clock.now.date())] == ["From phone"]

        # terminus → phone: quick add, then complete
        await pilot.press("2", "a", *"From laptop", "enter", "escape")
        await app.workers.wait_for_complete()
        assert fake.item_named("From laptop")["due"] == {"date": "2026-09-25"}
        await pilot.press("space")  # cursor is on the first row: From phone
        await app.workers.wait_for_complete()
        assert fake.items[iid]["checked"]

        # phone deletes → gone locally
        fake.phone_delete(fake.item_named("From laptop")["id"])
        await pilot.press("r")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert [t.title for t in app.db.tasks_for_day(clock.now.date())] == ["From phone"]


async def test_todoist_offline_does_not_break_the_app(config, clock):
    app, fake = make_synced_app(config, clock)
    fake.online = False
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        await pilot.press("2", "a", *"Queued", "enter")
        await app.workers.wait_for_complete()
        assert app.todoist.pending() == 1
        assert "offline" in app._sync_line().plain
        fake.online = True
        await pilot.press("escape", "r")
        await app.workers.wait_for_complete()
        assert app.todoist.pending() == 0
        assert "✓" in app._sync_line().plain


async def test_sync_key_hidden_without_todoist(config, clock):
    app = make_app(config, clock)
    async with app.run_test():
        assert app.check_action("sync", ()) is False


async def test_overdue_section_and_do_today(config, clock):
    from datetime import timedelta
    app = make_app(config, clock)
    tid = app.db.add_task("Stale", clock.now.date() - timedelta(days=2))
    app.db.add_task("Fresh", clock.now.date())
    async with app.run_test() as pilot:  # startup carry-over marks it overdue
        assert [t.title for t in app.plan.overdue] == ["Stale"]
        timeline = str(app.query_one("#timeline").render())
        assert "Overdue" in timeline and "since Sep 23" in timeline
        await pilot.press("2")
        await pilot.press("down", "t")  # plan order: Fresh, then overdue Stale
        assert app.plan.overdue == []
        assert app.plan.block_for(tid) is not None


# ── stats & agent ──

async def test_stats_view_shows_focus(config, clock):
    app = make_app(config, clock)
    tid = app.db.add_task("Code", clock.now.date(), tags=["code"])
    app.db.start_session(tid, clock.now.replace(hour=9))
    app.db.stop_session(clock.now.replace(hour=9, minute=45))
    async with app.run_test() as pilot:
        await pilot.press("3")
        body = str(app.query_one("#stats-body").render())
        assert "45m" in body and "#code" in body


def make_agent_app(config, clock, fake):
    from terminus.agent_client import HermesClient
    client = HermesClient("box", "hermes-key", transport=fake.transport)
    return TerminusApp(config=config, db=Database(":memory:"), clock=clock, hermes=client)


async def test_agent_chat_streams_reply_with_plan_context(config, clock):
    from fake_hermes import FakeHermes
    fake = FakeHermes(reply="Do **Write** first.")
    app = make_agent_app(config, clock, fake)
    app.db.add_task("Write", clock.now.date())
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        assert app._hermes_online is True
        await pilot.press("slash", *"what next?", "enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        system, user = fake.requests[0]["messages"]
        assert system["role"] == "system" and "Write" in system["content"]
        assert user == {"role": "user", "content": "what next?"}
        assert app._chat[-1]["content"].strip() == "Do **Write** first."
        # second turn carries the history
        await pilot.press(*"thanks", "enter")
        await app.workers.wait_for_complete()
        assert [m["role"] for m in fake.requests[1]["messages"]] == ["system", "user", "assistant", "user"]
        await pilot.press("ctrl+l")
        assert app._chat == []


async def test_agent_offline_shows_error_and_keeps_working(config, clock):
    from fake_hermes import FakeHermes
    fake = FakeHermes()
    fake.online = False
    app = make_agent_app(config, clock, fake)
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        assert app._hermes_online is False
        await pilot.press("slash", *"hello", "enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        errors = app.query(".agent-error")
        assert len(errors) == 1 and "Can't reach Hermes" in str(errors.first().render())
        assert app._chat == [] and not app._agent_busy
        fake.online = True
        await pilot.press(*"again", "enter")
        await app.workers.wait_for_complete()
        assert app._hermes_online is True and len(app._chat) == 2


async def test_agent_unconfigured_shows_setup(config, clock):
    app = make_app(config, clock)  # config fixture has no hermes key
    async with app.run_test() as pilot:
        await pilot.press("4")
        assert app.query(".agent-note")
        await pilot.press(*"hi", "enter")
        await app.workers.wait_for_complete()
        assert "No Hermes API key" in str(app.query(".agent-error").first().render())


# ── morning brief ──

@pytest.fixture
def fake_gather(monkeypatch):
    from test_brief import sample_inputs
    calls = []

    async def gather(**kwargs):
        calls.append(kwargs)
        return sample_inputs()

    monkeypatch.setattr("terminus.brief.gather", gather)
    return calls


async def test_brief_auto_generates_with_hermes(config, clock, fake_gather):
    import dataclasses
    from datetime import time as dtime
    from fake_hermes import FakeHermes
    from terminus.agent_client import HermesClient
    fake = FakeHermes(reply="## Today\nStart with **Write**.")
    cfg = dataclasses.replace(config, brief_time=dtime(6))
    app = TerminusApp(config=cfg, db=Database(":memory:"), clock=clock,
                      hermes=HermesClient("box", "hermes-key", transport=fake.transport))
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        created, markdown, by_hermes = app.db.get_brief(clock.now.date())
        assert by_hermes and "Start with **Write**." in markdown
        system, user = fake.requests[-1]["messages"]
        assert "Gmail inbox" in system["content"] and "<headlines>" in user["content"]
        assert "Morning brief ready" in str(app.query_one("#today-brief").render())
        await pilot.press("5")
        assert app.current_view == "brief"
        assert not app.query_one("#today-brief").display  # seen → banner gone
        # only once per day
        app.maybe_brief()
        await app.workers.wait_for_complete()
        assert len(fake_gather) == 1


async def test_brief_falls_back_to_raw_digest_without_hermes(config, clock, fake_gather):
    app = make_app(config, clock)  # no Hermes key, auto-brief off
    async with app.run_test() as pilot:
        assert app.db.get_brief(clock.now.date()) is None
        await pilot.press("5", "g")
        await app.workers.wait_for_complete()
        await pilot.pause()
        _, markdown, by_hermes = app.db.get_brief(clock.now.date())
        assert not by_hermes and "connect Hermes" in markdown and "Fed signals pause" in markdown
        assert "headlines only" in str(app.query_one("#brief-status").render())


# ── proposed changes (v3) ──

PROPOSAL_REPLY = """Here's the plan: pull in the job hunt, add a follow-up, and drop the old course.

```terminus-actions
[{"op": "schedule", "task": TASK_JOB, "day": "today"},
 {"op": "add", "title": "Email Kim back", "day": "tomorrow", "at": "09:00", "tags": ["job"]},
 {"op": "delete", "task": TASK_COURSE},
 {"op": "done", "task": 999}]
```"""


def make_proposal_app(config, clock):
    from datetime import timedelta
    from fake_hermes import FakeHermes
    from fake_todoist import FakeTodoist
    from terminus.agent_client import HermesClient
    from terminus.todoist import TodoistSync
    db = Database(":memory:")
    today = clock.now.date()
    job = db.add_task("Find a job", today - timedelta(days=13), estimate=2)
    course = db.add_task("Finish C++ course", today - timedelta(days=13))
    reply = PROPOSAL_REPLY.replace("TASK_JOB", str(job)).replace("TASK_COURSE", str(course))
    hermes = FakeHermes(reply=reply)
    hermes.tool_events = False
    todo = FakeTodoist()
    sync = TodoistSync(db, "secret", clock=clock, transport=todo.transport)
    app = TerminusApp(config=config, db=db, clock=clock, todoist=sync,
                      hermes=HermesClient("box", "hermes-key", transport=hermes.transport))
    return app, hermes, todo, job, course


async def ask(app, pilot, text="sort out my job hunt"):
    await pilot.press("slash", *text, "enter")
    await app.workers.wait_for_complete()
    await pilot.pause()


async def test_proposed_changes_apply_on_y(config, clock):
    from datetime import timedelta
    from terminus.widgets.proposal_panel import ProposalPanel
    app, hermes, todo, job, course = make_proposal_app(config, clock)
    async with app.run_test(size=(120, 50)) as pilot:
        await app.workers.wait_for_complete()
        await ask(app, pilot)
        # the block is hidden from the chat text but kept in history for Hermes
        shown = app.query_one(".agent-reply").source
        assert "terminus-actions" not in shown and "Here's the plan" in shown
        assert "terminus-actions" in app._chat[-1]["content"]
        system = hermes.requests[-1]["messages"][0]["content"]
        assert f"[#{job}] Find a job" in system and "```terminus-actions" in system

        panel = app.query_one(ProposalPanel)
        assert len(panel.proposal.changes) == 3
        assert panel.proposal.rejected == ["#4 done: no task [#999]"]
        assert app.db.get_task(course) is not None  # nothing applied yet

        await pilot.press("y")
        await app.workers.wait_for_complete()
        await pilot.pause()
        today = clock.now.date()
        moved = app.db.get_task(job)
        assert moved.day == today and not moved.backlogged
        assert app.plan.block_for(job) is not None  # on the timeline now
        added = [t for t in app.db.upcoming_tasks(today) if t.title == "Email Kim back"][0]
        assert added.day == today + timedelta(days=1) and added.fixed_start == time(9)
        assert app.db.get_task(course) is None
        assert "applied 3 of 3" in app._chat[-1]["content"]
        assert "✓ Add “Email Kim back”" in str(app.query_one(".proposal-outcome").render())
        # Todoist got the same changes
        assert todo.item_named("Email Kim back")["due"] == {"date": "2026-09-26T09:00:00"}
        assert todo.item_named("Find a job")["due"] == {"date": "2026-09-25"}
        assert todo.item_named("Finish C++ course")["is_deleted"]
        assert app.focused.id == "agent-input"


async def test_unticked_changes_are_skipped(config, clock):
    from terminus.widgets.proposal_panel import ProposalPanel
    app, _, _, job, course = make_proposal_app(config, clock)
    async with app.run_test(size=(120, 50)) as pilot:
        await app.workers.wait_for_complete()
        await ask(app, pilot)
        assert app.query_one(ProposalPanel) is not None
        await pilot.press("space")  # untick the first (schedule Find a job)
        await pilot.press("y")
        await pilot.pause()
        assert app.db.get_task(job).backlogged  # left alone
        assert app.db.get_task(course) is None  # the ticked ones went through
        assert "1 unticked" in str(app.query_one(".proposal-outcome").render())


async def test_discard_changes_nothing(config, clock):
    app, _, todo, job, course = make_proposal_app(config, clock)
    async with app.run_test(size=(120, 50)) as pilot:
        await app.workers.wait_for_complete()
        await ask(app, pilot)
        before = [(t.id, t.day, t.overdue_from) for t in app.db.tasks_for_day(clock.now.date())]
        await pilot.press("n")
        await pilot.pause()
        after = [(t.id, t.day, t.overdue_from) for t in app.db.tasks_for_day(clock.now.date())]
        assert before == after
        assert "discarded" in app._chat[-1]["content"]
        assert "Discarded" in str(app.query_one(".proposal-outcome").render())


async def test_new_message_supersedes_pending_proposal(config, clock):
    app, _, _, job, course = make_proposal_app(config, clock)
    async with app.run_test(size=(120, 50)) as pilot:
        await app.workers.wait_for_complete()
        await ask(app, pilot)
        app.query_one("#agent-input").focus()  # click back into the input without deciding
        await pilot.press(*"actually, never mind", "enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert "Superseded" in str(app.query(".proposal-outcome").first().render())
        assert app.db.get_task(course) is not None


async def test_brief_does_not_offer_changes(config, clock, fake_gather):
    import dataclasses
    from datetime import time as dtime
    from fake_hermes import FakeHermes
    from terminus.agent_client import HermesClient
    fake = FakeHermes(reply="## Today\\nFine.")
    cfg = dataclasses.replace(config, brief_time=dtime(6))
    app = TerminusApp(config=cfg, db=Database(":memory:"), clock=clock,
                      hermes=HermesClient("box", "hermes-key", transport=fake.transport))
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        assert "terminus-actions" not in fake.requests[-1]["messages"][0]["content"]


# ── jobs ──

def job_rows(app) -> int:
    """Rows in the Jobs table that are applications (not week headings)."""
    table = app.query_one("#jobs-table")
    return sum(not str(k.value).startswith("hdr:") for k in table.rows)


async def test_jobs_page_add_stage_toggle_delete(config, clock):
    from terminus.widgets import JobModal, StagePicker
    app = make_app(config, clock)
    app.db.add_job("Vandelay", role="Trading Intern", stage="rejected", now=clock.now)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("6")
        assert app.current_view == "jobs"
        assert job_rows(app) == 0  # rejected is hidden
        await pilot.press("h")
        assert job_rows(app) == 1
        await pilot.press("h")

        await pilot.press("a")
        assert isinstance(app.screen, JobModal)
        await pilot.press(*"Northwind")
        app.screen.query_one("#j-role").value = "Quant Research Intern"
        app.screen.query_one("#j-next").value = "Interview"
        app.screen.query_one("#j-next-date").value = "tuesday"
        await pilot.press("enter")
        job = app.db.find_job("Northwind")
        assert job.stage == "applied" and job.applied_on == clock.now.date()
        assert str(job.next_date) == "2026-09-29"

        await pilot.press("s")
        assert isinstance(app.screen, StagePicker)
        await pilot.press("down", "down", "enter")  # applied → screening → interview
        assert app.db.get_job(job.id).stage == "interview"
        assert [s for s, _ in app.db.job_history(job.id)] == ["applied", "interview"]

        await pilot.press("d", "d")
        assert app.db.get_job(job.id) is None


def test_import_jobs_cli(tmp_path, monkeypatch, capsys):
    from terminus.app import main
    monkeypatch.setenv("TERMINUS_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    csv = tmp_path / "t.csv"
    csv.write_text("Company,Role,Status\nNorthwind,Quant Intern,Interview\n")
    main(["import-jobs", str(csv), "--dry-run"])
    assert "Would add 1:" in capsys.readouterr().out
    main(["import-jobs", str(csv)])
    out = capsys.readouterr().out
    assert "Added 1:" in out and "Northwind — Quant Intern · interview" in out
    assert Database(tmp_path / "t.db").find_job("Northwind").stage == "interview"


async def test_jobs_split_by_day_with_stats_margin(config, clock):
    from datetime import date, timedelta
    app = make_app(config, clock)  # Friday 25 Sep 2026
    today = clock.now.date()
    app.db.add_job("Fabrikam", stage="screening", applied_on=today - timedelta(days=1),
                   next_step="Assessment", next_date=today + timedelta(days=3), now=clock.now)
    app.db.add_job("Contoso", applied_on=today - timedelta(days=8), now=clock.now)
    app.db.add_job("Acme", applied_on=date(2026, 6, 2), now=clock.now)
    app.db.add_job("Tyrell", stage="saved", now=clock.now)
    async with app.run_test(size=(150, 40)) as pilot:
        await pilot.press("6")
        table = app.query_one("#jobs-table")
        headings = [f"{table.get_row(k)[0]} | {table.get_row(k)[1]}"
                    for k in table.rows if str(k.value).startswith("hdr:")]
        assert headings == ["Thu 24 Sep | yesterday · 1",
                            "Thu 17 Sep | 1",
                            "Tue 02 Jun | 1",
                            "No date | 1"]
        assert app.query_one(JobsView).selected_job_id() is not None  # cursor skips the heading
        margin = str(app.query_one("#jobs-stats").render())
        assert "Pipeline" in margin and "Screening" in margin
        assert "this week    1" in margin and "last week    1" in margin
        assert "Mon 28  Fabrikam: Assessment" in margin
        await pilot.press("up")  # onto the "This week" heading: actions ignore it
        await pilot.press("s")
        assert not isinstance(app.screen, ModalScreen)


# ── Jobs service import (u) ──

def service_rows():
    from datetime import datetime as dt
    from conftest import gmail_id
    def r(company, stage, when, n, **kw):
        base = {"id": gmail_id(when, n), "from": company, "subject": f"{company} {stage}",
                "date": when.strftime("%Y-%m-%d"), "company": company, "role": "",
                "stage": stage, "summary": f"{company}: {stage}", "next_step": "", "due": ""}
        return base | kw
    return [
        r("Fabrikam", "applied", dt(2026, 9, 21, 9), 1, role="Graduate Analyst"),
        r("Fabrikam", "screening", dt(2026, 9, 23, 9), 2, role="Graduate Analyst",
          next_step="Complete assessment", due="2026-10-07", summary="Assessment invite"),
        r("Northwind", "applied", dt(2026, 9, 22, 9), 3),
        r("Northwind", "rejected", dt(2026, 9, 24, 9), 4, summary="Not progressing"),
        r("LinkedIn", "applied", dt(2026, 9, 24, 9), 5),  # a job board: skipped
        r("Some newsletter", "none", dt(2026, 9, 24, 9), 6),
    ]


def make_import_app(config, clock, db=None):
    from fake_jobs_service import FakeJobsService
    fake = FakeJobsService(service_rows())
    app = TerminusApp(config=config, db=db or Database(":memory:"), clock=clock,
                      jobs_service=fake.client())
    return app, fake


async def test_u_imports_from_the_jobs_service(config, clock):
    from datetime import date
    from terminus.widgets.proposal_panel import ProposalPanel
    db = Database(":memory:")
    p72 = db.add_job("Northwind", role="?", now=clock.now)
    t = db.add_task("Northwind: Online test", clock.now.date(), tags=["job"], now=clock.now)
    db.set_task_job(t, p72)
    app, fake = make_import_app(config, clock, db)
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.press("6", "u")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert fake.calls == ["POST /jobs/refresh"]
        shown = app.query(".agent-reply").last().source
        assert "Checked 6 labelled emails — 5 about your applications." in shown
        summaries = [c.summary for c in app.query_one(ProposalPanel).proposal.changes]
        assert any(s.startswith("Track job “Fabrikam — Graduate Analyst” · Screening") for s in summaries)
        assert any(s.startswith("Update job “Northwind — ?” · Applied → Rejected") for s in summaries)
        assert "Add task “Fabrikam: Complete assessment” · due Wed 07 Oct" in summaries
        assert "Complete task “Northwind: Online test” (application rejected)" in summaries
        await pilot.press("y")
        await pilot.pause()
        fabrikam = app.db.find_job("Fabrikam")
        assert (fabrikam.stage, fabrikam.next_date, len(app.db.job_emails(fabrikam.id))) == (
            "screening", date(2026, 10, 7), 2)
        assert app.db.get_job(p72).stage == "rejected"
        assert app.db.get_task(t).done  # rejected → its task is completed
        task = app.db.open_task_for_job(fabrikam.id)
        assert (task.title, task.day) == ("Fabrikam: Complete assessment", date(2026, 10, 7))
        assert app.db.find_job("LinkedIn") is None

        # pressing u again changes nothing
        app.query_one("#agent-input").blur()
        app.action_show("jobs")
        await pilot.press("u")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert "already up to date" in app.query(".agent-reply").last().source
        assert app._proposal is None


async def test_u_falls_back_to_last_results_when_refresh_fails(config, clock):
    app, fake = make_import_app(config, clock)
    fake.refresh_fails = True
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.press("6", "u")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert fake.calls == ["POST /jobs/refresh", "GET /jobs"]
        shown = app.query(".agent-reply").last().source
        assert "Couldn't refresh" in shown and "results from Fri 25 Sep" in shown
        assert app._proposal is not None  # the last results are still offered


async def test_u_reports_a_rejected_key(config, clock):
    from fake_jobs_service import FakeJobsService
    from terminus.jobs_client import JobsService
    fake = FakeJobsService(service_rows())
    app = TerminusApp(config=config, db=Database(":memory:"), clock=clock,
                      jobs_service=JobsService("http://box:8643", "wrong", transport=fake.transport))
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.press("6", "u")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert "rejected the API key" in str(app.query(".agent-error").last().render())
        assert not app._agent_busy
