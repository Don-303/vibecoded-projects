"""AI Agent view: a chat with the remote Hermes agent."""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Input, Markdown, Static

from terminus.theme import CYAN, GREEN, MUTED, RED

SETUP_HELP = """\
**Hermes isn't configured yet.** On the machine that runs Hermes, add to `~/.hermes/.env`:

```
API_SERVER_ENABLED=true
API_SERVER_HOST=0.0.0.0
API_SERVER_KEY=<a long random secret>
```

then run `hermes gateway`. Here, add to `~/.config/terminus/config.toml`:

```
[hermes]
host = "100.x.y.z"        # its Tailscale IP; port 8642 is assumed
api_key = "<the same secret>"
```

and restart Terminus.
"""


class AgentConsole(Vertical):
    BINDINGS = [Binding("ctrl+l", "app.new_chat", "New chat")]

    def compose(self) -> ComposeResult:
        yield Static("AI Agent", classes="view-title")
        yield Static(id="agent-status")
        yield VerticalScroll(id="agent-log")
        yield Input(placeholder="ask Hermes…  (enter to send · esc to leave · ctrl+l new chat)",
                    id="agent-input")

    @property
    def log_area(self) -> VerticalScroll:
        return self.query_one("#agent-log", VerticalScroll)

    def set_status(self, *, configured: bool, online: bool | None, host: str, busy: bool) -> None:
        text = Text()
        if not configured:
            text.append("● not configured", style=MUTED)
        elif online is None:
            text.append("● checking…", style=MUTED)
        elif online:
            text.append("● online", style=GREEN)
        else:
            text.append("● offline", style=RED)
        text.append(f"   {host}", style=MUTED)
        if busy:
            text.append("   thinking…", style=f"italic {CYAN}")
        self.query_one("#agent-status", Static).update(text)

    def show_setup(self) -> None:
        self.log_area.mount(Markdown(SETUP_HELP, classes="agent-note"))

    def add_user(self, prompt: str) -> None:
        self.log_area.mount(Static(Text.assemble(("you  ", f"bold {CYAN}"), prompt),
                                   classes="agent-user"))
        self.log_area.scroll_end(animate=False)

    def start_reply(self) -> tuple[Markdown, Static]:
        """A Markdown widget to stream the reply into, and a line for tool activity."""
        activity = Static("", classes="agent-activity")
        reply = Markdown("", classes="agent-reply")
        self.log_area.mount(activity)
        self.log_area.mount(reply)
        self.log_area.scroll_end(animate=False)
        return reply, activity

    def add_error(self, message: str) -> None:
        self.log_area.mount(Static(Text(f"✗ {message}", style=RED), classes="agent-error"))
        self.log_area.scroll_end(animate=False)

    def clear(self) -> None:
        self.log_area.remove_children()

    def focus_input(self) -> None:
        self.query_one("#agent-input", Input).focus()

    def on_key(self, event) -> None:
        if event.key == "escape" and isinstance(self.app.focused, Input):
            event.stop()
            self.log_area.focus()  # hand the keys back to the app
