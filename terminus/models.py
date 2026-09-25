"""Plain data types shared by the database, scheduler and UI."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from enum import StrEnum


@dataclass
class Task:
    """A unit of work: *what* to do. When it happens is decided by the scheduler."""

    id: int
    title: str
    day: date
    position: int
    estimate: int = 1  # pomodoros
    tags: list[str] = field(default_factory=list)
    status: str = "todo"  # "todo" | "done"
    fixed_start: time | None = None  # set => pinned block (meeting); else floating
    done_at: datetime | None = None
    todoist_id: str | None = None
    overdue_from: date | None = None  # set => in the Overdue list, not on the timeline
    no_due: bool = False  # undated in Todoist => also in the Overdue list

    @property
    def backlogged(self) -> bool:
        """In the Overdue list (overdue or undated) rather than on the timeline."""
        return self.overdue_from is not None or self.no_due

    @property
    def done(self) -> bool:
        return self.status == "done"

    @property
    def fixed(self) -> bool:
        return self.fixed_start is not None


@dataclass
class Session:
    """A stretch of focused work. ``task_id`` is None for free focus; ``end`` is None while running."""

    id: int
    task_id: int | None
    start: datetime
    end: datetime | None = None

    def elapsed(self, now: datetime) -> timedelta:
        return (self.end or now) - self.start


class BlockState(StrEnum):
    DONE = "done"
    RUNNING = "running"
    NEXT = "next"
    MISSED = "missed"


@dataclass
class Block:
    """A task placed on the day's timeline."""

    task: Task
    start: datetime
    end: datetime
    state: BlockState
    progress: float = 0.0  # 0..1, meaningful for RUNNING blocks

    @property
    def duration(self) -> timedelta:
        return self.end - self.start


@dataclass
class DayPlan:
    blocks: list[Block]  # sorted by start
    overflow: list[Task]  # floating tasks that don't fit before day end
    overdue: list[Task] = field(default_factory=list)  # overdue or undated

    def block_for(self, task_id: int) -> Block | None:
        return next((b for b in self.blocks if b.task.id == task_id), None)

    @property
    def running(self) -> Block | None:
        return next((b for b in self.blocks if b.state is BlockState.RUNNING), None)

    @property
    def up_next(self) -> Block | None:
        return next((b for b in self.blocks if b.state is BlockState.NEXT), None)
