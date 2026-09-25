"""Task changes proposed by the Hermes agent, checked before the user confirms them.

Hermes appends a fenced block to its reply:

    ```terminus-actions
    [{"op": "add", "title": "Email Kim back", "day": "tomorrow", "at": "09:00", "estimate": 1},
     {"op": "schedule", "task": 12, "day": "today"},
     {"op": "done", "task": 7}]
    ```

``extract()`` separates that block from the prose, and ``validate()`` turns
each entry into a ``Change`` with a human-readable summary, or a rejection
reason. Nothing here touches the database: the app applies the changes the
user ticks, through the same paths as its own keybindings (so Todoist sync
follows).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, time, timedelta

from terminus.models import Task
from terminus.quickadd import parse_time

FENCE = "```terminus-actions"
BLOCK_RE = re.compile(r"```terminus-actions\s*\n(.*?)(?:```|\Z)", re.S)
OPS = ("add", "done", "schedule", "edit", "delete")
MAX_ESTIMATE = 12
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

#: Appended to the agent's system prompt so it knows how to propose changes.
PROTOCOL = f"""\
You can propose changes to the user's planner. The user reviews and confirms them in \
Terminus; nothing changes until they do. To propose changes, end your reply with ONE block:

{FENCE}
[{{"op": "add", "title": "…", "day": "today|tomorrow|YYYY-MM-DD", "at": "HH:MM", \
"estimate": 1, "tags": ["…"]}},
 {{"op": "done", "task": 12}},
 {{"op": "schedule", "task": 12, "day": "tomorrow", "at": "09:00"}},
 {{"op": "edit", "task": 12, "title": "…", "estimate": 2, "tags": ["…"]}},
 {{"op": "delete", "task": 12}}]
