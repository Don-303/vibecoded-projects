"""Two-way sync with one Todoist project, via Todoist's Sync API (v1).

Local changes are written to an outbox table and pushed as Sync API commands;
remote changes arrive incrementally via ``sync_token``. The outbox is durable,
so edits made offline are pushed on the next successful sync, and every command
carries a stable ``uuid`` so a retried batch is de-duplicated by Todoist.

Mapping (Todoist → Terminus):
    content      → title   (a trailing ``~N`` sets the pomodoro estimate)
    labels       → tags
    due date     → day     (no date or overdue → today; future → that day)
    due time     → pinned start (``fixed_start``)
    duration     → estimate (rounded up to whole pomodoros)
    checked      → done
    deleted / moved out of the project → removed locally

Local reordering isn't synced; carry-over isn't pushed either (overdue tasks
simply show on today, as Todoist itself treats them).
"""

from __future__ import annotations

import json
import math
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

import httpx

from terminus.db import Database
from terminus.models import Task

SYNC_URL = "https://api.todoist.com/api/v1/sync"
ESTIMATE_RE = re.compile(r"\s*~(\d+)\s*$")


class TodoistError(Exception):
    """A sync failed as a whole (network, auth, rate limit). The outbox is kept."""


@dataclass
class SyncResult:
    pushed: int = 0
    pulled: int = 0
    errors: list[str] = field(default_factory=list)


def split_estimate(content: str) -> tuple[str, int | None]:
    """``"Write report ~2"`` → ``("Write report", 2)``."""
    m = ESTIMATE_RE.search(content)
    if not m:
        return content.strip(), None
    return content[: m.start()].strip() or content.strip(), max(1, int(m.group(1)))


def parse_due(due: dict | None) -> tuple[date | None, time | None]:
    """Todoist due object → (date, time or None). Fixed-timezone times become local."""
    if not due:
        return None, None
    raw = due.get("datetime") or due.get("date")
    if not raw:
        return None, None
    if "T" not in raw:
        return date.fromisoformat(raw), None
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt.date(), dt.time().replace(second=0, microsecond=0)


def format_due(day: date, fixed: time | None) -> dict:
    """Terminus day/pin → Todoist due (floating time, i.e. no timezone)."""
    if fixed is None:
        return {"date": day.isoformat()}
    return {"date": datetime.combine(day, fixed).isoformat(timespec="seconds")}


