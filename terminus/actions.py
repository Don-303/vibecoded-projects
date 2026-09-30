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

from terminus.jobs_import import map_stage
from terminus.models import STAGES, Job, Task
from terminus.quickadd import parse_time

FENCE = "```terminus-actions"
BLOCK_RE = re.compile(r"```terminus-actions\s*\n(.*?)(?:```|\Z)", re.S)
OPS = ("add", "done", "schedule", "edit", "delete", "job_add", "job_update")
JOB_TEXT = ("role", "next_step", "link", "notes", "status")
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
 {{"op": "delete", "task": 12}},
 {{"op": "job_add", "company": "…", "role": "…", "stage": "applied", "applied_on": "YYYY-MM-DD", \
"next_step": "…", "next_date": "YYYY-MM-DD", "link": "https://…", "email_ref": "<gmail message id>"}},
 {{"op": "job_update", "job": 3, "stage": "interview", "next_step": "…", "next_date": "YYYY-MM-DD"}}]
```

Rules: "task" is the number shown as [#N] next to a task above. "estimate" is a count of \
25-minute pomodoros (1–{MAX_ESTIMATE}). In "add", only "title" is required; "day" \
defaults to today. In "schedule", give "day" and/or "at"; "at": null removes a pinned \
time; scheduling an overdue or undated task for today puts it back on the timeline. \
Never schedule in the past. Job applications are shown as [J#N]; "job" is that number. \
Stages: {", ".join(STAGES)}. For a job email, first check whether the application is \
already listed and use job_update rather than job_add; always set "email_ref" to the Gmail \
message id on job_add. Only include what the email actually says: leave "role" out if the \
position isn't named, and leave "next_step"/"next_date" out unless the email gives a concrete \
next step or date — never invent follow-ups. A reminder to finish an application is stage \
"saved", not "applied". Only propose changes the user asked for or clearly agreed to, and \
describe them in plain words before the block. No block if nothing should change."""


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
    job_id: int | None = None
    job_fields: dict = field(default_factory=dict)  # job_add / job_update columns
    emails: list[dict] = field(default_factory=list)  # emails to link to the application


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


def parse_email_list(text: str) -> list[dict]:
    """The JSON array of emails Hermes returns in step 1 of a job scan.

    Tolerates prose or code fences around the array. Raises ValueError if there
    isn't one.
    """
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end < start:
        raise ValueError("Hermes didn't return an email list")
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"the email list wasn't valid JSON ({exc.msg})") from None
    emails = [e for e in data if isinstance(e, dict) and (e.get("subject") or e.get("summary"))]
    for e in emails:
        e["id"] = str(e.get("id") or "").strip()
        for key in ("from", "subject", "date", "summary"):
            e[key] = " ".join(str(e.get(key) or "").split())[:300]  # one line each
    return emails


#: Pipeline order for "don't move backwards"; closing stages are always allowed.
PROGRESS = {"saved": 0, "applied": 1, "screening": 2, "interview": 3, "offer": 4}


ASSESSMENT = re.compile(
    r"assessment|online test|\btest\b|hirevue|codility|hackerrank|codesignal|pymetrics|"
    r"\bshl\b|numerical|verbal reasoning|situational judg|game.based|case study|"
    r"video interview|coding challenge|technical exercise", re.I)


def _email_record(e: dict, stage: str) -> dict:
    return {k: e.get(k, "") for k in ("id", "date", "from", "subject", "summary")} | {"stage": stage}


def job_entries_from_emails(
    emails: list[dict],
    jobs: dict[int, Job],
    *,
    logged: set[str] = frozenset(),
    today: date | None = None,
) -> list[dict]:
    """Turn Hermes' labelled email list into job_add / job_update (+ task) entries.

    Deterministic on purpose: the model only labels each email (company, role,
    stage, summary, due); matching against the tracker happens here, so it can't
    invent entries. Emails are handled oldest first, so the newest word on an
    application sets its status. Every new email is attached to its application;
    emails in ``logged`` were handled by an earlier scan and are skipped. An
    assessment email also proposes a task to do it.
    """
    today = today or date.today()
    updates: dict[int, dict] = {}  # job id → job_update entry
    adds: list[dict] = []
    tasks: list[dict] = []
    state = {j.id: j.stage for j in jobs.values()}

    for e in sorted(emails, key=lambda e: e.get("date") or ""):
        raw_stage = str(e.get("stage") or "").strip().lower()
        company = str(e.get("company") or "").strip()
        ref = e.get("id") or None
        if (not company or PLACEHOLDER.match(company)
                or raw_stage in ("", "none", "null", "n/a", "not_application", "ignore")):
            continue  # not about an application, or the employer isn't named
        if ref and ref in logged:
            continue
        stage = _stage(raw_stage)
        role = str(e.get("role") or "").strip()
        if PLACEHOLDER.match(role) or "application" in role.lower():
            role = ""  # "Application", "HR Application"… describe the email, not a position
        summary = str(e.get("summary") or "").strip()
        try:
            due = date.fromisoformat(str(e.get("due") or "")[:10])
        except ValueError:
            due = None
        text = f"{e.get('subject', '')} {summary}"
        is_assessment = stage == "screening" and bool(ASSESSMENT.search(text))
        next_step = str(e.get("next_step") or "").strip()
        if PLACEHOLDER.match(next_step):
            next_step = ""
        if is_assessment and not next_step:
            next_step = "Complete assessment"

        job = next((j for j in jobs.values() if ref and j.email_ref == ref), None) or next(
            (j for j in jobs.values() if j.company.lower() == company.lower()
             and (not role or not j.role or j.role.lower() == role.lower())), None)
        if job is not None:
            if job.closed:
                continue
            entry = updates.setdefault(job.id, {"op": "job_update", "job": job.id, "emails": []})
            entry["emails"].append(_email_record(e, stage))
            if summary:
                entry["status"] = summary
            current = state[job.id]
            moves_on = stage not in PROGRESS or PROGRESS[stage] > PROGRESS.get(current, 0)
            if stage != current and moves_on:
                state[job.id] = stage
                entry["stage"] = stage
            target = entry
        else:
            key = (company.lower(), role.lower())
            target = next((a for a in adds if a["company"].lower() == key[0] and (
                not a.get("role") or not key[1] or a["role"].lower() == key[1])), None)
            if target is None:
                target = {"op": "job_add", "company": company, "stage": stage,
                          "email_ref": ref, "emails": []}
                if stage != "saved" and e.get("date"):
                    target["applied_on"] = e["date"]
                adds.append(target)
            elif stage not in PROGRESS or PROGRESS[stage] > PROGRESS.get(target["stage"], 0):
                target["stage"] = stage
            if role and not target.get("role"):
                target["role"] = role
            target["emails"].append(_email_record(e, stage))
            if summary:
                target["status"] = summary

        if next_step or due:
            target["next_step"] = next_step
            if due:
                target["next_date"] = due.isoformat()
        if is_assessment and (due is None or due >= today):
            label = f"{company} {role}".strip() if role else company
            tasks.append({"op": "add", "title": f"{label} assessment",
                          "day": (due or today).isoformat(), "estimate": 2, "tags": ["job"]})

    return adds + list(updates.values()) + tasks


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


def validate(
    entries: list,
    tasks: dict[int, Task],
    *,
    today: date,
    now_time: time,
    jobs: dict[int, Job] | None = None,
) -> Proposal:
    """Check each proposed entry against the current tasks and jobs. Never raises."""
    proposal = Proposal()
    jobs = jobs or {}
    for i, entry in enumerate(entries, 1):
        try:
            if isinstance(entry, dict) and str(entry.get("op", "")).startswith("job_"):
                proposal.changes.append(_job_change(entry, jobs, today))
                continue
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


def _stage(value) -> str:
    text = str(value).strip().lower()
    return text if text in STAGES else map_stage(text)


def _job_date(value, today: date) -> date | None:
    return parse_day(value, today) if value not in (None, "") else None


#: Filler the model sometimes writes instead of leaving a field out.
PLACEHOLDER = re.compile(r"^\W*(unknown|n/?a|none|tbc|tbd|not (specified|stated|given)|"
                         r"position unknown|role unknown|unspecified|-+)\W*$", re.I)


def _job_change(entry: dict, jobs: dict[int, Job], today: date) -> Change:
    fields: dict = {}
    for key in JOB_TEXT:
        if entry.get(key) is not None:
            value = str(entry[key]).strip()
            if not PLACEHOLDER.match(value):
                fields[key] = value
    if fields.get("link") and not fields["link"].startswith(("http://", "https://")):
        raise ValueError(f"link {fields['link']!r} isn't a web address")
    if entry.get("stage") is not None:
        fields["stage"] = _stage(entry["stage"])
    for key in ("applied_on", "next_date"):
        if key in entry:
            fields[key] = _job_date(entry[key], today)
    emails = [
        {k: " ".join(str(m.get(k) or "").split())[:300]
         for k in ("id", "date", "from", "subject", "summary", "stage")}
        for m in entry.get("emails") or [] if isinstance(m, dict) and m.get("id")
    ]
    mail_note = f" · +{len(emails)} email{'s' if len(emails) != 1 else ''}" if emails else ""

    def describe(f: dict) -> str:
        bits = []
        if f.get("next_step") or f.get("next_date"):
            when = f"{f['next_date']:%a %d %b}" if f.get("next_date") else ""
            bits.append("next: " + " ".join(filter(None, [when, f.get("next_step", "")])))
        return " · ".join(bits)

    if entry["op"] == "job_add":
        company = str(entry.get("company") or "").strip()
        if not company:
            raise ValueError("needs a company")
        ref = str(entry.get("email_ref") or "").strip() or None
        for job in jobs.values():
            if ref and job.email_ref == ref:
                raise ValueError(f"that email is already tracked as [J#{job.id}] {job.label}")
            same_role = not fields.get("role") or not job.role or \
                job.role.lower() == fields["role"].lower()
            if job.company.lower() == company.lower() and same_role:
                raise ValueError(f"already tracked as [J#{job.id}] {job.label} — use job_update")
        fields.setdefault("stage", "applied")
        if fields["stage"] != "saved":
            fields.setdefault("applied_on", today)
        fields["email_ref"] = ref
        label = f"{company} — {fields['role']}" if fields.get("role") else company
        extra = " · ".join(filter(None, [fields["stage"].capitalize(), describe(fields),
                                         f"status: {fields['status']}" if fields.get("status") else ""]))
        return Change("job_add", f"Track job “{label}” · {extra}{mail_note}",
                      job_fields={"company": company, **fields}, emails=emails)

    try:
        job = jobs[int(entry["job"])]
    except (KeyError, ValueError, TypeError):
        raise ValueError(f"no job [J#{entry.get('job')}]") from None
    changed = {k: v for k, v in fields.items() if getattr(job, k) != v}
    if not changed and not emails:
        raise ValueError(f"nothing to change on {job.label}")
    diffs = []
    if "stage" in changed:
        diffs.append(f"{job.stage.capitalize()} → {changed['stage'].capitalize()}")
    if next_bits := describe(changed):
        diffs.append(next_bits)
    if "status" in changed:
        diffs.append(f"status: {changed['status']}")
    others = sorted(set(changed) - {"stage", "next_step", "next_date", "status"})
    if others:
        diffs.append("edits " + ", ".join(k.replace("_", " ") for k in others))
    verb = "Update" if changed else "Log"
    detail = " · ".join(diffs) if diffs else ""
    summary = f"{verb} job “{job.label}”" + (f" · {detail}" if detail else "") + mail_note
    return Change("job_update", summary, job_id=job.id, job_fields=changed, emails=emails)
