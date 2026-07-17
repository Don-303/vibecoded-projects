"""Cell-based notebook editor -- quantlab's TUI editor, adapted.

The code panel is a scrollable list of cells with Jupyter's two-mode
keymap: command mode on the panel itself (j/k select, Enter to edit, a/b
insert, dd delete, x/c/v cut/copy/paste, z undo, m/y convert, o collapse
output, e/f ask the tutor), edit mode inside a cell's TextArea (Esc back
out, Shift+Enter run + advance, Ctrl+Enter run in place). Cells execute in
app.kernel's persistent IPython kernel; stdout/stderr stream into the
cell's output area live and matplotlib figures render inline as unicode
half-block pixels.

Persistence: a lesson's notebook serializes to a jupytext-style percent
script (cells_to_script / script_to_cells), so it fits progress.json's
existing per-lesson code string -- and a plain script saved by the old
single-buffer editor loads as one cell.
"""
from __future__ import annotations

import asyncio
import contextlib
import re
import time

from rich.console import Group
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.message import Message
from textual.widgets import Label, Markdown, Static, TextArea

from app import kernel

SAVED_PLOT_RE = re.compile(r"^\[saved plot: (.+)\]$", re.MULTILINE)
CELL_MARKER_RE = re.compile(r"^# %%( \[markdown\])?[ \t]*$", re.MULTILINE)


def _plot_renderable(path: str, max_width: int):
    """A saved matplotlib PNG rendered as unicode half-block pixels, so
    figures show up right under the cell that drew them. Returns None if
    the file or imaging deps are unavailable (the saved-path text line is
    already in the cell's stdout as a fallback pointer)."""
    try:
        from PIL import Image
        from rich_pixels import Pixels

        img = Image.open(path).convert("RGB")
        width = max(24, min(max_width, 80))
        img = img.resize((width, max(2, int(img.height * width / img.width))))
        return Pixels.from_image(img)
    except Exception:
        return None


def cells_to_script(cells: list[dict]) -> str:
    """Serialize cells to a percent-format script. A single code cell
    serializes to its bare source, so notebooks that never grew past one
    cell stay plain scripts (and old saved code round-trips untouched)."""
    if len(cells) == 1 and cells[0]["cell_type"] == "code":
        return cells[0]["source"]
    parts = []
    for cell in cells:
        source = cell["source"].rstrip("\n")
        if cell["cell_type"] == "markdown":
            body = "\n".join(
                f"# {line}" if line else "#" for line in source.splitlines()
            )
            parts.append("# %% [markdown]\n" + body)
        else:
            parts.append("# %%\n" + source)
    return "\n\n".join(parts)


def script_to_cells(text: str) -> list[dict]:
    """Parse a percent-format script back into cells. Text without any
    cell markers (old single-buffer saves) becomes one code cell."""
    markers = list(CELL_MARKER_RE.finditer(text))
    if not markers:
        return [{"cell_type": "code", "source": text.rstrip("\n")}]
    cells = []
    head = text[: markers[0].start()].strip("\n")
    if head.strip():
        cells.append({"cell_type": "code", "source": head})
    for i, match in enumerate(markers):
        end = markers[i + 1].start() if i + 1 < len(markers) else len(text)
        body = text[match.end(): end].strip("\n")
        if match.group(1):  # markdown: strip the comment prefix back off
            lines = [
                line[2:] if line.startswith("# ") else ("" if line == "#" else line)
                for line in body.splitlines()
            ]
            cells.append({"cell_type": "markdown", "source": "\n".join(lines)})
        else:
            cells.append({"cell_type": "code", "source": body})
    if not cells:
        cells = [{"cell_type": "code", "source": ""}]
    return cells


