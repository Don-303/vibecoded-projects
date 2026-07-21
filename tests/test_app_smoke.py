"""Headless smoke tests for the Textual app with a mocked model."""
import asyncio
import json
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

from textual.widgets import Button, Input, Select, Tree

from app import agent, kernel, progress, theming
from app.main import ImportScreen, ModelPicker, TutorApp
from app.notebook import NotebookEditor

CANNED = 'Intro!\n```python\nprint(1 + 1)\n```\nReady?'


async def _fake_stream(lesson, history, user_message, skill_name=None):
    for tok in CANNED.split(" "):
        yield tok + " "


def _mocked_model(stream=_fake_stream):
    """Patch every agent entry point that would hit the local Ollama."""
    stack = ExitStack()
    stack.enter_context(patch.object(agent, "stream_reply", stream))
    stack.enter_context(
        patch.object(agent, "installed_models", AsyncMock(return_value={agent.DEFAULT_MODEL}))
    )
    stack.enter_context(patch.object(agent, "warm_up", AsyncMock()))
    stack.enter_context(patch.object(agent, "prewarm", AsyncMock()))
    # Theme detection reads the host desktop's config; pin it for tests.
    stack.enter_context(
        patch.object(theming, "system_theme", lambda builtin: "textual-dark")
    )
    # Don't spawn a real IPython kernel per smoke test.
    stack.enter_context(patch.object(kernel, "ensure", lambda: None))
    return stack


async def _pick_model(pilot):
    """Dismiss the launch-time model picker (Enter keeps the preselection)."""
    await pilot.pause(0.2)
    await pilot.press("enter")
    await pilot.pause(0.2)


def test_boot_picker_autostart_and_layout():
    async def scenario():
        with _mocked_model():
            app = TutorApp()
            async with app.run_test(size=(140, 40)) as pilot:
                # the model picker gates startup; Enter accepts the default
                await pilot.pause(0.2)
                assert isinstance(app.screen, ModelPicker)
                await pilot.press("enter")
                await pilot.pause(0.2)
                assert agent.MODEL == agent.DEFAULT_MODEL
                assert progress.get_model() == agent.DEFAULT_MODEL

                # auto-start streams the tutor intro for a fresh lesson
                for _ in range(50):
                    await pilot.pause(0.1)
                    if "Intro!" in app.chat_buffer:
                        break
                assert "Intro!" in app.chat_buffer

                # panels side by side
                chat, code = app.query_one("#chat-panel"), app.query_one("#code-panel")
                assert chat.region.x < code.region.x
                assert chat.region.width > 20 and code.region.width > 20

                # only current module expanded
                tree = app.query_one("#curriculum-tree", Tree)
                assert sum(n.is_expanded for n in tree.root.children) == 1

                # code -> editor drops the fenced block into a cell
                app.copy_last_code_to_editor()
                await pilot.pause(0.2)
                assert "print(1 + 1)" in app.query_one("#notebook", NotebookEditor).to_script()

                # all action buttons exist
                for bid in ("quiz-btn", "practice-btn", "example-btn",
                            "flashcards-btn", "project-btn",
                            "to-editor-btn", "stop-btn",
                            "reset-btn", "run-btn", "run-all-btn", "send-btn"):
                    app.query_one(f"#{bid}", Button)

                # tutor-style selector exists and defaults to standard
                assert app.query_one("#style-select", Select).value == "standard"

                # theme followed the (pinned) system detection
                assert app.theme == "textual-dark"

    asyncio.run(scenario())


def test_picker_remembers_last_choice():
    async def scenario():
        with _mocked_model():
            progress.save_model("phi4-mini")
            app = TutorApp()
            async with app.run_test(size=(140, 40)) as pilot:
                await _pick_model(pilot)
                assert agent.MODEL == "phi4-mini"

    asyncio.run(scenario())


def test_code_persists_across_lesson_switch():
    async def scenario():
        with _mocked_model():
            app = TutorApp()
            async with app.run_test(size=(140, 40)) as pilot:
                await _pick_model(pilot)
                await pilot.pause(0.3)
                notebook = app.query_one("#notebook", NotebookEditor)
                notebook.cells[0].editor.text = "x = 42"
                app.load_lesson("py-numpy")
                await pilot.pause(0.2)
                assert notebook.to_script() == ""
                app.load_lesson("py-core")
                await pilot.pause(0.2)
                assert notebook.to_script() == "x = 42"

    asyncio.run(scenario())


