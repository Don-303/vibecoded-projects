"""Shared test setup: project root on sys.path, kernel fixture, ollama gate."""
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest


def ollama_running() -> bool:
    try:
        s = socket.create_connection(("localhost", 11434), timeout=1)
        s.close()
        return True
    except OSError:
        return False


requires_ollama = pytest.mark.skipif(
    not ollama_running(), reason="local Ollama not reachable on :11434")


@pytest.fixture(scope="session")
def kernel():
    """One live kernel for the whole test session (core-level tests)."""
    from core import kernel_service
    kernel_service.start("pytest-session")
    yield kernel_service
    kernel_service.shutdown()
