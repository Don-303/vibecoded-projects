from datetime import date, datetime

from terminus.jobs_stats import compute, day_label, group_jobs, sparkline
from terminus.models import Job, JobEmail

TODAY = date(2026, 9, 30)  # Wednesday


def test_group_jobs_by_day():
    jobs = [Job(1, "Old", applied_on=date(2026, 7, 1)), Job(2, "Saved", stage="saved"),
            Job(3, "B", applied_on=date(2026, 9, 29)), Job(4, "Gone", stage="rejected",
            applied_on=date(2026, 9, 29)), Job(5, "A", applied_on=date(2026, 9, 29)),
            Job(6, "C", applied_on=date(2026, 9, 30))]
    groups = group_jobs(jobs)
    assert [d for d, _ in groups] == [date(2026, 9, 30), date(2026, 9, 29), date(2026, 7, 1), None]
    assert [j.company for j in groups[1][1]] == ["A", "B", "Gone"]  # open first, closed last


def test_day_labels():
    assert day_label(date(2026, 9, 30), TODAY) == "today"
    assert day_label(date(2026, 9, 29), TODAY) == "yesterday"
    assert day_label(date(2026, 9, 26), TODAY) == "4 days ago"
    assert day_label(date(2026, 9, 1), TODAY) == ""
    assert day_label(date(2025, 12, 1), TODAY) == "2025"


def test_compute_stats():
    jobs = [
        Job(1, "Fabrikam", stage="screening", applied_on=date(2026, 9, 24),
            next_step="Assessment", next_date=date(2026, 10, 3)),
        Job(2, "INITECH", stage="rejected", applied_on=date(2026, 9, 20)),
        Job(3, "Contoso", applied_on=date(2026, 9, 29)),
        Job(4, "Tyrell", stage="saved"),
        Job(5, "Late", applied_on=date(2026, 9, 1), next_date=date(2026, 9, 29), next_step="Call"),
    ]
    emails = {1: [JobEmail("z", date(2026, 9, 26), "", "", "", "screening")]}
    history = {2: [("applied", datetime(2026, 9, 20)), ("rejected", datetime(2026, 9, 25, 9))]}
    s = compute(jobs, TODAY, history=history, emails=emails)
    assert s.by_stage["applied"] == 2 and s.by_stage["saved"] == 1
    assert s.applied_this_week == 1 and s.applied_last_week == 1  # Contoso; Fabrikam (INITECH is w/c 14th)
    assert len(s.weekly) == 8 and sum(s.weekly) == 4
    assert (s.responded, s.submitted) == (2, 4) and s.response_rate == 0.5
    assert s.avg_days_to_reply == 3.5  # Fabrikam: email 2 days after; INITECH: stage change 5 days after
    assert s.due == [(date(2026, 9, 29), "Late: Call"), (date(2026, 10, 3), "Fabrikam: Assessment")]


def test_sparkline():
    assert sparkline([0, 0]) == "▁▁"
    assert sparkline([0, 2, 4]) == "▁▅█"
