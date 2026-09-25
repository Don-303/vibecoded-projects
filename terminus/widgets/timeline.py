"""Today view: a vertical agenda of the day with a live "now" marker."""

from __future__ import annotations

from datetime import datetime, timedelta

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Static

from terminus.models import Block, BlockState, DayPlan
from terminus.theme import AMBER, CYAN, FAINT, MUTED, STATE_COLOR, STATE_ICON

#: Each block gets one row per this much time, so longer blocks look longer.
ROW = timedelta(minutes=15)


def fmt_duration(td: timedelta) -> str:
    minutes = max(0, round(td.total_seconds() / 60))
    h, m = divmod(minutes, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m"


def progress_bar(fraction: float, width: int = 12) -> str:
    filled = round(fraction * width)
    return "█" * filled + "░" * (width - filled)


def _header(block: Block, now: datetime, first_next: bool) -> Text:
    color = STATE_COLOR[block.state]
    t = block.task
    line = Text(f"{block.start:%H:%M} ", style=MUTED)
    line.append("┃ ", style=color)
    line.append(f"{STATE_ICON[block.state]} ", style=f"bold {color}")
    title_style = f"strike {MUTED}" if block.state is BlockState.DONE else color
    if block.state is BlockState.RUNNING or first_next:
        title_style += " bold"
    line.append(t.title, style=title_style)
    line.append(f"  {block.start:%H:%M}–{block.end:%H:%M}", style=MUTED)
    if t.fixed:
        line.append("  pinned", style=MUTED)
    for tag in t.tags:
        line.append(f"  #{tag}", style=MUTED)
    if block.state is BlockState.RUNNING:
        left = block.end - now
        line.append(f"  {progress_bar(block.progress)} ", style=CYAN)
        line.append(f"{block.progress:.0%} · {fmt_duration(left)} left", style=CYAN)
    elif first_next:
        line.append("  up next", style=f"italic {CYAN}")
    elif block.state is BlockState.MISSED:
        line.append("  missed", style=f"italic {STATE_COLOR[BlockState.MISSED]}")
    return line


def render_timeline(plan: DayPlan, now: datetime, start: datetime, end: datetime) -> tuple[Text, int]:
    """Render the plan as an agenda. Returns the text and the line index of "now"."""
    # (time, order, line) — order breaks ties: headers, then now, then fillers.
    rows: list[tuple[datetime, int, Text]] = []
    first_next = plan.up_next

    for block in plan.blocks:
        rows.append((block.start, 0, _header(block, now, block is first_next)))
        color = STATE_COLOR[block.state]
        n = max(1, round(block.duration / ROW))
        for k in range(1, n):
            at = block.start + k * ROW
            done_part = block.state is BlockState.RUNNING and at <= now
            rows.append((at, 2, Text.assemble(("      ", ""), ("┃" if done_part else "│", color))))

    def covered(at: datetime) -> bool:
        return any(b.start <= at < b.end for b in plan.blocks)

    hour = start.replace(minute=0)
    while hour <= end:
        if hour >= start and not covered(hour):
            rows.append((hour, 3, Text.assemble((f"{hour:%H:%M} ", MUTED), ("┆", FAINT))))
        hour += timedelta(hours=1)

    rows.append((now, 1, Text(f"{now:%H:%M} ◀━━ now", style=f"bold {CYAN}")))
    rows.sort(key=lambda r: (r[0], r[1]))

    text = Text(no_wrap=True, overflow="ellipsis")
    now_line = 0
    for i, (_, order, line) in enumerate(rows):
        if order == 1:
            now_line = i
        text.append_text(line)
        text.append("\n")

    if plan.overflow:
        text.append("\nWon't fit today → carries to tomorrow\n", style=f"bold {AMBER}")
        for t in plan.overflow:
            text.append(f"   · {t.title}", style=AMBER)
            text.append(f"  ~{t.estimate}\n", style=MUTED)
    return text, now_line


def summary(plan: DayPlan) -> Text:
    counts = {s: 0 for s in BlockState}
    for b in plan.blocks:
        counts[b.state] += 1
    text = Text()
    labels = [
        (BlockState.DONE, "done"),
        (BlockState.RUNNING, "running"),
        (BlockState.NEXT, "up next"),
        (BlockState.MISSED, "missed"),
    ]
    for state, label in labels:
        text.append(f"{STATE_ICON[state]} {counts[state]} {label}   ", style=STATE_COLOR[state])
    if plan.overflow:
        text.append(f"» {len(plan.overflow)} overflow", style=AMBER)
    return text


class TodayView(Vertical):
    """Header summary + scrollable agenda."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._scrolled_once = False

    def compose(self) -> ComposeResult:
        yield Static(id="today-title", classes="view-title")
        yield Static(id="today-summary")
        with VerticalScroll(id="timeline-scroll"):
            yield Static(id="timeline")

    def update_plan(self, plan: DayPlan, now: datetime, start: datetime, end: datetime) -> None:
        self.query_one("#today-title", Static).update(f"Today · {now:%A %d %B}")
        self.query_one("#today-summary", Static).update(summary(plan))
        text, now_line = render_timeline(plan, now, start, end)
        self.query_one("#timeline", Static).update(text)
        if not self._scrolled_once:
            self._scrolled_once = True
            scroll = self.query_one("#timeline-scroll", VerticalScroll)
            self.call_after_refresh(scroll.scroll_to, y=max(0, now_line - 4), animate=False)
