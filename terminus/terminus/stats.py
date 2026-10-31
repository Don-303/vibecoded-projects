"""Aggregate focus sessions and completed tasks for the Stats view."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from terminus.db import Database


@dataclass
class DayStat:
    day: date
    focus: timedelta
    done: int


@dataclass
class Stats:
    days: list[DayStat]  # oldest first, ending today
    week_focus: timedelta
    prev_week_focus: timedelta
    streak: int
    longest_streak: int
    tags: list[tuple[str, timedelta]]  # this week, biggest first; "" = free focus


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min)
    return start, start + timedelta(days=1)


def _clip(start: datetime, end: datetime, lo: datetime, hi: datetime) -> timedelta:
    return max(timedelta(), min(end, hi) - max(start, lo))


def focus_by_day(db: Database, days: list[date], now: datetime) -> dict[date, timedelta]:
    lo, _ = _day_bounds(days[0])
    _, hi = _day_bounds(days[-1])
    totals: dict[date, timedelta] = {d: timedelta() for d in days}
    for s in db.sessions_between(lo, hi):
        for d in days:
            d_lo, d_hi = _day_bounds(d)
            totals[d] += _clip(s.start, s.end or now, d_lo, d_hi)
    return totals


def done_by_day(db: Database, days: list[date]) -> dict[date, int]:
    counts = {d: 0 for d in days}
    rows = db.conn.execute(
        "SELECT substr(done_at, 1, 10), COUNT(*) FROM tasks"
        " WHERE status = 'done' AND done_at >= ? AND done_at < ? GROUP BY 1",
        (days[0].isoformat(), (days[-1] + timedelta(days=1)).isoformat()),
    )
    for day, count in rows:
        counts[date.fromisoformat(day)] = count
    return counts


def focus_by_tag(db: Database, since: date, now: datetime) -> list[tuple[str, timedelta]]:
    """Focus time per tag since ``since``. A task with several tags counts toward each."""
    lo = datetime.combine(since, time.min)
    totals: dict[str, timedelta] = defaultdict(timedelta)
    for s in db.sessions_between(lo, now):
        spent = _clip(s.start, s.end or now, lo, now)
        task = db.get_task(s.task_id) if s.task_id else None
        if task is None:
            buckets = [""]  # free focus (or a deleted task)
        else:
            buckets = task.tags or ["untagged"]
        for tag in buckets:
            totals[tag] += spent
    return sorted(totals.items(), key=lambda kv: kv[1], reverse=True)


def longest_streak(db: Database) -> int:
    active: set[date] = set()
    for (value,) in db.conn.execute(
        "SELECT done_at FROM tasks WHERE done_at IS NOT NULL UNION SELECT start FROM sessions"
    ):
        active.add(datetime.fromisoformat(value).date())
    best = 0
    for d in active:
        if d - timedelta(days=1) in active:
            continue  # not the start of a run
        run = 1
        while d + timedelta(days=run) in active:
            run += 1
        best = max(best, run)
    return best


def compute(db: Database, now: datetime, days: int = 7) -> Stats:
    today = now.date()
    window = [today - timedelta(days=i) for i in range(days - 1, -1, -1)]
    prev = [d - timedelta(days=days) for d in window]
    focus = focus_by_day(db, window, now)
    done = done_by_day(db, window)
    return Stats(
        days=[DayStat(d, focus[d], done[d]) for d in window],
        week_focus=sum(focus.values(), timedelta()),
        prev_week_focus=sum(focus_by_day(db, prev, now).values(), timedelta()),
        streak=db.streak(today),
        longest_streak=longest_streak(db),
        tags=focus_by_tag(db, window[0], now),
    )
