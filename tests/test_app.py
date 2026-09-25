"""Drive the real app headlessly with Textual's Pilot."""

from datetime import time

import pytest

from terminus.app import TerminusApp
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