class CellTextArea(TextArea):
    """Cell editor that understands Jupyter's run/escape keys."""

    def __init__(self, cell_widget: Cell, **kwargs) -> None:
        super().__init__(**kwargs)
        self.cell_widget = cell_widget

    async def _on_key(self, event: events.Key) -> None:
        notebook = self.cell_widget.notebook
        if event.key == "escape":
            event.stop()
            event.prevent_default()
            notebook.enter_command_mode()
            return
        if event.key == "shift+enter":
            event.stop()
            event.prevent_default()
            await notebook.run_current(advance=True)
            return
        if event.key == "ctrl+enter":
            event.stop()
            event.prevent_default()
            await notebook.run_current(advance=False)
            return
        await super()._on_key(event)

    def on_focus(self) -> None:
        notebook = self.cell_widget.notebook
        notebook.select_widget(self.cell_widget)
        notebook.mode = "edit"
        notebook.refresh_selection()


class Cell(Vertical):
    """One notebook cell: gutter + editor (+ rendered markdown) + outputs."""

    def __init__(self, notebook: NotebookEditor, cell: dict) -> None:
        super().__init__(classes=f"cell {cell['cell_type']}")
        self.notebook = notebook
        self.cell = cell
        self.running = False
        self.queued = False
        self.collapsed = False
        self.execution_count: int | None = None
        self.duration: float | None = None
        self.stdout = ""
        self.stderr = ""
        self.plots: list[str] = []
        self.failed = False
        self.timed_out = False

    def compose(self) -> ComposeResult:
        is_code = self.cell["cell_type"] == "code"
        with Horizontal(classes="cell-row"):
            yield Label(self.gutter_text(), classes="gutter")
            yield CellTextArea(
                self,
                text=self.cell.get("source", ""),
                language="python" if is_code else "markdown",
                classes="cell-editor",
                soft_wrap=True,
                show_line_numbers=is_code,
                tab_behavior="indent",
                theme="monokai",
            )
        if not is_code:
            yield Markdown("", classes="md-view")
        yield Static("", classes="outputs")

    def on_mount(self) -> None:
        if self.cell["cell_type"] == "markdown":
            self.show_rendered(bool(self.cell.get("source", "").strip()))
        self.refresh_outputs()

    @property
    def editor(self) -> CellTextArea:
        return self.query_one(CellTextArea)

    def sync_source(self) -> None:
        # The editor may not be mounted yet right after a load; the dict
        # already holds the source in that case.
        with_editor = None
        try:
            with_editor = self.editor
        except NoMatches:
            return
        self.cell["source"] = with_editor.text

    def gutter_text(self) -> str:
        if self.cell["cell_type"] != "code":
            return "  md  "
        if self.running:
            return "In [*]:"
        if self.queued:
            return "In [.]:"
        count = self.execution_count
        return f"In [{count if count is not None else ' '}]:"

    def refresh_gutter(self) -> None:
        try:
            gutter = self.query_one(".gutter", Label)
        except NoMatches:  # cell was removed (e.g. deleted mid-run)
            return
        gutter.update(self.gutter_text())
        gutter.set_class(self.running, "busy")
        gutter.set_class(self.queued and not self.running, "queued")

    def show_rendered(self, rendered: bool) -> None:
        if self.cell["cell_type"] != "markdown":
            return
        self.query_one(".cell-row").display = not rendered
        md_view = self.query_one(".md-view", Markdown)
        md_view.display = rendered
        if rendered:
            md_view.update(self.cell.get("source", "") or "*empty markdown cell*")

    def refresh_outputs(self) -> None:
        self.refresh_gutter()
        try:
            out_widget = self.query_one(".outputs", Static)
        except NoMatches:  # cell was removed mid-run
            return
        has_output = bool(self.stdout or self.stderr or self.duration is not None)
        if self.cell["cell_type"] != "code" or not has_output:
            out_widget.update("")
            out_widget.display = False
            return
        out_widget.display = True
        if self.collapsed:
            out_widget.update(
                Text("▸ output collapsed — press o to expand", style="dim italic")
            )
            return
        parts: list = []
        if self.stdout.strip():
            parts.append(Text.from_ansi(self.stdout.rstrip("\n")))
        if self.stderr.strip():
            err = Text.from_ansi(self.stderr.rstrip("\n"))
            err.stylize("red3")
            parts.append(err)
        for path in self.plots:
            pixels = _plot_renderable(path, max(24, self.size.width - 12))
            if pixels is not None:
                parts.append(pixels)
        if self.failed:
            parts.append(
                Text(
                    "✗ the run failed — Ctrl+F (or f) asks the tutor for help",
                    style="bold red3",
                )
            )
        if self.duration is not None:
            mark = "◌ interrupted after" if self.timed_out else "✓"
            parts.append(Text(f"{mark} {self.duration:.2f}s", style="dim"))
        out_widget.update(Group(*parts))


