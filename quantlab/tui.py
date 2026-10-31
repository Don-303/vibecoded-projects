#!/usr/bin/env python3
"""
QuantLab TUI — a full terminal (CLI) Jupyter notebook editor with live kernel
execution and an AI Quant Reviewer sidebar. No browser, no webview.

Run:  quantlab            (or: .venv/bin/python tui.py [notebook.ipynb])

Jupyter-style keyboard shortcuts (press ? for the full list):
  command mode: j/k or arrows move · Enter edit · a/b insert · dd delete
                m/y markdown/code · x/c/v cut/copy/paste · z undo delete
                r or Ctrl+Enter run · Shift+Enter run+advance · 00 restart
                ii interrupt · Ctrl+S save · e explain cell · f fix error
  edit mode:    Esc back to command mode · Shift+Enter run+advance
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import re
import time
from typing import Any, Dict, List, Optional

import httpx
from rich.console import Group
from rich.text import Text
from textual import events, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import (DataTable, Footer, Input, Label, Markdown, OptionList,
                             Static, TextArea)
from textual.widgets.option_list import Option

from core import (
    KERNEL_NAME,
    OLLAMA_MODEL,
    OLLAMA_TEMPERATURE,
    OLLAMA_URL,
    NotebookPathError,
    build_chat_messages,
    create_notebook,
    expand_file_references,
    import_notebook,
    kernel_service,
    list_notebooks,
    load_notebook_file,
    save_notebook_file,
    strip_ansi,
)

CODE_BLOCK_RE = re.compile(r"```(?:python|py)?\n(.*?)```", re.S)

# Compiling the tree-sitter highlight query costs ~20ms and Textual redoes it
# from scratch twice per TextArea — ~2.4s of a 60-cell notebook load, all
# producing the identical Query. Textual caches Language objects per name, so
# the compiled Query can be shared safely (execution uses a fresh QueryCursor).
try:
    from textual.document._syntax_aware_document import SyntaxAwareDocument

    _QUERY_CACHE: Dict[tuple, Any] = {}
    _orig_prepare_query = SyntaxAwareDocument.prepare_query

    def _cached_prepare_query(self, query: str):  # type: ignore[no-untyped-def]
        key = (id(self.language), query)
        if key not in _QUERY_CACHE:
            # store the Language too so its id can't be recycled
            _QUERY_CACHE[key] = (_orig_prepare_query(self, query), self.language)
        return _QUERY_CACHE[key][0]

    SyntaxAwareDocument.prepare_query = _cached_prepare_query
except Exception:  # pragma: no cover - tree-sitter not installed
    pass

# Runs silently inside the kernel to inventory user variables for the
# variable explorer (F2). Underscore-prefixed temps are filtered back out.
VARS_SNIPPET = """\
import json as _ql_json
_ql_rows = []
for _ql_k, _ql_v in list(globals().items()):
    if _ql_k.startswith('_') or _ql_k in ('In', 'Out', 'exit', 'quit', 'get_ipython', 'open'):
        continue
    _ql_t = type(_ql_v).__name__
    if _ql_t in ('module', 'function', 'type', 'ABCMeta', 'builtin_function_or_method', 'method'):
        continue
    try:
        _ql_shape = getattr(_ql_v, 'shape', None)
        _ql_shape = str(_ql_shape) if _ql_shape is not None else (
            str(len(_ql_v)) if hasattr(_ql_v, '__len__') else '')
    except Exception:
        _ql_shape = ''
    try:
        _ql_r = repr(_ql_v).replace('\\n', ' ')
        _ql_r = _ql_r[:64] + ('…' if len(_ql_r) > 64 else '')
    except Exception:
        _ql_r = '<unrepresentable>'
    _ql_rows.append((_ql_k, _ql_t, _ql_shape, _ql_r))
print('QLVARS::' + _ql_json.dumps(_ql_rows))
"""

# Structured shortcut data for the help screen. Rendered via DataTable
# rather than a Markdown table — Textual's Markdown-table widget silently
# drops most row content when deeply nested (ModalScreen > VerticalScroll)
# inside this app's full widget tree; DataTable (already used for the
# variable explorer and output viewer) doesn't have that failure mode.
HELP_SECTIONS: list[tuple[str, list[tuple[str, str]]]] = [
    ("Command mode (blue cell border)", [
        ("↑ / k  ·  ↓ / j", "select cell above / below"),
        ("Enter", "edit selected cell"),
        ("Shift+Enter", "run cell, select below"),
        ("Ctrl+Enter / r", "run cell, stay"),
        ("a / b", "insert code cell above / below"),
        ("d d", "delete cell"),
        ("x / c / v", "cut / copy / paste cell below"),
        ("z", "undo cell deletion"),
        ("m / y", "convert to markdown / code"),
        ("0 0", "restart kernel"),
        ("i i", "interrupt kernel"),
        ("Ctrl+S", "save notebook"),
        ("o / O", "collapse output / full-screen output viewer"),
        ("/", "search across all cells"),
        ("e", "ask AI to explain/review selected cell"),
        ("f", "ask AI to fix the selected cell's error"),
        ("w", "walkthrough: run/render cell, AI narrates it, advance to next"),
        ("Ctrl+Y", "apply AI's last code block to selected cell"),
        ("Ctrl+E", "agent step: insert AI's code, run it, report back"),
    ]),
    ("Edit mode (green cell border)", [
        ("Esc", "back to command mode"),
        ("Shift+Enter", "run cell, select below"),
        ("Ctrl+Enter", "run cell, stay"),
    ]),
    ("Global", [
        ("Tab", "cycle focus (notebook ↔ chat)"),
        ("Ctrl+L", "focus AI chat input (un-collapses it)"),
        ("F2", "toggle variable explorer (live kernel variables)"),
        ("F4", "collapse / expand the chat panel"),
        ("F5", "run all cells"),
        ("F6", "autonomous agent: set a goal, it plans+runs cells until done; F6 again stops"),
        ("/auto <goal>", "(typed in chat) same as F6"),
        ("Ctrl+T", "switch AI chat model (installed Ollama models)"),
        ("Ctrl+O", "open another notebook"),
        ("Ctrl+N", "new (blank) notebook"),
        ("F7", "import a .ipynb from anywhere on disk"),
        ("Ctrl+G", "new strategy scaffold (data+signal+costed backtest+report)"),
        ("? / F1", "this help"),
        ("Ctrl+Q", "quit"),
    ]),
]

HELP_NOTES = """\
The AI mentor receives the **entire live notebook** (sources, execution
counts, outputs and errors) with every message — just ask about "cell 3",
no pasting needed. To have it read a file that ISN'T in the notebook (e.g. a
textbook's script), reference it in chat: `review @chapter4_momentum.py` —
resolved against the notebook dir, the project dir, then as given (`~` ok).

**Per-notebook kernels:** every notebook runs in its own kernel. Switching
notebooks (`Ctrl+O`) starts or resumes that notebook's kernel — variables
never leak between research projects, and state is preserved when you switch
back. `00` restarts only the current notebook's kernel.

Autosave writes the notebook 30s after the last change (disable with
`QUANTLAB_AUTOSAVE=0`). Each cell shows its last run time under the output.
Export from the shell: `quantlab foo.ipynb --export html` (also `py`, `md`).
"""


def _join(v: Any) -> str:
    return "".join(v) if isinstance(v, list) else str(v)


def outputs_to_dataframe(outputs: List[Dict[str, Any]]):
    """Extract the first HTML table from a cell's outputs as a DataFrame."""
    import pandas as pd
    for out in outputs or []:
        if out.get("output_type") not in ("execute_result", "display_data"):
            continue
        html = _join((out.get("data") or {}).get("text/html", ""))
        if "<table" not in html:
            continue
        try:
            df = pd.read_html(io.StringIO(html))[0]
            first = df.columns[0]
            if str(first).startswith("Unnamed"):  # pandas index column
                df = df.set_index(first).rename_axis(None)
            return df
        except Exception:
            continue
    return None


