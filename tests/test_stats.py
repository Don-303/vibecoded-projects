from datetime import datetime, time, timedelta

from conftest import DAY
from terminus import stats
from terminus.widgets.stats_view import render_stats


def at(h, m=0, day=DAY):
    return datetime.combine(day, time(h, m))


def test_compute_week(db):
    code = db.add_task("Code", DAY, tags=["code"])
    db.start_session(code, at(9))
    db.stop_session(at(10))
    yesterday = DAY - timedelta(days=1)
    db.start_session(code, at(9, day=yesterday))
    db.stop_session(at(9, 30, day=yesterday))
    last_week = DAY - timedelta(days=8)
    db.start_session(code, at(9, day=last_week))
    db.stop_session(at(9, 15, day=last_week))
    db.start_session(None, at(11))  # free focus, still running at "now" (started last:
    #                                  starting a session stops the running one)
    done = db.add_task("Ship", DAY)
    db.set_done(done, True, at(12))

    s = stats.compute(db, at(11, 20))
    assert [d.day for d in s.days][-1] == DAY and len(s.days) == 7
    assert s.days[-1].focus == timedelta(minutes=80)
    assert s.days[-2].focus == timedelta(minutes=30)
    assert s.week_focus == timedelta(minutes=110)
    assert s.prev_week_focus == timedelta(minutes=15)
    assert s.days[-1].done == 1
    assert s.streak == 2 and s.longest_streak == 2
    assert dict(s.tags) == {"code": timedelta(minutes=90), "": timedelta(minutes=20)}
    text = render_stats(s).plain
    assert "total 1h50m" in text and "(+633%)" in text and "free focus" in text


def test_empty_database_renders(db):
    text = render_stats(stats.compute(db, at(10))).plain
    assert "No focus sessions yet" in text and "Streak   0 days" in text
