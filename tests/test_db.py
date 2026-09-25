from datetime import datetime, time, timedelta

from conftest import DAY


def at(h, m=0, day=DAY):
    return datetime.combine(day, time(h, m))


def test_migration_is_idempotent(tmp_path):
    from terminus.db import MIGRATIONS, Database
    path = tmp_path / "x.db"
    Database(path).close()
    db = Database(path)
    assert db.conn.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)


def test_add_and_order(db):
    a = db.add_task("A", DAY)
    b = db.add_task("B", DAY, estimate=2, tags=["work"], fixed_start=time(14))
    tasks = db.tasks_for_day(DAY)
    assert [t.id for t in tasks] == [a, b]
    assert tasks[1].tags == ["work"] and tasks[1].fixed_start == time(14)
    db.move_task(b, -1)
    assert [t.id for t in db.tasks_for_day(DAY)] == [b, a]


def test_sessions_and_worked(db):
    t = db.add_task("A", DAY)
    db.start_session(t, at(9))
    assert db.active_session().task_id == t
    assert db.worked_by_task([t], at(9, 20)) == {t: timedelta(minutes=20)}
    db.stop_session(at(9, 30))
    assert db.active_session() is None
    assert db.worked_by_task([t], at(12)) == {t: timedelta(minutes=30)}


def test_starting_a_session_stops_the_previous(db):
    a, b = db.add_task("A", DAY), db.add_task("B", DAY)
    db.start_session(a, at(9))
    db.start_session(b, at(9, 10))
    worked = db.worked_by_task([a, b], at(9, 30))
    assert worked == {a: timedelta(minutes=10), b: timedelta(minutes=20)}


def test_focus_time_clips_at_midnight(db):
    t = db.add_task("A", DAY)
    db.start_session(t, at(23, 0))
    db.stop_session(at(1, 0, DAY + timedelta(days=1)))
    assert db.focus_time_on(DAY, at(12)) == timedelta(hours=1)


def test_carry_over_moves_only_floating_todo(db):
    yesterday = DAY - timedelta(days=1)
    floating = db.add_task("float", yesterday)
    db.add_task("meeting", yesterday, fixed_start=time(9))
    done = db.add_task("done", yesterday)
    db.set_done(done, True, at(9, day=yesterday))
    assert db.carry_over(DAY) == 1
    assert [t.id for t in db.tasks_for_day(DAY)] == [floating]


def test_streak(db):
    for offset in (1, 2, 3):
        t = db.add_task("x", DAY)
        db.set_done(t, True, at(10, day=DAY - timedelta(days=offset)))
    assert db.streak(DAY) == 3  # inactive today doesn't break it
    t = db.add_task("y", DAY)
    db.start_session(t, at(9))
    assert db.streak(DAY) == 4
    assert db.streak(DAY + timedelta(days=2)) == 0


def test_deleting_a_task_keeps_focus_history(db):
    t = db.add_task("A", DAY)
    db.start_session(t, at(9))
    db.stop_session(at(9, 30))
    db.delete_task(t)
    assert db.focus_time_on(DAY, at(12)) == timedelta(minutes=30)
    assert db.sessions_between(at(0), at(23))[0].task_id is None


def test_free_focus_session(db):
    db.start_session(None, at(9))
    assert db.active_session().task_id is None
    assert db.focus_time_on(DAY, at(9, 40)) == timedelta(minutes=40)
    assert db.streak(DAY) == 1


def test_upgrade_from_v1_keeps_sessions(tmp_path):
    import sqlite3
    from terminus.db import MIGRATIONS, Database
    path = tmp_path / "v1.db"
    conn = sqlite3.connect(path)
    conn.executescript(MIGRATIONS[0])
    conn.execute("PRAGMA user_version = 1")
    conn.execute("INSERT INTO tasks (title, day, position, created_at) VALUES ('x', '2026-09-25', 1, '')")
    conn.execute("INSERT INTO sessions (task_id, start, end) VALUES (1, '2026-09-25T09:00:00', '2026-09-25T09:25:00')")
    conn.commit()
    conn.close()
    db = Database(path)
    assert db.worked_by_task([1], at(12)) == {1: timedelta(minutes=25)}
    assert db.get_task(1).todoist_id is None


def test_carry_over_marks_overdue_since_original_day(db):
    t = db.add_task("old", DAY - timedelta(days=3))
    db.carry_over(DAY - timedelta(days=1))
    db.carry_over(DAY)
    task = db.get_task(t)
    assert task.day == DAY and task.overdue_from == DAY - timedelta(days=3)
