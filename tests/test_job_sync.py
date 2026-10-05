from datetime import date, datetime

from conftest import gmail_id
from terminus.job_sync import companies_match, plan_job_import, roles_match
from terminus.models import Job, JobEmail, Task


def row(company, stage, when, *, role="", n=0, next_step="", due="", summary=""):
    return {"id": gmail_id(when, n), "from": company, "subject": f"{company} {stage}",
            "date": "1999-01-01",  # deliberately wrong: the id's own date must win
            "company": company, "role": role, "stage": stage,
            "summary": summary or f"{company} {stage}", "next_step": next_step, "due": due}


def plan(rows, jobs=(), imported=None, open_tasks=None, logged=()):
    return plan_job_import(rows, list(jobs), imported=imported or {},
                           open_tasks=open_tasks or {}, logged_ids=set(logged))


def test_loose_role_and_company_matching():
    assert roles_match("2027 Finance Graduate Programme - 1954", "Finance Graduate Programme")
    assert roles_match("Programme 1954", "2027 Finance Grad - 1954")  # shared number
    assert roles_match("Wealth Management (Portfolio Management) Graduate Programme",
                       "2027 Wealth Management (Portfolio Management) Graduate Programme - 1999")
    assert not roles_match("2027 Finance Graduate Programme", "2027 Wealth Management Programme")
    assert not roles_match("?", "Finance")
    assert companies_match("Umbrella", "Umbrella HR") and not companies_match("Fabrikam", "Fabrikam2")


def test_groups_emails_and_newest_wins():
    rows = [
        row("Fabrikam", "applied", datetime(2026, 9, 30, 9), role="Graduate Analyst"),
        row("Fabrikam", "screening", datetime(2026, 10, 1, 9), role="Graduate Analyst",
            next_step="Complete assessment", due="2026-10-07", summary="Assessment invite"),
        row("Soylent", "applied", datetime(2026, 9, 28, 9)),
        row("Soylent", "rejected", datetime(2026, 10, 2, 9)),
        row("Wonka", "screening", datetime(2026, 10, 2, 9)),  # platform, never a company
        row("Newsletter Co", "none", datetime(2026, 10, 2, 9)),
    ]
    out = plan(rows)
    by = {e.get("company", "task"): e for e in out}
    fabrikam, bs, task = by["Fabrikam"], by["Soylent"], by["task"]
    assert fabrikam["op"] == "job_add" and fabrikam["stage"] == "screening" and fabrikam["role"] == "Graduate Analyst"
    assert fabrikam["status"] == "Assessment invite" and fabrikam["next_date"] == "2026-10-07"
    assert fabrikam["applied_on"] == "2026-09-30" and len(fabrikam["emails"]) == 2
    assert bs["stage"] == "rejected" and bs["role"] == "?"
    assert task == {"op": "job_task", "action": "create", "title": "Fabrikam: Complete assessment",
                    "due": "2026-10-07", "job_ref": fabrikam["email_ref"]}


def test_updates_existing_job_and_its_one_task():
    fabrikam = Job(3, "Fabrikam", "Graduate Analyst", "screening")
    old = gmail_id(datetime(2026, 10, 1, 9), 1)
    imported = {3: [JobEmail(old, date(2026, 10, 1), "", "", "", "screening")]}
    task = Task(15, "Fabrikam Graduate Analyst assessment", date(2026, 10, 5), 1, job_id=3)
    rows = [row("Fabrikam", "screening", datetime(2026, 10, 5, 9), role="Graduate Analyst", n=2,
                next_step="Complete assessment", due="2026-10-07", summary="Reminder")]
    upd, t = plan(rows, [fabrikam], imported, {3: task}, logged={old})
    assert upd == {"op": "job_update", "job": 3, "import": True, "emails": upd["emails"],
                   "status": "Reminder", "next_step": "Complete assessment",
                   "next_date": date(2026, 10, 7)}  # stage unchanged, so not proposed
    assert t == {"op": "job_task", "action": "update", "task": 15, "job": 3,
                 "title": "Fabrikam: Complete assessment", "due": "2026-10-07"}
    # running again after applying: everything matches, so nothing is proposed
    fabrikam.status, fabrikam.next_step, fabrikam.next_date = "Reminder", "Complete assessment", date(2026, 10, 7)
    imported[3].append(JobEmail(rows[0]["id"], date(2026, 10, 5), "", rows[0]["subject"],
                                "Reminder", "screening"))
    task.title, task.day = "Fabrikam: Complete assessment", date(2026, 10, 7)
    assert plan(rows, [fabrikam], imported, {3: task}, logged={old, rows[0]["id"]}) == []


