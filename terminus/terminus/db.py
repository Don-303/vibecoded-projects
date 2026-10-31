"""SQLite storage for tasks and focus sessions.

Datetimes are stored as naive local ISO strings — this is a single-user,
single-machine tool. Schema changes are applied via ``PRAGMA user_version``.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path

from terminus.models import STAGES, Job, JobEmail, Session, Task

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
    # v3: overdue tasks sit in their own list instead of on the timeline.
    #     Dropping the sync token makes the next Todoist sync a full one,
    #     which back-fills overdue_from from Todoist's due dates.
    """
    ALTER TABLE tasks ADD COLUMN overdue_from TEXT;
    DELETE FROM sync_state WHERE key = 'sync_token';
    """,
    # v4: undated Todoist tasks join the Overdue list too (full resync back-fills it).
    """
    ALTER TABLE tasks ADD COLUMN no_due INTEGER NOT NULL DEFAULT 0;
    DELETE FROM sync_state WHERE key = 'sync_token';
    """,
    # v5: one morning brief per day
    """
    CREATE TABLE briefs (
        day        TEXT PRIMARY KEY,
        created_at TEXT NOT NULL,
        markdown   TEXT NOT NULL,
        by_hermes  INTEGER NOT NULL
    );
    """,
    # v6: job applications, with a history of stage changes
    """
    CREATE TABLE jobs (
        id         INTEGER PRIMARY KEY,
        company    TEXT NOT NULL,
        role       TEXT NOT NULL DEFAULT '',
        stage      TEXT NOT NULL DEFAULT 'applied',
        applied_on TEXT,
        next_step  TEXT NOT NULL DEFAULT '',
        next_date  TEXT,
        link       TEXT NOT NULL DEFAULT '',
        email_ref  TEXT,
        notes      TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE UNIQUE INDEX idx_jobs_email ON jobs(email_ref) WHERE email_ref IS NOT NULL;
    CREATE TABLE job_history (
        id     INTEGER PRIMARY KEY,
        job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
        stage  TEXT NOT NULL,
        at     TEXT NOT NULL
    );
    """,
    # v7: a free-text status per application, and the emails linked to it
    #     (Hermes' description of each email — Terminus never reads mail itself)
    """
    ALTER TABLE jobs ADD COLUMN status TEXT NOT NULL DEFAULT '';
    CREATE TABLE job_emails (
        id       INTEGER PRIMARY KEY,
        job_id   INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
        gmail_id TEXT NOT NULL,
        sent_on  TEXT,
        sender   TEXT NOT NULL DEFAULT '',
        subject  TEXT NOT NULL DEFAULT '',
        summary  TEXT NOT NULL DEFAULT '',
        stage    TEXT NOT NULL DEFAULT '',
        added_at TEXT NOT NULL,
        UNIQUE (job_id, gmail_id)
    );
    """,
    # v8: a task can belong to a job application (at most one open task per job)
    """
    ALTER TABLE tasks ADD COLUMN job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL;
    """,
    # v9: the next step a job's task was made for, so a task the user completed or deleted
    #     isn't made again until a new next step arrives; back-filled from existing tasks
    """
    ALTER TABLE jobs ADD COLUMN task_step TEXT NOT NULL DEFAULT '';
    UPDATE jobs SET task_step = next_step
     WHERE next_step != '' AND id IN (SELECT job_id FROM tasks WHERE job_id IS NOT NULL);
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
        overdue_from=date.fromisoformat(row["overdue_from"]) if row["overdue_from"] else None,
        no_due=bool(row["no_due"]),
        job_id=row["job_id"],
    )


