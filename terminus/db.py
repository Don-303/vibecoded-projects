"""SQLite storage for tasks and focus sessions.

Datetimes are stored as naive local ISO strings — this is a single-user,
single-machine tool. Schema changes are applied via ``PRAGMA user_version``.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path

from terminus.models import Session, Task

MIGRATIONS = [
    # v1
    """
    CREATE TABLE tasks (
        id          INTEGER PRIMARY KEY,
        title       TEXT NOT NULL,
        tags        TEXT NOT NULL DEFAULT '',
        estimate    INTEGER NOT NULL DEFAULT 1,
        status      TEXT NOT NULL DEFAULT 'todo',
        day         TEXT NOT NULL,
        position    INTEGER NOT NULL,
        fixed_start TEXT,
        created_at  TEXT NOT NULL,
        done_at     TEXT
    );
    CREATE INDEX idx_tasks_day ON tasks(day);
    CREATE TABLE sessions (
        id      INTEGER PRIMARY KEY,
        task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
        start   TEXT NOT NULL,
        end     TEXT
    );
    CREATE INDEX idx_sessions_task ON sessions(task_id);
    """,
    # v2: free focus sessions (task_id NULL) that survive task deletion,
    #     plus Todoist sync state (see terminus/todoist.py)
    """
    CREATE TABLE sessions_v2 (
        id      INTEGER PRIMARY KEY,
        task_id INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
        start   TEXT NOT NULL,
        end     TEXT
    );
    INSERT INTO sessions_v2 SELECT id, task_id, start, end FROM sessions;
    DROP TABLE sessions;
    ALTER TABLE sessions_v2 RENAME TO sessions;
    CREATE INDEX idx_sessions_task ON sessions(task_id);
    ALTER TABLE tasks ADD COLUMN todoist_id TEXT;
    ALTER TABLE tasks ADD COLUMN todoist_temp_id TEXT;
    CREATE UNIQUE INDEX idx_tasks_todoist ON tasks(todoist_id);
    CREATE TABLE sync_state (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    CREATE TABLE todoist_outbox (
        id      INTEGER PRIMARY KEY,
        uuid    TEXT NOT NULL,
        type    TEXT NOT NULL,
        ref     TEXT,
        temp_id TEXT,
        args    TEXT NOT NULL
    );
    """,
]


def _task(row: sqlite3.Row) -> Task:
    return Task(
        id=row["id"],
        title=row["title"],
        tags=[t for t in row["tags"].split(",") if t],
        estimate=row["estimate"],
        status=row["status"],
        day=date.fromisoformat(row["day"]),
        position=row["position"],
        fixed_start=time.fromisoformat(row["fixed_start"]) if row["fixed_start"] else None,
        done_at=datetime.fromisoformat(row["done_at"]) if row["done_at"] else None,
        todoist_id=row["todoist_id"],
    )


def _session(row: sqlite3.Row) -> Session:
    return Session(
        id=row["id"],
        task_id=row["task_id"],
        start=datetime.fromisoformat(row["start"]),
        end=datetime.fromisoformat(row["end"]) if row["end"] else None,
    )


class Database:
    def __init__(self, path: Path | str) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._migrate()

    def _migrate(self) -> None:
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        for i, script in enumerate(MIGRATIONS[version:], start=version + 1):
            with self.conn:
                self.conn.executescript(script)
                self.conn.execute(f"PRAGMA user_version = {i}")

    def close(self) -> None:
        self.conn.close()

    # ── tasks ────────────────────────────────────────────────────────────

    def add_task(
        self,
        title: str,
        day: date,
        *,
        estimate: int = 1,
        tags: list[str] | None = None,
        fixed_start: time | None = None,
        now: datetime | None = None,
    ) -> int:
        now = now or datetime.now()
        with self.conn:
            (pos,) = self.conn.execute(
                "SELECT COALESCE(MAX(position), 0) + 1 FROM tasks WHERE day = ?", (day.isoformat(),)
            ).fetchone()
            cur = self.conn.execute(
                "INSERT INTO tasks (title, tags, estimate, day, position, fixed_start, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    title,
                    ",".join(tags or []),
                    estimate,
                    day.isoformat(),
                    pos,
                    fixed_start.isoformat("minutes") if fixed_start else None,
                    now.isoformat(timespec="seconds"),
                ),
            )
        return cur.lastrowid

    def get_task(self, task_id: int) -> Task | None:
        row = self.conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return _task(row) if row else None

    def update_task(
        self,
        task_id: int,
        *,
        title: str,
        estimate: int,
        tags: list[str],
        fixed_start: time | None,
    ) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE tasks SET title = ?, estimate = ?, tags = ?, fixed_start = ? WHERE id = ?",
                (
                    title,
                    estimate,
                    ",".join(tags),
                    fixed_start.isoformat("minutes") if fixed_start else None,
                    task_id,
                ),
            )

    def delete_task(self, task_id: int) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))

    def set_done(self, task_id: int, done: bool, now: datetime) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE tasks SET status = ?, done_at = ? WHERE id = ?",
                ("done", now.isoformat(timespec="seconds"), task_id)
                if done
                else ("todo", None, task_id),
            )

    def tasks_for_day(self, day: date) -> list[Task]:
        rows = self.conn.execute(
            "SELECT * FROM tasks WHERE day = ? ORDER BY position", (day.isoformat(),)
        )
        return [_task(r) for r in rows]

    def move_task(self, task_id: int, offset: int) -> None:
        """Swap a task with its neighbour ``offset`` places away in its day's order."""
        task = self.get_task(task_id)
        if task is None:
            return
        tasks = self.tasks_for_day(task.day)
        i = next(i for i, t in enumerate(tasks) if t.id == task_id)
        j = i + offset
        if not 0 <= j < len(tasks):
            return
        other = tasks[j]
        with self.conn:
            self.conn.execute("UPDATE tasks SET position = ? WHERE id = ?", (other.position, task.id))
            self.conn.execute("UPDATE tasks SET position = ? WHERE id = ?", (task.position, other.id))

    def set_day(self, task_id: int, day: date) -> None:
        """Move a task to another day, appending it to that day's order."""
        with self.conn:
            (pos,) = self.conn.execute(
                "SELECT COALESCE(MAX(position), 0) + 1 FROM tasks WHERE day = ?", (day.isoformat(),)
            ).fetchone()
            self.conn.execute(
                "UPDATE tasks SET day = ?, position = ? WHERE id = ? AND day != ?",
                (day.isoformat(), pos, task_id, day.isoformat()),
            )

    def task_by_todoist_id(self, todoist_id: str) -> Task | None:
        row = self.conn.execute("SELECT * FROM tasks WHERE todoist_id = ?", (todoist_id,)).fetchone()
        return _task(row) if row else None

    def carry_over(self, today: date) -> int:
        """Move unfinished floating tasks from past days onto ``today``.

        Fixed (pinned) tasks stay on their day — a missed meeting doesn't roll over.
        """
        with self.conn:
            (base,) = self.conn.execute(
                "SELECT COALESCE(MAX(position), 0) FROM tasks WHERE day = ?", (today.isoformat(),)
            ).fetchone()
            stale = self.conn.execute(
                "SELECT id FROM tasks WHERE status = 'todo' AND fixed_start IS NULL AND day < ?"
                " ORDER BY day, position",
                (today.isoformat(),),
            ).fetchall()
            for offset, (task_id,) in enumerate(stale, start=1):
                self.conn.execute(
                    "UPDATE tasks SET day = ?, position = ? WHERE id = ?",
                    (today.isoformat(), base + offset, task_id),
                )
        return len(stale)

    # ── focus sessions ───────────────────────────────────────────────────

    def active_session(self) -> Session | None:
        row = self.conn.execute("SELECT * FROM sessions WHERE end IS NULL LIMIT 1").fetchone()
        return _session(row) if row else None

    def start_session(self, task_id: int | None, now: datetime) -> int:
        """Start focusing (on a task, or free focus if None), ending any running session."""
        self.stop_session(now)
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO sessions (task_id, start) VALUES (?, ?)",
                (task_id, now.isoformat(timespec="seconds")),
            )
        return cur.lastrowid

    def stop_session(self, now: datetime) -> Session | None:
        active = self.active_session()
        if active is None:
            return None
        with self.conn:
            self.conn.execute(
                "UPDATE sessions SET end = ? WHERE id = ?",
                (now.isoformat(timespec="seconds"), active.id),
            )
        active.end = now
        return active

    def sessions_between(self, start: datetime, end: datetime) -> list[Session]:
        rows = self.conn.execute(
            "SELECT * FROM sessions WHERE start < ? AND (end IS NULL OR end > ?) ORDER BY start",
            (end.isoformat(timespec="seconds"), start.isoformat(timespec="seconds")),
        )
        return [_session(r) for r in rows]

    def worked_by_task(self, task_ids: list[int], now: datetime) -> dict[int, timedelta]:
        """Total focused time per task, across all days, including a running session."""
        if not task_ids:
            return {}
        marks = ",".join("?" * len(task_ids))
        rows = self.conn.execute(f"SELECT * FROM sessions WHERE task_id IN ({marks})", task_ids)
        worked: dict[int, timedelta] = defaultdict(timedelta)
        for s in map(_session, rows):
            worked[s.task_id] += s.elapsed(now)
        return dict(worked)

    # ── stats ────────────────────────────────────────────────────────────

    def focus_time_on(self, day: date, now: datetime) -> timedelta:
        """Focused time that fell within ``day`` (sessions clipped at midnight)."""
        lo = datetime.combine(day, time.min)
        hi = lo + timedelta(days=1)
        total = timedelta()
        for s in self.sessions_between(lo, hi):
            total += max(timedelta(), min(s.end or now, hi) - max(s.start, lo))
        return total

    def streak(self, today: date) -> int:
        """Consecutive active days (a task completed or a session logged).

        Today counts if active; an inactive today doesn't break a streak that
        ran through yesterday.
        """
        active: set[date] = set()
        for (value,) in self.conn.execute(
            "SELECT done_at FROM tasks WHERE done_at IS NOT NULL UNION SELECT start FROM sessions"
        ):
            active.add(datetime.fromisoformat(value).date())
        day = today if today in active else today - timedelta(days=1)
        count = 0
        while day in active:
            count += 1
            day -= timedelta(days=1)
        return count
