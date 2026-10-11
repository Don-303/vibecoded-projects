"""Turn the Jobs service's labelled emails into tracker and task changes.

The Jobs service (terminus.jobs_client) labels every job email with company,
role, stage, summary, next_step and due. This module decides, deterministically,
what that means for the tracker:

* Emails are grouped into applications by company plus a *loose* role match
  ("2027 Finance Graduate Programme - 1954" ≈ "Finance Graduate Programme").
  An email without a role joins the company's only open application; if there
  isn't exactly one, it goes to a "?" application for that company.
* An application's stage, status and next step come from its **newest** email
  (by Gmail timestamp, counting emails imported earlier), except that a rejected
  or offered application only moves on to a later stage, never back to
  saved/applied.
* Each application has at most one open task: created from the newest email's
  next_step, updated (title, and due date only when the email gives one)
  instead of duplicated, and completed when the application is rejected.
* Gmail ids already imported are skipped, so running it twice changes nothing.

Output is a list of proposal entries (job_add / job_update / job_task) that go
through terminus.actions.validate and the confirmation panel like any other.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from terminus.actions import NOT_EMPLOYERS, PLACEHOLDER, gmail_id_date
from terminus.models import STAGES, Job, JobEmail, Task

#: Words that don't tell two roles apart.
GENERIC = {"graduate", "graduates", "programme", "program", "scheme", "the", "and", "of", "for",
           "role", "position", "application", "job", "in", "at", "a", "an", "uk", "london"}
YEAR = re.compile(r"^20[23]\d$")  # intake years; programme numbers like 1954/1999 must survive
FORWARD = {"screening": 2, "interview": 3, "offer": 4}  # stages an acknowledgement can't undo
#: What can revive a rejected (or move on an offered) application. Not "screening": after a
#: rejection those are feedback reports and assessment results, not new progress.
REVIVE = {"interview", "offer"}
PIPELINE = {"saved": 0, "applied": 1, "screening": 2, "interview": 3, "offer": 4}
CLOSED = {"rejected", "withdrawn"}


def stamp(gmail_id: str) -> int:
    """Gmail's own millisecond timestamp for an id (0 if it isn't one)."""
    try:
        return int(gmail_id, 16) >> 20 if gmail_id_date(gmail_id) else 0
    except ValueError:
        return 0


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9&]+", text.lower())


def blank(role: str | None) -> bool:
    return not role or bool(PLACEHOLDER.match(role))


def roles_match(a: str, b: str) -> bool:
    """Loose: one contains the other, or a shared programme number, or same key words."""
    if blank(a) or blank(b):
        return False
    wa, wb = _words(a), _words(b)
    ja, jb = " ".join(wa), " ".join(wb)
    if ja in jb or jb in ja:
        return True
    nums_a = {w for w in wa if w.isdigit() and len(w) >= 3 and not YEAR.match(w)}
    nums_b = {w for w in wb if w.isdigit() and len(w) >= 3 and not YEAR.match(w)}
    if nums_a & nums_b:
        return True
    ka = {w for w in wa if w not in GENERIC and not w.isdigit()}
    kb = {w for w in wb if w not in GENERIC and not w.isdigit()}
    return bool(ka and kb and (ka <= kb or kb <= ka))


def companies_match(a: str, b: str) -> bool:
    """Same employer, allowing "Umbrella" vs "Umbrella HR"."""
    ja, jb = " ".join(_words(a)), " ".join(_words(b))
    return bool(ja and jb) and (ja == jb or ja.startswith(jb + " ") or jb.startswith(ja + " "))


@dataclass
class App:
    """One application while importing: an existing job or a new one."""

    company: str
    role: str
    stage: str
    job: Job | None = None
    new: list[dict] = field(default_factory=list)  # emails to link now, oldest first
    rows: list[dict] = field(default_factory=list)  # every email known for it (incl. linked)
    last_logged: int = 0  # Gmail timestamp of the newest email imported before

    @property
    def closed(self) -> bool:
        return self.stage in CLOSED


def _clean(e: dict) -> dict | None:
    """Normalise one service row; None if it isn't about one of the user's applications."""
    stage = str(e.get("stage") or "").strip().lower()
    company = " ".join(str(e.get("company") or "").split())
    gid = str(e.get("id") or "").strip()
    if (stage not in STAGES or not company or PLACEHOLDER.match(company)
            or NOT_EMPLOYERS.match(company) or not stamp(gid)):
        return None
    role = " ".join(str(e.get("role") or "").split())
    due = str(e.get("due") or "").strip()[:10]
    return {
        "id": gid, "company": company, "role": "" if blank(role) else role, "stage": stage,
        "from": str(e.get("from") or "").strip(), "subject": str(e.get("subject") or "").strip(),
        "summary": " ".join(str(e.get("summary") or "").split()),
        "next_step": _actionable(str(e.get("next_step") or "")),
        "due": due if re.match(r"^\d{4}-\d{2}-\d{2}$", due) else "",
        "date": gmail_id_date(gid).isoformat(),  # trust the id, not the label
        "ts": stamp(gid),
    }


def _actionable(step: str) -> str:
    """A next step worth a task: not a placeholder, not conditional ("If you have…")."""
    step = " ".join(step.split())
    if PLACEHOLDER.match(step) or re.match(r"(?i)^(if|should|in case|when ready)\b", step):
        return ""
    return step


def _find(apps: list[App], e: dict) -> App | None:
    same = [a for a in apps if companies_match(a.company, e["company"])]
    if e["role"]:
        hits = [a for a in same if roles_match(a.role, e["role"])]
        if hits:
            return min(hits, key=lambda a: (a.closed, -(a.last_logged or 0)))
        blanks = [a for a in same if blank(a.role) and not a.closed]
        return blanks[0] if len(blanks) == 1 else None
    # no role: an application that already has an email with this subject (a re-send)…
    subject = e["subject"].lower()
    if subject and (twins := [a for a in same if any(
            r["subject"].lower() == subject for r in a.rows)]):
        return twins[0]
    open_ = [a for a in same if not a.closed]
    if len(open_) == 1:  # …else the company's only open application…
        return open_[0]
    return next((a for a in same if a.role == "?"), None)  # …else its "?" application


def derive(rows: list[dict], start: str | None = None) -> dict:
    """Stage, status and next step of an application from its emails (oldest first).

    * A rejection/withdrawal always applies; from rejected/offer only a later
      stage (screening, interview, offer) moves it on.
    * A saved/applied email (often an automated "under review" note) never moves
      it back from screening, interview or offer.
    * Status: the newest email at the resulting stage.
    * Next step: since the last stage change, the newest email with a due date,
      else the newest with any next step; none once the application is closed.
    """
    stage, changed_at = start, 0  # start: the job's stage when no linked email explains it
    for i, r in enumerate(rows):
        new = _next_stage(stage, r["stage"])
        if new != stage:
            stage, changed_at = new, i
    at_stage = [r for r in rows if r["stage"] == stage] or rows
    since = rows[changed_at:]
    step = None
    if stage not in CLOSED:
        step = (next((r for r in reversed(since) if r.get("due") and r.get("next_step")), None)
                or next((r for r in reversed(since) if r.get("next_step")), None))
    return {
        "stage": stage,
        "status": at_stage[-1]["summary"],
        "next_step": step["next_step"] if step else "",
        "next_date": date.fromisoformat(step["due"]) if step and step.get("due") else None,
    }


def plan_job_import(
    rows: list[dict],
    jobs: list[Job],
    *,
    imported: dict[int, list[JobEmail]],
    open_tasks: dict[int, Task],
    logged_ids: set[str],
) -> list[dict]:
    """Proposal entries that bring the tracker in line with the service's labels.

    Emails already linked to a job stay with it (and are not added again), but
    their *current* labels count when working out the job's state, so earlier
    mislabelling gets corrected. Idempotent: a second run proposes nothing.
    """
    owner: dict[str, int] = {}
    apps: list[App] = []
    by_job: dict[int, App] = {}
    for j in jobs:
        app = App(j.company, j.role, j.stage, job=j)
        for m in imported.get(j.id, []):
            owner[m.gmail_id] = j.id
            if stamp(m.gmail_id):  # what we knew before, in case the service no longer lists it
                app.rows.append({"id": m.gmail_id, "ts": stamp(m.gmail_id), "stage": m.stage or j.stage,
                                 "summary": m.summary, "subject": m.subject, "next_step": "",
                                 "due": "", "known": True})
        if j.email_ref:
            owner.setdefault(j.email_ref, j.id)
        app.last_logged = max((r["ts"] for r in app.rows), default=0)
        apps.append(app)
        by_job[j.id] = app

    for e in sorted(filter(None, map(_clean, rows)), key=lambda e: e["ts"]):
        if e["stage"] == "none":
            continue
        if e["id"] in owner:  # already linked: refresh its labels, don't add it again
            app = by_job[owner[e["id"]]]
            app.rows = [r for r in app.rows if r["id"] != e["id"]] + [e | {"known": True,
                                                                             "fresh": True}]
            continue
        if e["id"] in logged_ids:
            continue
        app = _find(apps, e)
        if app is None:
            app = App(e["company"], e["role"] or "?", e["stage"])
            apps.append(app)
        elif blank(app.role) and e["role"]:
            app.role = e["role"]  # an email finally names the position
        app.rows.append(e | {"fresh": True})
        app.new.append(e)

    entries: list[dict] = []
    tasks: list[dict] = []
    for app in apps:
        if not any(r.get("fresh") for r in app.rows):
            continue  # the service said nothing about it this time: leave it as it is
        app.rows.sort(key=lambda r: r["ts"])
        known = any(r.get("known") for r in app.rows)
        state = derive(app.rows, start=None if known or app.job is None else app.job.stage)
        emails = [{k: e[k] for k in ("id", "date", "from", "subject", "summary", "stage")}
                  for e in app.new]
        if app.job is None:
            first = app.new[0]
            entries.append({"op": "job_add", "company": app.company, "role": app.role,
                            "email_ref": first["id"], "import": True, "emails": emails,
                            "applied_on": None if state["stage"] == "saved" else first["date"],
                            **state, "next_date": state["next_date"] and state["next_date"].isoformat()})
            ref, task = {"job_ref": first["id"]}, None
        else:
            j = app.job
            changed = {k: v for k, v in state.items() if getattr(j, k) != v}
            if app.role != j.role:
                changed["role"] = app.role
            if changed or emails:
                entries.append({"op": "job_update", "job": j.id, "import": True,
                                "emails": emails, **changed})
            ref, task = {"job": j.id}, open_tasks.get(j.id)
        tasks.extend(_task_entries(app, state, task, ref))
    return entries + tasks


def _next_stage(current: str | None, incoming: str) -> str:
    if current is None or incoming in CLOSED:
        return incoming
    if current in ("rejected", "offer", "withdrawn"):
        return incoming if incoming in REVIVE and incoming != current else current
    if PIPELINE.get(incoming, 0) < PIPELINE.get(current, 0):
        return current  # reminders and acknowledgements don't undo progress
    return incoming


def _task_entries(app: App, state: dict, task: Task | None, ref: dict) -> list[dict]:
    if state["stage"] in CLOSED:
        return [{"op": "job_task", "action": "close", "task": task.id, **ref}] if task else []
    step = state["next_step"]
    if not step:
        # the task was made from a next step that no longer applies: close it too
        old = app.job.next_step.rstrip(". ") if app.job and app.job.next_step else ""
        if task and old and task.title == f"{app.company}: {old}":
            return [{"op": "job_task", "action": "close", "task": task.id,
                     "reason": "next step no longer applies", **ref}]
        return []
    title = f"{app.company}: {step.rstrip('. ')}"
    due = state["next_date"].isoformat() if state["next_date"] else ""
    if task is None:
        if app.job and app.job.task_step == step:
            return []  # its task was completed or deleted: don't bring it back
        return [{"op": "job_task", "action": "create", "title": title, "due": due,
                 "step": step, **ref}]
    # an overdue task has been carried forward to today; its original day is overdue_from
    current_due = (task.overdue_from or task.day).isoformat()
    if task.title != title or (due and current_due != due):
        return [{"op": "job_task", "action": "update", "task": task.id, "title": title,
                 "due": due, "step": step, **ref}]
    return []
