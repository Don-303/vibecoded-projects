"""Headless smoke tests for the Textual app with a mocked model."""
import asyncio
from unittest.mock import patch

from textual.widgets import Button, TextArea, Tree

from app import agent
from app.main import TutorApp

CANNED = 'Intro!\n```python\nprint(1 + 1)\n```\nReady?'


async def _fake_stream(lesson, history, user_message, skill_name=None):
    for tok in CANNED.split(" "):
        yield tok + " "


def test_boot_autostart_and_layout():
    async def scenario():
        with patch.object(agent, "stream_reply", _fake_stream):
            app = TutorApp()
            async with app.run_test(size=(140, 40)) as pilot:
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
                from textual.widgets import Select
                assert app.query_one("#style-select", Select).value == "standard"

    asyncio.run(scenario())


def test_code_persists_across_lesson_switch():
    async def scenario():
        with patch.object(agent, "stream_reply", _fake_stream):
            app = TutorApp()
            async with app.run_test(size=(140, 40)) as pilot:
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
        with patch.object(agent, "stream_reply", slow_stream):
            app = TutorApp()
            async with app.run_test(size=(140, 40)) as pilot:
                await pilot.pause(0.5)
                app.action_stop_generation()
                await pilot.pause(0.3)
                assert "[generation stopped]" in app.chat_buffer
                from app import progress
                assert all(
                    "never" not in t["content"]
                    for t in progress.get_history("py-core")
                )

    asyncio.run(scenario())


def test_style_selection_persists_per_lesson():
    async def scenario():
        with patch.object(agent, "stream_reply", _fake_stream):
            app = TutorApp()
            async with app.run_test(size=(140, 40)) as pilot:
                await pilot.pause(0.3)
                from textual.widgets import Select

                from app import progress

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
