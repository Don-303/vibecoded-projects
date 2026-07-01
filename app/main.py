"""Offline terminal tutor for ML in finance / econometrics / quant finance.

Runs entirely locally via Ollama + Qwen2.5-7B-Instruct. No network required
once the model is pulled.
"""
from __future__ import annotations

import time

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Markdown,
    RichLog,
    Static,
    TabbedContent,
    TabPane,
    TextArea,
    Tree,
)

from app import agent, curriculum, progress, sandbox

DEFAULT_LESSON_ID = "py-core"


class TutorApp(App):
    CSS = """
    #sidebar {
        width: 42;
        border-right: solid $accent;
    }
    #header-row {
        height: auto;
        background: $boost;
    }
    #lesson-header {
        padding: 0 1;
        width: 1fr;
    }
    #session-timer {
        padding: 0 1;
        width: auto;
        color: $text-muted;
    }
    #progress-view {
        padding: 1;
    }
    #chat-log {
        padding: 1;
    }
    #code-editor {
        height: 1fr;
    }
    #code-output {
        height: 12;
        border-top: solid $accent;
    }
    """

    BINDINGS = [
        ("ctrl+q", "quit", "Quit"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.current_lesson_id = DEFAULT_LESSON_ID
        self.chat_buffer = ""
        self.session_start = time.monotonic()
        self.lesson_start: float | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal():
            with Vertical(id="sidebar"):
                yield Tree("Curriculum", id="curriculum-tree")
            with Vertical():
                with Horizontal(id="header-row"):
                    yield Static("", id="lesson-header")
                    yield Static("Session: 00:00:00", id="session-timer")
                with TabbedContent():
                    with TabPane("Tutor Chat", id="tab-chat"):
                        with VerticalScroll(id="chat-scroll"):
                            yield Markdown("", id="chat-log")
                        yield Input(
                            placeholder="Ask the tutor, or paste code/output for review...",
                            id="chat-input",
                        )
                    with TabPane("Code Editor", id="tab-code"):
                        yield TextArea(
                            "",
                            language="python",
                            id="code-editor",
                            theme="monokai",
                        )
                        with Horizontal():
                            yield Button("Run", id="run-btn", variant="primary")
                            yield Button("Send output to tutor", id="send-btn")
                        yield RichLog(id="code-output", highlight=True, markup=True)
                    with TabPane("Progress", id="tab-progress"):
                        with VerticalScroll():
                            yield Markdown("", id="progress-view")
                with Horizontal():
                    yield Button("Mark lesson complete", id="complete-btn", variant="success")
                    yield Button("Next lesson →", id="next-btn")
        yield Footer()

    def on_mount(self) -> None:
        self.build_tree()
        self.load_lesson(self.current_lesson_id)
        self.refresh_progress_view()
        self.run_worker(self.check_model(), exclusive=True)
        self.set_interval(1.0, self.update_session_timer)

    def update_session_timer(self) -> None:
        elapsed = int(time.monotonic() - self.session_start)
        h, rem = divmod(elapsed, 3600)
        m, s = divmod(rem, 60)
        self.query_one("#session-timer", Static).update(f"Session: {h:02d}:{m:02d}:{s:02d}")

    async def action_quit(self) -> None:
        self._flush_lesson_time()
        self.exit()

    def _flush_lesson_time(self) -> None:
        if self.lesson_start is not None and self.current_lesson_id:
            elapsed = time.monotonic() - self.lesson_start
            progress.add_time(self.current_lesson_id, elapsed)
        self.lesson_start = None

    async def check_model(self) -> None:
        ok = await agent.check_model_available()
        if not ok:
            self.notify(
                f"Model '{agent.MODEL}' not found on local Ollama. "
                f"Run: ollama pull {agent.MODEL}",
                severity="error",
                timeout=10,
            )

    def build_tree(self) -> None:
        tree = self.query_one("#curriculum-tree", Tree)
        tree.root.expand()
        for module in curriculum.load_modules():
            module_node = tree.root.add(module.title, expand=True)
            for lesson in module.lessons:
                mark = "✓ " if progress.is_complete(lesson.id) else "  "
                module_node.add_leaf(f"{mark}{lesson.title}", data=lesson.id)

    def refresh_tree(self) -> None:
        tree = self.query_one("#curriculum-tree", Tree)
        tree.root.remove_children()
        self.build_tree()

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        lesson_id = event.node.data
        if lesson_id:
            self.load_lesson(lesson_id)

    def load_lesson(self, lesson_id: str) -> None:
        lesson = curriculum.find_lesson(lesson_id)
        if lesson is None:
            return
        self._flush_lesson_time()
        self.current_lesson_id = lesson_id
        self.lesson_start = time.monotonic()
        status = "done" if progress.is_complete(lesson_id) else "in progress"
        header = self.query_one("#lesson-header", Static)
        header.update(f"[b]{lesson.module_title}[/b] › {lesson.title}  ({status})")

        history = progress.get_history(lesson_id)
        self.chat_buffer = self._render_history(history)
        self.query_one("#chat-log", Markdown).update(self.chat_buffer or "*No conversation yet. Say hi to start the lesson.*")
        self.query_one("#code-output", RichLog).clear()

    @staticmethod
    def _render_history(history: list[dict]) -> str:
        parts = []
        for turn in history:
            speaker = "**You**" if turn["role"] == "user" else "**Tutor**"
            parts.append(f"{speaker}:\n\n{turn['content']}\n")
        return "\n---\n\n".join(parts)

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "chat-input":
            return
        message = event.value.strip()
        if not message:
            return
        event.input.value = ""
        await self.send_to_tutor(message)

    async def send_to_tutor(self, message: str) -> None:
        lesson = curriculum.find_lesson(self.current_lesson_id)
        if lesson is None:
            return
        progress.append_history(self.current_lesson_id, "user", message)
        self.chat_buffer += f"\n\n---\n\n**You**:\n\n{message}\n"
        chat_log = self.query_one("#chat-log", Markdown)
        await chat_log.update(self.chat_buffer)
        self._scroll_chat_to_end()
        self.run_worker(self.stream_tutor_reply(lesson), exclusive=True)

    async def stream_tutor_reply(self, lesson) -> None:
        history = progress.get_history(self.current_lesson_id)[:-1]
        user_message = progress.get_history(self.current_lesson_id)[-1]["content"]

        self.chat_buffer += "\n\n---\n\n**Tutor**:\n\n"
        reply_so_far = ""
        chat_log = self.query_one("#chat-log", Markdown)
        try:
            async for token in agent.stream_reply(lesson, history, user_message):
                reply_so_far += token
                await chat_log.update(self.chat_buffer + reply_so_far)
                self._scroll_chat_to_end()
        except Exception as exc:
            reply_so_far = f"_Error talking to local model: {exc}_"
            await chat_log.update(self.chat_buffer + reply_so_far)

        self.chat_buffer += reply_so_far
        progress.append_history(self.current_lesson_id, "assistant", reply_so_far)

    def _scroll_chat_to_end(self) -> None:
        self.query_one("#chat-scroll", VerticalScroll).scroll_end(animate=False)

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        if event.pane.id == "tab-progress":
            self.refresh_progress_view()

    def refresh_progress_view(self) -> None:
        self.query_one("#progress-view", Markdown).update(self._render_progress_markdown())

    @staticmethod
    def _render_progress_markdown() -> str:
        modules = curriculum.load_modules()
        total = sum(len(m.lessons) for m in modules)
        done = sum(1 for m in modules for l in m.lessons if progress.is_complete(l.id))
        pct = int(100 * done / total) if total else 0
        bar_len = 30
        filled = int(bar_len * done / total) if total else 0
        bar = "█" * filled + "░" * (bar_len - filled)

        lines = [
            "## Overall progress",
            f"`{bar}` {done}/{total} lessons ({pct}%)",
            "",
            "## By module",
        ]
        for m in modules:
            m_done = sum(1 for l in m.lessons if progress.is_complete(l.id))
            lines.append(f"- **{m.title}**: {m_done}/{len(m.lessons)}")

        total_seconds = progress.total_time_spent()
        h, rem = divmod(int(total_seconds), 3600)
        mins, _ = divmod(rem, 60)
        lines += [
            "",
            "## Time & streaks",
            f"- Total time spent: {h}h {mins}m",
            f"- Current streak: {progress.current_streak()} day(s)",
            f"- Longest streak: {progress.longest_streak()} day(s)",
        ]

        remaining = [l for m in modules for l in m.lessons if not progress.is_complete(l.id)]
        if remaining:
            lines.append("")
            lines.append(f"## Remaining lessons ({len(remaining)})")
            for l in remaining:
                lines.append(f"- {l.module_title}: {l.title}")
        else:
            lines.append("\nAll lessons complete!")

        return "\n".join(lines)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "run-btn":
            self.run_code()
        elif event.button.id == "send-btn":
            await self.send_code_output_to_tutor()
        elif event.button.id == "complete-btn":
            progress.mark_complete(self.current_lesson_id)
            self.refresh_tree()
            self.load_lesson(self.current_lesson_id)
            self.refresh_progress_view()
        elif event.button.id == "next-btn":
            nxt = curriculum.next_lesson(self.current_lesson_id)
            if nxt:
                self.load_lesson(nxt.id)

    def run_code(self) -> None:
        code = self.query_one("#code-editor", TextArea).text
        output_log = self.query_one("#code-output", RichLog)
        output_log.clear()
        result = sandbox.run_code(code)
        if result.stdout:
            output_log.write(result.stdout)
        if result.stderr:
            output_log.write(f"[red]{result.stderr}[/red]")
        if not result.stdout and not result.stderr:
            output_log.write("[dim](no output)[/dim]")

    async def send_code_output_to_tutor(self) -> None:
        code = self.query_one("#code-editor", TextArea).text
        output_log = self.query_one("#code-output", RichLog)
        lines = output_log.lines
        output_text = "\n".join(str(line) for line in lines) if lines else "(no output captured)"
        message = f"Here is my code:\n```python\n{code}\n```\n\nOutput:\n```\n{output_text}\n```"
        self.query_one(TabbedContent).active = "tab-chat"
        await self.send_to_tutor(message)


def main() -> None:
    TutorApp().run()


if __name__ == "__main__":
    main()
