from datetime import date, time, timedelta

import pytest

from conftest import DAY
from terminus.actions import VisibleFilter, extract, parse_day, validate
from terminus.models import Task

NOW = time(10, 0)  # DAY is Friday 2026-09-25


def tasks():
    overdue = Task(5, "Find a job", DAY, 2, overdue_from=DAY - timedelta(days=13))
    done = Task(7, "Standup", DAY, 3, status="done")
    later = Task(9, "Gym", DAY + timedelta(days=2), 1, fixed_start=time(18))
    return {t.id: t for t in (Task(1, "Write", DAY, 1, estimate=2, tags=["work"]), overdue, done, later)}


def check(entries):
    return validate(entries, tasks(), today=DAY, now_time=NOW)


def test_extract_splits_prose_and_block():
    text = 'Sure — two changes.\n\n```terminus-actions\n[{"op": "done", "task": 1}]\n```\n'
    prose, entries, error = extract(text)
    assert prose == "Sure — two changes." and entries == [{"op": "done", "task": 1}] and error is None
    assert extract("no block here") == ("no block here", None, None)
    _, entries, error = extract("x\n```terminus-actions\n[{bad json\n```")
    assert entries is None and "valid JSON" in error
    # an unterminated block at the end of the stream still parses
    assert extract('ok\n```terminus-actions\n{"op": "done", "task": 1}')[1] == [{"op": "done", "task": 1}]


@pytest.mark.parametrize("value,expected", [
    ("today", DAY), ("tomorrow", DAY + timedelta(days=1)), ("2026-10-02", date(2026, 10, 2)),
    ("monday", date(2026, 9, 28)), ("Friday", date(2026, 10, 2)),  # next Friday, not today
])
def test_parse_day(value, expected):
    assert parse_day(value, DAY) == expected


def test_valid_changes_get_readable_summaries():
    p = check([
        {"op": "add", "title": "Email Kim back", "day": "tomorrow", "at": "9:00", "tags": "#job"},
        {"op": "schedule", "task": 5, "day": "today"},
        {"op": "schedule", "task": 9, "at": None},
        {"op": "done", "task": "1"},
        {"op": "edit", "task": 1, "estimate": 3, "tags": []},
        {"op": "delete", "task": 7},
    ])
    assert p.rejected == []
    assert [c.summary for c in p.changes] == [
        "Add “Email Kim back” · tomorrow 09:00 ~1 #job",
        "Move “Find a job” · overdue → today",
        "Move “Gym” · Sun 27 Sep 18:00 → Sun 27 Sep",
        "Mark “Write” done",
        "Edit “Write” · ~2 → ~3, tags → none",
        "Delete “Standup”",
    ]
    assert p.changes[0].day == DAY + timedelta(days=1) and p.changes[0].at == time(9)
    assert p.changes[2].unpin


def test_bad_changes_are_rejected_with_reasons():
    p = check([
        {"op": "done", "task": 99},
        {"op": "done", "task": 7},
        {"op": "schedule", "task": 1, "day": "2026-09-01"},
        {"op": "schedule", "task": 1, "at": "08:00"},
        {"op": "schedule", "task": 1},
        {"op": "add", "title": ""},
        {"op": "add", "title": "Huge", "estimate": 40},
        {"op": "edit", "task": 1, "title": "Write"},
        {"op": "launch_rockets"},
        "not an object",
    ])
    assert p.changes == []
    assert p.rejected == [
        "#1 done: no task [#99]",
        "#2 done: “Standup” is already done",
        "#3 schedule: 2026-09-01 is in the past",
        "#4 schedule: 08:00 today has already passed",
        "#5 schedule: needs a day or a time",
        "#6 add: needs a title",
        "#7 add: estimate 40 is outside 1–12 pomodoros",
        "#8 edit: nothing to change on “Write”",
        "#9 launch_rockets: unknown op 'launch_rockets'",
        "#10 ?: not an object",
    ]


def test_morning_pin_on_a_future_task_is_fine():
    # Gym is on Sunday: 08:00 there hasn't passed even though it's 10:00 today
    assert check([{"op": "schedule", "task": 9, "at": "08:00"}]).rejected == []


def test_filter_hides_block_even_when_fence_is_split():
    f = VisibleFilter()
    out = "".join(f.feed(c) for c in ["Moving it", " now.\n``", "`terminus-", 'actions\n[{"op"', ": 1}]\n```"])
    assert out + f.flush() == "Moving it now.\n"
    g = VisibleFilter()
    assert g.feed("Use `code` ``") == "Use `code` "  # held back: could be a fence
    assert g.feed(" no") == "`` no" and g.flush() == ""
