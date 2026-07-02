"""Runs student code in a subprocess using this project's venv interpreter."""
from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# Generous enough for the small PyTorch/statsmodels exercises in the
# MoQuant/MSc lessons, which can exceed 20s on this CPU-only machine.
TIMEOUT_SECONDS = 60
PLOTS_DIR = Path(__file__).resolve().parent.parent / "data" / "plots"

# Wraps the student's code so matplotlib works in a windowless subprocess:
# the Agg backend (set via env below) can't open windows, so any figures the
# code creates are saved to PLOTS_DIR instead, with the paths printed so the
# app (and the tutor) can see that plots were produced.
_WRAPPER = """\
import os
exec(compile({code!r}, "<exercise>", "exec"), {{"__name__": "__main__"}})
import sys
if "matplotlib" in sys.modules:
    import matplotlib.pyplot as plt
    for _i, _num in enumerate(plt.get_fignums(), 1):
        os.makedirs({plots_dir!r}, exist_ok=True)
        _path = os.path.join({plots_dir!r}, f"figure_{{_i}}.png")
        plt.figure(_num).savefig(_path)
        print(f"[saved plot: {{_path}}]")
"""


@dataclass
class RunResult:
    stdout: str
    stderr: str
    timed_out: bool
    returncode: int


def run_code(code: str) -> RunResult:
    wrapped = _WRAPPER.format(code=code, plots_dir=str(PLOTS_DIR))
    env = dict(os.environ, MPLBACKEND="Agg")
    try:
        proc = subprocess.run(
            [sys.executable, "-c", wrapped],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            env=env,
        )
        return RunResult(stdout=proc.stdout, stderr=proc.stderr, timed_out=False, returncode=proc.returncode)
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode(errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode(errors="replace")
        return RunResult(
            stdout=stdout,
            stderr=stderr + f"\n[timed out after {TIMEOUT_SECONDS}s]",
            timed_out=True,
            returncode=-1,
        )
