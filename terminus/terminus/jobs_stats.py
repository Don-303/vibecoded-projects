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


def group_jobs(jobs: list[Job]) -> list[tuple[date | None, list[Job]]]:
    """Jobs grouped by the day they were applied for: newest day first, undated last.

    Within a day, open applications come before closed ones.
    """
    groups: dict[date | None, list[Job]] = {}
    for job in jobs:
        groups.setdefault(job.applied_on, []).append(job)
    days = sorted((d for d in groups if d is not None), reverse=True)
    if None in groups:
        days.append(None)
    return [(d, sorted(groups[d], key=lambda j: (j.closed, j.company.lower()))) for d in days]


def day_label(day: date | None, today: date) -> str:
    """The small label next to a day heading: Today / Yesterday / 3 days ago / …"""
    if day is None:
        return "no date"
    ago = (today - day).days
    if ago == 0:
        return "today"
    if ago == 1:
        return "yesterday"
    if 1 < ago < 7:
        return f"{ago} days ago"
    if day.year != today.year:
        return str(day.year)
    return ""


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
