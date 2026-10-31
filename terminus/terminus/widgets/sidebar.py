"""Left sidebar: view navigation plus a live status panel."""

from __future__ import annotations

from datetime import datetime, timedelta

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from terminus.theme import AMBER, CYAN, GREEN, MUTED
from terminus.widgets.timeline import fmt_duration

VIEWS = [
    ("today", "Today"),
    ("tasks", "Tasks"),
    ("stats", "Stats"),
    ("agent", "AI Agent"),
    ("brief", "Brief"),
    ("jobs", "Jobs"),
]


class Sidebar(Vertical):
    def compose(self) -> ComposeResult:
        yield Static("terminus", id="brand")
        yield OptionList(
            *(Option(f"[{i}] {label}", id=view) for i, (view, label) in enumerate(VIEWS, 1)),
            id="nav",
        )
        yield Static(id="status")

    def select(self, view: str) -> None:
        nav = self.query_one("#nav", OptionList)
        nav.highlighted = nav.get_option_index(view)

    def update_status(
        self,
        *,
        now: datetime,
        streak: int,
        focus_today: timedelta,
        focus_task: str | None,
        pomodoro_left: timedelta | None,
        pomodoro_number: int,
        service_lines: list[Text] = (),
    ) -> None:
        text = Text()
        text.append(f"{now:%H:%M}\n", style=f"bold {CYAN}")
        text.append(f"{now:%a %d %b}\n\n", style=MUTED)
        text.append("streak   ", style=MUTED)
        text.append(f"{streak} day{'s' if streak != 1 else ''}\n", style=GREEN if streak else MUTED)
        text.append("focus    ", style=MUTED)
        text.append(f"{fmt_duration(focus_today)} today\n\n", style=GREEN if focus_today else MUTED)
        text.append("focus    ", style=MUTED)
        if focus_task is None:
            text.append("off\n", style=MUTED)
            text.append("f task · F free", style=MUTED)
        else:
            text.append("active\n", style=f"bold {CYAN}")
            text.append(f"{focus_task}\n", style=CYAN)
            secs = int(pomodoro_left.total_seconds()) if pomodoro_left else 0
            text.append(f"pomodoro {pomodoro_number} · {secs // 60:02d}:{secs % 60:02d} left", style=AMBER)
        if service_lines:
            text.append("\n")
        for line in service_lines:
            text.append("\n")
            text.append_text(line)
        self.query_one("#status", Static).update(text)
