"""Grouping and summary numbers for the Jobs page. Pure functions — no database access."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from terminus.models import Job, JobEmail

#: Stages that mean the employer replied with something beyond "we got it".
RESPONSE_STAGES = {"screening", "interview", "offer", "rejected"}
SPARK = "▁▂▃▄▅▆▇█"
WEEKS = 8


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())  # Monday


def group_label(applied: date | None, today: date) -> str:
    if applied is None:
        return "No date"
    this_week = week_start(today)
    start = week_start(applied)
    if start >= this_week:
        return "This week"
    if start == this_week - timedelta(weeks=1):
        return "Last week"
    if start > this_week - timedelta(weeks=WEEKS):
        return f"w/c {start:%d %b}"
    return f"{applied:%b %Y}"


def group_jobs(jobs: list[Job], today: date) -> list[tuple[str, list[Job]]]:
    """Jobs grouped by when they were applied for: newest group first, "No date" last.

    Within a group, open applications come before closed ones, newest first.
    """
    ordered = sorted(jobs, key=lambda j: (j.applied_on is None, -(j.applied_on or date.min).toordinal()))
    groups: dict[str, list[Job]] = {}
    for job in ordered:
        groups.setdefault(group_label(job.applied_on, today), []).append(job)
    return [
        (label, sorted(members, key=lambda j: (j.closed, -(j.applied_on or date.min).toordinal(),
                                                j.company.lower())))
        for label, members in groups.items()
    ]


@dataclass
class JobStats:
    by_stage: Counter
    applied_this_week: int
    applied_last_week: int
    weekly: list[int]  # applications per week, oldest first, ending this week
    responded: int  # applications that got past "applied" (or were rejected)
    submitted: int  # applications actually sent (not "saved")
    avg_days_to_reply: float | None
    due: list[tuple[date, str]] = field(default_factory=list)  # upcoming next steps

    @property
    def response_rate(self) -> float | None:
        return self.responded / self.submitted if self.submitted else None


def compute(
    jobs: list[Job],
    today: date,
    *,
    history: dict[int, list[tuple[str, datetime]]] | None = None,
    emails: dict[int, list[JobEmail]] | None = None,
    horizon: int = 7,
) -> JobStats:
    history = history or {}
    emails = emails or {}
    this_week = week_start(today)
    weekly = [0] * WEEKS
    for j in jobs:
        if j.applied_on and j.stage != "saved":
            weeks_ago = (this_week - week_start(j.applied_on)).days // 7
            if 0 <= weeks_ago < WEEKS:
                weekly[WEEKS - 1 - weeks_ago] += 1

    submitted = [j for j in jobs if j.stage != "saved"]
    responded = [j for j in submitted if j.stage in RESPONSE_STAGES or any(
        s in RESPONSE_STAGES for s, _ in history.get(j.id, []))]

    waits = []
    for j in responded:
        if not j.applied_on:
            continue
        # Prefer the date of the reply email; fall back to when the stage changed here.
        dates = [m.sent_on for m in emails.get(j.id, []) if m.stage in RESPONSE_STAGES and m.sent_on]
        if not dates:
            dates = [at.date() for s, at in history.get(j.id, []) if s in RESPONSE_STAGES]
        if dates:
            waits.append(max(0, (min(dates) - j.applied_on).days))

    due = sorted(
        (j.next_date, f"{j.company}: {j.next_step}" if j.next_step else j.company)
        for j in jobs
        if j.next_date and not j.closed and j.next_date <= today + timedelta(days=horizon)
    )
    return JobStats(
        by_stage=Counter(j.stage for j in jobs),
        applied_this_week=weekly[-1],
        applied_last_week=weekly[-2],
        weekly=weekly,
        responded=len(responded),
        submitted=len(submitted),
        avg_days_to_reply=sum(waits) / len(waits) if waits else None,
        due=due,
    )


def sparkline(values: list[int]) -> str:
    peak = max(values, default=0)
    if not peak:
        return SPARK[0] * len(values)
    return "".join(SPARK[min(len(SPARK) - 1, round(v / peak * (len(SPARK) - 1)))] for v in values)
