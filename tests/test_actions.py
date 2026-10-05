from datetime import date, time, timedelta

import pytest

from conftest import DAY
from terminus.actions import VisibleFilter, extract, parse_day, validate
from terminus.models import Task

NOW = time(10, 0)  # DAY is Friday 2026-09-25


def tasks():
    overdue = Task(5, "Find a job", DAY, 2, overdue_from=DAY - timedelta(days=13))
    done = Task(7, "Standup", DAY, 3, status="done")
    later = Task(9, "Gym", DAY + timedelta(days=2), 1, fixed_start=time(18))
    return {t.id: t for t in (Task(1, "Write", DAY, 1, estimate=2, tags=["work"]), overdue, done, later)}


def check(entries):
    return validate(entries, tasks(), today=DAY, now_time=NOW)


def test_extract_splits_prose_and_block():
    text = 'Sure — two changes.\n\n```terminus-actions\n[{"op": "done", "task": 1}]\n```\n'
    prose, entries, error = extract(text)
    assert prose == "Sure — two changes." and entries == [{"op": "done", "task": 1}] and error is None
    assert extract("no block here") == ("no block here", None, None)
    _, entries, error = extract("x\n```terminus-actions\n[{bad json\n```")
    assert entries is None and "valid JSON" in error
    # an unterminated block at the end of the stream still parses
    assert extract('ok\n```terminus-actions\n{"op": "done", "task": 1}')[1] == [{"op": "done", "task": 1}]


@pytest.mark.parametrize("value,expected", [
    ("today", DAY), ("tomorrow", DAY + timedelta(days=1)), ("2026-10-02", date(2026, 10, 2)),
    ("monday", date(2026, 9, 28)), ("Friday", date(2026, 10, 2)),  # next Friday, not today
])
def test_parse_day(value, expected):
    assert parse_day(value, DAY) == expected


def test_valid_changes_get_readable_summaries():
    p = check([
        {"op": "add", "title": "Email Kim back", "day": "tomorrow", "at": "9:00", "tags": "#job"},
        {"op": "schedule", "task": 5, "day": "today"},
        {"op": "schedule", "task": 9, "at": None},
        {"op": "done", "task": "1"},
        {"op": "edit", "task": 1, "estimate": 3, "tags": []},
        {"op": "delete", "task": 7},
    ])
    assert p.rejected == []
    assert [c.summary for c in p.changes] == [
        "Add “Email Kim back” · tomorrow 09:00 ~1 #job",
        "Move “Find a job” · overdue → today",
        "Move “Gym” · Sun 27 Sep 18:00 → Sun 27 Sep",
        "Mark “Write” done",
        "Edit “Write” · ~2 → ~3, tags → none",
        "Delete “Standup”",
    ]
    assert p.changes[0].day == DAY + timedelta(days=1) and p.changes[0].at == time(9)
    assert p.changes[2].unpin


def test_bad_changes_are_rejected_with_reasons():
    p = check([
        {"op": "done", "task": 99},
        {"op": "done", "task": 7},
        {"op": "schedule", "task": 1, "day": "2026-09-01"},
        {"op": "schedule", "task": 1, "at": "08:00"},
        {"op": "schedule", "task": 1},
        {"op": "add", "title": ""},
        {"op": "add", "title": "Huge", "estimate": 40},
        {"op": "edit", "task": 1, "title": "Write"},
        {"op": "launch_rockets"},
        "not an object",
    ])
    assert p.changes == []
    assert p.rejected == [
        "#1 done: no task [#99]",
        "#2 done: “Standup” is already done",
        "#3 schedule: 2026-09-01 is in the past",
        "#4 schedule: 08:00 today has already passed",
        "#5 schedule: needs a day or a time",
        "#6 add: needs a title",
        "#7 add: estimate 40 is outside 1–12 pomodoros",
        "#8 edit: nothing to change on “Write”",
        "#9 launch_rockets: unknown op 'launch_rockets'",
        "#10 ?: not an object",
    ]


def test_morning_pin_on_a_future_task_is_fine():
    # Gym is on Sunday: 08:00 there hasn't passed even though it's 10:00 today
    assert check([{"op": "schedule", "task": 9, "at": "08:00"}]).rejected == []


def test_filter_hides_block_even_when_fence_is_split():
    f = VisibleFilter()
    out = "".join(f.feed(c) for c in ["Moving it", " now.\n``", "`terminus-", 'actions\n[{"op"', ": 1}]\n```"])
    assert out + f.flush() == "Moving it now.\n"
    g = VisibleFilter()
    assert g.feed("Use `code` ``") == "Use `code` "  # held back: could be a fence
    assert g.feed(" no") == "`` no" and g.flush() == ""


# ── jobs ──

def jobs():
    from terminus.models import Job
    return {3: Job(3, "Northwind", "Quant Research Intern", "applied", email_ref="m-1")}