class TodoistSync:
    def __init__(
        self,
        db: Database,
        token: str,
        *,
        project_name: str = "Terminus",
        pomodoro: timedelta = timedelta(minutes=25),
        clock: Callable[[], datetime] = datetime.now,
        transport: httpx.AsyncBaseTransport | None = None,
        url: str = SYNC_URL,
    ) -> None:
        self.db = db
        self.token = token
        self.project_name = project_name
        self.pomodoro = pomodoro
        self.clock = clock
        self.transport = transport
        self.url = url
        self.last_synced: datetime | None = None
        self.last_error: str | None = None

    # ── sync state (key/value) ───────────────────────────────────────────

    def _get(self, key: str) -> str | None:
        row = self.db.conn.execute("SELECT value FROM sync_state WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def _set(self, key: str, value: str | None) -> None:
        with self.db.conn:
            if value is None:
                self.db.conn.execute("DELETE FROM sync_state WHERE key = ?", (key,))
            else:
                self.db.conn.execute(
                    "INSERT INTO sync_state (key, value) VALUES (?, ?)"
                    " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, value),
                )

    # ── outbox: called by the app after each local change ────────────────

    def _enqueue(self, type: str, ref: str | None, args: dict, temp_id: str | None = None) -> None:
        with self.db.conn:
            self.db.conn.execute(
                "INSERT INTO todoist_outbox (uuid, type, ref, temp_id, args) VALUES (?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), type, ref, temp_id, json.dumps(args)),
            )

    def _ref(self, task_id: int) -> str | None:
        """The Todoist id for a task — or its temp id if its add hasn't synced yet."""
        row = self.db.conn.execute(
            "SELECT todoist_id, todoist_temp_id FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        return (row[0] or row[1]) if row else None

    def _item_fields(self, task: Task) -> dict:
        args = {
            "content": task.title,
            "labels": task.tags,
            "due": format_due(task.day, task.fixed_start),
        }
        if task.estimate > 1:
            args["duration"] = {"amount": task.estimate * int(self.pomodoro.total_seconds() // 60),
                                "unit": "minute"}
        return args

    def queue_add(self, task_id: int) -> None:
        task = self.db.get_task(task_id)
        if task is None or task.todoist_id:
            return
        temp_id = str(uuid.uuid4())
        with self.db.conn:
            self.db.conn.execute("UPDATE tasks SET todoist_temp_id = ? WHERE id = ?", (temp_id, task_id))
        self._enqueue("item_add", None, self._item_fields(task), temp_id=temp_id)

    def queue_update(self, before: Task, after: Task) -> None:
        ref = self._ref(after.id)
        if ref is None:
            return
        args: dict = {"content": after.title, "labels": after.tags}
        # Only touch due/duration when they changed: rewriting ``due`` would
        # strip recurrence ("every day") from a task created on the phone.
        if (before.day, before.fixed_start) != (after.day, after.fixed_start):
            args["due"] = format_due(after.day, after.fixed_start)
        if before.estimate != after.estimate:
            args["duration"] = {"amount": after.estimate * int(self.pomodoro.total_seconds() // 60),
                                "unit": "minute"}
        self._enqueue("item_update", ref, args)

    def queue_reschedule(self, task_id: int) -> None:
        """Push a task's current day/pin as its due date (e.g. overdue → today)."""
        task = self.db.get_task(task_id)
        ref = self._ref(task_id)
        if task is not None and ref is not None:
            self._enqueue("item_update", ref, {"due": format_due(task.day, task.fixed_start)})

    def queue_done(self, task_id: int, done: bool) -> None:
        ref = self._ref(task_id)
        if ref is not None:
            self._enqueue("item_close" if done else "item_uncomplete", ref, {})

    def queue_delete(self, task_id: int) -> None:
        """Call *before* deleting the local row (the Todoist id lives on it)."""
        ref = self._ref(task_id)
        if ref is not None:
            self._enqueue("item_delete", ref, {})

    def pending(self) -> int:
        return self.db.conn.execute("SELECT COUNT(*) FROM todoist_outbox").fetchone()[0]

    # ── network ──────────────────────────────────────────────────────────

    async def _post(self, client: httpx.AsyncClient, data: dict) -> dict:
        try:
            resp = await client.post(self.url, data=data)
        except httpx.HTTPError as exc:
            raise TodoistError(f"offline ({type(exc).__name__})") from exc
        if resp.status_code in (401, 403):
            raise TodoistError("Todoist rejected the API token")
        if resp.status_code == 429:
            raise TodoistError("Todoist rate limit hit; will retry")
        if resp.status_code >= 400:
            raise TodoistError(f"Todoist error {resp.status_code}: {resp.text[:120]}")
        return resp.json()

    async def _ensure_project(self, client: httpx.AsyncClient) -> str:
        if self._get("project_name") != self.project_name:
            # Configured project changed: forget the old one and pull the new one in full.
            self._set("project_id", None)
            self._set("sync_token", None)
            self._set("project_name", self.project_name)
        project_id = self._get("project_id")
        if project_id:
            return project_id
        # A projects-only full sync. Its token covers projects alone, so don't keep it.
        data = await self._post(client, {"sync_token": "*", "resource_types": '["projects"]'})
        for p in data.get("projects", []):
            wanted = self.project_name.lower()
            named = p["name"].lower() == wanted or (wanted == "inbox" and p.get("inbox_project"))
            if named and not p.get("is_deleted") and not p.get("is_archived"):
                project_id = p["id"]
                break
        else:
            temp, cmd_uuid = str(uuid.uuid4()), str(uuid.uuid4())
            cmd = {"type": "project_add", "uuid": cmd_uuid, "temp_id": temp,
                   "args": {"name": self.project_name}}
            data = await self._post(client, {"commands": json.dumps([cmd])})
            status = data.get("sync_status", {}).get(cmd_uuid)
            if status != "ok":
                raise TodoistError(f"couldn't create project {self.project_name!r}: {status}")
            project_id = data["temp_id_mapping"][temp]
        self._set("project_id", project_id)
        return project_id

    def _bootstrap(self) -> None:
        """First sync ever: push existing unfinished local tasks (today onwards)."""
        today = self.clock().date()
        rows = self.db.conn.execute(
            "SELECT id FROM tasks WHERE todoist_id IS NULL AND todoist_temp_id IS NULL"
            " AND status = 'todo' AND day >= ?",
            (today.isoformat(),),
        ).fetchall()
        for (task_id,) in rows:
            self.queue_add(task_id)

    async def sync(self) -> SyncResult:
        """Push the outbox and pull remote changes. Raises TodoistError if unreachable."""
        result = SyncResult()
        headers = {"Authorization": f"Bearer {self.token}"}
        try:
            async with httpx.AsyncClient(headers=headers, timeout=15, transport=self.transport) as client:
                project_id = await self._ensure_project(client)
                sync_token = self._get("sync_token") or "*"
                if sync_token == "*":
                    self._bootstrap()

                rows = self.db.conn.execute(
                    "SELECT id, uuid, type, ref, temp_id, args FROM todoist_outbox ORDER BY id"
                ).fetchall()
                commands = []
                for _, cmd_uuid, type, ref, temp_id, args in rows:
                    args = json.loads(args)
                    if type == "item_add":
                        args["project_id"] = project_id
                    else:
                        args["id"] = ref
                    cmd = {"type": type, "uuid": cmd_uuid, "args": args}
                    if temp_id:
                        cmd["temp_id"] = temp_id
                    commands.append(cmd)

                data = await self._post(client, {
                    "sync_token": sync_token,
                    "resource_types": '["items", "projects"]',
                    "commands": json.dumps(commands),
                })
        except TodoistError as exc:
            self.last_error = str(exc)
            raise

        self._apply_mapping(data.get("temp_id_mapping", {}))
        result.pushed, result.errors = self._settle_outbox(rows, data.get("sync_status", {}))

        for p in data.get("projects", []):
            if p["id"] == project_id and (p.get("is_deleted") or p.get("is_archived")):
                self._set("project_id", None)  # recreated/re-found next sync

        today = self.clock().date()
        seen: set[str] = set()
        for item in data.get("items", []):
            seen.add(item["id"])
            if self._apply_item(item, project_id, today):
                result.pulled += 1
        if data.get("full_sync"):
            result.pulled += self._reconcile_full(seen)

        self._set("sync_token", data.get("sync_token", sync_token))
        self.last_synced = self.clock()
        self.last_error = None
        return result

    # ── applying the response ────────────────────────────────────────────

    def _apply_mapping(self, mapping: dict[str, str]) -> None:
        with self.db.conn:
            for temp, real in mapping.items():
                self.db.conn.execute(
                    "UPDATE tasks SET todoist_id = ?, todoist_temp_id = NULL WHERE todoist_temp_id = ?",
                    (real, temp),
                )
                # Commands queued during the request still reference the temp id.
                self.db.conn.execute("UPDATE todoist_outbox SET ref = ? WHERE ref = ?", (real, temp))

    def _settle_outbox(self, rows, status: dict) -> tuple[int, list[str]]:
        """Drop commands Todoist answered; keep any it didn't mention."""
        pushed, errors = 0, []
        with self.db.conn:
            for row_id, cmd_uuid, type, *_ in rows:
                outcome = status.get(cmd_uuid)
                if outcome is None:
                    continue
                self.db.conn.execute("DELETE FROM todoist_outbox WHERE id = ?", (row_id,))
                if outcome == "ok":
                    pushed += 1
                else:
                    msg = outcome.get("error", outcome) if isinstance(outcome, dict) else outcome
                    errors.append(f"{type}: {msg}")
        return pushed, errors

    def _has_pending(self, todoist_id: str) -> bool:
        return self.db.conn.execute(
            "SELECT 1 FROM todoist_outbox WHERE ref = ? LIMIT 1", (todoist_id,)
        ).fetchone() is not None

    def _apply_item(self, item: dict, project_id: str, today: date) -> bool:
        """Merge one remote item into the local DB. Returns True if anything changed."""
        if self._has_pending(item["id"]):
            return False  # a newer local change (edit, or even a delete) goes out next sync
        local = self.db.task_by_todoist_id(item["id"])

        if item.get("is_deleted") or item.get("project_id") != project_id:
            if local is None:
                return False
            active = self.db.active_session()
            if active and active.task_id == local.id:
                self.db.stop_session(self.clock())
            self.db.delete_task(local.id)
            return True

        if item.get("checked"):
            if local is None or local.done:
                return False
            done_at = item.get("completed_at")
            when = (datetime.fromisoformat(done_at).astimezone().replace(tzinfo=None)
                    if done_at else self.clock())
            self.db.set_done(local.id, True, when.replace(microsecond=0))
            return True

        title, text_estimate = split_estimate(item["content"])
        due_day, due_time = parse_due(item.get("due"))
        if due_day is None:
            day = local.day if local and local.day >= today else today
            fixed = None
        elif due_day < today:
            day, fixed = today, None  # overdue: listed on today, the old time has passed
        else:
            day, fixed = due_day, due_time
        overdue = due_day if due_day is not None and due_day < today else None

        estimate = text_estimate or (local.estimate if local else 1)
        duration = item.get("duration")
        if duration and duration.get("amount"):
            minutes = duration["amount"] * (60 if duration.get("unit") == "hour" else 1)
            estimate = max(1, math.ceil(minutes / (self.pomodoro.total_seconds() / 60)))
        tags = [label.lower() for label in item.get("labels", [])]

        if local is None:
            task_id = self.db.add_task(title, day, estimate=estimate, tags=tags,
                                       fixed_start=fixed, overdue_from=overdue, now=self.clock())
            with self.db.conn:
                self.db.conn.execute("UPDATE tasks SET todoist_id = ? WHERE id = ?",
                                     (item["id"], task_id))
            return True

        changed = (local.title, local.tags, local.estimate, local.fixed_start, local.day,
                   local.overdue_from) != (title, tags, estimate, fixed, day, overdue)
        if local.done:  # reopened on the phone (or a recurring task rolled forward)
            self.db.set_done(local.id, False, self.clock())
            changed = True
        if changed:
            self.db.update_task(local.id, title=title, estimate=estimate, tags=tags, fixed_start=fixed)
            self.db.set_day(local.id, day)
            self.db.set_overdue(local.id, overdue)
        return changed

    def _reconcile_full(self, seen: set[str]) -> int:
        """After a full sync, linked open tasks Todoist no longer lists were closed/removed."""
        rows = self.db.conn.execute(
            "SELECT id, todoist_id FROM tasks WHERE todoist_id IS NOT NULL AND status = 'todo'"
        ).fetchall()
        gone = [task_id for task_id, tid in rows if tid not in seen and not self._has_pending(tid)]
        for task_id in gone:
            self.db.set_done(task_id, True, self.clock().replace(microsecond=0))
        return len(gone)
