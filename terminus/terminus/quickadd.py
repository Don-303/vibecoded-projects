"""Parse one-line task entries like ``Write report #work ~2 @14:30``.

Syntax:
    #tag      add a tag (repeatable)
    ~N        estimate in pomodoros (default 1)
    @HH:MM    pin to a fixed start time (also @9, @930, @0930)
Everything else is the title.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time


@dataclass
class ParsedTask:
    title: str
    tags: list[str] = field(default_factory=list)
    estimate: int = 1
    fixed_start: time | None = None


def parse_time(text: str) -> time:
    """Accept ``14:30``, ``1430``, ``930`` or ``9``."""
    text = text.strip()
    if ":" in text:
        h, m = text.split(":", 1)
    elif len(text) <= 2:
        h, m = text, "0"
    else:
        h, m = text[:-2], text[-2:]
    try:
        return time(int(h), int(m))
    except ValueError:
        raise ValueError(f"invalid time: {text!r}") from None


def parse_quickadd(line: str) -> ParsedTask:
    words, tags = [], []
    estimate, fixed = 1, None
    for token in line.split():
        if token.startswith("#") and len(token) > 1:
            tags.append(token[1:].lower())
        elif token.startswith("~") and token[1:].isdigit():
            estimate = max(1, int(token[1:]))
        elif token.startswith("@") and len(token) > 1:
            fixed = parse_time(token[1:])
        else:
            words.append(token)
    title = " ".join(words)
    if not title:
        raise ValueError("task needs a title")
    return ParsedTask(title=title, tags=tags, estimate=estimate, fixed_start=fixed)
