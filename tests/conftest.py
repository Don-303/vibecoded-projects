import pytest

from app import agent, progress


@pytest.fixture(autouse=True)
def isolated_progress(tmp_path, monkeypatch):
    """Point all progress persistence at a throwaway file so tests never
    touch (or depend on) the user's real data/progress.json."""
    monkeypatch.setattr(progress, "DATA_PATH", tmp_path / "progress.json")


@pytest.fixture(autouse=True)
def reset_active_model(monkeypatch):
    """The launch picker mutates the agent.MODEL global; keep tests isolated."""
    monkeypatch.setattr(agent, "MODEL", agent.DEFAULT_MODEL)