def check_jobs(entries):
    return validate(entries, tasks(), today=DAY, now_time=NOW, jobs=jobs())


def test_job_changes():
    p = check_jobs([
        {"op": "job_add", "company": "Acme", "role": "Junior Analyst", "stage": "Applied",
         "link": "https://acme.example/jobs/1", "email_ref": "m-2"},
        {"op": "job_update", "job": 3, "stage": "Interview scheduled", "next_step": "Interview",
         "next_date": "2026-09-30"},
    ])
    assert p.rejected == []
    add, update = p.changes
    assert add.summary == "Track job “Acme — Junior Analyst” · Applied"
    assert add.job_fields["applied_on"] == DAY and add.job_fields["email_ref"] == "m-2"
    assert update.summary == ("Update job “Northwind — Quant Research Intern” · Applied → Interview"
                              " · next: Wed 30 Sep Interview")
    assert update.job_fields == {"stage": "interview", "next_step": "Interview",
                                 "next_date": date(2026, 9, 30)}


def test_bad_job_changes_are_rejected():
    p = check_jobs([
        {"op": "job_add", "company": "northwind", "role": "quant research intern"},
        {"op": "job_add", "company": "Other", "email_ref": "m-1"},
        {"op": "job_add", "role": "No company"},
        {"op": "job_add", "company": "X", "link": "javascript:alert(1)"},
        {"op": "job_update", "job": 42, "stage": "offer"},
        {"op": "job_update", "job": 3, "stage": "applied"},
    ])
    assert p.changes == []
    assert p.rejected == [
        "#1 job_add: already tracked as [J#3] Northwind — Quant Research Intern — use job_update",
        "#2 job_add: that email is already tracked as [J#3] Northwind — Quant Research Intern",
        "#3 job_add: needs a company",
        "#4 job_add: link 'javascript:alert(1)' isn't a web address",
        "#5 job_update: no job [J#42]",
        "#6 job_update: nothing to change on Northwind — Quant Research Intern",
    ]


def test_placeholder_roles_are_dropped():
    p = check_jobs([{"op": "job_add", "company": "Contoso", "role": "(position unknown)",
                     "next_step": "N/A"}])
    assert p.changes[0].summary == "Track job “Contoso” · Applied"
    assert "role" not in p.changes[0].job_fields and "next_step" not in p.changes[0].job_fields


def test_parse_email_list():
    from terminus.actions import parse_email_list
    text = 'Here you go:\n```json\n[{"id": 12, "from": "Acme", "subject": "Thanks\\nfor applying",' \
           ' "summary": "applied"}, {"id": "x"}, "junk"]\n```'
    assert parse_email_list(text) == [{"id": "12", "from": "Acme", "subject": "Thanks for applying",
                                       "date": "", "summary": "applied"}]
    assert parse_email_list("[]") == []
    with pytest.raises(ValueError, match="didn't return"):
        parse_email_list("I couldn't access Gmail.")


def test_job_entries_from_emails():
    from terminus.actions import job_entries_from_emails
    from terminus.models import Job
    known = {3: Job(3, "Northwind", "Quant Research Intern", "interview"),
             4: Job(4, "INITECH", "", "rejected"),
             5: Job(5, "Umbrella", "", "applied")}
    emails = [
        {"id": "a", "date": "2026-09-20", "company": "Acme", "role": "", "stage": "saved",
         "subject": "Finish your application", "summary": "Reminder to finish"},
        {"id": "b", "date": "2026-09-22", "company": "acme", "role": "Analyst", "stage": "applied",
         "subject": "Thanks for applying", "summary": "Application received"},
        {"id": "c", "date": "2026-09-21", "company": "Northwind", "role": "Quant Research Intern",
         "stage": "applied", "summary": "Old confirmation"},  # older news: stage stays interview
        {"id": "d", "date": "2026-09-24", "company": "Northwind", "role": "", "stage": "rejected",
         "summary": "Not progressing"},
        {"id": "e", "date": "2026-09-23", "company": "INITECH", "stage": "interview"},  # closed: leave
        {"id": "f", "date": "2026-09-25", "company": "beBee", "stage": "none"},
        {"id": "g", "date": "2026-09-25", "company": "", "stage": "applied"},
        {"id": "h", "date": "2026-09-25", "company": "Umbrella", "stage": "applied",
         "subject": "Your application was viewed", "summary": "Application viewed"},
        {"id": "old", "date": "2026-09-19", "company": "Umbrella", "stage": "applied"},  # logged
        {"id": "u", "date": "2026-09-25", "company": "Unknown", "stage": "applied"},  # unnamed
    ]
    out = job_entries_from_emails(emails, known, logged={"old"}, today=DAY)
    acme, p72, city = out
    assert acme["op"] == "job_add" and acme["company"] == "Acme" and acme["role"] == "Analyst"
    assert acme["stage"] == "applied" and acme["status"] == "Application received"
    assert [m["id"] for m in acme["emails"]] == ["a", "b"]
    assert p72 == {"op": "job_update", "job": 3, "stage": "rejected", "status": "Not progressing",
                   "emails": [p72["emails"][0], p72["emails"][1]]}
    assert [m["id"] for m in p72["emails"]] == ["c", "d"]
    assert city["job"] == 5 and "stage" not in city and city["status"] == "Application viewed"
    assert [m["id"] for m in city["emails"]] == ["h"]


