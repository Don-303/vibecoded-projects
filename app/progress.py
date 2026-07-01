"""Local persistence of lesson completion, chat history, and time/streak tracking."""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "progress.json"


def _load() -> dict:
    if DATA_PATH.exists():
        state = json.loads(DATA_PATH.read_text())
    else:
        state = {}
    state.setdefault("completed", [])
    state.setdefault("history", {})
    state.setdefault("time_spent", {})
    state.setdefault("activity_dates", [])
    return state


def _save(state: dict) -> None:
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATA_PATH.write_text(json.dumps(state, indent=2))


def is_complete(lesson_id: str) -> bool:
    return lesson_id in _load()["completed"]


def mark_complete(lesson_id: str) -> None:
    state = _load()
    if lesson_id not in state["completed"]:
        state["completed"].append(lesson_id)
    _save(state)


def mark_incomplete(lesson_id: str) -> None:
    state = _load()
    state["completed"] = [l for l in state["completed"] if l != lesson_id]
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
    for prev, curr in zip(dates, dates[1:]):
        if (curr - prev).days == 1:
            current += 1
        else:
            current = 1
        longest = max(longest, current)
    return longest