def test_stop_generation_persists_nothing():
    async def slow_stream(lesson, history, user_message, skill_name=None):
        yield "partial "
        await asyncio.sleep(60)
        yield "never"

    async def scenario():
        with _mocked_model(stream=slow_stream):
            app = TutorApp()
            async with app.run_test(size=(140, 40)) as pilot:
                await _pick_model(pilot)
                await pilot.pause(0.5)
                app.action_stop_generation()
                await pilot.pause(0.3)
                assert "[generation stopped]" in app.chat_buffer
                assert all(
                    "never" not in t["content"]
                    for t in progress.get_history("py-core")
                )

    asyncio.run(scenario())


def test_style_selection_persists_per_lesson():
    async def scenario():
        with _mocked_model():
            app = TutorApp()
            async with app.run_test(size=(140, 40)) as pilot:
                await _pick_model(pilot)
                await pilot.pause(0.3)
                app.query_one("#style-select", Select).value = "socratic-tutor"
                await pilot.pause(0.2)
                assert progress.get_teaching_skill("py-core") == "socratic-tutor"
                # switching lessons shows that lesson's own saved style
                app.load_lesson("py-numpy")
                await pilot.pause(0.2)
                assert app.query_one("#style-select", Select).value == "standard"
                # and coming back restores the choice
                app.load_lesson("py-core")
                await pilot.pause(0.2)
                assert app.query_one("#style-select", Select).value == "socratic-tutor"

    asyncio.run(scenario())


def test_quiz_score_line_is_recorded():
    async def scenario():
        with _mocked_model():
            app = TutorApp()
            async with app.run_test(size=(140, 40)) as pilot:
                await _pick_model(pilot)
                await pilot.pause(0.3)
                app._maybe_record_quiz_score("Well done! Final score: 4/5")
                assert progress.last_quiz_score("py-core")["correct"] == 4
                # replies without a score line record nothing
                app._maybe_record_quiz_score("Keep going, no score here.")
                assert progress.last_quiz_score("py-core")["correct"] == 4

    asyncio.run(scenario())


def test_lesson_filter_narrows_tree():
    async def scenario():
        with _mocked_model():
            app = TutorApp()
            async with app.run_test(size=(140, 40)) as pilot:
                await _pick_model(pilot)
                await pilot.pause(0.3)
                from textual.widgets import Input, Tree

                tree = app.query_one("#curriculum-tree", Tree)
                all_modules = len(tree.root.children)
                app.query_one("#lesson-filter", Input).value = "garch"
                await pilot.pause(0.3)
                filtered = tree.root.children
                assert 0 < len(filtered) < all_modules
                assert all(n.is_expanded for n in filtered)
                titles = [str(leaf.label) for n in filtered for leaf in n.children]
                assert any("GARCH" in t for t in titles)
                # clearing restores the full tree
                app.query_one("#lesson-filter", Input).value = ""
                await pilot.pause(0.3)
                assert len(tree.root.children) == all_modules

    asyncio.run(scenario())


def test_help_screen_opens_and_closes():
    async def scenario():
        with _mocked_model():
            app = TutorApp()
            async with app.run_test(size=(140, 40)) as pilot:
                await _pick_model(pilot)
                await pilot.pause(0.2)
                await pilot.press("f1")
                await pilot.pause(0.2)
                from app.main import HelpScreen

                assert isinstance(app.screen, HelpScreen)
                await pilot.press("escape")
                await pilot.pause(0.2)
                assert not isinstance(app.screen, HelpScreen)

    asyncio.run(scenario())


def test_reset_requires_confirmation():
    async def scenario():
        with _mocked_model():
            app = TutorApp()
            async with app.run_test(size=(140, 40)) as pilot:
                await _pick_model(pilot)
                for _ in range(50):
                    await pilot.pause(0.1)
                    if progress.get_history("py-core"):
                        break
                assert progress.get_history("py-core")

                from app.main import ConfirmScreen

                # cancelling keeps the history
                await pilot.click("#reset-btn")
                await pilot.pause(0.2)
                assert isinstance(app.screen, ConfirmScreen)
                await pilot.press("escape")
                await pilot.pause(0.2)
                assert progress.get_history("py-core")

                # confirming clears it
                await pilot.click("#reset-btn")
                await pilot.pause(0.2)
                await pilot.click("#confirm-yes")
                await pilot.pause(0.3)
                assert not isinstance(app.screen, ConfirmScreen)

    asyncio.run(scenario())


def test_resumes_last_lesson_on_launch():
    async def scenario():
        with _mocked_model():
            progress.save_last_lesson("py-numpy")
            app = TutorApp()
            async with app.run_test(size=(140, 40)) as pilot:
                await _pick_model(pilot)
                await pilot.pause(0.3)
                assert app.current_lesson_id == "py-numpy"

    asyncio.run(scenario())


