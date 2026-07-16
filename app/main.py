"""Offline terminal tutor for ML in finance / econometrics / quant finance.

Runs entirely locally via Ollama (see agent.MODEL). No network required once
the model is pulled.
"""
from __future__ import annotations

import asyncio
import re
import subprocess
import time

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Markdown,
    OptionList,
    RichLog,
    Select,
    Static,
    TabbedContent,
    TabPane,
    TextArea,
    Tree,
)
from textual.widgets.option_list import Option

from app import __version__, agent, curriculum, progress, rag, sandbox, theming

DEFAULT_LESSON_ID = "py-core"

HELP_TEXT = f"""\
# Quant Tutor v{__version__}

## Keyboard shortcuts
| Key | Action |
| --- | --- |
| Ctrl+R | Run the code in the editor |
| Ctrl+N | Next lesson |
| Ctrl+B | Show/hide the curriculum sidebar |
| Ctrl+E | Show/hide the code editor panel |
| Ctrl+T | Switch model (Escape cancels) |
| F1 | This help |
| Ctrl+Q | Quit |

## Chat actions
- **Quiz me** — 5 questions, one at a time, graded as you go. The final
  score is recorded; scoring under 60% brings the lesson back for review
  in 3 days instead of 7.
- **Practice** — skeleton code with TODOs to fill in.
- **Example** — a complete runnable worked example.
- **Cards** — a flashcard deck (Q/A + cloze) from the lesson.
- **Recap** — key takeaways so far, and what's left.
- **Project** — three suggested mini-projects; pick one and it's walked
  through in stages (data → exploration → model → evaluation → report).
- **Code → editor** — copy the tutor's last code block into the editor.

## Workflow
Pick a lesson (filter box narrows the tree) — the tutor introduces it and
sets an exercise. Write code in the editor, Ctrl+R to run, then "Send
output to tutor" for review. Mark the lesson complete when it clicks;
it returns for spaced review a week later (↻ in the tree).

The tutor style selector (header) switches how it teaches this lesson:
standard, Socratic questioning, or layered explanations.
"""

# Tutor styles: teaching-skill playbooks (skills/, from DeepTutor's EduHub)
# the student can switch per lesson. "standard" = no extra skill.
STYLE_OPTIONS = [
    ("Style: standard", "standard"),
    ("Style: Socratic", "socratic-tutor"),
    ("Style: explainer", "concept-explainer"),
]

# Skill-derived action prompts. Sent as user messages so they extend the
# prompt cache instead of invalidating the system prompt. Both are phrased
# for one-reply-at-a-time interaction (num_predict bounds reply length).
QUIZ_MESSAGE = (
    "Quiz me on this lesson, exam-blueprint style: cover different "
    "sub-topics of what we've studied, mix question types (multiple choice "
    "with plausible distractors that reflect common mistakes, short answer, "
    "and application problems), spreading difficulty roughly 30% easy / "
    "50% medium / 20% hard. Test mode: ONE question at a time, tagged with "
    "its sub-topic and difficulty -- wait for my answer, grade it with a "
    "one-line explanation, then ask the next. Ask 5 questions in total; "
    "after grading the fifth, end your reply with a line in exactly this "
    "format: Final score: N/5"
)
# Matches the score line the quiz is instructed to end with; recording is
# best-effort (a reply without it simply records nothing).
QUIZ_SCORE_RE = re.compile(r"Final score:\s*(\d+)\s*/\s*(\d+)", re.IGNORECASE)
FLASHCARDS_MESSAGE = (
    "Make me a flashcard deck for this lesson: extract the 5-10 ideas most "
    "worth remembering from what we've covered. One fact per card; fronts "
    "must force recall, not recognition (prefer why/how over what); mix Q/A "
    "cards with cloze deletions, ordered easy to hard. Output a numbered "
    "list of Q: / A: pairs I can drill."
)
RECAP_MESSAGE = (
    "Recap this lesson: summarize the key takeaways we've covered so far in "
    "at most 8 short bullet points (include formulas or code idioms where "
    "they matter), then say in one line what's still left to cover."
)
PROJECT_MESSAGE = (
    "Suggest 3 quant finance mini-projects that build on this lesson, sized "
    "small enough to finish in a few sessions: for each, two lines -- what "
    "I'd build, and which packages/APIs it would teach me (be specific, "
    "e.g. statsmodels.tsa, sklearn.model_selection, scipy.optimize, arch; "
    "only packages that run offline: numpy, pandas, matplotlib, scipy, "
    "statsmodels, scikit-learn, arch -- synthetic seeded data, no web "
    "APIs). Then ask me which one to start, and walk me through it in "
    "stages: data, exploration, model, evaluation, report -- one stage at "
    "a time, skeleton code with TODOs, and review my output before we "
    "move on."
)


