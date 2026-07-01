"""Local persistence of lesson completion and per-lesson chat history."""
from __future__ import annotations

import json
from pathlib import Path

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "progress.json"


def _load() -> dict:
    if DATA_PATH.exists():
        return json.loads(DATA_PATH.read_text())
    return {"completed": [], "history": {}}


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
