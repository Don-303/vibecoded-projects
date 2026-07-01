"""Offline terminal tutor for ML in finance / econometrics / quant finance.

Runs entirely locally via Ollama + Qwen2.5-Coder-7B. No network required
once the model is pulled.
"""
from __future__ import annotations

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
    #lesson-header {
        padding: 0 1;
        background: $boost;
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

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal():
            with Vertical(id="sidebar"):
                yield Tree("Curriculum", id="curriculum-tree")
            with Vertical():
                yield Static("", id="lesson-header")
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
                with Horizontal():
                    yield Button("Mark lesson complete", id="complete-btn", variant="success")
                    yield Button("Next lesson →", id="next-btn")
        yield Footer()

    def on_mount(self) -> None:
        self.build_tree()
        self.load_lesson(self.current_lesson_id)
        self.run_worker(self.check_model(), exclusive=True)

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
        self.current_lesson_id = lesson_id
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
            async for token in agent.stream_reply(
                lesson.module_title, lesson.title, history, user_message
            ):
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

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "run-btn":
            self.run_code()
        elif event.button.id == "send-btn":
            await self.send_code_output_to_tutor()
        elif event.button.id == "complete-btn":
            progress.mark_complete(self.current_lesson_id)
            self.refresh_tree()
            self.load_lesson(self.current_lesson_id)
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