def html_table_to_rich(html: str, max_rows: int = 30, max_cols: int = 12):
    """Render a Pandas HTML table as an aligned Rich table (None on failure)."""
    try:
        import pandas as pd
        from rich import box
        from rich.table import Table

        df = pd.read_html(io.StringIO(html))[0]
        clipped_rows = len(df) > max_rows
        clipped_cols = len(df.columns) > max_cols
        df = df.iloc[:max_rows, :max_cols]
        table = Table(box=box.SIMPLE_HEAVY, header_style="bold cyan", padding=(0, 1))
        for col in df.columns:
            name = str(col)
            table.add_column("" if name.startswith("Unnamed") else name,
                             overflow="ellipsis", max_width=32)
        for _, row in df.iterrows():
            table.add_row(*(str(v) for v in row))
        if clipped_rows or clipped_cols:
            table.caption = "clipped — press O for the full table"
        return table
    except Exception:
        return None


# ==========================================================================
# Widgets
# ==========================================================================


class CellTextArea(TextArea):
    """Cell editor that understands Jupyter's run/escape keys."""

    def __init__(self, cell_widget: "CellWidget", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.cell_widget = cell_widget

    async def _on_key(self, event: events.Key) -> None:
        app: QuantLabApp = self.app  # type: ignore[assignment]
        if event.key == "escape":
            event.stop()
            event.prevent_default()
            app.enter_command_mode()
            return
        if event.key == "shift+enter":
            event.stop()
            event.prevent_default()
            await app.run_selected_cell(advance=True)
            return
        if event.key == "ctrl+enter":
            event.stop()
            event.prevent_default()
            await app.run_selected_cell(advance=False)
            return
        await super()._on_key(event)

    def on_focus(self) -> None:
        app: QuantLabApp = self.app  # type: ignore[assignment]
        app.select_cell_widget(self.cell_widget, focus_editor=False)
        app.set_mode("edit")


class CellWidget(Vertical):
    """One notebook cell: gutter + editor (+ rendered markdown) + outputs."""

    def __init__(self, cell: Dict[str, Any]) -> None:
        super().__init__(classes=f"cell {cell['cell_type']}")
        self.cell = cell
        self.running = False
        self.queued = False
        self.duration: Optional[float] = None
        self.output_collapsed = False
        # rendered-plot cache: refresh_outputs re-renders every output (each
        # 0.2s streaming tick, collapse toggles…) — decode/resize/rasterise
        # each figure once per (content, width) instead of every refresh
        self._img_cache: Dict[tuple, Any] = {}

    def compose(self) -> ComposeResult:
        with Horizontal(classes="cell-row"):
            yield Label(self.gutter_text(), classes="gutter")
            editor = CellTextArea(
                self,
                text=self.cell.get("source", ""),
                language="python" if self.cell["cell_type"] == "code" else "markdown",
                classes="editor",
                soft_wrap=True,
                show_line_numbers=self.cell["cell_type"] == "code",
                tab_behavior="indent",
            )
            yield editor
        if self.cell["cell_type"] == "markdown":
            yield Markdown(self.cell.get("source", "") or "*empty markdown cell*",
                           classes="md-view")
        yield Static("", classes="outputs")

    def on_mount(self) -> None:
        if self.cell["cell_type"] == "markdown":
            self.show_markdown_rendered(bool(self.cell.get("source", "").strip()))
        self.refresh_outputs()

    # -- helpers -----------------------------------------------------------

    @property
    def editor(self) -> CellTextArea:
        return self.query_one(CellTextArea)

    def sync_source(self) -> None:
        self.cell["source"] = self.editor.text

    def gutter_text(self) -> str:
        if self.cell["cell_type"] != "code":
            return "  md  "
        if self.running:
            return "In [*]:"
        if self.queued:
            return "In [.]:"
        ec = self.cell.get("execution_count")
        return f"In [{ec if ec is not None else ' '}]:"

    def refresh_gutter(self) -> None:
        try:
            gutter = self.query_one(".gutter", Label)
        except NoMatches:  # cell was removed (e.g. deleted mid-run)
            return
        gutter.update(self.gutter_text())
        gutter.set_class(self.running, "busy")
        gutter.set_class(self.queued and not self.running, "queued")

    def show_markdown_rendered(self, rendered: bool) -> None:
        if self.cell["cell_type"] != "markdown":
            return
        self.query_one(".cell-row").display = not rendered
        md = self.query_one(".md-view", Markdown)
        md.display = rendered
        if rendered:
            md.update(self.cell.get("source", "") or "*empty markdown cell*")

    def refresh_outputs(self) -> None:
        self.refresh_gutter()
        try:
            out_widget = self.query_one(".outputs", Static)
        except NoMatches:  # cell was removed (e.g. deleted mid-run)
            return
        outputs = self.cell.get("outputs") or []
        if self.cell["cell_type"] != "code" or (not outputs and self.duration is None):
            out_widget.update("")
            out_widget.display = False
            return
        out_widget.display = True
        if self.output_collapsed and outputs:
            out_widget.update(Text(f"▸ {len(outputs)} output(s) collapsed — press o to expand",
                                   style="dim italic"))
            return
        parts = [r for o in outputs for r in self._render_output(o)]
        if self.duration is not None:
            parts.append(Text(f"✓ {self.duration:.2f}s", style="dim"))
        out_widget.update(Group(*parts))

    def _render_output(self, out: Dict[str, Any]) -> List[Any]:
        ot = out.get("output_type")
        if ot == "stream":
            text = out.get("text", "")
            text = "".join(text) if isinstance(text, list) else text
            t = Text.from_ansi(text.rstrip("\n"))
            if out.get("name") == "stderr":
                t.stylize("red3")
            return [t]
        if ot in ("execute_result", "display_data"):
            data = out.get("data", {}) or {}
            rendered: List[Any] = []
            if "image/png" in data:
                rendered.append(self._render_image(data["image/png"]))
            elif "text/html" in data and "<table" in _join(data["text/html"]):
                table = html_table_to_rich(_join(data["text/html"]))
                if table is not None:
                    rendered.append(table)
                elif "text/plain" in data:
                    rendered.append(Text.from_ansi(_join(data["text/plain"]).rstrip("\n")))
            elif "text/plain" in data:
                rendered.append(Text.from_ansi(_join(data["text/plain"]).rstrip("\n")))
            return rendered
        if ot == "error":
            tb = out.get("traceback", [])
            tb = "\n".join(tb) if isinstance(tb, list) else str(tb)
            body = Text.from_ansi(tb)
            header = Text(f"✗ {out.get('ename', 'Error')}: {out.get('evalue', '')}"
                          f"   (press f to fix with AI)", style="bold red3")
            return [header, body]
        return []

    def _render_image(self, b64: Any) -> Any:
        """Render a base64 PNG as unicode half-block pixels in the terminal."""
        try:
            from PIL import Image
            from rich_pixels import Pixels

            b64s = "".join(b64) if isinstance(b64, list) else b64
            max_w = max(40, min(self.app.size.width // 2 - 14, 110))
            key = (hash(b64s), len(b64s), max_w)
            cached = self._img_cache.get(key)
            if cached is not None:
                return cached
            raw = base64.b64decode(b64s)
            img = Image.open(io.BytesIO(raw)).convert("RGB")
            ratio = max_w / img.width
            img = img.resize((max_w, max(1, int(img.height * ratio))))
            pixels = Pixels.from_image(img)
            if len(self._img_cache) > 16:
                self._img_cache.clear()
            self._img_cache[key] = pixels
            return pixels
        except Exception:
            return Text("[matplotlib figure — save & open in Jupyter/GUI to view]",
                        style="italic cyan")

    def has_error(self) -> Optional[Dict[str, Any]]:
        for out in self.cell.get("outputs") or []:
            if out.get("output_type") == "error":
                return out
        return None


class NotebookPanel(VerticalScroll, can_focus=True):
    """Scrollable list of cells; owns command-mode key handling."""

    async def _on_key(self, event: events.Key) -> None:
        app: QuantLabApp = self.app  # type: ignore[assignment]
        if await app.handle_command_key(event):
            event.stop()
            event.prevent_default()
            return
        await super()._on_key(event)


class ChatInput(Input):
    """Chat prompt; Esc returns focus to the notebook."""

    async def _on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            event.stop()
            event.prevent_default()
            self.app.query_one(NotebookPanel).focus()  # type: ignore[union-attr]
            return
        await super()._on_key(event)


# ==========================================================================
# Modal screens
# ==========================================================================


class HelpScreen(ModalScreen[None]):
    BINDINGS = [
        Binding("escape", "dismiss", "close"),
        Binding("q", "dismiss", "close"),
        Binding("question_mark", "dismiss", "close"),
    ]

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="help-box"):
            yield Static("QuantLab keyboard shortcuts", classes="help-title")
            for heading, rows in HELP_SECTIONS:
                yield Label(heading, classes="help-heading")
                table: DataTable = DataTable(show_cursor=False, zebra_stripes=True)
                table.add_columns("Key", "Action")
                for key, action in rows:
                    table.add_row(Text(key, style="bold yellow"), action)
                table.styles.height = len(rows) + 1
                yield table
            yield Markdown(HELP_NOTES)

    def on_mount(self) -> None:
        self.query_one("#help-box").focus()


class OpenScreen(ModalScreen[Optional[str]]):
    BINDINGS = [Binding("escape", "dismiss(None)", "cancel")]

    def compose(self) -> ComposeResult:
        with Vertical(id="open-box"):
            yield Label("Open notebook  (Enter to open · Esc to cancel)", classes="modal-title")
            yield OptionList(*(Option(p, id=p) for p in list_notebooks()))

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)


class NewNotebookScreen(ModalScreen[Optional[str]]):
    BINDINGS = [Binding("escape", "dismiss(None)", "cancel")]

    def compose(self) -> ComposeResult:
        with Vertical(id="open-box"):
            yield Label("New notebook name  (Enter to create · Esc to cancel)", classes="modal-title")
            yield Input(placeholder="research.ipynb")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)


