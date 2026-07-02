"""Offline terminal tutor for ML in finance / econometrics / quant finance.

Runs entirely locally via Ollama + Qwen2.5-7B-Instruct. No network required
once the model is pulled.
"""
from __future__ import annotations

import asyncio
import re
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
    TITLE = "Quant Tutor"
    SUB_TITLE = "offline ML-for-finance tutor"

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
    #chat-panel {
        width: 1fr;
    }
    #code-panel {
        width: 1fr;
        border-left: solid $accent;
    }
    #chat-actions, #code-actions, #lesson-actions {
        height: auto;
        padding: 0 1;
    }
    #chat-actions Button, #code-actions Button, #lesson-actions Button {
        margin-right: 1;
    }
    #chat-input {
        margin: 0 1;
    }
    #code-editor {
        height: 1fr;
    }
    #code-output {
        height: 10;
        border-top: solid $accent;
    }
    """

    BINDINGS = [
        ("ctrl+q", "quit", "Quit"),
        ("ctrl+r", "run_code", "Run code"),
        ("ctrl+n", "next_lesson", "Next lesson"),
        ("ctrl+b", "toggle_sidebar", "Sidebar"),
        ("ctrl+e", "toggle_code_panel", "Code panel"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.current_lesson_id = DEFAULT_LESSON_ID
        self.chat_buffer = ""
        self.session_start = time.monotonic()
        self.lesson_start: float | None = None
        self.last_run_result: sandbox.RunResult | None = None
        # Which lesson's code currently occupies the editor -- None until the
        # first load so app start can't clobber saved code with an empty buffer.
        self._code_loaded_for: str | None = None

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
                    with TabPane("Workspace", id="tab-work"):
                        with Horizontal(id="workspace"):
                            with Vertical(id="chat-panel"):
                                with VerticalScroll(id="chat-scroll"):
                                    yield Markdown("", id="chat-log")
                                yield Input(
                                    placeholder="Ask the tutor, or paste code/output for review...",
                                    id="chat-input",
                                )
                                with Horizontal(id="chat-actions"):
                                    yield Button(
                                        "Quiz me", id="quiz-btn", compact=True,
                                        tooltip="One-question-at-a-time quiz on this lesson (counts as a review)",
                                    )
                                    yield Button(
                                        "Practice", id="practice-btn", compact=True,
                                        tooltip="Skeleton code with TODOs for you to fill in",
                                    )
                                    yield Button(
                                        "Example", id="example-btn", compact=True,
                                        tooltip="Complete runnable worked example",
                                    )
                                    yield Button(
                                        "Code → editor", id="to-editor-btn", compact=True,
                                        tooltip="Copy the tutor's last code block into the editor",
                                    )
                                    yield Button(
                                        "Stop", id="stop-btn", variant="error", compact=True,
                                        tooltip="Cancel the tutor's current reply",
                                    )
                            with Vertical(id="code-panel"):
                                # code_editor preset: Tab indents (instead of
                                # moving focus), line numbers, no soft wrap.
                                yield TextArea.code_editor(
                                    "",
                                    language="python",
                                    id="code-editor",
                                    theme="monokai",
                                )
                                with Horizontal(id="code-actions"):
                                    yield Button(
                                        "Run", id="run-btn", variant="primary", compact=True,
                                        tooltip="Run the code (Ctrl+R); plots are saved to data/plots/",
                                    )
                                    yield Button(
                                        "Send output to tutor", id="send-btn", compact=True,
                                        tooltip="Send your code and its output to the tutor for review",
                                    )
                                    yield Button(
                                        "Copy", id="copy-btn", compact=True,
                                        tooltip="Copy the editor contents to the clipboard",
                                    )
                                    yield Button(
                                        "Clear", id="clear-btn", compact=True,
                                        tooltip="Clear the editor and output pane",
                                    )
                                yield RichLog(id="code-output", highlight=True, markup=True)
                    with TabPane("Progress", id="tab-progress"):
                        with VerticalScroll():
                            yield Markdown("", id="progress-view")
                with Horizontal(id="lesson-actions"):
                    yield Button(
                        "Mark complete", id="complete-btn", variant="success", compact=True,
                        tooltip="Mark this lesson done (comes due for review in a week)",
                    )
                    yield Button(
                        "Next lesson →", id="next-btn", compact=True,
                        tooltip="Move to the next lesson (Ctrl+N)",
                    )
                    yield Button(
                        "Reset chat", id="reset-btn", variant="warning", compact=True,
                        tooltip="Clear this lesson's conversation and start over",
                    )
        yield Footer()

    def on_mount(self) -> None:
        self.build_tree()
        self.load_lesson(self.current_lesson_id)
        self.refresh_progress_view()
        self.run_worker(self.check_model(), group="model-check")
        self.set_interval(1.0, self.update_session_timer)
        self.query_one("#chat-input", Input).focus()

    def update_session_timer(self) -> None:
        elapsed = int(time.monotonic() - self.session_start)
        h, rem = divmod(elapsed, 3600)
        m, s = divmod(rem, 60)
        self.query_one("#session-timer", Static).update(f"Session: {h:02d}:{m:02d}:{s:02d}")
        # Checkpoint tracked time and editor code every minute so closing the
        # terminal abruptly (instead of ctrl+q) loses at most a minute.
        if elapsed and elapsed % 60 == 0:
            self._flush_lesson_time(restart=True)
            self._save_editor_code()

    async def action_quit(self) -> None:
        self._flush_lesson_time()
        self._save_editor_code()
        self.exit()

    def _save_editor_code(self) -> None:
        if self._code_loaded_for is not None:
            progress.save_code(self._code_loaded_for, self.query_one("#code-editor", TextArea).text)

    def action_run_code(self) -> None:
        self.run_code()

    def action_next_lesson(self) -> None:
        nxt = curriculum.next_lesson(self.current_lesson_id)
        if nxt:
            self.load_lesson(nxt.id)

    def action_toggle_sidebar(self) -> None:
        sidebar = self.query_one("#sidebar")
        sidebar.display = not sidebar.display

    def action_toggle_code_panel(self) -> None:
        panel = self.query_one("#code-panel")
        panel.display = not panel.display

    def _flush_lesson_time(self, restart: bool = False) -> None:
        if self.lesson_start is not None and self.current_lesson_id:
            elapsed = time.monotonic() - self.lesson_start
            progress.add_time(self.current_lesson_id, elapsed)
        self.lesson_start = time.monotonic() if restart else None

    async def check_model(self) -> None:
        ok = await agent.check_model_available()
        if not ok:
            self.notify(
                f"Model '{agent.MODEL}' not found on local Ollama. "
                f"Run: ollama pull {agent.MODEL}",
                severity="error",
                timeout=10,
            )
            return
        # Load the model now, while the user is still picking a lesson,
        # instead of making the first message pay the cold-load wait.
        await agent.warm_up()

    def build_tree(self) -> None:
        tree = self.query_one("#curriculum-tree", Tree)
        tree.root.expand()
        for module in curriculum.load_modules():
            # Collapse everything except the module being studied -- a fully
            # expanded 10-module tree is mostly scrolling.
            is_current = any(l.id == self.current_lesson_id for l in module.lessons)
            module_node = tree.root.add(module.title, expand=is_current)
            for lesson in module.lessons:
                if progress.is_review_due(lesson.id):
                    mark = "↻ "
                elif progress.is_complete(lesson.id):
                    mark = "✓ "
                else:
                    mark = "  "
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
        self._save_editor_code()
        self.current_lesson_id = lesson_id
        self.lesson_start = time.monotonic()
        self.query_one("#code-editor", TextArea).text = progress.get_code(lesson_id)
        self._code_loaded_for = lesson_id
        if progress.is_review_due(lesson_id):
            status = "↻ review due"
        elif progress.is_complete(lesson_id):
            status = "✓ done"
        else:
            status = "○ in progress"
        header = self.query_one("#lesson-header", Static)
        header.update(f"[b]{lesson.module_title}[/b] › {lesson.title}  [dim]{status}[/dim]")

        history = progress.get_history(lesson_id)
        self.chat_buffer = self._render_history(history)
        chat_log = self.query_one("#chat-log", Markdown)
        self.query_one("#code-output", RichLog).clear()
        if history:
            chat_log.update(self.chat_buffer)
            # Prefill the model's prompt cache for this lesson in the
            # background so the first real message starts replying in
            # seconds. (Fresh lessons don't need this: auto-start below
            # prefills as a side effect.)
            self.run_worker(
                self._prewarm_worker(lesson), group="prefix-warm", exclusive=True
            )
        else:
            # Fresh lesson: have the tutor open it instead of waiting for the
            # student to say something. Only the tutor's intro is persisted,
            # so the canned opener doesn't clutter the visible history.
            chat_log.update("*Starting lesson...*")
            self.run_worker(
                self.stream_tutor_reply(lesson, [], agent.AUTO_START_MESSAGE),
                group="tutor-stream",
                exclusive=True,
            )

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
        history = progress.get_history(self.current_lesson_id)
        progress.append_history(self.current_lesson_id, "user", message)
        self.chat_buffer += f"\n\n---\n\n**You**:\n\n{message}\n"
        chat_log = self.query_one("#chat-log", Markdown)
        await chat_log.update(self.chat_buffer)
        self._scroll_chat_to_end()
        self.run_worker(
            self.stream_tutor_reply(lesson, history, message),
            group="tutor-stream",
            exclusive=True,
        )

    async def _prewarm_worker(self, lesson) -> None:
        # Debounce: browsing quickly through lessons shouldn't queue a
        # prefill per click (exclusive=True cancels this sleep on the next
        # lesson switch, before any request is sent).
        await asyncio.sleep(1.5)
        await agent.prewarm(lesson, progress.get_history(lesson.id))

    async def stream_tutor_reply(self, lesson, history: list[dict], user_message: str) -> None:
        separator = "\n\n---\n\n" if self.chat_buffer else ""
        self.chat_buffer += f"{separator}**Tutor**:\n\n"
        reply_so_far = ""
        chat_log = self.query_one("#chat-log", Markdown)
        # First token can take a while on CPU -- show that something is happening.
        await chat_log.update(self.chat_buffer + "_Thinking..._")
        self._scroll_chat_to_end()
        # Re-parsing the whole markdown document per token is O(n^2) over the
        # conversation and competes with Ollama for CPU; render at most ~4x/s.
        last_render = 0.0
        try:
            async for token in agent.stream_reply(lesson, history, user_message):
                reply_so_far += token
                now = time.monotonic()
                if now - last_render >= 0.25:
                    last_render = now
                    await chat_log.update(self.chat_buffer + reply_so_far + " ▌")
                    self._scroll_chat_to_end()
        except Exception as exc:
            reply_so_far = f"_Error talking to local model: {exc}_"

        self.chat_buffer += reply_so_far
        await chat_log.update(self.chat_buffer)
        self._scroll_chat_to_end()
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

        due = [l for m in modules for l in m.lessons if progress.is_review_due(l.id)]
        if due:
            lines.append("")
            lines.append(f"## Review due ({len(due)}) — use \"Quiz me\" to refresh")
            for l in due:
                lines.append(f"- ↻ {l.module_title}: {l.title}")

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
        elif event.button.id == "reset-btn":
            progress.clear_history(self.current_lesson_id)
            self.load_lesson(self.current_lesson_id)
            self.notify("Lesson conversation cleared.")
        elif event.button.id == "quiz-btn":
            if progress.is_complete(self.current_lesson_id):
                progress.mark_reviewed(self.current_lesson_id)
                self.refresh_tree()
            await self.send_to_tutor(
                "Quiz me on what we've covered so far in this lesson: ask me "
                "one short question at a time and check my answer before the next."
            )
        elif event.button.id == "practice-btn":
            await self.send_to_tutor(
                "Give me a practice exercise for this lesson: skeleton Python "
                "code with TODO comments where I fill in the key parts, plus a "
                "short description of what the finished code should print."
            )
        elif event.button.id == "example-btn":
            await self.send_to_tutor(
                "Show me a complete worked example for the current concept: "
                "full runnable Python code with a brief walkthrough of each "
                "step and what the output means."
            )
        elif event.button.id == "to-editor-btn":
            self.copy_last_code_to_editor()
        elif event.button.id == "stop-btn":
            self.action_stop_generation()
        elif event.button.id == "copy-btn":
            self.copy_to_clipboard(self.query_one("#code-editor", TextArea).text)
            self.notify("Editor contents copied to clipboard.")
        elif event.button.id == "clear-btn":
            self.query_one("#code-editor", TextArea).text = ""
            self.query_one("#code-output", RichLog).clear()
            self.last_run_result = None

    def action_stop_generation(self) -> None:
        """Cancel an in-flight tutor reply. Nothing is persisted for the
        cancelled turn, so the conversation stays consistent on reload."""
        cancelled = self.workers.cancel_group(self, "tutor-stream")
        if cancelled:
            self.chat_buffer += "_[generation stopped]_"
            self.query_one("#chat-log", Markdown).update(self.chat_buffer)
            self._scroll_chat_to_end()

    def copy_last_code_to_editor(self) -> None:
        """Pull the most recent ```python block from the tutor's replies into
        the code editor, so exercises are one click from runnable."""
        for turn in reversed(progress.get_history(self.current_lesson_id)):
            if turn["role"] != "assistant":
                continue
            blocks = re.findall(r"```(?:python|py)?\n(.*?)```", turn["content"], re.DOTALL)
            if blocks:
                editor = self.query_one("#code-editor", TextArea)
                editor.text = blocks[-1].strip() + "\n"
                self.query_one(TabbedContent).active = "tab-work"
                self.query_one("#code-panel").display = True
                editor.focus()
                self.notify("Code copied to editor — Ctrl+R to run.")
                return
        self.notify("No code block found in the tutor's replies yet.", severity="warning")

    def run_code(self) -> None:
        self.run_worker(self._run_code_worker(), exclusive=True, group="run-code")

    async def _run_code_worker(self) -> None:
        code = self.query_one("#code-editor", TextArea).text
        output_log = self.query_one("#code-output", RichLog)
        run_btn = self.query_one("#run-btn", Button)
        output_log.clear()
        output_log.write("[dim]running...[/dim]")
        run_btn.disabled = True
        try:
            # Subprocess call blocks up to sandbox.TIMEOUT_SECONDS; keep the UI alive.
            result = await asyncio.to_thread(sandbox.run_code, code)
        finally:
            run_btn.disabled = False
        self.last_run_result = result
        output_log.clear()
        if result.stdout:
            output_log.write(result.stdout)
        if result.stderr:
            output_log.write(f"[red]{result.stderr}[/red]")
        if not result.stdout and not result.stderr:
            output_log.write("[dim](no output)[/dim]")

    async def send_code_output_to_tutor(self) -> None:
        code = self.query_one("#code-editor", TextArea).text
        result = self.last_run_result
        if result is None:
            output_text = "(code has not been run yet)"
        else:
            output_text = "\n".join(p for p in (result.stdout, result.stderr) if p).strip()
            output_text = output_text or "(no output)"
        message = f"Here is my code:\n```python\n{code}\n```\n\nOutput:\n```\n{output_text}\n```"
        self.query_one(TabbedContent).active = "tab-work"
        await self.send_to_tutor(message)


def main() -> None:
    TutorApp().run()


if __name__ == "__main__":
    main()
