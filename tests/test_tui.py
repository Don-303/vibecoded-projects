"""TUI tests via Textual's pilot: navigation, CRUD, streaming, panels.

These drive the real app with real keystrokes and a real kernel. No Ollama
required (AI-dependent flows live in test_ai.py).
"""

import asyncio
import os

os.environ["QUANTLAB_AUTOSAVE"] = "0"  # keep demo notebooks pristine

import pytest  # noqa: E402

DEMO = "demo_quant_research.ipynb"


async def _boot(app):
    from core import kernel_service
    for _ in range(120):
        if kernel_service.is_alive():
            return
        await asyncio.sleep(0.5)
    raise TimeoutError("kernel did not start")


def test_navigation_crud_and_modes():
    from tui import NotebookPanel, QuantLabApp

    async def run():
        app = QuantLabApp(notebook=DEMO)
        async with app.run_test(size=(160, 50)) as pilot:
            await _boot(app)
            app.query_one(NotebookPanel).focus()

            await pilot.press("j", "j")
            assert app.selected == 2
            await pilot.press("k")
            assert app.selected == 1

            n = len(app.cells)
            await pilot.press("b")               # insert below
            assert len(app.cells) == n + 1
            await pilot.press("m")               # to markdown
            assert app.cells[app.selected].cell["cell_type"] == "markdown"
            await pilot.press("y")               # back to code
            assert app.cells[app.selected].cell["cell_type"] == "code"
            await pilot.press("d", "d")          # delete
            assert len(app.cells) == n
            await pilot.press("z")               # undo delete
            assert len(app.cells) == n + 1
            await pilot.press("d", "d")

            await pilot.press("enter")
            assert app.mode == "edit"
            await pilot.press("escape")
            assert app.mode == "command"

    asyncio.run(run())


def test_run_cell_and_streaming():
    from tui import NotebookPanel, QuantLabApp

    async def run():
        app = QuantLabApp(notebook=DEMO)
        async with app.run_test(size=(160, 50)) as pilot:
            await _boot(app)
            app.query_one(NotebookPanel).focus()

            # streaming: partial output visible while running
            await pilot.press("j", "b", "enter")
            app.cells[app.selected].editor.text = (
                "import time\nfor i in range(4):\n"
                "    print('tick', i, flush=True)\n    time.sleep(1)")
            await pilot.press("escape")
            w = app.cells[app.selected]
            task = asyncio.ensure_future(app.execute_cell_widget(w))
            saw_partial = False
            for _ in range(30):
                await asyncio.sleep(0.3)
                text = "".join(o.get("text", "") for o in (w.cell.get("outputs") or [])
                               if o.get("output_type") == "stream")
                if w.running and "tick 0" in text and "tick 3" not in text:
                    saw_partial = True
                    break
            await task
            assert saw_partial, "no live streaming observed"
            assert w.cell["execution_count"] is not None
            assert w.duration and w.duration > 3
            await pilot.press("d", "d")

    asyncio.run(run())


def test_cut_preserves_unsaved_edits():
    from tui import NotebookPanel, QuantLabApp

    async def run():
        app = QuantLabApp(notebook=DEMO)
        async with app.run_test(size=(160, 50)) as pilot:
            await _boot(app)
            app.query_one(NotebookPanel).focus()
            await pilot.press("j")
            app.cells[app.selected].editor.text = "unsynced_edit_zz = 1"
            await pilot.press("x")
            assert "unsynced_edit_zz" in app.cell_clipboard["source"]
            await pilot.press("v", "d", "d", "z", "d", "d")

    asyncio.run(run())


def test_delete_cell_mid_run_does_not_crash():
    from tui import NotebookPanel, QuantLabApp

    async def run():
        app = QuantLabApp(notebook=DEMO)
        async with app.run_test(size=(160, 50)) as pilot:
            await _boot(app)
            app.query_one(NotebookPanel).focus()
            await pilot.press("b", "enter")
            app.cells[app.selected].editor.text = "import time; time.sleep(3)"
            await pilot.press("escape")
            idx = app.selected
            w = app.cells[idx]
            task = asyncio.ensure_future(app.execute_cell_widget(w))
            await asyncio.sleep(0.8)
            await app.delete_cell(idx)
            await task  # must not raise

    asyncio.run(run())


def test_typing_marks_dirty():
    """Regression: unsaved typing must set the dirty flag, or quitting skips
    the save prompt and autosave never fires (silent data loss)."""
    from tui import NotebookPanel, QuantLabApp

    async def run():
        app = QuantLabApp(notebook=DEMO)
        async with app.run_test(size=(160, 50)) as pilot:
            await _boot(app)
            assert not app.dirty, "loading a notebook must not mark it dirty"
            app.query_one(NotebookPanel).focus()
            await pilot.press("j", "enter")   # edit first code cell
            await pilot.press("z")            # type one character
            await pilot.pause()
            assert app.dirty, "typing in a cell must mark the notebook dirty"
            # undo the typed char so the demo notebook stays pristine
            await pilot.press("ctrl+z")
            await pilot.press("escape")

    asyncio.run(run())