class NotebookEditor(VerticalScroll, can_focus=True):
    """The scrollable notebook: owns the cells, the selection, and the
    Jupyter command-mode keymap."""

    DEFAULT_CSS = """
    NotebookEditor {
        padding: 0 1;
    }
    .cell {
        height: auto;
        margin: 1 0 0 0;
        border: round $panel;
    }
    .cell.selected {
        border: round $primary;
    }
    .cell.selected.editing {
        border: round $success;
    }
    .cell-row {
        height: auto;
    }
    .gutter {
        width: 8;
        color: $success;
    }
    .gutter.busy {
        color: $warning;
    }
    .gutter.queued {
        color: $accent;
    }
    .cell-editor {
        height: auto;
        max-height: 30;
        border: none;
    }
    .md-view {
        padding: 0 1 0 8;
        height: auto;
    }
    .outputs {
        padding: 0 1 0 8;
        height: auto;
        max-height: 40;
        overflow-y: auto;
    }
    """

    class CellRan(Message):
        """A code cell finished executing in the kernel."""

        def __init__(self, result: kernel.RunResult, code: str) -> None:
            self.result = result
            self.code = code
            super().__init__()

    class TutorHelp(Message):
        """Command-mode e (explain) or f (fix) on the selected cell."""

        def __init__(self, kind: str, code: str) -> None:
            self.kind = kind
            self.code = code
            super().__init__()

    class RestartRequested(Message):
        """Command-mode 00: restart the kernel."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.cells: list[Cell] = []
        self.selected = 0
        self.mode = "command"
        self.cell_clipboard: dict | None = None
        self.undo_stack: list[tuple[int, dict]] = []
        self._pending_key: tuple[str, float] = ("", 0.0)
        self._exec_count = 0
        self._run_all_active = False
        self.last_result: kernel.RunResult | None = None
        self.last_code: str | None = None

    def on_mount(self) -> None:
        if not self.cells:
            self.load_script("")

    # -- (de)serialization -------------------------------------------------

    def to_script(self) -> str:
        for widget in self.cells:
            widget.sync_source()
        return cells_to_script([widget.cell for widget in self.cells])

    def load_script(self, text: str) -> None:
        """Replace all cells with the parsed script. Mount/removal are
        queued on the message pump in call order, so this is safe to call
        from sync handlers; cell dicts are authoritative until then."""
        for widget in self.cells:
            widget.remove()
        self.cells = []
        for cell in script_to_cells(text):
            widget = Cell(self, cell)
            self.cells.append(widget)
            self.mount(widget)
        self.selected = 0
        self.mode = "command"
        self.refresh_selection()

    # -- selection / modes -------------------------------------------------

    def refresh_selection(self) -> None:
        for i, widget in enumerate(self.cells):
            widget.set_class(i == self.selected, "selected")
            widget.set_class(i == self.selected and self.mode == "edit", "editing")

    def select_cell(self, index: int, focus_editor: bool = False) -> None:
        if not self.cells:
            return
        self.selected = max(0, min(index, len(self.cells) - 1))
        self.refresh_selection()
        widget = self.cells[self.selected]
        widget.scroll_visible()
        if focus_editor:
            if widget.cell["cell_type"] == "markdown":
                widget.show_rendered(False)
            widget.editor.focus()
            self.mode = "edit"
            self.refresh_selection()

    def select_widget(self, widget: Cell) -> None:
        if widget in self.cells:
            self.selected = self.cells.index(widget)
            self.refresh_selection()

    def current_cell(self) -> Cell | None:
        if 0 <= self.selected < len(self.cells):
            return self.cells[self.selected]
        return None

    def enter_command_mode(self) -> None:
        widget = self.current_cell()
        if widget and widget.cell["cell_type"] == "markdown":
            widget.sync_source()
            widget.show_rendered(True)
        self.mode = "command"
        self.refresh_selection()
        self.focus()

    # -- command-mode keymap -----------------------------------------------

    async def _on_key(self, event: events.Key) -> None:
        if await self.handle_command_key(event):
            event.stop()
            event.prevent_default()
            return
        await super()._on_key(event)

    async def handle_command_key(self, event: events.Key) -> bool:
        """Jupyter command-mode keymap. Returns True if the key was handled."""
        key = event.key
        now = time.monotonic()
        prev, prev_t = self._pending_key
        self._pending_key = (key, now)
        chord = prev + key if now - prev_t < 0.8 else key

        if key in ("down", "j"):
            self.select_cell(self.selected + 1)
        elif key in ("up", "k"):
            self.select_cell(self.selected - 1)
        elif key == "enter":
            self.select_cell(self.selected, focus_editor=True)
        elif key == "shift+enter":
            await self.run_current(advance=True)
        elif key in ("ctrl+enter", "r"):
            await self.run_current(advance=False)
        elif key == "a":
            self.insert_cell(self.selected, "code")
        elif key == "b":
            self.insert_cell(self.selected + 1, "code")
        elif chord == "dd":
            self.delete_cell(self.selected)
            self._pending_key = ("", 0.0)
        elif key == "x":
            widget = self.current_cell()
            if widget:
                widget.sync_source()
                self.cell_clipboard = dict(widget.cell)
                self.delete_cell(self.selected)
        elif key == "c":
            widget = self.current_cell()
            if widget:
                widget.sync_source()
                self.cell_clipboard = dict(widget.cell)
                self.notify("Cell copied", timeout=2)
        elif key == "v":
            if self.cell_clipboard:
                self.insert_cell(self.selected + 1, raw=dict(self.cell_clipboard))
        elif key == "z":
            self.undo_delete()
        elif key == "m":
            self.convert_cell("markdown")
        elif key == "y":
            self.convert_cell("code")
        elif key == "o":
            widget = self.current_cell()
            if widget and widget.cell["cell_type"] == "code":
                widget.collapsed = not widget.collapsed
                widget.refresh_outputs()
        elif chord == "00":
            self.post_message(self.RestartRequested())
            self._pending_key = ("", 0.0)
        elif key == "e":
            widget = self.current_cell()
            if widget:
                widget.sync_source()
                if widget.cell["source"].strip():
                    self.post_message(self.TutorHelp("explain", widget.cell["source"]))
        elif key == "f":
            self.post_message(self.TutorHelp("fix", ""))
        elif key == "question_mark":
            self.app.action_show_help()
        else:
            return False
        return True

    # -- cell CRUD ----------------------------------------------------------

    def insert_cell(
        self, index: int, cell_type: str = "code", raw: dict | None = None
    ) -> None:
        cell = raw or {"cell_type": cell_type, "source": ""}
        index = max(0, min(index, len(self.cells)))
        widget = Cell(self, cell)
        if index < len(self.cells):
            self.mount(widget, before=self.cells[index])
        else:
            self.mount(widget)
        self.cells.insert(index, widget)
        self.select_cell(index)

    def delete_cell(self, index: int) -> None:
        if not (0 <= index < len(self.cells)):
            return
        widget = self.cells.pop(index)
        widget.sync_source()
        self.undo_stack.append((index, dict(widget.cell)))
        if len(self.undo_stack) > 50:
            self.undo_stack.pop(0)
        widget.remove()
        if not self.cells:
            self.insert_cell(0, "code")
        self.select_cell(min(index, len(self.cells) - 1))

    def undo_delete(self) -> None:
        if not self.undo_stack:
            self.notify("Nothing to undo", timeout=2)
            return
        index, cell = self.undo_stack.pop()
        self.insert_cell(min(index, len(self.cells)), raw=cell)

    def convert_cell(self, to_type: str) -> None:
        widget = self.current_cell()
        if not widget or widget.cell["cell_type"] == to_type:
            return
        widget.sync_source()
        raw = {"cell_type": to_type, "source": widget.cell["source"]}
        index = self.selected
        self.cells.pop(index)
        widget.remove()
        self.insert_cell(index, raw=raw)

    def insert_code_below(self, code: str) -> None:
        """Drop code (e.g. the tutor's last block) into a new cell below
        the selection -- or into the current cell if it's still empty."""
        widget = self.current_cell()
        if widget is not None:
            widget.sync_source()
            if widget.cell["cell_type"] == "code" and not widget.cell["source"].strip():
                widget.cell["source"] = code
                with contextlib.suppress(NoMatches):
                    widget.editor.text = code
                return
        self.insert_cell(
            self.selected + 1, raw={"cell_type": "code", "source": code}
        )

    # -- execution -----------------------------------------------------------

    async def run_current(self, advance: bool = False) -> None:
        widget = self.current_cell()
        if widget is None:
            return
        index = self.selected
        if widget.cell["cell_type"] == "markdown":
            widget.sync_source()
            widget.show_rendered(True)
            self.enter_command_mode()
        else:
            await self.execute_cell(widget)
        if advance:
            # cells may have shifted while the run awaited -- re-derive the
            # position from the widget, not the saved index
            if widget in self.cells:
                index = self.cells.index(widget)
            if index + 1 >= len(self.cells):
                self.insert_cell(len(self.cells), "code")
            self.enter_command_mode()
            self.select_cell(index + 1)

    async def execute_cell(self, widget: Cell) -> None:
        if widget.running:
            return
        widget.sync_source()
        code = widget.cell["source"]
        if not code.strip():
            return
        widget.running = True
        widget.collapsed = False
        widget.stdout = widget.stderr = ""
        widget.plots = []
        widget.failed = widget.timed_out = False
        widget.duration = None
        widget.refresh_outputs()

        # Stream output into the cell while it runs (throttled); chunks
        # arrive on the kernel thread, so hop to the UI thread to render.
        last_render = [0.0]

        def on_chunk(name: str, text: str) -> None:
            if name == "stdout":
                widget.stdout += text
            else:
                widget.stderr += text
            now = time.monotonic()
            if now - last_render[0] < 0.25:
                return
            last_render[0] = now
            # suppress: the app may be shutting down mid-run
            with contextlib.suppress(Exception):
                self.app.call_from_thread(widget.refresh_outputs)

        started = time.monotonic()
        try:
            result = await asyncio.to_thread(kernel.run_code, code, on_chunk)
        finally:
            widget.running = False
        self._exec_count += 1
        widget.execution_count = self._exec_count
        widget.stdout, widget.stderr = result.stdout, result.stderr
        widget.plots = SAVED_PLOT_RE.findall(result.stdout)
        widget.failed = result.returncode != 0 and not result.timed_out
        widget.timed_out = result.timed_out
        widget.duration = time.monotonic() - started
        if widget.is_mounted:  # may have been deleted mid-run
            widget.refresh_outputs()
        self.last_result = result
        self.last_code = code
        self.post_message(self.CellRan(result, code))

    async def run_all(self) -> None:
        if self._run_all_active:
            return
        self._run_all_active = True
        try:
            code_cells = [w for w in self.cells if w.cell["cell_type"] == "code"]
            for widget in code_cells:
                widget.queued = True
                widget.refresh_gutter()
            for widget in code_cells:
                widget.queued = False
                if widget in self.cells:  # may have been deleted mid-run
                    await self.execute_cell(widget)
        finally:
            self._run_all_active = False
