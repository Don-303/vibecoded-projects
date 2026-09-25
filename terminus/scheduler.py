"""Lay the day's tasks out on a timeline.

``plan_day`` is a pure function: given tasks, time already worked and the
current moment, it returns where every block sits and what state it's in.
The UI calls it again on every change and every minute, so "shifting
subsequent tasks" when something finishes early or late falls out for free.

Rules:
* Done tasks sit where they happened (ending at ``done_at``).
* Fixed tasks sit at their pinned time. They are RUNNING while their window
  covers now (or while being focused on), and MISSED once it has passed.
* Floating tasks queue in ``position`` order from now (or day start),
  skipping around upcoming fixed blocks. The one being focused on is
  RUNNING; the rest are NEXT. Whatever doesn't fit before day end is
  overflow (it will carry over to tomorrow).
* Overdue tasks (left over from past days) are listed separately, not
  placed, until they're pulled onto today — unless being focused on.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from terminus.models import Block, BlockState, DayPlan, Session, Task

#: Remaining time never shrinks below this, so over-running tasks stay visible.
MIN_REMAINING = timedelta(minutes=5)


def _ceil_minute(dt: datetime) -> datetime:
    floored = dt.replace(second=0, microsecond=0)
    return floored if floored == dt else floored + timedelta(minutes=1)


def plan_day(
    tasks: list[Task],
    *,
    day: date,
    now: datetime,
    worked: dict[int, timedelta],
    active: Session | None,
    day_start: time,
    day_end: time,
    pomodoro: timedelta,
) -> DayPlan:
    start_of_day = datetime.combine(day, day_start)
    end_of_day = datetime.combine(day, day_end)
    active_id = active.task_id if active else None

    def duration(t: Task) -> timedelta:
        return t.estimate * pomodoro

    def remaining(t: Task) -> timedelta:
        return max(duration(t) - worked.get(t.id, timedelta()), MIN_REMAINING)

    blocks: list[Block] = []

    # Done: anchored to when they were finished.
    for t in tasks:
        if t.done and t.done_at:
            spent = worked.get(t.id) or duration(t)
            blocks.append(Block(t, t.done_at - spent, t.done_at, BlockState.DONE, 1.0))

    # Fixed: pinned in place.
    fixed_pending: list[Block] = []
    for t in tasks:
        if t.done or not t.fixed:
            continue
        start = datetime.combine(day, t.fixed_start)
        end = start + duration(t)
        if t.id == active_id:
            end = max(end, now + MIN_REMAINING)
            state = BlockState.RUNNING
        elif end <= now:
            state = BlockState.MISSED
        elif start <= now:
            state = BlockState.RUNNING
        else:
            state = BlockState.NEXT
        progress = min(max((now - start) / (end - start), 0.0), 1.0)
        block = Block(t, start, end, state, progress)
        blocks.append(block)
        if state is not BlockState.MISSED:
            fixed_pending.append(block)
    fixed_pending.sort(key=lambda b: b.start)

    # Floating: queue from now.
    cursor = max(_ceil_minute(now), start_of_day)
    overdue = [t for t in tasks if not t.done and t.overdue_from and t.id != active_id]
    overdue.sort(key=lambda t: (t.overdue_from, t.position))
    floating = [t for t in tasks if not t.done and not t.fixed and t not in overdue]
    floating.sort(key=lambda t: (t.id != active_id, t.position))  # focused task first
    overflow: list[Task] = []

    for t in floating:
        if t.id == active_id:
            assert active is not None
            end = now + remaining(t)
            progress = min(worked.get(t.id, timedelta()) / duration(t), 1.0)
            blocks.append(Block(t, active.start, end, BlockState.RUNNING, progress))
            cursor = max(cursor, _ceil_minute(end))
            continue

        need = remaining(t)
        start = cursor
        clash = True
        while clash:  # bump past fixed blocks until the slot is clear
            clash = False
            for fb in fixed_pending:
                if start < fb.end and start + need > fb.start:
                    start, clash = fb.end, True
        if start + need > end_of_day:
            overflow.append(t)
            continue
        blocks.append(Block(t, start, start + need, BlockState.NEXT))
        cursor = start + need

    blocks.sort(key=lambda b: (b.start, b.end))
    return DayPlan(blocks=blocks, overflow=overflow, overdue=overdue)