def test_panels_and_modals():
    from textual.widgets import DataTable
    from tui import ChatInput, HelpScreen, NotebookPanel, QuantLabApp

    async def run():
        app = QuantLabApp(notebook=DEMO)
        async with app.run_test(size=(160, 50)) as pilot:
            await _boot(app)
            app.query_one(NotebookPanel).focus()

            # help open/close via all keys
            await pilot.press("f1")
            assert isinstance(app.screen, HelpScreen)
            await pilot.press("escape")
            assert not isinstance(app.screen, HelpScreen)

            # chat collapse + Ctrl+L restore
            panel = app.query_one("#chat-panel")
            await pilot.press("f4")
            assert not panel.display
            app.action_focus_chat()
            assert panel.display

            # vars explorer fills after defining a variable
            from core import kernel_service
            await asyncio.to_thread(kernel_service.execute, "pytest_var_zz = 5")
            await pilot.press("f2")
            table = app.query_one("#vars-table", DataTable)
            for _ in range(40):
                if table.row_count > 0:
                    break
                await asyncio.sleep(0.5)
            assert table.row_count > 0
            await pilot.press("f2")

    asyncio.run(run())


def test_switch_notebook_prompts_on_unsaved_changes():
    """Regression: Ctrl+O with unsaved edits must prompt instead of silently
    discarding them (same data-loss class as the v1.4.1 quit bug)."""
    from tui import ConfirmQuitScreen, NotebookPanel, QuantLabApp

    async def run():
        app = QuantLabApp(notebook=DEMO)
        async with app.run_test(size=(160, 50)) as pilot:
            await _boot(app)
            app.query_one(NotebookPanel).focus()
            app.set_dirty()
            proceeded = []
            app._confirm_discard("switch", lambda: proceeded.append(True))
            await pilot.pause()
            assert isinstance(app.screen, ConfirmQuitScreen)
            await pilot.press("escape")           # cancel keeps everything
            assert not proceeded
            app._confirm_discard("switch", lambda: proceeded.append(True))
            await pilot.pause()
            await pilot.press("q")                # explicit discard proceeds
            assert proceeded
            app.set_dirty(False)

    asyncio.run(run())


def test_import_notebook_via_f7(tmp_path):
    """F7 pulls in an external .ipynb (outside the sandboxed notebooks/ dir,
    e.g. one downloaded elsewhere) and makes it the active notebook."""
    import nbformat
    from textual.widgets import Input

    from core import NOTEBOOK_DIR
    from tui import ImportScreen, QuantLabApp

    src = tmp_path / "external_walkthrough.ipynb"
    nb = nbformat.v4.new_notebook()
    nb.cells = [nbformat.v4.new_code_cell("print('from outside')")]
    nbformat.write(nb, str(src))

    async def run():
        app = QuantLabApp(notebook=DEMO)
        async with app.run_test(size=(160, 50)) as pilot:
            await _boot(app)
            await pilot.press("f7")
            assert isinstance(app.screen, ImportScreen)
            app.screen.query_one(Input).value = str(src)
            await pilot.press("enter")
            for _ in range(20):
                await pilot.pause()
                if app.nb_path == "external_walkthrough.ipynb":
                    break
            assert app.nb_path == "external_walkthrough.ipynb"
            assert app.cells[0].cell["source"] == "print('from outside')"

    try:
        asyncio.run(run())
    finally:
        (NOTEBOOK_DIR / "external_walkthrough.ipynb").unlink(missing_ok=True)


def test_walkthrough_runs_narrates_and_advances():
    """`w` should run/render the selected cell and land on the next one.
    Stubs the network call so this doesn't need Ollama — the real chat
    round-trip is covered by test_ai.py's requires_ollama tests."""
    from tui import NotebookPanel, QuantLabApp

    async def run():
        app = QuantLabApp(notebook=DEMO)
        async with app.run_test(size=(160, 50)) as pilot:
            await _boot(app)
            app.query_one(NotebookPanel).focus()

            async def fake_send_chat_impl(prompt):
                app.chat_history.append({"role": "user", "content": prompt})
                app.chat_history.append({"role": "assistant", "content": "stubbed narration"})
                return "stubbed narration"
            app._send_chat_impl = fake_send_chat_impl

            app.select_cell(0)
            w0 = app.cells[0]
            start_selected = app.selected
            await pilot.press("w")
            for _ in range(20):
                await pilot.pause()
                if app.selected != start_selected:
                    break
            assert app.selected == start_selected + 1
            assert not app.chat_busy
            assert app.chat_history[-1]["content"] == "stubbed narration"
            if w0.cell["cell_type"] == "code":
                assert w0.cell.get("execution_count") is not None

    asyncio.run(run())