def test_assessment_email_tracks_job_and_proposes_task():
    from terminus.actions import job_entries_from_emails
    emails = [{"id": "z1", "date": "2026-09-26", "company": "Fabrikam", "role": "Graduate Analyst",
               "stage": "screening", "subject": "Online assessment: Graduate Analyst",
               "summary": "Online assessment invite", "due": "2026-10-03"}]
    job, task = job_entries_from_emails(emails, {}, today=DAY)
    assert job["op"] == "job_add" and job["stage"] == "screening"
    assert job["next_step"] == "Complete assessment" and job["next_date"] == "2026-10-03"
    assert task == {"op": "add", "title": "Fabrikam Graduate Analyst assessment", "day": "2026-10-03",
                    "estimate": 2, "tags": ["job"]}
    # a deadline that has passed: still tracked, but no task
    emails[0]["due"] = "2026-09-01"
    assert [e["op"] for e in job_entries_from_emails(emails, {}, today=DAY)] == ["job_add"]
    # a screening phone call isn't an assessment task
    call = [{"id": "z2", "date": "2026-09-26", "company": "Contoso", "stage": "screening",
             "subject": "Quick chat", "summary": "Recruiter phone call"}]
    assert [e["op"] for e in job_entries_from_emails(call, {}, today=DAY)] == ["job_add"]


def test_validate_job_emails_and_log_only_updates():
    from terminus.models import Job
    jobs = {5: Job(5, "Umbrella", "", "applied")}
    mail = [{"id": "h", "date": "2026-09-25", "from": "LinkedIn", "subject": "Viewed",
             "summary": "Application viewed", "stage": "applied"}]
    p = validate([{"op": "job_update", "job": 5, "emails": mail, "status": "Application viewed"},
                  {"op": "job_update", "job": 5, "emails": mail}],
                 {}, today=DAY, now_time=NOW, jobs=jobs)
    assert [c.summary for c in p.changes] == [
        "Update job “Umbrella” · status: Application viewed · +1 email",
        "Log job “Umbrella” · +1 email",
    ]
    assert p.changes[1].job_fields == {} and p.changes[1].emails[0]["id"] == "h"


def test_email_dates_come_from_gmail_ids():
    from terminus.actions import gmail_id_date, parse_email_list
    assert gmail_id_date("1a0d8ba30a200001") == date(2026, 9, 25)  # INITECH update, 25 Sep 13:21 UTC
    assert gmail_id_date("m-1") is None and gmail_id_date("") is None
    listed = parse_email_list('[{"id": "1a0d4bb881c61918", "subject": "Thanks", "date": "2026-09-30"}]')
    assert listed[0]["date"] == "2026-09-24"  # the model said the 30th; the id says the 24th


def test_recent_emails_drops_memory_answers():
    from datetime import datetime as dt
    from conftest import gmail_id
    from terminus.actions import recent_emails
    real = {"id": gmail_id(dt(2026, 9, 24, 12))}
    old = {"id": gmail_id(dt(2026, 9, 1, 12))}
    fake = {"id": "initech-rejection"}
    kept, dropped = recent_emails([real, old, fake], since=date(2026, 9, 18))
    assert kept == [real] and dropped == 2


def test_platforms_are_never_the_company():
    from terminus.actions import job_entries_from_emails
    emails = [{"id": "x1", "date": "2026-09-24", "company": "HireVue", "stage": "screening"},
              {"id": "x2", "date": "2026-09-24", "company": "LinkedIn", "stage": "applied"},
              {"id": "x3", "date": "2026-09-24", "company": "Codility", "stage": "screening"}]
    assert job_entries_from_emails(emails, {}, today=DAY) == []


def test_check_gmail_search():
    import pytest
    from terminus.actions import check_gmail_search
    ok = ["mcp__gmail_readonly__search_emails · running", "mcp__gmail_readonly__search_emails · completed"]
    check_gmail_search("[]", ok)  # fine
    with pytest.raises(ValueError, match="sign-in needs renewing"):
        check_gmail_search("Error: Invalid grant — unable to access inbox threads.", ok)
    with pytest.raises(ValueError, match="without searching Gmail"):
        check_gmail_search('[{"id": "1a0d112233445566"}]', [])


def test_fabricated_ids_are_dropped():
    from terminus.actions import recent_emails
    kept, dropped = recent_emails([{"id": "1a0d112233445566"}, {"id": "1a0d900123456789"}],
                                  since=date(2026, 9, 1))
    assert kept == [] and dropped == 2
