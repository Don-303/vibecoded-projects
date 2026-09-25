"""Confirmation panel for task changes proposed by the agent."""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import SelectionList, Static

from terminus.actions import Proposal
from terminus.theme import CYAN, GREEN, MUTED, RED


class ProposalPanel(Vertical):
    """Lists proposed changes, all ticked. space toggles one, y applies, n discards."""

    BINDINGS = [
        Binding("y", "decide(True)", "Apply", priority=True),
        Binding("n,escape", "decide(False)", "Discard", priority=True),
    ]

    class Decided(Message):
        def __init__(self, panel: ProposalPanel, apply: bool, selected: list[int]) -> None:
            super().__init__()
            self.panel = panel
            self.apply = apply
            self.selected = selected

    def __init__(self, proposal: Proposal) -> None:
        super().__init__(classes="proposal")
        self.proposal = proposal
        self.decided = False

    def compose(self) -> ComposeResult:
        n = len(self.proposal.changes)
        header = Text.assemble(
            (f"Hermes proposes {n} change{'s' if n != 1 else ''}", f"bold {CYAN}"),
            ("   space toggle · y apply ticked · n discard", MUTED),
        )
        yield Static(header, classes="proposal-header")
        if self.proposal.changes:
            yield SelectionList[int](
                *((c.summary, i, True) for i, c in enumerate(self.proposal.changes)),
                classes="proposal-list",
            )
        if self.proposal.rejected:
            text = Text("Skipped (invalid):\n", style=MUTED)
            for reason in self.proposal.rejected:
                text.append(f"  ✗ {reason}\n", style=RED)
            yield Static(text, classes="proposal-rejected")

    def on_mount(self) -> None:
        if self.proposal.changes:
            self.query_one(SelectionList).focus()

    def action_decide(self, apply: bool) -> None:
        if self.decided:
            return
        self.decided = True
        lists = self.query(SelectionList)
        selected = sorted(lists.first().selected) if lists else []
        self.post_message(self.Decided(self, apply, selected))

    def resolve(self, message: str, *, applied: bool) -> None:
        """Replace the interactive list with a one-line outcome."""
        self.remove_children()
        self.mount(Static(Text(message, style=GREEN if applied else MUTED), classes="proposal-outcome"))
