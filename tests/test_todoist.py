import json
from datetime import datetime, time, timedelta

import pytest

from conftest import DAY
from fake_todoist import FakeTodoist
from terminus.todoist import TodoistError, TodoistSync, parse_due, split_estimate


@pytest.fixture
def fake():
    return FakeTodoist()


@pytest.fixture
def sync(db, fake, clock):
    return TodoistSync(db, "secret", clock=clock, transport=fake.transport)


def only_task(db, day=DAY):
    tasks = db.tasks_for_day(day)
    assert len(tasks) == 1
    return tasks[0]


# ── pure helpers ──

def test_split_estimate():
    assert split_estimate("Write report ~3") == ("Write report", 3)
    assert split_estimate("No estimate") == ("No estimate", None)
    assert split_estimate("~2") == ("~2", 2)


def test_parse_due_variants():
    assert parse_due(None) == (None, None)
    assert parse_due({"date": "2026-09-25"}) == (DAY, None)
    assert parse_due({"date": "2026-09-25T14:30:00"}) == (DAY, time(14, 30))
    # v1 may split date/datetime; the datetime wins
    assert parse_due({"date": "2026-09-25", "datetime": "2026-09-25T09:15:00"}) == (DAY, time(9, 15))


# ── setup ──

async def test_first_sync_creates_project_and_pushes_existing_tasks(db, fake, sync):
    local = db.add_task("Existing", DAY, estimate=2, tags=["work"])
    db.add_task("Yesterday's", DAY - timedelta(days=1))  # past: not pushed
    await sync.sync()
    project = next(p for p in fake.projects.values() if p["name"] == "Terminus")
    item = fake.item_named("Existing")
    assert item["project_id"] == project["id"]
    assert item["due"] == {"date": "2026-09-25"}
    assert item["labels"] == ["work"]
    assert item["duration"] == {"amount": 50, "unit": "minute"}
    assert db.get_task(local).todoist_id == item["id"]
    assert len(fake.items) == 1


async def test_reuses_existing_project(db, fake, sync):
    pid = fake.add_project("terminus")  # case-insensitive match
    await sync.sync()
    assert len(fake.projects) == 1
    assert sync._get("project_id") == pid


# ── phone → terminus ──

async def test_phone_add_shows_up_today(db, fake, sync):
    await sync.sync()
    fake.phone_add("Call dentist ~2", labels=["Personal"])
    await sync.sync()
    t = only_task(db)
    assert (t.title, t.estimate, t.tags, t.fixed_start) == ("Call dentist", 2, ["personal"], None)


async def test_phone_due_dates(db, fake, sync):
    await sync.sync()
    fake.phone_add("Pinned", due={"date": "2026-09-25T15:00:00"})
    fake.phone_add("Tomorrow", due={"date": "2026-09-26"})
    fake.phone_add("Overdue", due={"date": "2026-09-20T09:00:00"})
    await sync.sync()
    today = {t.title: t for t in db.tasks_for_day(DAY)}
    assert today["Pinned"].fixed_start == time(15)
    assert today["Overdue"].fixed_start is None  # the old time has passed
    assert "Tomorrow" not in today
    assert [t.title for t in db.tasks_for_day(DAY + timedelta(days=1))] == ["Tomorrow"]


async def test_other_projects_are_ignored(db, fake, sync):
    fake.add_project("Inbox")
    await sync.sync()
    fake.phone_add("Not mine", project="Inbox")
    await sync.sync()
    assert db.tasks_for_day(DAY) == []


async def test_duration_maps_to_pomodoros(db, fake, sync):
    await sync.sync()
    fake.phone_add("Long", duration={"amount": 1, "unit": "hour"})
    await sync.sync()
    assert only_task(db).estimate == 3  # 60m / 25m, rounded up


async def test_phone_complete_reopen_delete(db, fake, sync):
    await sync.sync()
    iid = fake.phone_add("Thing")
    await sync.sync()
    fake.phone_complete(iid)
    await sync.sync()
    assert only_task(db).done
    fake.phone_edit(iid, checked=False, completed_at=None)
    await sync.sync()
    assert not only_task(db).done
    fake.phone_delete(iid)
    await sync.sync()
    assert db.tasks_for_day(DAY) == []


async def test_phone_edit_and_move_out_of_project(db, fake, sync):
    await sync.sync()
    iid = fake.phone_add("Draft")
    await sync.sync()
    fake.phone_edit(iid, content="Final ~2")
    await sync.sync()
    assert (only_task(db).title, only_task(db).estimate) == ("Final", 2)
    fake.phone_edit(iid, project_id="elsewhere")
    await sync.sync()
    assert db.tasks_for_day(DAY) == []


# ── terminus → phone ──