def test_ctrl_d_jumps_to_review_due():
    async def scenario():
        with _mocked_model():
            from datetime import date, timedelta

            progress.mark_complete("py-numpy")
            state = progress._load()
            state["completed_at"]["py-numpy"] = (
                date.today() - timedelta(days=10)
            ).isoformat()
            progress._save(state)
            app = TutorApp()
            async with app.run_test(size=(140, 40)) as pilot:
                await _pick_model(pilot)
                await pilot.pause(0.3)
                app.action_next_review()
                await pilot.pause(0.3)
                assert app.current_lesson_id == "py-numpy"

    asyncio.run(scenario())


def test_editor_code_attached_once_to_typed_questions():
    async def scenario():
        with _mocked_model():
            app = TutorApp()
            async with app.run_test(size=(140, 40)) as pilot:
                await _pick_model(pilot)
                await pilot.pause(0.5)
                notebook = app.query_one("#notebook", NotebookEditor)
                notebook.cells[0].editor.text = "z = 7"
                await app.send_to_tutor("why is my code wrong?", attach_editor=True)
                await pilot.pause(0.5)
                turns = [t for t in progress.get_history("py-core") if t["role"] == "user"]
                assert "My current editor code" in turns[-1]["content"]
                assert "z = 7" in turns[-1]["content"]
                # unchanged editor -> not re-attached
                await app.send_to_tutor("and now?", attach_editor=True)
                await pilot.pause(0.5)
                turns = [t for t in progress.get_history("py-core") if t["role"] == "user"]
                assert "My current editor code" not in turns[-1]["content"]

    asyncio.run(scenario())


def test_variable_explorer_toggles_and_refreshes():
    async def scenario():
        with _mocked_model(), patch.object(
            kernel, "list_variables", lambda: [("x", "int", "", "7")]
        ):
            app = TutorApp()
            async with app.run_test(size=(140, 40)) as pilot:
                await _pick_model(pilot)
                await pilot.pause(0.3)
                from textual.widgets import DataTable

                table = app.query_one("#vars-table", DataTable)
                assert not table.display
                await pilot.press("f2")
                await pilot.pause(0.3)
                assert table.display
                assert table.row_count == 1
                assert "Kernel variables (1)" in str(table.border_title)
                await pilot.press("f2")
                await pilot.pause(0.1)
                assert not table.display

    asyncio.run(scenario())


def test_ctrl_f_sends_failed_run_to_tutor():
    async def scenario():
        with _mocked_model():
            app = TutorApp()
            async with app.run_test(size=(140, 40)) as pilot:
                await _pick_model(pilot)
                await pilot.pause(0.5)
                app.last_run_code = "1/0"
                app.last_run_result = kernel.RunResult(
                    stdout="",
                    stderr="ZeroDivisionError: division by zero",
                    timed_out=False,
                    returncode=1,
                )
                await app.action_fix_error()
                # wait for the tutor's reply to the fix request to land
                for _ in range(50):
                    await pilot.pause(0.1)
                    history = progress.get_history("py-core")
                    if len(history) >= 3 and history[-1]["role"] == "assistant":
                        break
                turns = [t for t in history if t["role"] == "user"]
                assert "My run failed" in turns[-1]["content"]
                assert "ZeroDivisionError" in turns[-1]["content"]
                assert "1/0" in turns[-1]["content"]
                # a successful (or absent) run has nothing to fix
                before = len(progress.get_history("py-core"))
                app.last_run_result = None
                await app.action_fix_error()
                await pilot.pause(0.3)
                assert len(progress.get_history("py-core")) == before

    asyncio.run(scenario())


def test_plot_renderable_and_saved_plot_parsing(tmp_path):
    from PIL import Image

    from app.notebook import SAVED_PLOT_RE, _plot_renderable

    path = tmp_path / "fig.png"
    Image.new("RGB", (64, 48), (200, 30, 30)).save(path)
    assert _plot_renderable(str(path), max_width=60) is not None
    # unreadable file -> graceful None (the text fallback is already printed)
    assert _plot_renderable(str(tmp_path / "missing.png"), max_width=60) is None
    stdout = f"some output\n[saved plot: {path}]\n"
    assert SAVED_PLOT_RE.findall(stdout) == [str(path)]


def _write_sample_notebook(tmp_path):
    nb = tmp_path / "demo.ipynb"
    nb.write_text(json.dumps({
        "cells": [
            {"cell_type": "markdown", "source": ["# Demo\n", "a tiny notebook"]},
            {"cell_type": "code", "source": ["x = 1\n", "print(x)\n"]},
        ]
    }))
    return nb


