"""
QuantLab core — shared engine used by both the web app (app.py) and the
terminal TUI (tui.py): configuration, the Quant Mentor system prompt, the
Jupyter kernel service, and nbformat-safe notebook file I/O.
"""

from __future__ import annotations

import logging
import os
import queue
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import nbformat

log = logging.getLogger("quantlab")

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
NOTEBOOK_DIR = Path(os.environ.get("QUANTLAB_NOTEBOOK_DIR", BASE_DIR / "notebooks")).resolve()

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5-coder:3b-instruct-q8_0")
OLLAMA_TEMPERATURE = float(os.environ.get("OLLAMA_TEMPERATURE", "0.3"))

KERNEL_NAME = os.environ.get("QUANTLAB_KERNEL", "python3")
EXEC_TIMEOUT_SECONDS = int(os.environ.get("QUANTLAB_EXEC_TIMEOUT", "300"))

NOTEBOOK_DIR.mkdir(parents=True, exist_ok=True)

# --------------------------------------------------------------------------
# Quantitative Finance Research Mentor persona (hardcoded system prompt)
# --------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are an expert Quantitative Finance Research Mentor embedded inside a local
Jupyter notebook editor. The user is doing quantitative research in Python and
you review, explain, debug and improve their notebook cells.

Your teaching style:
- Act as a patient senior quant walking a junior researcher through the work.
- When reviewing code, explicitly teach the underlying quantitative concepts:
  time-series indexed DataFrames (DatetimeIndex, resampling, alignment,
  look-ahead bias), vectorised backtesting logic (shifted signals, position
  vectors, avoiding loops), and mathematical risk metrics — define them when
  they appear. E.g. Sharpe Ratio = mean(excess returns) / std(excess returns),
  annualised with sqrt(252); Maximum Drawdown = min(equity / equity.cummax() - 1);
  also Sortino, Calmar, CAGR, volatility targeting where relevant.
- Give step-by-step, documentation-style guidance for the common quant stack:
  `yfinance` (download, auto_adjust, multi-ticker column levels), `pandas`,
  `numpy`, `scipy`, `matplotlib`/`seaborn`, `pandas-ta` (indicator usage and
  its DataFrame extension API), and `PyPortfolioOpt` (expected_returns,
  risk_models, EfficientFrontier, discrete allocation).
- Flag classic research bugs proactively: look-ahead bias, survivorship bias,
  overfitting, ignoring transaction costs/slippage, using close-to-close fills
  on signal day, incorrect annualisation factors, and silent NaN propagation.

You are given the full current notebook (cell sources, execution counts and
recent outputs) as context with every request — use it. When the user says
"cell 3" they mean the 1-based index in that context. Ground every answer in
the actual notebook content rather than guessing. When a message contains a
section headed "--- contents of @<path> ---", that is a file the user asked
you to read (e.g. a textbook script) — treat it as real code you have full
access to, not a description; review, compare, or critique it directly.

Formatting rules (strict):
- BE BRIEF. Lead with the answer in one sentence. Keep total prose under
  ~100 words unless the user explicitly asks for depth. No greetings, no
  restating the question, no filler ("Certainly!", "Great question").
