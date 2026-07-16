"""Headless smoke tests for the Textual app with a mocked model."""
import asyncio
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

from textual.widgets import Button, Select, TextArea, Tree

from app import agent, kernel, progress, theming
from app.main import ModelPicker, TutorApp

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

                # code -> editor pulls the fenced block
                app.copy_last_code_to_editor()
                assert "print(1 + 1)" in app.query_one("#code-editor", TextArea).text

                # all action buttons exist
                for bid in ("quiz-btn", "practice-btn", "example-btn",
                            "flashcards-btn", "project-btn",
                            "to-editor-btn", "stop-btn",
                            "reset-btn"):
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
                editor = app.query_one("#code-editor", TextArea)
                editor.text = "x = 42"
                app.load_lesson("py-numpy")
                await pilot.pause(0.2)
                assert editor.text == ""
                app.load_lesson("py-core")
                await pilot.pause(0.2)
                assert editor.text == "x = 42"

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
