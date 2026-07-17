"""Notebook editor: percent-format persistence and in-app cell behavior."""
import asyncio
from unittest.mock import patch

from app import kernel
from app.main import TutorApp
from app.notebook import NotebookEditor, cells_to_script, script_to_cells
from tests.test_app_smoke import _mocked_model, _pick_model


def test_single_code_cell_stays_a_plain_script():
    cells = [{"cell_type": "code", "source": "x = 1\nprint(x)"}]
    assert cells_to_script(cells) == "x = 1\nprint(x)"
    assert script_to_cells("x = 1\nprint(x)") == cells


def test_multi_cell_percent_roundtrip():
    cells = [
        {"cell_type": "markdown", "source": "## Notes\n\nkey idea"},
        {"cell_type": "code", "source": "import numpy as np\nxs = np.arange(3)"},
        {"cell_type": "code", "source": "xs.mean()"},
    ]
    script = cells_to_script(cells)
    assert script.count("# %%") == 3
    assert "# %% [markdown]" in script
    assert script_to_cells(script) == cells


def test_script_without_markers_is_one_cell():
    assert script_to_cells("") == [{"cell_type": "code", "source": ""}]
    old = "a = 1\n# a comment, not a marker\nprint(a)\n"
    assert script_to_cells(old) == [{"cell_type": "code", "source": old.rstrip("\n")}]


def test_cell_insert_delete_undo_via_keys():
    async def scenario():
        with _mocked_model():
            app = TutorApp()
            async with app.run_test(size=(140, 40)) as pilot:
                await _pick_model(pilot)
                await pilot.pause(0.3)
                notebook = app.query_one("#notebook", NotebookEditor)
                notebook.focus()
                await pilot.pause(0.1)
                assert len(notebook.cells) == 1

                await pilot.press("b")  # insert below
                await pilot.pause(0.2)
                assert len(notebook.cells) == 2
                assert notebook.selected == 1

                notebook.cells[1].editor.text = "keep = 1"
                await pilot.press("d", "d")  # delete selected
                await pilot.pause(0.2)
                assert len(notebook.cells) == 1

                await pilot.press("z")  # undo restores source too
                await pilot.pause(0.2)
                assert len(notebook.cells) == 2
                assert "keep = 1" in notebook.to_script()

    asyncio.run(scenario())


def test_run_current_cell_updates_output_and_app_state():
    def fake_run(code, on_output=None):
        if on_output is not None:
            on_output("stdout", "hello from kernel\n")
        return kernel.RunResult(
            stdout="hello from kernel\n", stderr="", timed_out=False, returncode=0
        )

    async def scenario():
        with _mocked_model(), patch.object(kernel, "run_code", fake_run):
            app = TutorApp()
            async with app.run_test(size=(140, 40)) as pilot:
                await _pick_model(pilot)
                await pilot.pause(0.3)
                notebook = app.query_one("#notebook", NotebookEditor)
                cell = notebook.cells[0]
                cell.editor.text = "print('hello from kernel')"
                await notebook.run_current()
                await pilot.pause(0.3)
                assert cell.execution_count == 1
                assert "hello from kernel" in cell.stdout
                assert cell.gutter_text() == "In [1]:"
                # the CellRan message reached the app
                assert app.last_run_result is not None
                assert app.last_run_result.returncode == 0
                assert app.last_run_code == "print('hello from kernel')"

    asyncio.run(scenario())


def test_failed_cell_marks_failure_for_ctrl_f():
    def fake_run(code, on_output=None):
        return kernel.RunResult(
            stdout="", stderr="NameError: name 'nope' is not defined",
            timed_out=False, returncode=1,
        )

    async def scenario():
        with _mocked_model(), patch.object(kernel, "run_code", fake_run):
            app = TutorApp()
            async with app.run_test(size=(140, 40)) as pilot:
                await _pick_model(pilot)
                await pilot.pause(0.3)
                notebook = app.query_one("#notebook", NotebookEditor)
                cell = notebook.cells[0]
                cell.editor.text = "nope"
                await notebook.run_current()
                await pilot.pause(0.3)
                assert cell.failed
                assert app.last_run_result.returncode == 1
                assert app.last_run_code == "nope"

    asyncio.run(scenario())