def _job(row: sqlite3.Row) -> Job:
    return Job(
        id=row["id"],
        company=row["company"],
        role=row["role"],
        stage=row["stage"],
        applied_on=date.fromisoformat(row["applied_on"]) if row["applied_on"] else None,
        next_step=row["next_step"],
        next_date=date.fromisoformat(row["next_date"]) if row["next_date"] else None,
        link=row["link"],
        email_ref=row["email_ref"],
        notes=row["notes"],
        status=row["status"],
        task_step=row["task_step"],
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


JOB_FIELDS = ("company", "role", "stage", "applied_on", "next_step", "next_date", "link",
              "email_ref", "notes", "status", "task_step")


def _job_value(field: str, value):
    if field in ("applied_on", "next_date"):
        return value.isoformat() if value else None
    if field == "stage" and value not in STAGES:
        raise ValueError(f"unknown stage {value!r}")
    if field == "email_ref":
        return value or None
    return value if value is not None else ""


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
        overdue_from: date | None = None,
        no_due: bool = False,
        now: datetime | None = None,
    ) -> int:
        now = now or datetime.now()
        with self.conn:
            (pos,) = self.conn.execute(
                "SELECT COALESCE(MAX(position), 0) + 1 FROM tasks WHERE day = ?", (day.isoformat(),)
            ).fetchone()
            cur = self.conn.execute(
                "INSERT INTO tasks"
                " (title, tags, estimate, day, position, fixed_start, created_at, overdue_from,"
                "  no_due) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    title,
                    ",".join(tags or []),
                    estimate,
                    day.isoformat(),
                    pos,
                    fixed_start.isoformat("minutes") if fixed_start else None,
                    now.isoformat(timespec="seconds"),
                    overdue_from.isoformat() if overdue_from else None,
                    int(no_due),
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

    def open_task_for_job(self, job_id: int) -> Task | None:
        row = self.conn.execute(
            "SELECT * FROM tasks WHERE job_id = ? AND status = 'todo' ORDER BY id LIMIT 1", (job_id,)
        ).fetchone()
        return _task(row) if row else None

    def set_task_job(self, task_id: int, job_id: int | None) -> None:
        with self.conn:
            self.conn.execute("UPDATE tasks SET job_id = ? WHERE id = ?", (job_id, task_id))

    def set_task_due(self, task_id: int, due: date | None, today: date) -> None:
        """Give a task a due day, or none (it then sits in the undated list on today)."""
        with self.conn:
            if due is None:
                self.conn.execute(
                    "UPDATE tasks SET day = ?, no_due = 1, overdue_from = NULL WHERE id = ?",
                    (today.isoformat(), task_id))
            else:
                self.conn.execute(
                    "UPDATE tasks SET day = ?, no_due = 0, overdue_from = NULL, fixed_start = NULL"
                    " WHERE id = ?", (due.isoformat(), task_id))

    def job_emails_by_job(self) -> dict[int, list[JobEmail]]:
        out: dict[int, list[JobEmail]] = {}
        for job in self.list_jobs():
            out[job.id] = self.job_emails(job.id)
        return out

    def open_task_titles(self) -> list[str]:
        """Titles of every unfinished task, any day."""
        return [r[0] for r in self.conn.execute("SELECT title FROM tasks WHERE status = 'todo'")]

    def upcoming_tasks(self, after: date, days: int = 7) -> list[Task]:
        """Unfinished tasks on the ``days`` days after ``after``, by day and time."""
        rows = self.conn.execute(
            "SELECT * FROM tasks WHERE status = 'todo' AND day > ? AND day <= ?"
            " ORDER BY day, fixed_start IS NULL, fixed_start, position",
            (after.isoformat(), (after + timedelta(days=days)).isoformat()),
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

    def set_overdue(self, task_id: int, since: date | None, no_due: bool = False) -> None:
        """Put a task in the Overdue list (overdue since a day, or undated).

        ``set_overdue(id, None)`` takes it out, back onto the plan.
        """
        with self.conn:
            self.conn.execute(
                "UPDATE tasks SET overdue_from = ?, no_due = ? WHERE id = ?",
                (since.isoformat() if since else None, int(no_due), task_id),
            )

    def task_by_todoist_id(self, todoist_id: str) -> Task | None:
        row = self.conn.execute("SELECT * FROM tasks WHERE todoist_id = ?", (todoist_id,)).fetchone()
        return _task(row) if row else None

    def carry_over(self, today: date) -> int:
        """Move unfinished floating tasks from past days onto ``today``, marked overdue.

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
                    "UPDATE tasks SET overdue_from = COALESCE(overdue_from, day),"
                    " day = ?, position = ? WHERE id = ?",
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

    # ── small settings (key/value) ───────────────────────────────────────

    def get_state(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM sync_state WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_state(self, key: str, value: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO sync_state (key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    # ── job applications ─────────────────────────────────────────────────

    def add_job(self, company: str, *, now: datetime, **fields) -> int:
        fields["company"] = company
        unknown = set(fields) - set(JOB_FIELDS)
        if unknown:
            raise TypeError(f"unknown job fields: {sorted(unknown)}")
        fields.setdefault("stage", "applied")
        cols = list(fields)
        stamp = now.isoformat(timespec="seconds")
        with self.conn:
            cur = self.conn.execute(
                f"INSERT INTO jobs ({', '.join(cols)}, created_at, updated_at)"
                f" VALUES ({', '.join('?' * len(cols))}, ?, ?)",
                [_job_value(c, fields[c]) for c in cols] + [stamp, stamp],
            )
            self.conn.execute("INSERT INTO job_history (job_id, stage, at) VALUES (?, ?, ?)",
                              (cur.lastrowid, fields["stage"], stamp))
        return cur.lastrowid

    def update_job(self, job_id: int, *, now: datetime, **fields) -> None:
        """Change some fields; a stage change is also recorded in the history."""
        unknown = set(fields) - set(JOB_FIELDS)
        if unknown:
            raise TypeError(f"unknown job fields: {sorted(unknown)}")
        if not fields:
            return
        before = self.get_job(job_id)
        stamp = now.isoformat(timespec="seconds")
        sets = ", ".join(f"{c} = ?" for c in fields)
        with self.conn:
            self.conn.execute(
                f"UPDATE jobs SET {sets}, updated_at = ? WHERE id = ?",
                [_job_value(c, v) for c, v in fields.items()] + [stamp, job_id],
            )
            if before and "stage" in fields and fields["stage"] != before.stage:
                self.conn.execute("INSERT INTO job_history (job_id, stage, at) VALUES (?, ?, ?)",
                                  (job_id, fields["stage"], stamp))

    def delete_job(self, job_id: int) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))

    def get_job(self, job_id: int) -> Job | None:
        row = self.conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return _job(row) if row else None

    def list_jobs(self, *, include_closed: bool = True) -> list[Job]:
        where = "" if include_closed else "WHERE stage NOT IN ('rejected', 'withdrawn')"
        return [_job(r) for r in self.conn.execute(f"SELECT * FROM jobs {where} ORDER BY id")]

    def find_job(self, company: str, role: str = "") -> Job | None:
        """Case-insensitive match on company and role (an empty role matches any)."""
        for job in self.list_jobs():
            if job.company.strip().lower() == company.strip().lower() and (
                not role or not job.role or job.role.strip().lower() == role.strip().lower()
            ):
                return job
        return None

    def job_by_email_ref(self, ref: str) -> Job | None:
        row = self.conn.execute("SELECT * FROM jobs WHERE email_ref = ?", (ref,)).fetchone()
        return _job(row) if row else None

    def add_job_emails(self, job_id: int, emails: list[dict], now: datetime) -> int:
        """Link emails (as described by Hermes) to an application. Duplicates are ignored."""
        added = 0
        with self.conn:
            for e in emails:
                if not e.get("id"):
                    continue
                cur = self.conn.execute(
                    "INSERT OR IGNORE INTO job_emails"
                    " (job_id, gmail_id, sent_on, sender, subject, summary, stage, added_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (job_id, e["id"], e.get("date") or None, e.get("from", ""),
                     e.get("subject", ""), e.get("summary", ""), e.get("stage", ""),
                     now.isoformat(timespec="seconds")),
                )
                added += cur.rowcount
        return added

    def job_emails(self, job_id: int) -> list[JobEmail]:
        rows = self.conn.execute(
            "SELECT * FROM job_emails WHERE job_id = ? ORDER BY sent_on DESC, id DESC", (job_id,)
        )
        return [
            JobEmail(r["gmail_id"], date.fromisoformat(r["sent_on"]) if r["sent_on"] else None,
                     r["sender"], r["subject"], r["summary"], r["stage"])
            for r in rows
        ]

    def job_email_counts(self) -> dict[int, int]:
        rows = self.conn.execute("SELECT job_id, COUNT(*) FROM job_emails GROUP BY job_id")
        return dict(rows.fetchall())

    def logged_gmail_ids(self) -> set[str]:
        """Every email already linked to an application (or used to create one)."""
        ids = {r[0] for r in self.conn.execute("SELECT gmail_id FROM job_emails")}
        ids |= {r[0] for r in self.conn.execute(
            "SELECT email_ref FROM jobs WHERE email_ref IS NOT NULL")}
        return ids

    def job_history(self, job_id: int) -> list[tuple[str, datetime]]:
        rows = self.conn.execute(
            "SELECT stage, at FROM job_history WHERE job_id = ? ORDER BY at, id", (job_id,)
        )
        return [(stage, datetime.fromisoformat(at)) for stage, at in rows]

    # ── morning brief ────────────────────────────────────────────────────

    def get_brief(self, day: date) -> tuple[datetime, str, bool] | None:
        """(created_at, markdown, written by Hermes?) for a day, if one exists."""
        row = self.conn.execute(
            "SELECT created_at, markdown, by_hermes FROM briefs WHERE day = ?", (day.isoformat(),)
        ).fetchone()
        return (datetime.fromisoformat(row[0]), row[1], bool(row[2])) if row else None

    def save_brief(self, day: date, markdown: str, by_hermes: bool, now: datetime) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO briefs (day, created_at, markdown, by_hermes) VALUES (?, ?, ?, ?)"
                " ON CONFLICT(day) DO UPDATE SET created_at = excluded.created_at,"
                " markdown = excluded.markdown, by_hermes = excluded.by_hermes",
                (day.isoformat(), now.isoformat(timespec="seconds"), markdown, int(by_hermes)),
            )

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
