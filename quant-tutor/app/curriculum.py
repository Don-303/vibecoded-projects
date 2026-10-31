"""Loads the syllabus and exposes lesson lookups."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

SYLLABUS_PATH = Path(__file__).resolve().parent.parent / "curriculum" / "syllabus.yaml"


@dataclass
class SourceRef:
    repo: str
    file: str


@dataclass
class Lesson:
    id: str
    title: str
    module_id: str
    module_title: str
    sources: list[SourceRef] = field(default_factory=list)


@dataclass
class Module:
    id: str
    title: str
    lessons: list[Lesson]


def load_modules() -> list[Module]:
    raw = yaml.safe_load(SYLLABUS_PATH.read_text())
    modules = []
    for m in raw["modules"]:
        lessons = [
            Lesson(
                id=raw_lesson["id"],
                title=raw_lesson["title"],
                module_id=m["id"],
                module_title=m["title"],
                sources=[
                    SourceRef(repo=s["repo"], file=s["file"])
                    for s in raw_lesson.get("sources", [])
                ],
            )
            for raw_lesson in m["lessons"]
        ]
        modules.append(Module(id=m["id"], title=m["title"], lessons=lessons))
    return modules


def all_lessons() -> list[Lesson]:
    return [lesson for module in load_modules() for lesson in module.lessons]


def find_lesson(lesson_id: str) -> Lesson | None:
    for lesson in all_lessons():
        if lesson.id == lesson_id:
            return lesson
    return None


def next_lesson(lesson_id: str) -> Lesson | None:
    lessons = all_lessons()
    ids = [lesson.id for lesson in lessons]
    if lesson_id not in ids:
        return None
    idx = ids.index(lesson_id)
    return lessons[idx + 1] if idx + 1 < len(lessons) else None