async def test_local_changes_are_pushed(db, fake, sync):
    await sync.sync()
    tid = db.add_task("Local", DAY, fixed_start=time(16))
    sync.queue_add(tid)
    await sync.sync()
    item = fake.item_named("Local")
    assert item["due"] == {"date": "2026-09-25T16:00:00"}

    before = db.get_task(tid)
    db.update_task(tid, title="Renamed", estimate=1, tags=["x"], fixed_start=time(16))
    sync.queue_update(before, db.get_task(tid))
    await sync.sync()
    update = json.loads(fake.requests[-1]["commands"])[0]["args"]
    assert "due" not in update  # unchanged due isn't rewritten (keeps recurrence)
    assert item["content"] == "Renamed" and item["labels"] == ["x"]

    db.set_done(tid, True, datetime(2026, 9, 25, 10))
    sync.queue_done(tid, True)
    await sync.sync()
    assert item["checked"]
    assert only_task(db).done  # the echo doesn't reopen it

    sync.queue_delete(tid)
    db.delete_task(tid)
    await sync.sync()
    assert item["is_deleted"]
    assert db.tasks_for_day(DAY) == []


async def test_add_then_delete_before_syncing(db, fake, sync):
    await sync.sync()
    tid = db.add_task("Oops", DAY)
    sync.queue_add(tid)
    sync.queue_delete(tid)
    db.delete_task(tid)
    await sync.sync()
    assert fake.item_named("Oops")["is_deleted"]
    assert db.tasks_for_day(DAY) == []
    assert sync.pending() == 0


# ── robustness ──

async def test_offline_changes_are_kept_and_pushed_once(db, fake, sync):
    await sync.sync()
    fake.online = False
    tid = db.add_task("Offline", DAY)
    sync.queue_add(tid)
    with pytest.raises(TodoistError, match="offline"):
        await sync.sync()
    assert sync.pending() == 1
    fake.online = True
    await sync.sync()
    assert sync.pending() == 0
    assert [i["content"] for i in fake.items.values()] == ["Offline"]
    assert db.get_task(tid).todoist_id is not None


async def test_bad_token(db, fake, clock):
    sync = TodoistSync(db, "wrong", clock=clock, transport=fake.transport)
    with pytest.raises(TodoistError, match="token"):
        await sync.sync()


async def test_command_error_is_reported_and_dropped(db, fake, sync):
    await sync.sync()
    iid = fake.phone_add("Gone soon")
    await sync.sync()
    task = only_task(db)
    fake.phone_delete(iid)  # deleted on the phone...
    sync.queue_done(task.id, True)  # ...while we complete it locally
    result = await sync.sync()
    assert result.errors and "item_close" in result.errors[0]
    assert sync.pending() == 0


async def test_pending_local_edit_wins_over_stale_remote(db, fake, sync):
    await sync.sync()
    iid = fake.phone_add("Shared")
    await sync.sync()
    task = only_task(db)
    fake.online = False
    db.update_task(task.id, title="Local wins", estimate=1, tags=[], fixed_start=None)
    sync.queue_update(task, db.get_task(task.id))
    with pytest.raises(TodoistError):
        await sync.sync()
    fake.online = True
    await sync.sync()
    assert fake.items[iid]["content"] == "Local wins"
    assert only_task(db).title == "Local wins"


async def test_full_resync_closes_tasks_missing_remotely(db, fake, sync):
    await sync.sync()
    iid = fake.phone_add("Closed while away")
    await sync.sync()
    fake.phone_complete(iid)
    sync._set("sync_token", "*")  # e.g. token expired → server sends a full sync
    await sync.sync()
    assert only_task(db).done


async def test_switching_project_resyncs_from_scratch(db, fake, clock):
    fake.add_project("Inbox")
    first = TodoistSync(db, "secret", clock=clock, transport=fake.transport)
    await first.sync()
    fake.phone_add("In inbox", project="Inbox")
    await first.sync()
    assert db.tasks_for_day(DAY) == []  # not the synced project
    inbox = TodoistSync(db, "secret", project_name="Inbox", clock=clock, transport=fake.transport)
    await inbox.sync()
    assert [t.title for t in db.tasks_for_day(DAY)] == ["In inbox"]


async def test_inbox_matched_by_flag(db, fake, clock):
    pid = fake.add_project("Eingang")  # localized inbox name
    fake.projects[pid]["inbox_project"] = True
    sync = TodoistSync(db, "secret", project_name="Inbox", clock=clock, transport=fake.transport)
    await sync.sync()
    assert sync._get("project_id") == pid


async def test_overdue_marked_and_rescheduled(db, fake, sync):
    await sync.sync()
    iid = fake.phone_add("Old", due={"date": "2026-09-12"})
    fake.phone_add("No date")
    await sync.sync()
    tasks = {t.title: t for t in db.tasks_for_day(DAY)}
    assert tasks["Old"].overdue_from == datetime(2026, 9, 12).date()
    assert tasks["No date"].overdue_from is None
    db.set_overdue(tasks["Old"].id, None)
    sync.queue_reschedule(tasks["Old"].id)
    await sync.sync()
    assert fake.items[iid]["due"] == {"date": "2026-09-25"}
    assert db.get_task(tasks["Old"].id).overdue_from is None
