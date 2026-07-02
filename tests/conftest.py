import pytest

from app import progress


@pytest.fixture(autouse=True)
def isolated_progress(tmp_path, monkeypatch):
    """Point all progress persistence at a throwaway file so tests never
    touch (or depend on) the user's real data/progress.json."""
    monkeypatch.setattr(progress, "DATA_PATH", tmp_path / "progress.json")