class ImportScreen(ModalScreen[Optional[str]]):
    """Pick an external .ipynb (anywhere on disk) to copy into QuantLab."""

    BINDINGS = [Binding("escape", "dismiss(None)", "cancel")]

    def compose(self) -> ComposeResult:
        with Vertical(id="open-box"):
            yield Label("Import notebook — path to a .ipynb file anywhere on disk "
                        "(Enter to import · Esc to cancel)", classes="modal-title")
            yield Input(placeholder="~/Downloads/some_analysis.ipynb")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)


class OutputViewerScreen(ModalScreen[None]):
    """Full-screen scrollable view of a cell's output (DataFrame or text)."""

    BINDINGS = [
        Binding("escape", "dismiss", "close"),
        Binding("q", "dismiss", "close"),
    ]

    def __init__(self, cell: Dict[str, Any], index: int) -> None:
        super().__init__()
        self.cell = cell
        self.index = index

    def compose(self) -> ComposeResult:
        with Vertical(id="viewer-box"):
            yield Label(f"Cell {self.index + 1} output  (Esc to close)", classes="modal-title")
            df = outputs_to_dataframe(self.cell.get("outputs") or [])
            if df is not None:
                table = DataTable(cursor_type="row", zebra_stripes=True, id="viewer-table")
                table.add_column("#")
                for col in df.columns:
                    table.add_column(str(col))
                for idx, row in df.iterrows():
                    table.add_row(Text(str(idx), style="bold cyan"),
                                  *(str(v) for v in row))
                yield table
            else:
                text_parts = []
                for out in self.cell.get("outputs") or []:
                    ot = out.get("output_type")
                    if ot == "stream":
                        text_parts.append(_join(out.get("text", "")))
                    elif ot in ("execute_result", "display_data"):
                        text_parts.append(_join((out.get("data") or {}).get("text/plain", "")))
                    elif ot == "error":
                        text_parts.append(_join(out.get("traceback", [])) if not
                                          isinstance(out.get("traceback"), list)
                                          else "\n".join(out.get("traceback", [])))
                with VerticalScroll():
                    yield Static(Text.from_ansi("\n".join(p for p in text_parts if p)
                                                or "(no output)"))

    def on_mount(self) -> None:
        try:
            self.query_one("#viewer-table", DataTable).focus()
        except Exception:
            self.query_one(VerticalScroll).focus()


class SearchScreen(ModalScreen[Optional[int]]):
    """Live search across all cell sources; returns the chosen cell index."""

    BINDINGS = [Binding("escape", "dismiss(None)", "cancel")]

    def __init__(self, cells: List[Dict[str, Any]]) -> None:
        super().__init__()
        self.cells = cells

    def compose(self) -> ComposeResult:
        with Vertical(id="search-box"):
            yield Label("Search cells  (Enter to jump · Esc to cancel)", classes="modal-title")
            yield Input(placeholder="query…", id="search-input")
            yield OptionList(id="search-results")

    def on_mount(self) -> None:
        self.query_one("#search-input", Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        results = self.query_one("#search-results", OptionList)
        results.clear_options()
        query = event.value.strip().lower()
        if not query:
            return
        for i, cell in enumerate(self.cells):
            source = cell.get("source", "")
            if query not in source.lower():
                continue
            line = next((ln.strip() for ln in source.splitlines()
                         if query in ln.lower()), "")[:70]
            results.add_option(Option(
                f"cell {i + 1} [{cell.get('cell_type', 'code')}]  {line}", id=str(i)))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        results = self.query_one("#search-results", OptionList)
        if results.option_count:
            self.dismiss(int(results.get_option_at_index(0).id))

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(int(event.option.id))


class AgentGoalScreen(ModalScreen[Optional[str]]):
    BINDINGS = [Binding("escape", "dismiss(None)", "cancel")]

    def compose(self) -> ComposeResult:
        with Vertical(id="open-box"):
            yield Label("Autonomous agent goal  (Enter to start · F6 stops it · Esc cancels)",
                        classes="modal-title")
            yield Input(placeholder="e.g. test 3/6/12-month momentum on the DAX with 5bps costs")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)


class ModelPickerScreen(ModalScreen[Optional[str]]):
    """Pick an Ollama chat model from the locally installed ones."""

    BINDINGS = [Binding("escape", "dismiss(None)", "cancel")]

    def __init__(self, models: List[str], current: str) -> None:
        super().__init__()
        self.models = models
        self.current = current

    def compose(self) -> ComposeResult:
        with Vertical(id="open-box"):
            yield Label("AI chat model  (Enter to select · Esc to cancel)", classes="modal-title")
            yield OptionList(*(Option(f"{'● ' if m == self.current else '  '}{m}", id=m)
                               for m in self.models))

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)