class ModelPicker(ModalScreen[str]):
    """Model chooser: the catalog with one-line descriptions and install
    status. Enter selects; the preselected entry is the current/last-used
    model, so Enter alone keeps it. Escape dismisses with "" (caller decides
    what that means: keep the preselection at launch, no-op mid-session)."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, installed: set[str], preselect: str) -> None:
        super().__init__()
        self._installed = installed
        self._preselect = preselect

    def action_cancel(self) -> None:
        self.dismiss("")

    def compose(self) -> ComposeResult:
        options = []
        for tag, desc in agent.MODEL_CATALOG:
            if agent.is_installed(tag, self._installed):
                mark = "[green]✓ installed[/green]"
            else:
                mark = f"[yellow]not installed — ollama pull {tag}[/yellow]"
            options.append(Option(f"[b]{tag}[/b]  {mark}\n[dim]{desc}[/dim]\n", id=tag))
        with Vertical(id="model-dialog"):
            yield Static("[b]Choose the tutor model for this session[/b]", id="model-title")
            yield OptionList(*options, id="model-list")
            yield Static("[dim]↑/↓ to browse · Enter to start[/dim]", id="model-hint")

    def on_mount(self) -> None:
        option_list = self.query_one(OptionList)
        tags = [tag for tag, _ in agent.MODEL_CATALOG]
        option_list.highlighted = tags.index(self._preselect) if self._preselect in tags else 0
        option_list.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)


class HelpScreen(ModalScreen[None]):
    """F1: shortcuts, chat actions, and the intended workflow."""

    BINDINGS = [("escape", "dismiss_help", "Close")]

    def action_dismiss_help(self) -> None:
        self.dismiss()

    def compose(self) -> ComposeResult:
        with Vertical(id="help-dialog"):
            with VerticalScroll():
                yield Markdown(HELP_TEXT)
            yield Static("[dim]Escape to close[/dim]", id="help-hint")


class ConfirmScreen(ModalScreen[bool]):
    """A yes/no confirmation for destructive actions."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, message: str, confirm_label: str = "Yes") -> None:
        super().__init__()
        self._message = message
        self._confirm_label = confirm_label

    def action_cancel(self) -> None:
        self.dismiss(False)

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-dialog"):
            yield Static(self._message, id="confirm-message")
            with Horizontal(id="confirm-actions"):
                yield Button(self._confirm_label, id="confirm-yes", variant="error")
                yield Button("Cancel", id="confirm-no")

    def on_mount(self) -> None:
        self.query_one("#confirm-no", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm-yes")


class TutorApp(App):
    TITLE = "Quant Tutor"
    SUB_TITLE = "offline ML-for-finance tutor"

    CSS = """
    #sidebar {
        width: 42;
        border-right: solid $accent;
    }
    #lesson-filter {
        margin: 0 1;
    }
    #header-row {
        height: auto;
        background: $boost;
    }
    /* Fit the tab area to the screen: without this it grows to its
       content and pushes #lesson-actions below the fold on shorter
       terminals. */
    TabbedContent {
        height: 1fr;
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
    #style-select {
        width: 24;
    }
    #progress-view {
        padding: 1;
    }
    #chat-log {
        padding: 1;
    }
    #chat-panel {
        width: 1fr;
        border-top: solid $accent;
    }
    #code-panel {
        width: 1fr;
        border-left: solid $accent;
        border-top: solid $accent;
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
    ModelPicker, HelpScreen, ConfirmScreen {
        align: center middle;
    }
    #help-dialog {
        width: 80;
        height: 80%;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    #help-hint {
        padding: 0 1;
        height: auto;
    }
    #confirm-dialog {
        width: 60;
        height: auto;
        border: thick $error;
        background: $surface;
        padding: 1 2;
    }
    #confirm-message {
        padding: 0 1 1 1;
    }
    #confirm-actions {
        height: auto;
    }
    #confirm-actions Button {
        margin-right: 2;
    }
    #model-dialog {
        width: 90;
        height: auto;
        max-height: 80%;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    #model-title, #model-hint {
        padding: 0 1;
    }
    #model-list {
        height: auto;
        margin: 1 0;
    }
    """

    BINDINGS = [
        ("ctrl+q", "quit", "Quit"),
        ("ctrl+r", "run_code", "Run code"),
        ("ctrl+n", "next_lesson", "Next lesson"),
        ("ctrl+b", "toggle_sidebar", "Sidebar"),
        ("ctrl+e", "toggle_code_panel", "Code panel"),
        ("ctrl+t", "switch_model", "Model"),
        ("f1", "show_help", "Help"),
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
                yield Input(placeholder="Filter lessons...", id="lesson-filter")
                yield Tree("Curriculum", id="curriculum-tree")
            with Vertical():
                with Horizontal(id="header-row"):
                    yield Static("", id="lesson-header")
                    yield Select(
                        STYLE_OPTIONS,
                        value="standard",
                        allow_blank=False,
                        id="style-select",
                        tooltip="How the tutor teaches this lesson: standard, "
                        "Socratic (guides with questions), or explainer "
                        "(layered concept explanations)",
                    )
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
                                        tooltip="One-question-at-a-time quiz on this "
                                        "lesson (counts as a review)",
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
                                        "Cards", id="flashcards-btn", compact=True,
                                        tooltip="Flashcard deck (Q/A + cloze) from this lesson",
                                    )
                                    yield Button(
                                        "Recap", id="recap-btn", compact=True,
                                        tooltip="Key takeaways so far + what's left in this lesson",
                                    )
                                    yield Button(
                                        "Project", id="project-btn", compact=True,
                                        tooltip="Suggest quant mini-projects for this lesson and "
                                        "walk through one in stages, teaching the packages",
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
                                        tooltip="Run the code (Ctrl+R); plots are "
                                        "saved to data/plots/",
                                    )
                                    yield Button(
                                        "Send output to tutor", id="send-btn", compact=True,
                                        tooltip="Send your code and its output to "
                                        "the tutor for review",
                                    )
                                    yield Button(
                                        "Copy", id="copy-btn", compact=True,
                                        tooltip="Copy the editor contents to the clipboard",
                                    )
                                    yield Button(
                                        "Clear", id="clear-btn", compact=True,
                                        tooltip="Clear the editor and output pane",
                                    )
                                    yield Button(
                                        "Plots", id="plots-btn", compact=True,
                                        tooltip="Open the folder of figures saved by your runs",
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
        self.query_one("#chat-panel").border_title = "Tutor"
        self.query_one("#code-panel").border_title = "Code editor"
        self.query_one("#code-output", RichLog).border_title = "Output"
        self.build_tree()
        self.refresh_progress_view()
        self.set_interval(1.0, self.update_session_timer)
        # Follow the desktop theme, and keep following it live. Only a
        # *change* in detection re-applies, so picking a theme manually
        # (command palette) sticks until the system theme actually changes.
        self._system_theme = theming.system_theme(set(self.available_themes))
        self.theme = self._system_theme
        self.set_interval(3.0, self._sync_theme)
        # Lesson loading waits for the model choice so the auto-started
        # intro (and the warm-up) run against the chosen model.
        self.run_worker(self._pick_model_and_start(), group="model-check")

    async def _sync_theme(self) -> None:
        # Off-thread: the gsettings fallback shells out, which must not
        # stall the UI loop (or token streaming) every tick.
        detected = await asyncio.to_thread(
            theming.system_theme, set(self.available_themes)
        )
        if detected != self._system_theme:
            self._system_theme = detected
            self.theme = detected

    async def _pick_model_and_start(self) -> None:
        installed = await agent.installed_models()
        preselect = progress.get_model() or agent.DEFAULT_MODEL
        chosen = await self.push_screen_wait(ModelPicker(installed, preselect)) or preselect
        agent.set_model(chosen)
        progress.save_model(chosen)
        self.sub_title = f"v{__version__} · {chosen}"
        self.load_lesson(self.current_lesson_id)
        self.query_one("#chat-input", Input).focus()
        if not rag.EMBEDDINGS_PATH.exists():
            self.notify(
                "Lecture corpus index not found — the tutor will rely on the "
                "model's own knowledge. Run scripts/build_rag_index.py to "
                "ground lessons in real lecture material.",
                severity="warning",
                timeout=12,
            )
        await self.check_model()

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

    def action_show_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_switch_model(self) -> None:
        self.run_worker(self._switch_model(), group="model-check", exclusive=True)

    async def _switch_model(self) -> None:
        installed = await agent.installed_models()
        chosen = await self.push_screen_wait(ModelPicker(installed, agent.MODEL))
        if not chosen or chosen == agent.MODEL:
            return
        agent.set_model(chosen)
        progress.save_model(chosen)
        self.sub_title = f"v{__version__} · {chosen}"
        self.notify(f"Switched to {chosen} — loading it in the background...")
        # Load the new model and prefill the current lesson's prompt so the
        # next message doesn't pay cold-load + full prefill back to back.
        await agent.warm_up()
        lesson = curriculum.find_lesson(self.current_lesson_id)
        if lesson:
            self.run_worker(
                self._prewarm_worker(lesson), group="prefix-warm", exclusive=True
            )

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
        query = self.query_one("#lesson-filter", Input).value.lower().strip()
        for module in curriculum.load_modules():
            lessons = module.lessons
            if query:
                # A module-title match shows the whole module; otherwise
                # only the matching lessons.
                if query not in module.title.lower():
                    lessons = [le for le in lessons if query in le.title.lower()]
                if not lessons:
                    continue
            # Filtered results are all expanded; otherwise collapse
            # everything except the module being studied -- a fully expanded
            # 11-module tree is mostly scrolling.
            is_current = any(le.id == self.current_lesson_id for le in module.lessons)
            module_node = tree.root.add(module.title, expand=bool(query) or is_current)
            for lesson in lessons:
                if progress.is_review_due(lesson.id):
                    mark = "↻ "
                elif progress.is_complete(lesson.id):
                    mark = "✓ "
                else:
                    mark = "  "
                module_node.add_leaf(f"{mark}{lesson.title}", data=lesson.id)

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "lesson-filter":
            self.refresh_tree()

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

        # Reflect this lesson's saved tutor style without treating the
        # programmatic update as a user change.
        style_select = self.query_one("#style-select", Select)
        with style_select.prevent(Select.Changed):
            style_select.value = progress.get_teaching_skill(lesson_id) or "standard"

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

    def _current_skill(self) -> str | None:
        return progress.get_teaching_skill(self.current_lesson_id)

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id != "style-select":
            return
        skill_name = None if event.value == "standard" else str(event.value)
        if skill_name == progress.get_teaching_skill(self.current_lesson_id):
            return
        progress.save_teaching_skill(self.current_lesson_id, skill_name)
        lesson = curriculum.find_lesson(self.current_lesson_id)
        if lesson:
            # The style changes the system prompt, so the prompt cache for
            # this lesson is stale -- prefill the new prefix in the background.
            self.run_worker(
                self._prewarm_worker(lesson), group="prefix-warm", exclusive=True
            )
        label = {value: text for text, value in STYLE_OPTIONS}.get(event.value, event.value)
        self.notify(f"Tutor style for this lesson: {label.removeprefix('Style: ')}")

    async def _prewarm_worker(self, lesson) -> None:
        # Debounce: browsing quickly through lessons shouldn't queue a
        # prefill per click (exclusive=True cancels this sleep on the next
        # lesson switch, before any request is sent).
        await asyncio.sleep(1.5)
        await agent.prewarm(
            lesson, progress.get_history(lesson.id), progress.get_teaching_skill(lesson.id)
        )

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
        started = time.monotonic()
        first_token_at: float | None = None
        n_tokens = 0
        try:
            async for token in agent.stream_reply(
                lesson, history, user_message, self._current_skill()
            ):
                if first_token_at is None:
                    first_token_at = time.monotonic()
                n_tokens += 1
                reply_so_far += token
                now = time.monotonic()
                if now - last_render >= 0.25:
                    last_render = now
                    await chat_log.update(self.chat_buffer + reply_so_far + " ▌")
                    self._scroll_chat_to_end()
        except Exception as exc:
            reply_so_far = (
                f"_Error talking to local model: {exc}_\n\n"
                "_Check that Ollama is running (`systemctl status ollama`) "
                "and the model is installed — Ctrl+T to pick another model, "
                "then resend your message._"
            )

        self.chat_buffer += reply_so_far
        # Speed stats for the reply (each stream chunk is ~one token). Shown
        # in the chat only -- never persisted, so they don't pollute the
        # model's context on later turns.
        if first_token_at is not None and n_tokens > 1:
            gen_secs = time.monotonic() - first_token_at
            rate = f" · {n_tokens / gen_secs:.1f} tok/s" if gen_secs > 0 else ""
            self.chat_buffer += (
                f"\n\n_[first token {first_token_at - started:.1f}s{rate}]_"
            )
        await chat_log.update(self.chat_buffer)
        self._scroll_chat_to_end()
        progress.append_history(self.current_lesson_id, "assistant", reply_so_far)
        self._maybe_record_quiz_score(reply_so_far)

    def _maybe_record_quiz_score(self, reply: str) -> None:
        match = QUIZ_SCORE_RE.search(reply)
        if not match:
            return
        correct, total = int(match.group(1)), int(match.group(2))
        progress.record_quiz_score(self.current_lesson_id, correct, total)
        self.refresh_progress_view()
        self.refresh_tree()  # a weak score can change the review-due glyph
        self.notify(f"Quiz score recorded: {correct}/{total}")

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
        done = sum(1 for m in modules for le in m.lessons if progress.is_complete(le.id))
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
            m_done = sum(1 for le in m.lessons if progress.is_complete(le.id))
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
        stats = progress.quiz_stats()
        if stats:
            count, avg = stats
            lines.append(f"- Quizzes taken: {count} (average score {int(avg * 100)}%)")

        due = [le for m in modules for le in m.lessons if progress.is_review_due(le.id)]
        if due:
            lines.append("")
            lines.append(f"## Review due ({len(due)}) — use \"Quiz me\" to refresh")
            for le in due:
                last = progress.last_quiz_score(le.id)
                note = f" (last quiz {last['correct']}/{last['total']})" if last else ""
                lines.append(f"- ↻ {le.module_title}: {le.title}{note}")

        remaining = [le for m in modules for le in m.lessons if not progress.is_complete(le.id)]
        if remaining:
            lines.append("")
            lines.append(f"## Remaining lessons ({len(remaining)})")
            for le in remaining:
                lines.append(f"- {le.module_title}: {le.title}")
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

            def _do_reset(confirmed: bool | None) -> None:
                if confirmed:
                    progress.clear_history(self.current_lesson_id)
                    self.load_lesson(self.current_lesson_id)
                    self.notify("Lesson conversation cleared.")

            self.push_screen(
                ConfirmScreen(
                    "Clear this lesson's entire conversation history?\n"
                    "Saved editor code and completion status are kept.",
                    confirm_label="Clear it",
                ),
                _do_reset,
            )
        elif event.button.id == "quiz-btn":
            if progress.is_complete(self.current_lesson_id):
                progress.mark_reviewed(self.current_lesson_id)
                self.refresh_tree()
            await self.send_to_tutor(QUIZ_MESSAGE)
        elif event.button.id == "flashcards-btn":
            await self.send_to_tutor(FLASHCARDS_MESSAGE)
        elif event.button.id == "recap-btn":
            await self.send_to_tutor(RECAP_MESSAGE)
        elif event.button.id == "project-btn":
            await self.send_to_tutor(PROJECT_MESSAGE)
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
        elif event.button.id == "plots-btn":
            if sandbox.PLOTS_DIR.is_dir() and any(sandbox.PLOTS_DIR.iterdir()):
                subprocess.Popen(
                    ["xdg-open", str(sandbox.PLOTS_DIR)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                self.notify("Opening the plots folder...")
            else:
                self.notify(
                    "No plots saved yet — run code that draws a matplotlib figure.",
                    severity="warning",
                )

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
