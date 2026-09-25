"""Stats view: a week of focus time, completions and streaks, drawn in text."""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Static

from terminus.stats import Stats
from terminus.theme import AMBER, CYAN, FAINT, GREEN, MUTED
from terminus.widgets.timeline import fmt_duration

BAR = 28


def _bar(value: float, peak: float, color: str, width: int = BAR) -> Text:
    filled = round(width * value / peak) if peak else 0
    if value and not filled:
        filled = 1  # anything non-zero stays visible
    return Text.assemble(("█" * filled, color), ("·" * (width - filled), FAINT))


def render_stats(stats: Stats) -> Text:
    text = Text()
    today = stats.days[-1].day

    # Focus per day
    avg = stats.week_focus / len(stats.days)
    text.append("Focus · last 7 days", style="bold")
    text.append(f"   total {fmt_duration(stats.week_focus)} · avg {fmt_duration(avg)}/day\n\n",
                style=MUTED)
    peak = max((d.focus.total_seconds() for d in stats.days), default=0)
    for d in stats.days:
        label_style = f"bold {CYAN}" if d.day == today else MUTED
        text.append(f"  {d.day:%a %d}  ", style=label_style)
        text.append_text(_bar(d.focus.total_seconds(), peak, CYAN if d.day == today else GREEN))
        text.append(f"  {fmt_duration(d.focus) if d.focus else '—'}\n",
                    style=CYAN if d.day == today else "")

    # Week over week
    text.append("\n  vs previous 7 days  ", style=MUTED)
    text.append(fmt_duration(stats.prev_week_focus), style=MUTED)
    if stats.prev_week_focus:
        change = (stats.week_focus - stats.prev_week_focus) / stats.prev_week_focus
        text.append(f"  ({change:+.0%})", style=GREEN if change >= 0 else AMBER)
    elif stats.week_focus:
        text.append("  (new)", style=GREEN)
    text.append("\n")

    # Completions
    text.append("\nTasks done · last 7 days", style="bold")
    text.append(f"   total {sum(d.done for d in stats.days)}\n\n", style=MUTED)
    most = max((d.done for d in stats.days), default=0)
    for d in stats.days:
        text.append(f"  {d.day:%a %d}  ", style=f"bold {CYAN}" if d.day == today else MUTED)
        text.append_text(_bar(d.done, most, GREEN, width=min(BAR, max(most, 1) * 2)))
        text.append(f"  {d.done or '—'}\n")

    # Streaks
    text.append("\nStreak", style="bold")
    text.append(f"   {stats.streak} day{'s' if stats.streak != 1 else ''}", style=GREEN if stats.streak else MUTED)
    text.append(f"   longest {stats.longest_streak}\n", style=MUTED)

    # Tags
    text.append("\nWhere the focus went · this week\n\n", style="bold")
    if not stats.tags:
        text.append("  No focus sessions yet — press f to start one.\n", style=MUTED)
    top = stats.tags[0][1].total_seconds() if stats.tags else 0
    for tag, spent in stats.tags[:8]:
        label = "free focus" if tag == "" else ("untagged" if tag == "untagged" else f"#{tag}")
        text.append(f"  {label:<14}", style=MUTED)
        text.append_text(_bar(spent.total_seconds(), top, CYAN, width=20))
        text.append(f"  {fmt_duration(spent)}\n")
    return text


class StatsView(Vertical):
    def compose(self) -> ComposeResult:
        yield Static("Stats", classes="view-title")
        with VerticalScroll(id="stats-scroll"):
            yield Static(id="stats-body")

    def update_stats(self, stats: Stats) -> None:
        self.query_one("#stats-body", Static).update(render_stats(stats))

