"""Runtime configuration, read from environment variables.

Every setting has a sensible default so Terminus runs with zero setup.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import time, timedelta
from pathlib import Path


def _default_db_path() -> Path:
    data_home = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(data_home) / "terminus" / "terminus.db"


def _config_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "terminus"


def _todoist_token(env) -> str | None:
    """``TODOIST_API_TOKEN``, else the first line of ~/.config/terminus/todoist-token."""
    if token := env.get("TODOIST_API_TOKEN", "").strip():
        return token
    path = _config_dir() / "todoist-token"
    try:
        return path.read_text().strip() or None
    except OSError:
        return None


def _parse_hhmm(value: str) -> time:
    hours, _, minutes = value.partition(":")
    return time(int(hours), int(minutes or 0))


@dataclass(frozen=True)
class Config:
    db_path: Path
    day_start: time = time(8, 0)
    day_end: time = time(20, 0)
    pomodoro: timedelta = timedelta(minutes=25)
    todoist_token: str | None = None  # None disables Todoist sync
    todoist_project: str = "Terminus"
    todoist_interval: int = 60  # seconds between background syncs

    @classmethod
    def from_env(cls, env: os._Environ[str] | dict[str, str] = os.environ) -> Config:
        """Build a config from ``TERMINUS_*`` environment variables."""
        return cls(
            db_path=Path(env.get("TERMINUS_DB", "")) if env.get("TERMINUS_DB") else _default_db_path(),
            day_start=_parse_hhmm(env.get("TERMINUS_DAY_START", "08:00")),
            day_end=_parse_hhmm(env.get("TERMINUS_DAY_END", "20:00")),
            pomodoro=timedelta(minutes=int(env.get("TERMINUS_POMODORO_MIN", "25"))),
            todoist_token=_todoist_token(env),
            todoist_project=env.get("TODOIST_PROJECT", "Terminus"),
            todoist_interval=max(15, int(env.get("TODOIST_SYNC_SECONDS", "60"))),
        )
