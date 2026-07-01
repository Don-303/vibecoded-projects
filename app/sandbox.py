"""Runs student code in a subprocess using this project's venv interpreter."""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass

TIMEOUT_SECONDS = 20


@dataclass
class RunResult:
    stdout: str
    stderr: str
    timed_out: bool
    returncode: int


def run_code(code: str) -> RunResult:
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
        )
        return RunResult(stdout=proc.stdout, stderr=proc.stderr, timed_out=False, returncode=proc.returncode)
    except subprocess.TimeoutExpired as exc:
        return RunResult(
            stdout=exc.stdout or "",
            stderr=(exc.stderr or "") + f"\n[timed out after {TIMEOUT_SECONDS}s]",
            timed_out=True,
            returncode=-1,
        )
