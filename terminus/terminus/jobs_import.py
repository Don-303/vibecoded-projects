"""Import job applications from a CSV export (e.g. Google Sheets → File → Download → CSV).

Columns are matched by name, loosely: "Company"/"Employer", "Role"/"Position"/"Title",
"Status"/"Stage", "Date applied"/"Applied", "Link"/"URL", "Notes", "Next step". Free-text
statuses are mapped onto Terminus' stages. Rows already tracked (same company and role)
are skipped, so importing twice is harmless.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from terminus.db import Database

ALIASES = {
    "company": ["company", "employer", "organisation", "organization", "firm", "company name"],
    "role": ["role", "position", "title", "job title", "job", "job role"],
    "stage": ["stage", "status", "state", "outcome"],
    "applied_on": ["date applied", "applied", "applied on", "application date", "date"],
    "next_step": ["next step", "next steps", "next action", "follow up", "follow-up"],
    "next_date": ["next date", "follow up date", "follow-up date", "interview date", "deadline"],
    "link": ["link", "url", "job link", "posting", "job posting", "listing"],
    "notes": ["notes", "note", "comments", "comment"],
}

#: Checked in order; the first keyword found in the status text wins.
STAGE_WORDS = [
    ("withdrawn", ["withdr", "declined", "not interested"]),
    ("rejected", ["reject", "unsuccessful", "no longer", "closed", "turned down", "denied"]),
    ("offer", ["offer", "accepted"]),
    ("interview", ["interview", "assessment centre", "assessment center", "superday", "onsite",
                   "final round"]),
    ("screening", ["screen", "phone", "hirevue", "online test", "assessment", "test", "oa"]),
    ("applied", ["applied", "submitted", "sent", "pending", "waiting"]),
    ("saved", ["saved", "wishlist", "to apply", "interested", "todo", "to do", "planned"]),
]

DATE_FORMATS = ["%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d.%m.%Y", "%d %b %Y",
                "%d %B %Y", "%b %d, %Y", "%B %d, %Y", "%m/%d/%Y"]  # UK day-first before US


@dataclass
class ImportResult:
    added: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    columns: dict[str, str] = field(default_factory=dict)  # our field → their header


def map_stage(text: str) -> str:
    low = text.strip().lower()
    for stage, words in STAGE_WORDS:
        if any(re.search(rf"\b{re.escape(w)}", low) for w in words):
            return stage
    return "applied"


def parse_date(text: str) -> date | None:
    text = text.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def match_columns(headers: list[str]) -> dict[str, str]:
    normal = {h: re.sub(r"[^a-z ]", "", h.lower()).strip() for h in headers}
    found: dict[str, str] = {}
    for key, names in ALIASES.items():
        for name in names:  # aliases are in priority order
            hit = next((h for h, n in normal.items() if n == name and h not in found.values()), None)
            if hit:
                found[key] = hit
                break
    return found


def import_csv(db: Database, path: Path, *, now: datetime, dry_run: bool = False) -> ImportResult:
    result = ImportResult()
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        result.columns = match_columns(reader.fieldnames or [])
        if "company" not in result.columns:
            raise ValueError(
                f"no company column found in {list(reader.fieldnames or [])} — "
                "name one 'Company' and try again"
            )
        cols = result.columns
        for n, row in enumerate(reader, start=2):
            def get(key: str) -> str:
                return (row.get(cols[key]) or "").strip() if key in cols else ""

            company = get("company")
            if not company:
                continue
            role = get("role")
            label = f"{company} — {role}" if role else company
            if db.find_job(company, role):
                result.skipped.append(f"line {n}: {label} (already tracked)")
                continue
            fields = {
                "role": role,
                "stage": map_stage(get("stage")) if get("stage") else "applied",
                "applied_on": parse_date(get("applied_on")),
                "next_step": get("next_step"),
                "next_date": parse_date(get("next_date")),
                "link": get("link"),
                "notes": get("notes"),
                "status": get("stage"),  # the sheet's own wording, e.g. "Interview 2 booked"
            }
            if not dry_run:
                db.add_job(company, now=now, **fields)
            result.added.append(f"{label} · {fields['stage']}")
    return result