```

Rules: "task" is the number shown as [#N] next to a task above. "estimate" is a count of \
25-minute pomodoros (1–{MAX_ESTIMATE}). In "add", only "title" is required; "day" \
defaults to today. In "schedule", give "day" and/or "at"; "at": null removes a pinned \
time; scheduling an overdue or undated task for today puts it back on the timeline. \
Never schedule in the past. Only propose changes the user asked for or clearly agreed \
to, and describe them in plain words before the block. No block if nothing should change."""


@dataclass
class Change:
    op: str
    summary: str  # one line for the confirmation list
    task_id: int | None = None
    title: str | None = None
    day: date | None = None
    at: time | None = None
    unpin: bool = False
    estimate: int | None = None
    tags: list[str] | None = None


@dataclass
class Proposal:
    changes: list[Change] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)


class VisibleFilter:
    """Hides the actions block from streamed text (the fence may arrive split across chunks)."""

    def __init__(self) -> None:
        self.text = ""
        self.shown = 0

    def feed(self, chunk: str) -> str:
        """Add a chunk; return the newly visible part."""
        self.text += chunk
        cut = self.text.find(FENCE)
        if cut == -1:
            # Hold back anything that could be the start of the fence.
            cut = len(self.text)
            for k in range(min(len(FENCE), len(self.text)), 0, -1):
                if FENCE.startswith(self.text[-k:]):
                    cut = len(self.text) - k
                    break
        visible = self.text[self.shown:cut] if cut > self.shown else ""
        self.shown = max(self.shown, cut)
        return visible

    def flush(self) -> str:
        """End of stream: release held-back text if it never became a fence."""
        if FENCE in self.text:
            return ""
        rest = self.text[self.shown:]
        self.shown = len(self.text)
        return rest


def extract(text: str) -> tuple[str, list | None, str | None]:
    """(prose without the block, parsed entries or None, parse error or None)."""
    match = BLOCK_RE.search(text)
    if not match:
        return text, None, None
    prose = (text[: match.start()] + text[match.end():]).strip()
    body = match.group(1).strip()
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        return prose, None, f"Hermes proposed changes, but they weren't valid JSON ({exc.msg})."
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return prose, None, "Hermes proposed changes in an unexpected format."
    return prose, data, None


def parse_day(value, today: date) -> date:
    text = str(value).strip().lower()
    if text in ("today", ""):
        return today
    if text == "tomorrow":
        return today + timedelta(days=1)
    if text in WEEKDAYS:  # the next such day (a week ahead if it's today)
        ahead = (WEEKDAYS.index(text) - today.weekday()) % 7 or 7
        return today + timedelta(days=ahead)
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise ValueError(f"unknown day {value!r}") from None


def _fmt_when(day: date | None, at: time | None, today: date) -> str:
    parts = []
    if day is not None:
        parts.append("today" if day == today else "tomorrow" if day == today + timedelta(days=1)
                     else f"{day:%a %d %b}")
    if at is not None:
        parts.append(f"{at:%H:%M}")
    return " ".join(parts)


def _estimate(value) -> int:
    n = int(value)
    if not 1 <= n <= MAX_ESTIMATE:
        raise ValueError(f"estimate {n} is outside 1–{MAX_ESTIMATE} pomodoros")
    return n


def _tags(value) -> list[str]:
    if isinstance(value, str):
        value = value.replace(",", " ").split()
    return [str(t).lstrip("#").lower() for t in value if str(t).strip()]


def validate(entries: list, tasks: dict[int, Task], *, today: date, now_time: time) -> Proposal:
    """Check each proposed entry against the current tasks. Never raises."""
    proposal = Proposal()
    for i, entry in enumerate(entries, 1):
        try:
            proposal.changes.append(_one(entry, tasks, today, now_time))
        except (ValueError, TypeError, KeyError) as exc:
            label = entry.get("op", "?") if isinstance(entry, dict) else "?"
            proposal.rejected.append(f"#{i} {label}: {exc}")
    return proposal


def _one(entry, tasks: dict[int, Task], today: date, now_time: time) -> Change:
    if not isinstance(entry, dict):
        raise ValueError("not an object")
    op = entry.get("op")
    if op not in OPS:
        raise ValueError(f"unknown op {op!r}")

    def when(require: bool, default_day: date) -> tuple[date | None, time | None, bool]:
        day = parse_day(entry["day"], today) if entry.get("day") is not None else None
        unpin = "at" in entry and entry["at"] is None
        at = parse_time(str(entry["at"])) if entry.get("at") else None
        if require and day is None and at is None and not unpin:
            raise ValueError("needs a day or a time")
        if day is not None and day < today:
            raise ValueError(f"{day} is in the past")
        if at is not None and (day or default_day) == today and at <= now_time:
            raise ValueError(f"{at:%H:%M} today has already passed")
        return day, at, unpin

    if op == "add":
        title = str(entry.get("title") or "").strip()
        if not title:
            raise ValueError("needs a title")
        day, at, _ = when(require=False, default_day=today)
        day = day or today
        est = _estimate(entry["estimate"]) if entry.get("estimate") is not None else 1
        tags = _tags(entry.get("tags") or [])
        extra = " ".join(filter(None, [_fmt_when(day, at, today), f"~{est}",
                                       " ".join(f"#{t}" for t in tags)]))
        return Change("add", f"Add “{title}” · {extra}", title=title, day=day, at=at,
                      estimate=est, tags=tags)

    try:
        task = tasks[int(entry["task"])]
    except (KeyError, ValueError, TypeError):
        raise ValueError(f"no task [#{entry.get('task')}]") from None
    name = f"“{task.title}”"

    if op == "done":
        if task.done:
            raise ValueError(f"{name} is already done")
        return Change("done", f"Mark {name} done", task_id=task.id)

    if op == "delete":
        return Change("delete", f"Delete {name}", task_id=task.id)

    if op == "schedule":
        day, at, unpin = when(require=True, default_day=today if task.backlogged else task.day)
        before = _fmt_when(task.day, task.fixed_start, today) or "unscheduled"
        if task.backlogged:
            before = "overdue" if task.overdue_from else "no date"
        after = _fmt_when(day or task.day, None if unpin else (at or task.fixed_start), today)
        return Change("schedule", f"Move {name} · {before} → {after}", task_id=task.id,
                      day=day, at=at, unpin=unpin)

    # edit
    title = str(entry["title"]).strip() if entry.get("title") else None
    est = _estimate(entry["estimate"]) if entry.get("estimate") is not None else None
    tags = _tags(entry["tags"]) if entry.get("tags") is not None else None
    diffs = []
    if title and title != task.title:
        diffs.append(f"title → “{title}”")
    if est is not None and est != task.estimate:
        diffs.append(f"~{task.estimate} → ~{est}")
    if tags is not None and tags != task.tags:
        diffs.append(f"tags → {' '.join('#' + t for t in tags) or 'none'}")
    if not diffs:
        raise ValueError(f"nothing to change on {name}")
    return Change("edit", f"Edit {name} · {', '.join(diffs)}", task_id=task.id, title=title,
                  estimate=est, tags=tags)