- Respond in GitHub-flavoured Markdown; prefer terse bullets over paragraphs.
- When you propose code, always emit it as a fenced ```python code block that
  is a complete, drop-in replacement for the cell (or the selected fragment)
  under discussion — the editor offers an "Apply to Cell" action on your code
  blocks, so partial pseudo-code breaks the workflow. One code block per
  reply; comments in the code carry the explanation.
- When given a stack trace: root cause in one sentence, then the corrected
  cell as a single python code block. Nothing else.
"""

# --------------------------------------------------------------------------
# Kernel service: MultiKernelManager + blocking client + iopub pump
# --------------------------------------------------------------------------


class KernelService:
    """Owns one long-lived ipykernel via MultiKernelManager.

    Execution is serialised with a lock (a notebook has one kernel; parallel
    execute requests are queued exactly like Jupyter's own queue).
    """

    def __init__(self) -> None:
        from concurrent.futures import ThreadPoolExecutor

        from jupyter_client import MultiKernelManager

        self.manager = MultiKernelManager()
        try:  # keep kernel-*.json connection files out of the project dir
            from jupyter_core.paths import jupyter_runtime_dir
            runtime_dir = Path(jupyter_runtime_dir())
            runtime_dir.mkdir(parents=True, exist_ok=True)
            self.manager.connection_dir = str(runtime_dir)
        except Exception:
            pass
        self.kernel_id: Optional[str] = None
        self.client = None
        # Named kernel sessions (one per notebook in the TUI): each keeps its
        # own live interpreter state, so switching notebooks never leaks
        # variables between research projects.
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._current: str = "default"
        self._lock = threading.Lock()
        # ZMQ sockets are not thread-safe and jupyter_client's blocking
        # channels keep per-thread event loops, so every channel operation
        # must happen on ONE dedicated thread. All public lifecycle/execute
        # methods funnel through this single-worker executor.
        self._kernel_thread: Optional[threading.Thread] = None
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="quantlab-kernel",
            initializer=self._register_kernel_thread)

    def _register_kernel_thread(self) -> None:
        self._kernel_thread = threading.current_thread()

    def _on_kernel_thread(self, fn, *args):
        """Run fn on the dedicated kernel thread (re-entrant safe)."""
        if threading.current_thread() is self._kernel_thread:
            return fn(*args)
        return self._executor.submit(fn, *args).result()

    # -- lifecycle ---------------------------------------------------------

    def start(self, session: Optional[str] = None) -> None:
        self._on_kernel_thread(self._start_impl, session)

    def _start_impl(self, session: Optional[str] = None) -> None:
        name = session or self._current
        log.info("Starting kernel '%s' for session '%s' ...", KERNEL_NAME, name)
        kid = self.manager.start_kernel(kernel_name=KERNEL_NAME)
        client = self.manager.get_kernel(kid).client()
        client.start_channels()
        client.wait_for_ready(timeout=60)
        self._sessions[name] = {"kernel_id": kid, "client": client}
        self._current = name
        self.kernel_id = kid
        self.client = client
        self._bootstrap()
        log.info("Kernel %s ready (session '%s').", kid, name)

    def switch(self, session: str) -> bool:
        """Activate the kernel for `session`, starting one if needed.
        Returns True if a fresh kernel was started, False if an existing
        session (with its live state) was resumed."""
        return bool(self._on_kernel_thread(self._switch_impl, session))

    def _switch_impl(self, session: str) -> bool:
        if session == self._current and self.is_alive():
            return False
        info = self._sessions.get(session)
        if info is not None:
            try:
                alive = self.manager.is_alive(info["kernel_id"])
            except Exception:
                alive = False
            if alive:
                self._current = session
                self.kernel_id = info["kernel_id"]
                self.client = info["client"]
                return False
            self._sessions.pop(session, None)
        self._start_impl(session)
        return True

    @property
    def session_count(self) -> int:
        return len(self._sessions)

    @property
    def current_session(self) -> str:
        return self._current

    def _bootstrap(self) -> None:
        """Activate the inline matplotlib backend so plt.show() publishes
        base64 image/png display_data messages, exactly like Jupyter, and
        make the bundled quantlab_data module importable from any cwd."""
        try:
            self.execute("import sys as _ql_sys\n"
                         f"_ql_sys.path.insert(0, {str(BASE_DIR)!r})\n"
                         "%matplotlib inline\n"
                         "import matplotlib.pyplot as _plt_bootstrap_check",
                         silent=True)
        except Exception:
            log.warning("matplotlib inline bootstrap failed (matplotlib may "
                        "not be installed in the kernel env)")

    def shutdown(self) -> None:
        """Shutdown hook — gracefully kill every session's kernel so no
        orphaned ipykernel processes survive the app."""
        if self.kernel_id is None and not self._sessions:
            return
        self.interrupt()  # break any long-running cell so shutdown isn't queued behind it
        self._on_kernel_thread(self._shutdown_impl)

    def _shutdown_impl(self) -> None:
        for name, info in list(self._sessions.items()):
            log.info("Shutting down kernel %s (session '%s') ...", info["kernel_id"], name)
            try:
                info["client"].stop_channels()
            except Exception:  # noqa: BLE001 - best effort teardown
                pass
            try:
                self.manager.shutdown_kernel(info["kernel_id"], now=True)
            except Exception:
                log.exception("Graceful shutdown failed; killing kernel process.")
                try:
                    self.manager.get_kernel(info["kernel_id"]).kernel.kill()
                except Exception:
                    pass
        self._sessions.clear()
        self.kernel_id = None
        self.client = None

    def restart(self) -> None:
        """Restart the *current* session's kernel (clears its state)."""
        self.interrupt()
        self._on_kernel_thread(self._restart_impl)

    def _restart_impl(self) -> None:
        with self._lock:
            assert self.kernel_id is not None
            log.info("Restarting kernel %s ...", self.kernel_id)
            self.manager.get_kernel(self.kernel_id).restart_kernel(now=True)
            self.client.stop_channels()
            self.client = self.manager.get_kernel(self.kernel_id).client()
            self.client.start_channels()
            self.client.wait_for_ready(timeout=60)
            self._sessions[self._current] = {"kernel_id": self.kernel_id,
                                             "client": self.client}
        self._bootstrap()

    def interrupt(self) -> None:
        if self.kernel_id is not None:
            self.manager.interrupt_kernel(self.kernel_id)

    def is_alive(self) -> bool:
        try:
            return self.kernel_id is not None and self.manager.is_alive(self.kernel_id)
        except Exception:
            return False

    # -- execution ---------------------------------------------------------

    def execute(self, code: str, silent: bool = False,
                on_output=None) -> Dict[str, Any]:
        """Run `code`, pumping the ZeroMQ iopub channel until the kernel goes
        idle. Returns nbformat-shaped outputs plus the execution count.

        Handles: stream (stdout/stderr), execute_result, display_data
        (incl. base64 image/png from Matplotlib and text/html from Pandas),
        and error messages.

        `on_output`, if given, is called with a snapshot of the outputs list
        each time it changes — from the kernel thread — enabling live
        streaming of long-running cells (training loops, tqdm, etc.).
        """
        return self._on_kernel_thread(self._execute_impl, code, silent, on_output)

    def _execute_impl(self, code: str, silent: bool, on_output=None) -> Dict[str, Any]:
        with self._lock:
            if not self.is_alive():
                raise RuntimeError("Kernel is not running")

            msg_id = self.client.execute(code, silent=silent,
                                         store_history=not silent,
                                         allow_stdin=False)
            outputs: List[Dict[str, Any]] = []
            execution_count: Optional[int] = None
            deadline = time.monotonic() + EXEC_TIMEOUT_SECONDS
            interrupted = False

            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 and not interrupted:
                    log.warning("Execution timed out after %ss; interrupting.", EXEC_TIMEOUT_SECONDS)
                    self.interrupt()
                    interrupted = True
                    deadline = time.monotonic() + 10  # grace period for the error msg
                try:
                    msg = self.client.get_iopub_msg(timeout=max(0.1, min(remaining, 1.0)))
                except queue.Empty:
                    if not self.is_alive():
                        outputs.append(error_output("KernelDied", "The kernel process died during execution."))
                        break
                    if interrupted and time.monotonic() > deadline:
                        outputs.append(error_output(
                            "ExecutionTimeout",
                            f"Cell exceeded {EXEC_TIMEOUT_SECONDS}s and was interrupted."))
                        break
                    continue

                if msg.get("parent_header", {}).get("msg_id") != msg_id:
                    continue  # message from a previous/other execution

                msg_type = msg["msg_type"]
                content = msg["content"]

                if msg_type == "status":
                    if content.get("execution_state") == "idle":
                        break
                elif msg_type == "execute_input":
                    execution_count = content.get("execution_count")
                elif msg_type == "stream":
                    # Coalesce consecutive writes to the same stream.
                    if outputs and outputs[-1].get("output_type") == "stream" \
                            and outputs[-1].get("name") == content["name"]:
                        outputs[-1]["text"] += content["text"]
                    else:
                        outputs.append({
                            "output_type": "stream",
                            "name": content["name"],
                            "text": content["text"],
                        })
                elif msg_type in ("execute_result", "display_data"):
                    # `data` mime bundle: image/png arrives base64-encoded from
                    # the kernel already — pass through for inline render.
                    out: Dict[str, Any] = {
                        "output_type": msg_type,
                        "data": content.get("data", {}),
                        "metadata": content.get("metadata", {}),
                    }
                    if msg_type == "execute_result":
                        out["execution_count"] = content.get("execution_count")
                    outputs.append(out)
                elif msg_type == "update_display_data":
                    outputs.append({
                        "output_type": "display_data",
                        "data": content.get("data", {}),
                        "metadata": content.get("metadata", {}),
                    })
                elif msg_type == "error":
                    outputs.append({
                        "output_type": "error",
                        "ename": content.get("ename", "Error"),
                        "evalue": content.get("evalue", ""),
                        "traceback": content.get("traceback", []),
                    })
                elif msg_type == "clear_output":
                    outputs.clear()

                if on_output is not None and msg_type in (
                        "stream", "execute_result", "display_data",
                        "update_display_data", "error", "clear_output"):
                    try:  # a broken callback must never kill execution
                        on_output([dict(o) for o in outputs])
                    except Exception:
                        pass

            # Drain the shell reply (carries the authoritative execution_count).
            try:
                reply = self.client.get_shell_msg(timeout=5)
                if reply.get("parent_header", {}).get("msg_id") == msg_id:
                    execution_count = reply["content"].get("execution_count", execution_count)
                    status = reply["content"].get("status", "ok")
                else:
                    status = "ok"
            except queue.Empty:
                status = "ok"

            return {
                "status": status,
                "execution_count": execution_count,
                "outputs": outputs,
            }


def error_output(ename: str, evalue: str) -> Dict[str, Any]:
    return {"output_type": "error", "ename": ename, "evalue": evalue,
            "traceback": [f"{ename}: {evalue}"]}


kernel_service = KernelService()

# Safety net: even if a frontend crashes past its own shutdown hook, never
# leave an orphaned ipykernel behind. Idempotent (no-op when already down).
import atexit  # noqa: E402

atexit.register(kernel_service.shutdown)

# --------------------------------------------------------------------------
# Notebook file I/O (nbformat)
# --------------------------------------------------------------------------


class NotebookPathError(ValueError):
    """A user-supplied notebook path is invalid or escapes NOTEBOOK_DIR."""


def safe_notebook_path(rel_path: str) -> Path:
    """Resolve a user-supplied path strictly inside NOTEBOOK_DIR."""
    candidate = (NOTEBOOK_DIR / rel_path).resolve()
    if not str(candidate).startswith(str(NOTEBOOK_DIR) + os.sep) and candidate != NOTEBOOK_DIR:
        raise NotebookPathError("Path escapes the notebook directory")
    if candidate.suffix != ".ipynb":
        raise NotebookPathError("Only .ipynb files are supported")
    return candidate


def list_notebooks() -> List[str]:
    return sorted(p.relative_to(NOTEBOOK_DIR).as_posix()
                  for p in NOTEBOOK_DIR.rglob("*.ipynb")
                  if ".ipynb_checkpoints" not in p.parts)


def create_notebook(name: str) -> str:
    name = name.strip()
    if not name:
        raise NotebookPathError("Name required")
    if not name.endswith(".ipynb"):
        name += ".ipynb"
    path = safe_notebook_path(name)
    if path.exists():
        raise FileExistsError("Notebook already exists")
    nb = nbformat.v4.new_notebook()
    nb.cells = [nbformat.v4.new_code_cell("")]
    nb.metadata["kernelspec"] = {"name": KERNEL_NAME, "display_name": "Python 3", "language": "python"}
    path.parent.mkdir(parents=True, exist_ok=True)
    nbformat.write(nb, str(path))
    return path.relative_to(NOTEBOOK_DIR).as_posix()


def load_notebook_file(rel_path: str) -> Dict[str, Any]:
    """Read an .ipynb and return {'path', 'cells', 'chat'} plain dicts."""
    fpath = safe_notebook_path(rel_path)
    if not fpath.exists():
        raise FileNotFoundError(rel_path)
    nb = nbformat.read(str(fpath), as_version=4)
    cells = []
    for cell in nb.cells:
        c: Dict[str, Any] = {"cell_type": cell.cell_type, "source": cell.source}
        if cell.cell_type == "code":
            c["execution_count"] = cell.get("execution_count")
            c["outputs"] = [dict(o) for o in cell.get("outputs", [])]
        cells.append(c)
    chat = nb.metadata.get("quantlab", {}).get("chat", [])
    return {"path": rel_path, "cells": cells, "chat": chat}


def sanitize_output(raw: Dict[str, Any]) -> Optional[nbformat.NotebookNode]:
    """Convert a frontend output dict into a valid nbformat v4 output node."""
    from nbformat.v4 import new_output

    ot = raw.get("output_type")
    try:
        if ot == "stream":
            return new_output("stream", name=raw.get("name", "stdout"), text=raw.get("text", ""))
        if ot in ("execute_result", "display_data"):
            kwargs: Dict[str, Any] = {
                "data": raw.get("data", {}) or {},
                "metadata": raw.get("metadata", {}) or {},
            }
            if ot == "execute_result":
                kwargs["execution_count"] = raw.get("execution_count")
            return new_output(ot, **kwargs)
        if ot == "error":
            return new_output("error", ename=raw.get("ename", "Error"),
                              evalue=raw.get("evalue", ""),
                              traceback=raw.get("traceback", []))
    except Exception:
        log.exception("Dropping malformed output during save")
    return None


def save_notebook_file(rel_path: str, cells: List[Dict[str, Any]],
                       chat: Optional[List[Dict[str, str]]] = None) -> int:
    """Write cells (+ chat transcript) to an .ipynb, preserving metadata.

    Returns the number of cells written. Validates before writing so the
    file always stays openable by JupyterLab/VS Code.
    """
    from nbformat.v4 import new_code_cell, new_markdown_cell, new_notebook

    fpath = safe_notebook_path(rel_path)
    if fpath.exists():
        try:
            nb = nbformat.read(str(fpath), as_version=4)
        except Exception:
            nb = new_notebook()
    else:
        nb = new_notebook()
        nb.metadata["kernelspec"] = {"name": KERNEL_NAME, "display_name": "Python 3", "language": "python"}

    new_cells = []
    for raw in cells:
        source = raw.get("source", "")
        if raw.get("cell_type") == "markdown":
            new_cells.append(new_markdown_cell(source))
        else:
            cell = new_code_cell(source)
            cell.execution_count = raw.get("execution_count")
            cell.outputs = [o for o in (sanitize_output(x) for x in raw.get("outputs", [])) if o]
            new_cells.append(cell)
    nb.cells = new_cells
    nb.metadata.setdefault("quantlab", {})["chat"] = list(chat or [])

    # never write a structurally broken .ipynb (normalize assigns cell ids)
    _, nb = nbformat.validator.normalize(nb)
    nbformat.validate(nb)
    fpath.parent.mkdir(parents=True, exist_ok=True)
    nbformat.write(nb, str(fpath))
    return len(new_cells)


def create_strategy_notebook(name: str) -> str:
    """Create a bias-safe strategy research scaffold: bundled data load,
    signal stub, vectorised backtest with costs, and a stats report."""
    from nbformat.v4 import new_code_cell, new_markdown_cell, new_notebook

    name = name.strip()
    if not name:
        raise NotebookPathError("Name required")
    if not name.endswith(".ipynb"):
        name += ".ipynb"
    path = safe_notebook_path(name)
    if path.exists():
        raise FileExistsError("Notebook already exists")
    title = path.stem.replace("_", " ").replace("-", " ")

    nb = new_notebook()
    nb.metadata["kernelspec"] = {"name": KERNEL_NAME, "display_name": "Python 3",
                                 "language": "python"}
    nb.cells = [
        new_markdown_cell(
            f"# {title}\n\n"
            "Strategy research scaffold — bundled offline data, a signal stub, a\n"
            "vectorised backtest **with transaction costs and no look-ahead**, and a\n"
            "full stats report. Edit the signal cell and re-run (`F5`).\n"
            "Press `e` on any cell to have the AI mentor review it."),
        new_code_cell(
            "import numpy as np\n"
            "import pandas as pd\n"
            "import matplotlib.pyplot as plt\n"
            "from quantlab_data import available, closes, load_index\n"
            "from quantlab_stats import metrics, report, tearsheet\n"
            "\n"
            "px = closes(\"sp500\", start=\"1990-01-01\")[\"sp500\"].dropna()\n"
            "rets = px.pct_change().dropna()\n"
            "px.tail()"),
        new_markdown_cell(
            "## Signal\n\n"
            "Edit this. Must only use information available at the close of day *t*\n"
            "— the `.shift(1)` in the backtest cell delays execution to *t+1*."),
        new_code_cell(
            "LOOKBACK = 252\n"
            "\n"
            "# example: 12-month time-series momentum, long/flat\n"
            "signal = (px.pct_change(LOOKBACK) > 0).astype(float)\n"
            "signal.tail()"),
        new_code_cell(
            "COST_BPS = 1.0   # one-way transaction cost, basis points\n"
            "\n"
            "position = signal.shift(1).fillna(0.0)          # trade the day AFTER the signal\n"
            "turnover = position.diff().abs().fillna(0.0)\n"
            "strat = position * rets - turnover * COST_BPS / 1e4\n"
            "print(f\"avg annual turnover: {turnover.mean() * 252:.1f}x\")"),
        new_code_cell(
            "report(strat.dropna(), name=\"strategy\")\n"
            "tearsheet(strat.dropna(), benchmark=rets, name=\"strategy\")"),
        new_markdown_cell(
            "## Next steps\n\n"
            "- Sweep `LOOKBACK` for parameter robustness (a strategy that only works\n"
            "  at exactly 252 is overfit).\n"
            "- Raise `COST_BPS` — does the edge survive 5–10 bps?\n"
            "- Try other indices from `available()`, or a multi-asset basket.\n"
            "- Ask the AI mentor to critique the methodology for biases."),
    ]
    _, nb = nbformat.validator.normalize(nb)
    nbformat.validate(nb)
    path.parent.mkdir(parents=True, exist_ok=True)
    nbformat.write(nb, str(path))
    return path.relative_to(NOTEBOOK_DIR).as_posix()


# --------------------------------------------------------------------------
# AI context: serialise the live notebook so the model can "read" it
# --------------------------------------------------------------------------


def notebook_context(cells: List[Dict[str, Any]], max_output_chars: int = 600) -> str:
    """Render the notebook into a compact plain-text digest for the LLM."""
    lines = ["CURRENT NOTEBOOK STATE (1-based cell indices):", ""]
    for i, cell in enumerate(cells, start=1):
        if cell.get("cell_type") == "markdown":
            lines.append(f"--- Cell {i} [markdown] ---")
            lines.append(cell.get("source", ""))
        else:
            ec = cell.get("execution_count")
            lines.append(f"--- Cell {i} [code] (execution_count={ec}) ---")
            lines.append(cell.get("source", ""))
            for out in cell.get("outputs", []) or []:
                ot = out.get("output_type")
                if ot == "stream":
                    text = out.get("text", "")
                    text = "".join(text) if isinstance(text, list) else text
                    lines.append(f"[{out.get('name', 'stdout')}] {text[:max_output_chars]}")
                elif ot in ("execute_result", "display_data"):
                    data = out.get("data", {}) or {}
                    plain = data.get("text/plain", "")
                    plain = "".join(plain) if isinstance(plain, list) else plain
                    if plain:
                        lines.append(f"[result] {plain[:max_output_chars]}")
                    if "image/png" in data:
                        lines.append("[result] <matplotlib figure rendered>")
                elif ot == "error":
                    tb = out.get("traceback", [])
                    tb = "\n".join(tb) if isinstance(tb, list) else str(tb)
                    tb = strip_ansi(tb)
                    lines.append(f"[ERROR] {out.get('ename')}: {out.get('evalue')}")
                    lines.append(tb[-max_output_chars:])
        lines.append("")
    digest = "\n".join(lines)
    # Overall budget so a huge notebook can't blow the local model's context
    # window: keep the head and the tail (recent cells matter most).
    max_total = 28_000
    if len(digest) > max_total:
        digest = (digest[: max_total // 2]
                  + "\n\n[... middle cells truncated — notebook too large ...]\n\n"
                  + digest[-max_total // 2:])
    return digest


def strip_ansi(text: str) -> str:
    import re
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


# --------------------------------------------------------------------------
# @file references in chat prompts — let the mentor read external files
# (e.g. a textbook's .py script) without you pasting them in by hand.
# --------------------------------------------------------------------------

FILE_REF_RE = None  # compiled lazily below
MAX_FILE_REF_CHARS = 20_000


def _resolve_file_ref(token: str) -> Optional[Path]:
    """Resolve an @token to a real file, trying the notebook dir, the
    project dir, then the token as given (supports ~ and absolute paths)."""
    candidates = [NOTEBOOK_DIR / token, BASE_DIR / token, Path(token).expanduser()]
    for c in candidates:
        try:
            c = c.resolve()
        except OSError:
            continue
        if c.is_file():
            return c
    return None


def expand_file_references(text: str) -> str:
    """Expand @path/to/file tokens in `text` into fenced code blocks so the
    AI mentor can read files that aren't already in the notebook. Unmatched
    tokens are left alone (so `me@example.com`-style text is untouched)."""
    import re

    global FILE_REF_RE
    if FILE_REF_RE is None:
        FILE_REF_RE = re.compile(r"(?<!\S)@([\w./~-]+\.\w+)")

    tokens = FILE_REF_RE.findall(text)
    if not tokens:
        return text

    blocks = []
    seen = set()
    for token in tokens:
        if token in seen:
            continue
        seen.add(token)
        path = _resolve_file_ref(token)
        if path is None:
            blocks.append(f"\n\n[@{token}: file not found — checked notebook dir, "
                          f"project dir, and as given]")
            continue
        try:
            content = path.read_text(errors="replace")
        except Exception as exc:  # noqa: BLE001
            blocks.append(f"\n\n[@{token}: could not read file — {exc}]")
            continue
        truncated = len(content) > MAX_FILE_REF_CHARS
        if truncated:
            content = content[:MAX_FILE_REF_CHARS] + "\n# … truncated …"
        lang = {"py": "python", "md": "markdown", "csv": "text", "json": "json",
                "txt": "text"}.get(path.suffix.lstrip("."), "")
        note = "  (truncated)" if truncated else ""
        blocks.append(f"\n\n--- contents of @{token}{note} ---\n```{lang}\n{content}\n```")
    return text + "".join(blocks)


def build_chat_messages(history: List[Dict[str, str]],
                        cells: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, str]]:
    """System prompt + (optional) live notebook digest + conversation."""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if cells is not None:
        messages.append({"role": "system", "content": notebook_context(cells)})
    messages.extend(history)
    return messages
