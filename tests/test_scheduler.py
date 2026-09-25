from datetime import datetime, time, timedelta

from conftest import DAY
from terminus.models import BlockState, Session, Task
from terminus.scheduler import plan_day

POMO = timedelta(minutes=25)


def at(h, m=0):
    return datetime.combine(DAY, time(h, m))


def task(id, estimate=1, fixed=None, done_at=None, position=None):
    return Task(id=id, title=f"t{id}", day=DAY, position=position or id, estimate=estimate,
                fixed_start=fixed, status="done" if done_at else "todo", done_at=done_at)


def plan(tasks, now, worked=None, active=None, end=time(20)):
    return plan_day(tasks, day=DAY, now=now, worked=worked or {}, active=active,
                    day_start=time(8), day_end=end, pomodoro=POMO)


def spans(p):
    return {b.task.id: (b.start.strftime("%H:%M"), b.end.strftime("%H:%M"), b.state.value)
            for b in p.blocks}


def test_floating_tasks_queue_from_now():
    p = plan([task(1), task(2, estimate=2)], at(10))
    assert spans(p) == {1: ("10:00", "10:25", "next"), 2: ("10:25", "11:15", "next")}


def test_before_day_start_queues_from_day_start():
    p = plan([task(1)], at(6))
    assert spans(p)[1][0] == "08:00"


def test_floating_skips_around_fixed_block():
    p = plan([task(1, estimate=2), task(2, fixed=time(10, 30))], at(10))
    s = spans(p)
    assert s[2] == ("10:30", "10:55", "next")
    assert s[1] == ("10:55", "11:45", "next")  # 50m doesn't fit in the 30m gap


def test_fixed_block_states():
    tasks = [task(1, fixed=time(9)), task(2, fixed=time(9, 50)), task(3, fixed=time(11))]
    s = spans(plan(tasks, at(10)))
    assert [s[i][2] for i in (1, 2, 3)] == ["missed", "running", "next"]


def test_completing_shifts_later_tasks_earlier():
    tasks = [task(1, estimate=2), task(2)]
    before = spans(plan(tasks, at(10)))
    assert before[2][0] == "10:50"
    tasks[0].status, tasks[0].done_at = "done", at(10, 5)
    after = spans(plan(tasks, at(10, 5)))
    assert after[1][2] == "done"
    assert after[2][0] == "10:05"


def test_active_session_runs_first_and_tracks_progress():
    tasks = [task(1), task(2, estimate=2)]
    active = Session(id=1, task_id=2, start=at(9, 50))
    p = plan(tasks, at(10), worked={2: timedelta(minutes=10)}, active=active)
    s = spans(p)
    assert s[2] == ("09:50", "10:40", "running")
    assert s[1] == ("10:40", "11:05", "next")
    assert p.running.progress == 0.2


def test_overrun_keeps_minimum_remaining():
    active = Session(id=1, task_id=1, start=at(9))
    p = plan([task(1)], at(10), worked={1: timedelta(hours=1)}, active=active)
    assert p.running.end == at(10, 5)


def test_overflow_past_day_end():
    p = plan([task(1, estimate=4), task(2, estimate=4)], at(18), end=time(20))
    assert [t.id for t in p.overflow] == [2]


def test_done_block_uses_worked_time():
    p = plan([task(1, estimate=2, done_at=at(9, 30))], at(10), worked={1: timedelta(minutes=30)})
    assert spans(p)[1] == ("09:00", "09:30", "done")


def test_overdue_tasks_are_listed_not_placed():
    old = task(1)
    old.overdue_from = DAY - timedelta(days=13)
    p = plan([old, task(2)], at(10))
    assert [t.id for t in p.overdue] == [1]
    assert p.block_for(1) is None
    assert spans(p)[2][0] == "10:00"  # today's work isn't pushed back by it


def test_focusing_on_an_overdue_task_puts_it_on_the_timeline():
    old = task(1)
    old.overdue_from = DAY - timedelta(days=1)
    active = Session(id=1, task_id=1, start=at(10))
    p = plan([old], at(10, 5), active=active)
    assert p.overdue == [] and p.running.task.id == 1
