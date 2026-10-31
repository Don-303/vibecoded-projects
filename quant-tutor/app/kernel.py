"""Persistent IPython kernel behind the code editor.

Unlike the old fresh-subprocess-per-run sandbox, the kernel keeps its
namespace between runs -- define a DataFrame once and keep exploring it,
exactly the workflow the lessons assume. Expression results echo like the
IPython REPL, and errors come back as real tracebacks. The kernel starts
lazily (or via ensure() from a background warm-up) and restart() wipes it.
"""
from __future__ import annotations

import atexit
import contextlib
import json
import os
import queue
import re
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from jupyter_client.manager import KernelManager

TIMEOUT_SECONDS = 60
PLOTS_DIR = Path(__file__).resolve().parent.parent / "data" / "plots"

# After each run, save any matplotlib figures (the Agg backend can't open
# windows in a TUI) and print their paths so the student and the tutor can
# see plots were produced. Underscored names keep the user namespace clean.
_PLOT_EPILOGUE = """\
import sys as _sys
if "matplotlib" in _sys.modules:
    import os as _os
    import matplotlib.pyplot as _plt
    for _i, _num in enumerate(_plt.get_fignums(), 1):
        _os.makedirs({plots_dir!r}, exist_ok=True)
        _path = _os.path.join({plots_dir!r}, f"figure_{{_i}}.png")
        _plt.figure(_num).savefig(_path)
        print(f"[saved plot: {{_path}}]")
    _plt.close("all")
"""

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

# Runs silently in the kernel to inventory the student's variables for the
# variable explorer. Underscored temps and non-data names (modules,
# functions, IPython plumbing) are filtered out; one marker line of JSON
# comes back on stdout.
_VARS_SNIPPET = """\
import json as _qt_json
_qt_rows = []
for _qt_k, _qt_v in list(globals().items()):
    if _qt_k.startswith('_') or _qt_k in ('In', 'Out', 'exit', 'quit', 'get_ipython', 'open'):
        continue
    _qt_t = type(_qt_v).__name__
    if _qt_t in ('module', 'function', 'type', 'ABCMeta',
                 'builtin_function_or_method', 'method'):
        continue
    try:
        _qt_shape = getattr(_qt_v, 'shape', None)
        _qt_shape = str(_qt_shape) if _qt_shape is not None else (
            str(len(_qt_v)) if hasattr(_qt_v, '__len__') else '')
    except Exception:
        _qt_shape = ''
    try:
        _qt_r = repr(_qt_v).replace('\\n', ' ')
        _qt_r = _qt_r[:64] + ('…' if len(_qt_r) > 64 else '')
    except Exception:
        _qt_r = '<unrepresentable>'
    _qt_rows.append((_qt_k, _qt_t, _qt_shape, _qt_r))
print('QTVARS::' + _qt_json.dumps(_qt_rows))
"""


@dataclass
class RunResult:
    stdout: str
    stderr: str
    timed_out: bool
    returncode: int


_lock = threading.Lock()
_km: KernelManager | None = None
_kc = None


def ensure() -> None:
    """Start the kernel if it isn't running (takes a few seconds cold)."""
    global _km, _kc
    with _lock:
        if _km is not None and _km.is_alive():
            return
        km = KernelManager()
        # Pin to this venv's interpreter: kernelspec discovery could find a
        # kernel registered by some other environment on the machine.
        km.kernel_cmd = [sys.executable, "-m", "ipykernel_launcher", "-f", "{connection_file}"]
        km.start_kernel(env=dict(os.environ, MPLBACKEND="Agg"))
        kc = km.client()
        kc.start_channels()
        kc.wait_for_ready(timeout=60)
        _km, _kc = km, kc


def run_code(code: str, on_output=None) -> RunResult:
    """Execute code in the persistent kernel. Blocking -- call via a thread.

    `on_output(name, text)` (name is "stdout" or "stderr"), if given, is
    called from this thread as output arrives, so long runs (training
    loops, simulations) can be shown live instead of all at the end. The
    full output is still returned in the RunResult.
    """
    try:
        ensure()
    except Exception as exc:
        return RunResult(
            stdout="",
            stderr=f"Could not start the IPython kernel: {exc}",
            timed_out=False,
            returncode=1,
        )
    with _lock:
        result = _execute(code, TIMEOUT_SECONDS, on_output)
        if not result.timed_out:
            plots = _execute(_PLOT_EPILOGUE.format(plots_dir=str(PLOTS_DIR)), 15, on_output)
            result.stdout += plots.stdout
        return result


def _execute(code: str, timeout: float, on_output=None) -> RunResult:
    def emit(name: str, text: str) -> None:
        if on_output is not None:
            with contextlib.suppress(Exception):
                on_output(name, text)

    msg_id = _kc.execute(code)
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    ok = True
    timed_out = False
    interrupted = False
    deadline = time.monotonic() + timeout
    while True:
        now = time.monotonic()
        if now > deadline:
            if interrupted:
                # The kernel didn't come back after the interrupt; give up
                # collecting (a restart will recover it).
                break
            interrupted = timed_out = True
            with contextlib.suppress(Exception):
                _km.interrupt_kernel()
            note = f"\n[interrupted after {timeout:.0f}s]"
            stderr_parts.append(note)
            emit("stderr", note)
            deadline = now + 10  # grace period to reach idle
        try:
            msg = _kc.get_iopub_msg(timeout=1.0)
        except queue.Empty:
            continue
        if msg.get("parent_header", {}).get("msg_id") != msg_id:
            continue
        mtype = msg["msg_type"]
        content = msg["content"]
        if mtype == "stream":
            name = "stdout" if content["name"] == "stdout" else "stderr"
            (stdout_parts if name == "stdout" else stderr_parts).append(content["text"])
            emit(name, content["text"])
        elif mtype in ("execute_result", "display_data"):
            text = content.get("data", {}).get("text/plain")
            if text:
                stdout_parts.append(text + "\n")
                emit("stdout", text + "\n")
        elif mtype == "error":
            ok = False
            traceback = _ANSI_RE.sub("", "\n".join(content.get("traceback", [])))
            stderr_parts.append(traceback)
            emit("stderr", traceback)
        elif mtype == "status" and content.get("execution_state") == "idle":
            break
    return RunResult(
        stdout="".join(stdout_parts),
        stderr="".join(stderr_parts),
        timed_out=timed_out,
        returncode=0 if ok and not timed_out else 1,
    )


def list_variables() -> list[tuple[str, str, str, str]]:
    """Inventory the kernel's user-defined variables for the explorer.

    Returns (name, type, shape, value) rows; empty on any failure or if the
    kernel isn't running (the explorer must never break the app). Blocking --
    call via a thread. Skips the plot epilogue: inspection draws nothing.
    """
    with _lock:
        if _km is None or not _km.is_alive():
            return []
        result = _execute(_VARS_SNIPPET, 15)
    for line in result.stdout.splitlines():
        if line.startswith("QTVARS::"):
            with contextlib.suppress(Exception):
                return [tuple(r) for r in json.loads(line[len("QTVARS::"):])]
    return []


def restart() -> None:
    """Shut the kernel down and start a fresh one (clears all variables)."""
    shutdown()
    ensure()


def shutdown() -> None:
    global _km, _kc
    with _lock:
        if _km is None:
            return
        with contextlib.suppress(Exception):
            _kc.stop_channels()
            _km.shutdown_kernel(now=True)
        _km = _kc = None


# The kernel is a child process; don't leave it behind if the app dies
# without reaching action_quit.
atexit.register(shutdown)