def test_rejection_closes_the_task_and_older_news_cannot_override():
    p72 = Job(16, "Northwind", "?", "applied")
    task = Task(30, "Northwind: Online test", date(2026, 10, 6), 1, job_id=16)
    rows = [row("Northwind", "rejected", datetime(2026, 10, 4, 9), n=1),
            row("Northwind", "applied", datetime(2026, 10, 3, 9), n=2)]  # older
    upd, close = plan(rows, [p72], open_tasks={16: task})
    assert upd["stage"] == "rejected" and len(upd["emails"]) == 2
    assert close == {"op": "job_task", "action": "close", "task": 30, "job": 16}


def test_rejected_never_falls_back_to_applied():
    initech = Job(12, "INITECH", "ML Researcher", "rejected")
    out = plan([row("INITECH", "applied", datetime(2026, 10, 3, 9), role="ML Researcher")], [initech])
    assert out[0]["op"] == "job_update" and "stage" not in out[0]  # stays rejected


def test_no_role_joins_the_only_open_application_else_question_mark():
    s_wm = Job(7, "Globex", "Wealth Management", "screening")
    s_fin = Job(8, "Globex", "Finance Graduate Programme", "applied")
    cl = Job(13, "Umbrella HR", "", "applied")
    rows = [row("Umbrella", "applied", datetime(2026, 10, 2, 9), n=1),
            row("Globex", "screening", datetime(2026, 10, 2, 9), n=2)]
    city, globex_q = plan(rows, [s_wm, s_fin, cl])
    assert city["op"] == "job_update" and city["job"] == 13  # "Umbrella" ≈ "Umbrella HR"
    assert globex_q["op"] == "job_add" and globex_q["role"] == "?"  # two open: ambiguous


def test_due_only_when_given_and_no_date_invented():
    out = plan([row("Wayne Enterprises", "screening", datetime(2026, 10, 2, 9),
                    next_step="Book a video interview")])
    assert out[1]["due"] == "" and out[0]["next_date"] is None


def test_linked_emails_are_relabelled_not_readded():
    """An email an old scan linked with the wrong label gets corrected, not duplicated."""
    p72 = Job(16, "Northwind", "?", "applied")
    gid = gmail_id(datetime(2026, 10, 3, 20), 9)
    imported = {16: [JobEmail(gid, date(2026, 10, 3), "", "Northwind update", "", "applied")]}
    rows = [row("Northwind", "rejected", datetime(2026, 10, 3, 20), n=9)]
    assert rows[0]["id"] == gid
    (upd,) = plan(rows, [p72], imported, logged={gid})
    assert upd["stage"] == "rejected" and upd["emails"] == []


def test_acknowledgement_does_not_undo_progress_and_dated_step_wins():
    wm = Job(7, "Globex", "2027 Wealth Management Graduate Programme - 1999", "screening")
    rows = [
        row("Globex", "screening", datetime(2026, 10, 2, 15), n=1,
            next_step="Complete the Stage 2 assessment", due="2026-10-09"),
        row("Globex", "screening", datetime(2026, 10, 2, 18), n=2, role="",
            next_step="Review feedback report"),
        row("Globex", "applied", datetime(2026, 10, 5, 16), n=3,
            role="2027 Wealth Management Graduate Programme - 1999", summary="Under review"),
    ]
    upd, task = plan(rows, [wm])
    assert "stage" not in upd  # stays at screening
    assert upd["next_step"] == "Complete the Stage 2 assessment"
    assert upd["next_date"] == date(2026, 10, 9)
    assert task["title"] == "Globex: Complete the Stage 2 assessment"


def test_jobs_the_service_does_not_mention_are_left_alone():
    cyberdyne = Job(10, "Cyberdyne", "Quantitative Researcher", "saved", next_step="Check status")
    gid = gmail_id(datetime(2026, 9, 20, 9), 4)
    imported = {10: [JobEmail(gid, date(2026, 9, 20), "", "Added", "Added to list", "saved")]}
    assert plan([], [cyberdyne], imported, logged={gid}) == []
