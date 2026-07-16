"""Loads teaching skills in the Agent-Skills format (skills/<name>/SKILL.md).

Skills are markdown playbooks with YAML frontmatter -- the open format used
by DeepTutor's EduHub registry (and Claude/Codex skills). The ones shipped in
skills/ came from https://eduhub.deeptutor.info (MIT-0); drop any other
SKILL.md folder in there and it becomes loadable by name.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    body: str  # the markdown playbook, frontmatter stripped


# Skill files never change at runtime, and the system prompt built from them
# must stay byte-identical across turns (prefix-cache requirement), so cache
# reads for the process lifetime.
_cache: dict[str, Skill | None] = {}


def load(name: str) -> Skill | None:
    if name in _cache:
        return _cache[name]
    path = SKILLS_DIR / name / "SKILL.md"
    skill = _parse(path) if path.exists() else None
    _cache[name] = skill
    return skill


def _parse(path: Path) -> Skill | None:
    text = path.read_text()
    if not text.startswith("---"):
        return Skill(name=path.parent.name, description="", body=text.strip())
    try:
        _, frontmatter, body = text.split("---", 2)
        meta = yaml.safe_load(frontmatter) or {}
    except (ValueError, yaml.YAMLError):
        return None
    return Skill(
        name=meta.get("name", path.parent.name),
        description=meta.get("description", ""),
        body=body.strip(),
    )


def available() -> list[Skill]:
    if not SKILLS_DIR.exists():
        return []
    found = [load(p.parent.name) for p in sorted(SKILLS_DIR.glob("*/SKILL.md"))]
    return [s for s in found if s is not None]