def test_import_notebook_starts_a_walkthrough(tmp_path):
    nb = _write_sample_notebook(tmp_path)
    seen_messages = []

    async def capture_stream(lesson, history, user_message, skill_name=None):
        seen_messages.append(user_message)
        yield "Overview!\n```python\nx = 1\n```\nReady?"

    async def scenario():
        with _mocked_model(stream=capture_stream):
            app = TutorApp()
            async with app.run_test(size=(140, 40)) as pilot:
                await _pick_model(pilot)
                await pilot.pause(0.3)
                app.action_import_notebook()
                await pilot.pause(0.3)
                assert isinstance(app.screen, ImportScreen)
                app.screen.query_one("#import-path", Input).value = str(nb)
                await pilot.press("enter")
                for _ in range(50):
                    await pilot.pause(0.1)
                    if "Overview!" in app.chat_buffer:
                        break
                assert "Overview!" in app.chat_buffer
                assert app.current_lesson_id.startswith("import-demo-")
                # the panel starts blank -- the student writes it themselves
                assert app.notebook.to_script() == ""
                # seen_messages[0] is py-core's own auto-start from launch;
                # the import triggers a second, distinct auto-start message
                assert agent.IMPORT_START_MESSAGE in seen_messages

    asyncio.run(scenario())


def test_import_notebook_cancelled_leaves_lesson_unchanged():
    async def scenario():
        with _mocked_model():
            app = TutorApp()
            async with app.run_test(size=(140, 40)) as pilot:
                await _pick_model(pilot)
                await pilot.pause(0.3)
                before = app.current_lesson_id
                app.action_import_notebook()
                await pilot.pause(0.3)
                await pilot.press("escape")
                await pilot.pause(0.2)
                assert app.current_lesson_id == before

    asyncio.run(scenario())


def test_import_rejects_missing_or_invalid_file(tmp_path):
    bad_json = tmp_path / "bad.ipynb"
    bad_json.write_text("not json")

    async def scenario():
        with _mocked_model():
            app = TutorApp()
            async with app.run_test(size=(140, 40)) as pilot:
                await _pick_model(pilot)
                await pilot.pause(0.3)
                before = app.current_lesson_id
                await app._import_notebook(str(tmp_path / "missing.ipynb"))
                assert app.current_lesson_id == before
                await app._import_notebook(str(bad_json))
                assert app.current_lesson_id == before

    asyncio.run(scenario())


def test_reimporting_same_notebook_resumes_history(tmp_path):
    nb = _write_sample_notebook(tmp_path)

    async def scenario():
        with _mocked_model():
            app = TutorApp()
            async with app.run_test(size=(140, 40)) as pilot:
                await _pick_model(pilot)
                await pilot.pause(0.3)
                await app._import_notebook(str(nb))
                for _ in range(50):
                    await pilot.pause(0.1)
                    if progress.get_history(app.current_lesson_id):
                        break
                first_id = app.current_lesson_id
                history_len = len(progress.get_history(first_id))
                assert history_len > 0

                app.load_lesson("py-core")
                await pilot.pause(0.2)
                await app._import_notebook(str(nb))
                await pilot.pause(0.2)
                assert app.current_lesson_id == first_id
                assert len(progress.get_history(first_id)) == history_len

    asyncio.run(scenario())


def test_tutor_check_runs_in_kernel_once():
    calls = {"n": 0}
    ran: list[str] = []

    async def checking_stream(lesson, history, user_message, skill_name=None):
        calls["n"] += 1
        if calls["n"] == 2:
            # reply to the student's message asks for a kernel check
            yield "Let me verify.\n```check\nprint(1 + 1)\n```"
        else:
            yield "All good."

    def fake_run(code, on_output=None):
        ran.append(code)
        return kernel.RunResult(stdout="2\n", stderr="", timed_out=False, returncode=0)

    async def scenario():
        with _mocked_model(stream=checking_stream), patch.object(kernel, "run_code", fake_run):
            app = TutorApp()
            async with app.run_test(size=(140, 40)) as pilot:
                await _pick_model(pilot)
                await pilot.pause(0.5)
                await app.send_to_tutor("check my work")
                for _ in range(50):
                    await pilot.pause(0.1)
                    if calls["n"] >= 3:
                        break
                await pilot.pause(0.3)
                assert ran == ["print(1 + 1)"]
                history = progress.get_history("py-core")
                auto = [t for t in history if t["content"].startswith("[auto]")]
                assert len(auto) == 1
                assert "2" in auto[0]["content"]
                # the follow-up reply exists and no further check ran
                assert history[-1]["role"] == "assistant"
                assert calls["n"] == 3

    asyncio.run(scenario())
