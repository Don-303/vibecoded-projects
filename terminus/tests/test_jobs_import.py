from datetime import date, datetime

import pytest

from terminus.jobs_import import import_csv, map_stage, match_columns, parse_date

NOW = datetime(2026, 9, 25, 10)

CSV = """﻿Company,Position,Status,Date Applied,Job Link,Notes
Northwind,Quant Research Intern,Interview scheduled,12/09/2026,https://example.com/p72,Tue 14:00
Acme,Junior Analyst,Applied,2026-09-01,,
Vandelay,Trading Intern,Rejected - unsuccessful,3 Sep 2026,,
,,,,,
Citadel,,Online test (HireVue),,,
"""


@pytest.mark.parametrize("text,stage", [
    ("Interview scheduled", "interview"), ("Rejected - unsuccessful", "rejected"),
    ("Online test (HireVue)", "screening"), ("Offer!", "offer"), ("To apply", "saved"),
    ("Withdrew application", "withdrawn"), ("Submitted", "applied"), ("???", "applied"),
])
def test_map_stage(text, stage):
    assert map_stage(text) == stage


def test_parse_date_prefers_uk_day_first():
    assert parse_date("03/09/2026") == date(2026, 9, 3)
    assert parse_date("3 Sep 2026") == date(2026, 9, 3)
    assert parse_date("nope") is None


def test_match_columns():
    cols = match_columns(["Company", "Position", "Status", "Date Applied", "Job Link", "Notes"])
    assert cols == {"company": "Company", "role": "Position", "stage": "Status",
                    "applied_on": "Date Applied", "link": "Job Link", "notes": "Notes"}


def test_import_csv(db, tmp_path):
    path = tmp_path / "tracker.csv"
    path.write_text(CSV, encoding="utf-8")
    dry = import_csv(db, path, now=NOW, dry_run=True)
    assert len(dry.added) == 4 and db.list_jobs() == []
    result = import_csv(db, path, now=NOW)
    assert result.added == ["Northwind — Quant Research Intern · interview", "Acme — Junior Analyst · applied",
                            "Vandelay — Trading Intern · rejected", "Citadel · screening"]
    p72 = db.find_job("Northwind", "Quant Research Intern")
    assert p72.applied_on == date(2026, 9, 12) and p72.link == "https://example.com/p72"
    again = import_csv(db, path, now=NOW)
    assert again.added == [] and len(again.skipped) == 4


def test_import_needs_company_column(db, tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("Name,When\nx,y\n")
    with pytest.raises(ValueError, match="no company column"):
        import_csv(db, path, now=NOW)
