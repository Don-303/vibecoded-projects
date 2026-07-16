"""Headless smoke tests for the Textual app with a mocked model."""
import asyncio
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

from textual.widgets import Button, Select, TextArea, Tree

from app import agent, progress
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
                            "flashcards-btn", "recap-btn", "to-editor-btn", "stop-btn",
                            "reset-btn"):
                    app.query_one(f"#{bid}", Button)

                # tutor-style selector exists and defaults to standard
                assert app.query_one("#style-select", Select).value == "standard"

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
