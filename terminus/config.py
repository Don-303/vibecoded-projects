"""Runtime configuration, read from environment variables.

Every setting has a sensible default so Terminus runs with zero setup.
"""

from __future__ import annotations

import os
import tomllib
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


def _config_file() -> dict:
    """``~/.config/terminus/config.toml`` — optional; env vars override it."""
    try:
        with open(_config_dir() / "config.toml", "rb") as f:
            return tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


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
    hermes_host: str = "127.0.0.1"  # IP/hostname or URL; port 8642 assumed
    hermes_api_key: str | None = None  # None: agent chat shows setup help
    hermes_model: str = "hermes-agent"
    brief_time: time | None = time(6, 0)  # auto-generate after this; None = only on demand
    brief_feeds: dict[str, str] | None = None  # None: terminus.brief.FINANCE_FEEDS
    brief_email: bool = True  # ask Hermes to check Gmail with its own tools

    @classmethod
    def from_env(cls, env: os._Environ[str] | dict[str, str] = os.environ) -> Config:
        """Build a config from ``config.toml``, overridden by environment variables."""
        file = _config_file()
        planner, todoist = file.get("planner", {}), file.get("todoist", {})
        hermes = file.get("hermes", {})
        brief = file.get("brief", {})
        brief_at = brief.get("time", "06:00")  # "" or false: only on demand (g)
        return cls(
            db_path=Path(env.get("TERMINUS_DB", "")) if env.get("TERMINUS_DB") else _default_db_path(),
            day_start=_parse_hhmm(env.get("TERMINUS_DAY_START") or planner.get("day_start", "08:00")),
            day_end=_parse_hhmm(env.get("TERMINUS_DAY_END") or planner.get("day_end", "20:00")),
            pomodoro=timedelta(
                minutes=int(env.get("TERMINUS_POMODORO_MIN") or planner.get("pomodoro_minutes", 25))
            ),
            todoist_token=_todoist_token(env),
            todoist_project=env.get("TODOIST_PROJECT") or todoist.get("project", "Terminus"),
            todoist_interval=max(
                15, int(env.get("TODOIST_SYNC_SECONDS") or todoist.get("sync_seconds", 60))
            ),
            hermes_host=env.get("HERMES_HOST") or hermes.get("host", "127.0.0.1"),
            hermes_api_key=env.get("HERMES_API_KEY") or hermes.get("api_key") or None,
            hermes_model=env.get("HERMES_MODEL") or hermes.get("model", "hermes-agent"),
            brief_time=_parse_hhmm(brief_at) if brief_at else None,
            brief_feeds=brief.get("feeds") or None,
            brief_email=bool(brief.get("email", True)),
        )
