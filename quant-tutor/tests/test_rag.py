import pytest

from app import rag

QR_CAPM = "Lecture30-CAPM-and-Arbitrage-Pricing-Theory.ipynb"
corpus_missing = not (rag.CORPUS_DIR / "quantrocket-lectures").exists()


def test_boilerplate_detection():
    assert rag._is_boilerplate("© Copyright Quantopian Inc blah")
    assert rag._is_boilerplate("see notebook-header-logo.png here")
    assert rag._is_boilerplate("***\n[Quant Finance Lectures](Introduction.ipynb) › L30\n***")
    assert not rag._is_boilerplate("The CAPM is a classic measure of the cost of capital.")


@pytest.mark.skipif(corpus_missing, reason="corpus not cloned")
def test_load_source_text_strips_boilerplate():
    text = rag.load_source_text("quantrocket-lectures", QR_CAPM)
    assert text is not None
    assert "Copyright Quantopian" not in text
    assert "Capital Asset Pricing Model" in text
    assert len(text) <= 3500


@pytest.mark.skipif(corpus_missing, reason="corpus not cloned")
def test_find_source_file():
    assert rag.find_source_file("quantrocket-lectures", QR_CAPM) is not None
    assert rag.find_source_file("quantrocket-lectures", "NoSuchFile.ipynb") is None
    assert rag.find_source_file("no-such-repo", QR_CAPM) is None


def test_load_source_text_missing_repo_returns_none():
    assert rag.load_source_text("no-such-repo", "x.ipynb") is None
