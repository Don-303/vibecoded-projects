"""Drive the real app headlessly with Textual's Pilot."""

from datetime import time

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
        for key, view in [("3", "stats"), ("slash", "agent"), ("1", "today")]:
            await pilot.press(key)
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
