"""Local persistence of lesson completion, chat history, and time/streak tracking."""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "progress.json"


def _load() -> dict:
    state = json.loads(DATA_PATH.read_text()) if DATA_PATH.exists() else {}
    state.setdefault("completed", [])
    state.setdefault("history", {})
    state.setdefault("time_spent", {})
    state.setdefault("activity_dates", [])
    state.setdefault("code", {})
    state.setdefault("completed_at", {})
    state.setdefault("reviewed_at", {})
    state.setdefault("teaching_skill", {})
    state.setdefault("quiz_scores", {})
    return state


def _save(state: dict) -> None:
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATA_PATH.write_text(json.dumps(state, indent=2))


def is_complete(lesson_id: str) -> bool:
    return lesson_id in _load()["completed"]


REVIEW_INTERVAL_DAYS = 7


def mark_complete(lesson_id: str) -> None:
    state = _load()
    if lesson_id not in state["completed"]:
        state["completed"].append(lesson_id)
    state["completed_at"][lesson_id] = date.today().isoformat()
    _save(state)


def mark_reviewed(lesson_id: str) -> None:
    state = _load()
    state["reviewed_at"][lesson_id] = date.today().isoformat()
    _save(state)


# Lessons whose last quiz scored below this come due for review sooner.
WEAK_SCORE_RATIO = 0.6
WEAK_REVIEW_INTERVAL_DAYS = 3


def is_review_due(lesson_id: str) -> bool:
    """A completed lesson comes due for review REVIEW_INTERVAL_DAYS after it
    was completed or last reviewed, whichever is later -- or sooner
    (WEAK_REVIEW_INTERVAL_DAYS) if the last quiz on it scored poorly.
    Lessons completed before dates were recorded never come due (no way to
    know how stale)."""
    state = _load()
    if lesson_id not in state["completed"]:
        return False
    stamps = [
        state["completed_at"].get(lesson_id),
        state["reviewed_at"].get(lesson_id),
    ]
    stamps = [date.fromisoformat(s) for s in stamps if s]
    if not stamps:
        return False
    interval = REVIEW_INTERVAL_DAYS
    last = last_quiz_score(lesson_id)
    if last and last["correct"] / last["total"] < WEAK_SCORE_RATIO:
        interval = WEAK_REVIEW_INTERVAL_DAYS
    return (date.today() - max(stamps)).days >= interval


def record_quiz_score(lesson_id: str, correct: int, total: int) -> None:
    if total <= 0 or correct < 0 or correct > total:
        return
    state = _load()
    state["quiz_scores"].setdefault(lesson_id, []).append(
        {"date": date.today().isoformat(), "correct": correct, "total": total}
    )
    _save(state)


def last_quiz_score(lesson_id: str) -> dict | None:
    scores = _load()["quiz_scores"].get(lesson_id, [])
    return scores[-1] if scores else None


def quiz_stats() -> tuple[int, float] | None:
    """(number of quizzes taken, average score ratio) across all lessons."""
    all_scores = [s for scores in _load()["quiz_scores"].values() for s in scores]
    if not all_scores:
        return None
    avg = sum(s["correct"] / s["total"] for s in all_scores) / len(all_scores)
    return len(all_scores), avg


def mark_incomplete(lesson_id: str) -> None:
    state = _load()
    state["completed"] = [c for c in state["completed"] if c != lesson_id]
    _save(state)


def get_history(lesson_id: str) -> list[dict]:
    return _load()["history"].get(lesson_id, [])


def append_history(lesson_id: str, role: str, content: str) -> None:
    state = _load()
    state["history"].setdefault(lesson_id, []).append({"role": role, "content": content})
    _save(state)


def clear_history(lesson_id: str) -> None:
    state = _load()
    state["history"].pop(lesson_id, None)
    _save(state)


def get_last_lesson() -> str | None:
    return _load().get("last_lesson")


def save_last_lesson(lesson_id: str) -> None:
    state = _load()
    state["last_lesson"] = lesson_id
    _save(state)


def get_model() -> str | None:
    return _load().get("model")


def save_model(tag: str) -> None:
    state = _load()
    state["model"] = tag
    _save(state)


def get_teaching_skill(lesson_id: str) -> str | None:
    return _load()["teaching_skill"].get(lesson_id)


def save_teaching_skill(lesson_id: str, skill_name: str | None) -> None:
    state = _load()
    if skill_name:
        state["teaching_skill"][lesson_id] = skill_name
    else:
        state["teaching_skill"].pop(lesson_id, None)
    _save(state)


def get_code(lesson_id: str) -> str:
    return _load()["code"].get(lesson_id, "")


def save_code(lesson_id: str, text: str) -> None:
    state = _load()
    if text.strip():
        state["code"][lesson_id] = text
    else:
        state["code"].pop(lesson_id, None)
    _save(state)


def add_time(lesson_id: str, seconds: float) -> None:
    if seconds <= 0:
        return
    state = _load()
    state["time_spent"][lesson_id] = state["time_spent"].get(lesson_id, 0.0) + seconds
    today = date.today().isoformat()
    if today not in state["activity_dates"]:
        state["activity_dates"].append(today)
    _save(state)


def get_time_spent(lesson_id: str) -> float:
    return _load()["time_spent"].get(lesson_id, 0.0)


def total_time_spent() -> float:
    return sum(_load()["time_spent"].values())


def current_streak() -> int:
    dates = {date.fromisoformat(d) for d in _load()["activity_dates"]}
    if not dates:
        return 0
    today = date.today()
    cursor = today if today in dates else today - timedelta(days=1)
    if cursor not in dates:
        return 0
    streak = 0
    while cursor in dates:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def longest_streak() -> int:
    dates = sorted(date.fromisoformat(d) for d in _load()["activity_dates"])
    if not dates:
        return 0
    longest = current = 1
    for prev, curr in zip(dates, dates[1:], strict=False):
        if (curr - prev).days == 1:
            current += 1
        else:
            current = 1
        longest = max(longest, current)
    return longest