class ConfirmQuitScreen(ModalScreen[str]):
    """Shown when an action (quit, switch notebook…) would discard unsaved
    changes."""

    BINDINGS = [
        Binding("s", "dismiss('save')", "save & continue"),
        Binding("q", "dismiss('quit')", "discard changes"),
        Binding("escape", "dismiss('cancel')", "cancel"),
    ]

    def __init__(self, verb: str = "quit") -> None:
        super().__init__()
        self.verb = verb

    def compose(self) -> ComposeResult:
        with Vertical(id="open-box"):
            yield Label(f"Unsaved changes — [b]s[/b]ave & {self.verb} · "
                        f"[b]q[/b] {self.verb} without saving · "
                        "[b]Esc[/b] cancel", classes="modal-title")


# ==========================================================================
# The app
# ==========================================================================


class QuantLabApp(App[None]):
    TITLE = "QuantLab"

    CSS = """
    Screen { layout: vertical; background: $surface; }
    #topbar { height: 1; background: $panel; color: $text; padding: 0 1; }
    #main { height: 1fr; }

    #left-side { width: 3fr; }
    NotebookPanel { height: 1fr; padding: 0 1; }
    #chat-panel { width: 2fr; border-left: solid $panel-lighten-2; }

    #vars-panel { height: 14; display: none; border-top: solid $panel-lighten-2; }
    .vars-title { background: $panel; color: $text-muted; padding: 0 1; width: 100%; }
    #vars-table { height: 1fr; }

    .cell { height: auto; margin: 1 0 0 0; border: round $panel-lighten-2; }
    .cell.selected { border: round $primary; }
    .cell.selected.editing { border: round $success; }
    .cell-row { height: auto; }
    .gutter { width: 8; color: $success; text-align: right; padding-top: 0; }
    .gutter.busy { color: $warning; }
    .gutter.queued { color: $accent; }
    .editor { height: auto; max-height: 40; border: none; background: $surface-lighten-1; }
    .editor:focus { border: none; }
    .md-view { padding: 0 1 0 8; height: auto; }
    .outputs { padding: 0 1 0 8; height: auto; max-height: 60; overflow-y: auto; }

    #chat-log { height: 1fr; padding: 0 1; }
    .chat-user { margin: 1 0 0 4; padding: 0 1; background: $primary 20%; height: auto; }
    .chat-ai { margin: 1 4 0 0; padding: 0 1; background: $surface-lighten-1; height: auto; }
    #chat-input { dock: bottom; }

    #help-box { width: 88; height: 80%; background: $surface; border: round $primary; padding: 1 2; }
    .help-title { text-style: bold; color: $primary; width: 100%; content-align: center middle;
                  margin-bottom: 1; }
    .help-heading { text-style: bold underline; color: $primary; margin: 1 0; }
    #open-box { width: 60; height: auto; max-height: 60%; background: $surface;
                border: round $primary; padding: 1 2; }
    #viewer-box { width: 90%; height: 85%; background: $surface;
                  border: round $primary; padding: 1 2; }
    #viewer-table { height: 1fr; }
    #search-box { width: 76; height: 60%; background: $surface;
                  border: round $primary; padding: 1 2; }
    #search-results { height: 1fr; margin-top: 1; }
    .modal-title { margin-bottom: 1; }
    HelpScreen, OpenScreen, NewNotebookScreen, ImportScreen, ConfirmQuitScreen,
    ModelPickerScreen, OutputViewerScreen, SearchScreen {
        align: center middle; background: black 60%;
    }
    """

    BINDINGS = [
        Binding("ctrl+q", "request_quit", "quit"),
        Binding("ctrl+s", "save", "save"),
        Binding("f2", "toggle_vars", "vars"),
        Binding("f4", "toggle_chat", "chat panel", show=False),
        Binding("f5", "run_all", "run all"),
        Binding("f6", "toggle_agent", "agent"),
        Binding("ctrl+l", "focus_chat", "chat"),
        Binding("ctrl+t", "pick_model", "model"),
        Binding("ctrl+o", "open_notebook", "open"),
        Binding("ctrl+n", "new_notebook", "new"),
        Binding("f7", "import_notebook", "import"),
        Binding("ctrl+g", "new_strategy", "scaffold", show=False),
        Binding("f1", "help", "help"),
        Binding("ctrl+y", "apply_ai_code", "apply AI code", show=False),
        Binding("ctrl+e", "run_ai_suggestion", "run AI code", show=False),
    ]

    def __init__(self, notebook: Optional[str] = None) -> None:
        super().__init__()
        self.nb_path: Optional[str] = notebook
        self.cells: List[CellWidget] = []
        self.selected: int = 0
        self.mode: str = "command"
        self.dirty = False
        self.chat_history: List[Dict[str, str]] = []
        self.chat_busy = False
        self.cell_clipboard: Optional[Dict[str, Any]] = None
        self.undo_stack: List[tuple[int, Dict[str, Any]]] = []
        self._pending_key: tuple[str, float] = ("", 0.0)
        self._run_queue_active = False
        self.model = OLLAMA_MODEL
        self.autosave_enabled = os.environ.get("QUANTLAB_AUTOSAVE", "1") != "0"
        self._last_autosave = 0.0
        self.agent_active = False
        self.agent_stop = False
        self._loading_notebook = False

    # -- layout ------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Static("", id="topbar")
        with Horizontal(id="main"):
            with Vertical(id="left-side"):
                yield NotebookPanel(id="nb-panel")
                with Vertical(id="vars-panel"):
                    yield Label("Kernel variables — refreshes after each run (F2 to hide)",
                                classes="vars-title")
                    yield DataTable(id="vars-table", cursor_type="row", zebra_stripes=True)
            with Vertical(id="chat-panel"):
                yield VerticalScroll(id="chat-log")
                yield ChatInput(placeholder=f"Ask the mentor ({self.model}) — reads your whole "
                                            "notebook, or @file.py to read a file…",
                                id="chat-input")
        yield Footer()

    async def on_mount(self) -> None:
        self.update_topbar("starting kernel…")
        table = self.query_one("#vars-table", DataTable)
        table.add_columns("name", "type", "shape/len", "value")
        notebooks = list_notebooks()
        if self.nb_path is None:
            self.nb_path = notebooks[0] if notebooks else create_notebook("untitled.ipynb")
        await self.load_notebook(self.nb_path)
        self.query_one(NotebookPanel).focus()
        self.start_kernel_worker()
        if self.autosave_enabled:
            self.set_interval(10.0, self._autosave_tick)

    # -- kernel ------------------------------------------------------------

    @work(thread=True, exclusive=True, group="kernel")
    def start_kernel_worker(self) -> None:
        try:
            if not kernel_service.is_alive():
                kernel_service.start(self.nb_path)
            self.call_from_thread(self.update_topbar)
            self.call_from_thread(self.notify, f"Kernel '{KERNEL_NAME}' ready", timeout=3)
        except Exception as exc:  # noqa: BLE001
            self.call_from_thread(self.update_topbar, f"kernel FAILED: {exc}")

    @work(thread=True, exclusive=True, group="kernel")
    def switch_kernel_worker(self, session: str) -> None:
        """Each notebook gets its own kernel — no state leaks between projects."""
        try:
            self.call_from_thread(self.update_topbar, "switching kernel…")
            started = kernel_service.switch(session)
            msg = ("fresh kernel started for this notebook" if started
                   else "kernel state restored for this notebook")
            self.call_from_thread(self.notify, msg, timeout=3)
        except Exception as exc:  # noqa: BLE001
            self.call_from_thread(self.notify, f"Kernel switch failed: {exc}",
                                  severity="error")
        self.call_from_thread(self.update_topbar)
        self.call_from_thread(self._refresh_vars_if_visible)

    def _refresh_vars_if_visible(self) -> None:
        if self.query_one("#vars-panel").display:
            self.refresh_variables()

    def update_topbar(self, status: Optional[str] = None) -> None:
        try:
            topbar = self.query_one("#topbar", Static)
        except NoMatches:  # app tearing down while a worker finishes
            return
        alive = kernel_service.is_alive()
        dot = "[green]●[/]" if alive else "[red]●[/]"
        state = status or ("kernel alive" if alive else "kernel dead — 00 to restart")
        dirty = "[yellow] ●unsaved[/]" if self.dirty else ""
        auto = "" if self.autosave_enabled else " autosave:off"
        pos = f"cell {self.selected + 1}/{len(self.cells)}" if self.cells else ""
        nk = kernel_service.session_count
        kernels = f" ({nk} nb kernels)" if nk > 1 else ""
        topbar.update(
            f"[b][cyan]Quant[/][green]Lab[/][/b]  {self.nb_path}{dirty}  {pos}   "
            f"{dot} {state}{kernels}   [dim]model:{self.model}{auto} ^t switch  ? help[/]")

    def set_dirty(self, value: bool = True) -> None:
        if value:
            self._dirty_since = time.monotonic()
        if value != self.dirty:
            self.dirty = value
            self.update_topbar()

    def _autosave_tick(self) -> None:
        """Debounced autosave: write 30s after the most recent change."""
        if (self.dirty and self.nb_path
                and time.monotonic() - getattr(self, "_dirty_since", 0) >= 30):
            self.action_save(quiet=True)

    # -- notebook load / save ----------------------------------------------

    async def load_notebook(self, rel_path: str) -> None:
        self._loading_notebook = True
        try:
            data = load_notebook_file(rel_path)
            switching = self.nb_path is not None and rel_path != self.nb_path
            self.nb_path = rel_path
            if switching and kernel_service.kernel_id is not None:
                self.switch_kernel_worker(rel_path)
            panel = self.query_one(NotebookPanel)
            await panel.remove_children()
            self.cells = []
            raw_cells = data["cells"] or [{"cell_type": "code", "source": "", "outputs": [],
                                           "execution_count": None}]
            for raw in raw_cells:
                raw.setdefault("outputs", [])
                raw.setdefault("execution_count", None)
                self.cells.append(CellWidget(raw))
            # one batched mount = one layout pass; per-cell awaits made a
            # 60-cell notebook take ~3.6s to open
            await panel.mount(*self.cells)
            self.selected = 0
            self.mode = "command"
            self.refresh_selection()
            self.chat_history = list(data.get("chat") or [])
            await self.replay_chat()
        finally:
            self._loading_notebook = False
        self.set_dirty(False)
        self.update_topbar()

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        """Typing in any cell editor marks the notebook dirty — without this,
        edits that are never run would quit without the save prompt and never
        trigger autosave (silent data loss)."""
        if not self._loading_notebook:
            self.set_dirty()

    def snapshot_cells(self) -> List[Dict[str, Any]]:
        for w in self.cells:
            w.sync_source()
        return [dict(w.cell) for w in self.cells]

    def action_save(self, quiet: bool = False) -> bool:
        if not self.nb_path:
            return False
        try:
            n = save_notebook_file(self.nb_path, self.snapshot_cells(), self.chat_history)
        except Exception as exc:  # noqa: BLE001
            self.notify(f"Save failed: {exc}", severity="error")
            return False
        self.set_dirty(False)
        if quiet:
            self.notify(f"Autosaved {self.nb_path}", timeout=1.5, severity="information")
        else:
            self.notify(f"Saved {self.nb_path} ({n} cells)", timeout=3)
        return True

    # -- selection / modes ---------------------------------------------------

    def refresh_selection(self) -> None:
        for i, w in enumerate(self.cells):
            w.set_class(i == self.selected, "selected")
            w.set_class(i == self.selected and self.mode == "edit", "editing")

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self.refresh_selection()

    def select_cell(self, index: int, focus_editor: bool = False) -> None:
        if not self.cells:
            return
        self.selected = max(0, min(index, len(self.cells) - 1))
        self.refresh_selection()
        self.update_topbar()
        widget = self.cells[self.selected]
        widget.scroll_visible()
        if focus_editor:
            if widget.cell["cell_type"] == "markdown":
                widget.show_markdown_rendered(False)
            widget.editor.focus()
            self.set_mode("edit")

    def select_cell_widget(self, widget: CellWidget, focus_editor: bool = False) -> None:
        if widget in self.cells:
            self.select_cell(self.cells.index(widget), focus_editor)

    def enter_command_mode(self) -> None:
        widget = self.current_cell()
        if widget and widget.cell["cell_type"] == "markdown":
            widget.sync_source()
            widget.show_markdown_rendered(True)
            self.set_dirty()
        self.set_mode("command")
        self.query_one(NotebookPanel).focus()

    def current_cell(self) -> Optional[CellWidget]:
        if 0 <= self.selected < len(self.cells):
            return self.cells[self.selected]
        return None

    # -- command mode keys ---------------------------------------------------

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
            await self.run_selected_cell(advance=True)
        elif key in ("ctrl+enter", "r"):
            await self.run_selected_cell(advance=False)
        elif key == "a":
            await self.insert_cell(self.selected, "code")
        elif key == "b":
            await self.insert_cell(self.selected + 1, "code")
        elif chord == "dd":
            await self.delete_cell(self.selected)
            self._pending_key = ("", 0.0)
        elif key == "x":
            w = self.current_cell()
            if w:
                w.sync_source()  # capture unsaved editor buffer before cutting
                self.cell_clipboard = dict(w.cell)
                await self.delete_cell(self.selected)
        elif key == "c":
            w = self.current_cell()
            if w:
                w.sync_source()
                self.cell_clipboard = dict(w.cell)
                self.notify("Cell copied", timeout=2)
        elif key == "v":
            if self.cell_clipboard:
                await self.insert_cell(self.selected + 1, raw=dict(self.cell_clipboard))
        elif key == "z":
            await self.undo_delete()
        elif key == "m":
            await self.convert_cell("markdown")
        elif key == "y":
            await self.convert_cell("code")
        elif chord == "00":
            self.restart_kernel_worker()
            self._pending_key = ("", 0.0)
        elif chord == "ii":
            kernel_service.interrupt()
            self.notify("Kernel interrupted", timeout=2)
            self._pending_key = ("", 0.0)
        elif key == "e":
            await self.ask_ai_about_cell()
        elif key == "f":
            await self.fix_cell_with_ai()
        elif key == "o":
            w = self.current_cell()
            if w and w.cell["cell_type"] == "code":
                w.output_collapsed = not w.output_collapsed
                w.refresh_outputs()
        elif key == "O":
            w = self.current_cell()
            if w and w.cell.get("outputs"):
                self.push_screen(OutputViewerScreen(w.cell, self.selected))
            else:
                self.notify("Selected cell has no outputs", timeout=2)
        elif key == "slash":
            self.action_search()
        elif key == "question_mark":
            self.action_help()
        elif key == "w":
            await self.walk_next_cell()
        else:
            return False
        return True

    # -- cell CRUD -----------------------------------------------------------

    async def insert_cell(self, index: int, cell_type: str = "code",
                          raw: Optional[Dict[str, Any]] = None) -> None:
        cell = raw or {"cell_type": cell_type, "source": "", "outputs": [],
                       "execution_count": None}
        index = max(0, min(index, len(self.cells)))
        widget = CellWidget(cell)
        panel = self.query_one(NotebookPanel)
        if index < len(self.cells):
            await panel.mount(widget, before=self.cells[index])
        else:
            await panel.mount(widget)
        self.cells.insert(index, widget)
        self.select_cell(index)
        self.set_dirty()

    async def delete_cell(self, index: int) -> None:
        if not (0 <= index < len(self.cells)):
            return
        widget = self.cells.pop(index)
        widget.sync_source()
        self.undo_stack.append((index, dict(widget.cell)))
        if len(self.undo_stack) > 50:
            self.undo_stack.pop(0)
        await widget.remove()
        if not self.cells:
            await self.insert_cell(0, "code")
        self.select_cell(min(index, len(self.cells) - 1))
        self.set_dirty()

    async def undo_delete(self) -> None:
        if not self.undo_stack:
            self.notify("Nothing to undo", timeout=2)
            return
        index, cell = self.undo_stack.pop()
        await self.insert_cell(min(index, len(self.cells)), raw=cell)

    async def convert_cell(self, to_type: str) -> None:
        w = self.current_cell()
        if not w or w.cell["cell_type"] == to_type:
            return
        w.sync_source()
        raw = {"cell_type": to_type, "source": w.cell["source"], "outputs": [],
               "execution_count": None}
        index = self.selected
        self.cells.pop(index)
        await w.remove()
        await self.insert_cell(index, raw=raw)

    # -- execution -----------------------------------------------------------

    async def run_selected_cell(self, advance: bool) -> None:
        w = self.current_cell()
        if w is None:
            return
        index = self.selected
        if w.cell["cell_type"] == "markdown":
            w.sync_source()
            w.show_markdown_rendered(True)
            self.enter_command_mode()
        else:
            await self.execute_cell_widget(w)
        if advance:
            # cells may have been inserted/deleted while the run awaited —
            # re-derive the position from the widget, not the saved index
            if w in self.cells:
                index = self.cells.index(w)
            if index + 1 >= len(self.cells):
                await self.insert_cell(len(self.cells), "code")
            self.enter_command_mode()
            self.select_cell(index + 1)

    async def execute_cell_widget(self, w: CellWidget) -> None:
        if w.running:
            return
        w.sync_source()
        w.running = True
        w.output_collapsed = False
        w.cell["outputs"] = []
        w.refresh_outputs()
        self.update_topbar("running…")
        t0 = time.monotonic()

        # Stream partial outputs into the cell while it runs (throttled) —
        # the callback fires on the kernel thread, so hop to the UI thread.
        last_stream = [0.0]

        def on_output(outputs: List[Dict[str, Any]]) -> None:
            now = time.monotonic()
            if now - last_stream[0] < 0.2:
                return
            last_stream[0] = now
            def apply() -> None:
                if w.running:  # final refresh happens in `finally` below
                    w.cell["outputs"] = outputs
                    w.refresh_outputs()
            try:
                self.call_from_thread(apply)
            except Exception:
                pass  # app may be shutting down

        try:
            result = await asyncio.to_thread(kernel_service.execute,
                                             w.cell["source"], False, on_output)
            w.cell["execution_count"] = result.get("execution_count")
            w.cell["outputs"] = result.get("outputs", [])
        except Exception as exc:  # noqa: BLE001
            w.cell["outputs"] = [{"output_type": "error", "ename": "KernelError",
                                  "evalue": str(exc), "traceback": [str(exc)]}]
        finally:
            w.running = False
            w.duration = time.monotonic() - t0
            if w.is_mounted:  # cell may have been deleted / notebook switched mid-run
                w.refresh_outputs()
            self.set_dirty()
            self.update_topbar()
            self._refresh_vars_if_visible()

    async def action_run_all(self) -> None:
        if self._run_queue_active:
            return
        self._run_queue_active = True
        try:
            code_cells = [w for w in self.cells if w.cell["cell_type"] == "code"]
            for w in code_cells:
                w.queued = True
                w.refresh_gutter()
            for w in code_cells:
                w.queued = False
                if w in self.cells:  # may have been deleted mid-run
                    await self.execute_cell_widget(w)
            self.notify("Run All complete", timeout=3)
        finally:
            self._run_queue_active = False

    @work(thread=True, exclusive=True, group="kernel")
    def restart_kernel_worker(self) -> None:
        self.call_from_thread(self.update_topbar, "restarting kernel…")
        try:
            if kernel_service.kernel_id is None:
                kernel_service.start()
            else:
                kernel_service.restart()
            self.call_from_thread(self.notify, "Kernel restarted — state cleared", timeout=3)
        except Exception as exc:  # noqa: BLE001
            self.call_from_thread(self.notify, f"Restart failed: {exc}", severity="error")
        self.call_from_thread(self.update_topbar)
        self.call_from_thread(self._refresh_vars_if_visible)

    # -- AI chat ---------------------------------------------------------------

    async def replay_chat(self) -> None:
        log = self.query_one("#chat-log", VerticalScroll)
        await log.remove_children()
        if not self.chat_history:
            await log.mount(Markdown(
                f"**Quant Mentor** ready (`{self.model}`). I can read your whole "
                "notebook — ask about any cell, press `e` on a cell for a review, "
                "`f` on a failed cell to debug it, or `w` to walk through the "
                "notebook cell by cell with me narrating as you go.", classes="chat-ai"))
            return
        await log.mount(*(
            Markdown(msg["content"],
                     classes="chat-user" if msg["role"] == "user" else "chat-ai")
            for msg in self.chat_history))
        log.scroll_end(animate=False)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "chat-input" and event.value.strip():
            if self.chat_busy:
                # keep the typed prompt in the box instead of losing it
                self.notify("The mentor is still responding — press Enter again "
                            "when it finishes", severity="warning", timeout=3)
                return
            prompt = event.value.strip()
            event.input.value = ""
            if prompt.startswith("/auto"):
                goal = prompt[len("/auto"):].strip()
                if goal:
                    self.agent_loop(goal)
                else:
                    self.notify("Usage: /auto <research goal>", timeout=4)
                return
            self.send_chat(prompt)

    async def ask_ai_about_cell(self) -> None:
        w = self.current_cell()
        if not w:
            return
        w.sync_source()
        idx = self.selected + 1
        self.send_chat(f"Review cell {idx} for me: explain what it does, teach me the "
                       f"underlying quant concepts, and point out any bugs or bad practice.")

    async def fix_cell_with_ai(self) -> None:
        w = self.current_cell()
        err = w.has_error() if w else None
        if not err:
            self.notify("Selected cell has no error output", timeout=3)
            return
        idx = self.selected + 1
        self.send_chat(f"Cell {idx} failed with `{err.get('ename')}: {err.get('evalue')}`. "
                       f"Diagnose the root cause and give me a corrected drop-in replacement "
                       f"for the whole cell.")

    async def walk_next_cell(self) -> None:
        """Guided walkthrough (`w`): run/render the selected cell, have the AI
        mentor briefly narrate what it does, then land on the next cell —
        one keypress per step, so you set the pace (great for stepping
        through an imported/unfamiliar notebook cell by cell)."""
        w = self.current_cell()
        if w is None:
            return
        if self.chat_busy:
            self.notify("Wait for the mentor to finish narrating", timeout=2)
            return
        idx = self.selected
        if w.cell["cell_type"] == "code":
            await self.execute_cell_widget(w)
            digest = self._output_digest(w.cell)
            prompt = (
                f"Walkthrough step: briefly explain what cell {idx + 1} does and "
                f"what its output shows — 2-4 plain sentences, no code block "
                f"unless something's actually broken. Output:\n```\n{digest}\n```")
        else:
            w.sync_source()
            w.show_markdown_rendered(True)
            self.set_dirty()
            prompt = (f"Walkthrough step: summarize this markdown cell {idx + 1} "
                     f"in your own words, 1-2 sentences.")

        self.chat_busy = True
        try:
            await self._send_chat_impl(prompt)
        finally:
            self.chat_busy = False

        next_idx = idx + 1
        if next_idx >= len(self.cells):
            await self.insert_cell(len(self.cells), "code")
        self.enter_command_mode()
        self.select_cell(next_idx)
        self.notify("w → continue walkthrough", timeout=2)

    @work(group="chat")
    async def send_chat(self, prompt: str) -> None:
        # NOT an exclusive worker: exclusive=True would cancel an in-flight
        # stream when a second message arrives, leaving chat_busy stuck True
        # (the reset would never run) and killing the chat for the session.
        # Instead we refuse re-entry up front and always reset in `finally`.
        if self.chat_busy:
            self.notify("The mentor is still responding — wait for it to finish",
                        severity="warning", timeout=3)
            return
        self.chat_busy = True
        try:
            await self._send_chat_impl(prompt)
        finally:
            self.chat_busy = False

    async def _send_chat_impl(self, prompt: str) -> str:
        log = self.query_one("#chat-log", VerticalScroll)
        prompt = expand_file_references(prompt)  # @path/to/file.py -> its contents
        self.chat_history.append({"role": "user", "content": prompt})
        await log.mount(Markdown(prompt, classes="chat-user"))
        ai_md = Markdown("_…thinking…_", classes="chat-ai")
        await log.mount(ai_md)
        log.scroll_end(animate=False)

        # the model gets the full live notebook with every request
        messages = build_chat_messages(self.chat_history, self.snapshot_cells())
        payload = {"model": self.model, "stream": True,
                   "options": {"temperature": OLLAMA_TEMPERATURE}, "messages": messages}
        acc, last_update = "", 0.0
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(None, connect=5.0)) as client:
                async with client.stream("POST", f"{OLLAMA_URL}/api/chat", json=payload) as resp:
                    if resp.status_code != 200:
                        body = (await resp.aread()).decode(errors="replace")
                        acc = f"⚠️ **Ollama HTTP {resp.status_code}**: {body[:400]}"
                    else:
                        async for line in resp.aiter_lines():
                            if not line.strip():
                                continue
                            try:
                                obj = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            acc += obj.get("message", {}).get("content", "")
                            if obj.get("error"):
                                acc += f"\n\n⚠️ **{obj['error']}**"
                            now = time.monotonic()
                            if now - last_update > 0.15:
                                ai_md.update(acc)
                                log.scroll_end(animate=False)
                                last_update = now
        except httpx.ConnectError:
            acc = (f"⚠️ **Cannot reach Ollama at {OLLAMA_URL}.** Start it with "
                   f"`ollama serve` and pull the model: `ollama pull {self.model}`.")
        except Exception as exc:  # noqa: BLE001
            acc += f"\n\n⚠️ **Chat error: {exc}**"

        acc = acc or "_(empty response)_"
        if CODE_BLOCK_RE.search(acc):
            acc += ("\n\n─ `Ctrl+E` inserts+runs this code and reports back · "
                    "`Ctrl+Y` applies it to the selected cell ─")
        ai_md.update(acc)
        log.scroll_end(animate=False)
        self.chat_history.append({"role": "assistant", "content": acc})
        if len(self.chat_history) > 24:
            self.chat_history = self.chat_history[-24:]
        self.set_dirty()  # chat transcript persists into the .ipynb on save
        return acc

    def _last_ai_code_block(self) -> Optional[str]:
        for msg in reversed(self.chat_history):
            if msg["role"] != "assistant":
                continue
            blocks = CODE_BLOCK_RE.findall(msg["content"])
            if blocks:
                return blocks[-1].rstrip("\n")
        return None

    async def action_run_ai_suggestion(self) -> None:
        """Agentic step: insert the AI's last code block below the selected
        cell, execute it, and report the result back to the mentor so it can
        iterate. One keypress per loop turn; you stay in control."""
        if self.chat_busy:
            self.notify("Wait for the mentor to finish responding", timeout=2)
            return
        code = self._last_ai_code_block()
        if code is None:
            self.notify("No AI code block to run yet", timeout=3)
            return
        index, w = await self._insert_and_run_ai_cell(code)
        digest = self._output_digest(w.cell)
        status = "failed" if w.has_error() else "succeeded"
        self.send_chat(
            f"I inserted your suggested code as cell {index + 1} and ran it — "
            f"it {status}. Output:\n```\n{digest}\n```\n"
            f"Continue: if it failed, give me a corrected cell; if it succeeded, "
            f"tell me what you see and propose the next step as a python code block.")

    async def _insert_and_run_ai_cell(self, code: str) -> tuple[int, CellWidget]:
        index = min(self.selected + 1, len(self.cells))
        await self.insert_cell(index, raw={"cell_type": "code", "source": code,
                                           "outputs": [], "execution_count": None})
        w = self.cells[index]
        w.cell["metadata_ai"] = True
        await self.execute_cell_widget(w)
        return index, w

    def _output_digest(self, cell: Dict[str, Any], limit: int = 1200) -> str:
        digest_lines: List[str] = []
        for out in cell.get("outputs") or []:
            ot = out.get("output_type")
            if ot == "stream":
                digest_lines.append(_join(out.get("text", "")))
            elif ot in ("execute_result", "display_data"):
                data = out.get("data") or {}
                if "text/plain" in data:
                    digest_lines.append(_join(data["text/plain"]))
                if "image/png" in data:
                    digest_lines.append("<matplotlib figure rendered>")
            elif ot == "error":
                tb = out.get("traceback", [])
                tb = "\n".join(tb) if isinstance(tb, list) else str(tb)
                digest_lines.append(f"ERROR {out.get('ename')}: {out.get('evalue')}\n"
                                    + strip_ansi(tb))
        digest = "\n".join(digest_lines).strip() or "(no output)"
        if len(digest) > limit:
            digest = digest[:limit // 2] + "\n…\n" + digest[-limit // 2:]
        return digest

    # -- autonomous agent mode (/auto <goal> or F6) -----------------------------

    AGENT_MAX_STEPS = 8

    @work(group="chat")
    async def agent_loop(self, goal: str) -> None:
        """Multi-step research agent: plan -> write cell -> run -> read result
        -> continue, until DONE, error-stall, F6 stop, or the step cap."""
        if self.chat_busy:
            self.notify("The mentor is busy — wait for it to finish", severity="warning")
            return
        self.chat_busy = True
        self.agent_active = True
        self.agent_stop = False
        self._recent_agent_code: List[str] = []
        steps_run = 0
        try:
            prompt = (
                f"AUTONOMOUS RESEARCH MODE. Goal: {goal}\n\n"
                "Work one step at a time. Every reply must be EXACTLY: one short "
                "sentence of intent, then ONE python code block for the next cell. "
                "When the goal is fully achieved, reply with the single word DONE "
                "on the first line plus a <=3 bullet summary — and no code block.")
            for step in range(1, self.AGENT_MAX_STEPS + 1):
                self.update_topbar(f"agent step {step}/{self.AGENT_MAX_STEPS} — F6 stops")
                reply = await self._send_chat_impl(prompt)
                if self.agent_stop:
                    self.notify("Agent stopped", timeout=3)
                    return
                blocks = CODE_BLOCK_RE.findall(reply)
                # small local models often emit a final code block AND "DONE"
                # in the same reply — honour DONE anywhere at a line start
                done = re.search(r"^\s*DONE\b", reply, re.M) is not None
                if blocks and not (done and steps_run > 0 and
                                   blocks[-1].strip() in self._recent_agent_code):
                    code = blocks[-1].rstrip("\n")
                    if code.strip() in self._recent_agent_code:
                        done = True  # repeating itself — treat as finished
                    else:
                        self._recent_agent_code.append(code.strip())
                        _, w = await self._insert_and_run_ai_cell(code)
                        steps_run += 1
                        if self.agent_stop:
                            self.notify("Agent stopped", timeout=3)
                            return
                        if not done:
                            digest = self._output_digest(w.cell)
                            status = "failed" if w.has_error() else "succeeded"
                            prompt = (f"Step {step} {status}. Output:\n```\n{digest}\n```\n"
                                      f"Continue toward the goal — next reply: one sentence "
                                      f"+ one code block, or DONE + summary if achieved.")
                if done or not blocks:
                    self.notify(f"Agent finished after {steps_run} step(s)", timeout=4)
                    return
            self.notify(f"Agent hit the {self.AGENT_MAX_STEPS}-step cap", timeout=4)
        finally:
            self.agent_active = False
            self.chat_busy = False
            self.update_topbar()

    def action_toggle_agent(self) -> None:
        if getattr(self, "agent_active", False):
            self.agent_stop = True
            self.notify("Stopping agent after the current step…", timeout=3)
            return

        def _go(goal: Optional[str]) -> None:
            if goal:
                self.agent_loop(goal)
        self.push_screen(AgentGoalScreen(), _go)

    def action_toggle_chat(self) -> None:
        panel = self.query_one("#chat-panel")
        panel.display = not panel.display
        if panel.display:
            self.query_one("#chat-input", ChatInput).focus()
        else:
            self.query_one(NotebookPanel).focus()

    def action_new_strategy(self) -> None:
        def _create(name: Optional[str]) -> None:
            if not name:
                return
            try:
                from core import create_strategy_notebook
                path = create_strategy_notebook(name)
            except Exception as exc:  # noqa: BLE001
                self.notify(f"Scaffold failed: {exc}", severity="error")
                return
            self.call_later(self.load_notebook, path)
            self.notify(f"Strategy scaffold created: {path}", timeout=3)
        self._confirm_discard(
            "switch", lambda: self.push_screen(NewNotebookScreen(), _create))

    def action_search(self) -> None:
        for w in self.cells:
            w.sync_source()

        def _jump(index: Optional[int]) -> None:
            if index is not None:
                self.select_cell(index)
                self.query_one(NotebookPanel).focus()
        self.push_screen(SearchScreen([w.cell for w in self.cells]), _jump)

    def action_apply_ai_code(self) -> None:
        code = self._last_ai_code_block()
        if code is None:
            self.notify("No AI code block to apply yet", timeout=3)
            return
        w = self.current_cell()
        if w is None:
            return
        w.editor.text = code
        w.sync_source()
        self.set_dirty()
        self.notify(f"Applied AI code block to cell {self.selected + 1}", timeout=3)

    # -- global actions ----------------------------------------------------------

    def action_focus_chat(self) -> None:
        panel = self.query_one("#chat-panel")
        if not panel.display:
            panel.display = True  # Ctrl+L un-collapses the chat if hidden
        self.query_one("#chat-input", ChatInput).focus()

    # -- variable explorer -------------------------------------------------------

    def action_toggle_vars(self) -> None:
        panel = self.query_one("#vars-panel")
        panel.display = not panel.display
        if panel.display:
            self.refresh_variables()

    @work(exclusive=True, group="vars")
    async def refresh_variables(self) -> None:
        if not kernel_service.is_alive():
            return
        try:
            result = await asyncio.to_thread(kernel_service.execute, VARS_SNIPPET, True)
        except Exception:  # noqa: BLE001 - explorer must never break the app
            return
        rows: List[tuple] = []
        for out in result.get("outputs", []):
            if out.get("output_type") != "stream":
                continue
            text = out.get("text", "")
            for line in ("".join(text) if isinstance(text, list) else text).splitlines():
                if line.startswith("QLVARS::"):
                    try:
                        rows = [tuple(r) for r in json.loads(line[len("QLVARS::"):])]
                    except json.JSONDecodeError:
                        rows = []
        table = self.query_one("#vars-table", DataTable)
        table.clear()
        for name, vtype, shape, value in sorted(rows):
            table.add_row(Text(name, style="bold cyan"), vtype, shape, value)
        title = self.query_one(".vars-title", Label)
        title.update(f"Kernel variables ({len(rows)}) — refreshes after each run (F2 to hide)")

    # -- model picker ------------------------------------------------------------

    @work(exclusive=True, group="model")
    async def action_pick_model(self) -> None:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{OLLAMA_URL}/api/tags")
                models = sorted(m["name"] for m in resp.json().get("models", []))
        except Exception as exc:  # noqa: BLE001
            self.notify(f"Cannot list Ollama models: {exc}", severity="error")
            return
        if not models:
            self.notify("No models installed — run "
                        "`ollama pull qwen2.5-coder:3b-instruct-q8_0`",
                        severity="warning")
            return

        def _picked(name: Optional[str]) -> None:
            if name and name != self.model:
                self.model = name
                self.update_topbar()
                self.query_one("#chat-input", ChatInput).placeholder = (
                    f"Ask the mentor ({name}) — reads your whole notebook, "
                    f"or @file.py to read a file…")
                self.notify(f"Chat model → {name}", timeout=3)
        self.push_screen(ModelPickerScreen(models, self.model), _picked)

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_open_notebook(self) -> None:
        def _open(path: Optional[str]) -> None:
            if path and path != self.nb_path:
                self._confirm_discard(
                    "switch", lambda: self.call_later(self.load_notebook, path))
        self.push_screen(OpenScreen(), _open)

    def action_new_notebook(self) -> None:
        def _create(name: Optional[str]) -> None:
            if not name:
                return
            try:
                path = create_notebook(name)
            except Exception as exc:  # noqa: BLE001
                self.notify(f"Create failed: {exc}", severity="error")
                return
            self.call_later(self.load_notebook, path)
        self._confirm_discard(
            "switch", lambda: self.push_screen(NewNotebookScreen(), _create))

    def action_import_notebook(self) -> None:
        def _import(src: Optional[str]) -> None:
            if not src:
                return
            try:
                path = import_notebook(src)
            except (NotebookPathError, OSError) as exc:
                self.notify(f"Import failed: {exc}", severity="error")
                return
            self.notify(f"Imported → {path}", timeout=3)
            self.call_later(self.load_notebook, path)
        self._confirm_discard(
            "switch", lambda: self.push_screen(ImportScreen(), _import))

    def _confirm_discard(self, verb: str, proceed) -> None:
        """Run `proceed` immediately if there are no unsaved changes, else
        prompt save/discard/cancel first (quit, Ctrl+O/N/G all route here —
        switching notebooks used to silently drop unsaved edits)."""
        if not self.dirty:
            proceed()
            return

        def _decide(choice: str) -> None:
            if choice == "save":
                if self.action_save():  # stay put if the save failed
                    proceed()
            elif choice == "quit":
                proceed()
        self.push_screen(ConfirmQuitScreen(verb), _decide)

    def action_request_quit(self) -> None:
        self._confirm_discard("quit", self.exit)

    async def on_unmount(self) -> None:
        await asyncio.to_thread(kernel_service.shutdown)


def main(notebook: Optional[str] = None) -> None:
    app = QuantLabApp(notebook=notebook)
    app.run()


if __name__ == "__main__":
    import sys
    main(sys.argv[1] if len(sys.argv) > 1 else None)
