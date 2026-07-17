"""Core engine tests: kernel execution, sessions, notebook I/O, AI context."""

import nbformat
import pytest

from core import (
    NOTEBOOK_DIR,
    NotebookPathError,
    build_chat_messages,
    create_strategy_notebook,
    expand_file_references,
    load_notebook_file,
    notebook_context,
    safe_notebook_path,
    save_notebook_file,
)


# ---------------------------------------------------------------- execution

def test_execute_stream_and_result(kernel):
    r = kernel.execute("print('hello'); 1 + 1")
    types = [o["output_type"] for o in r["outputs"]]
    assert "stream" in types and "execute_result" in types
    assert r["status"] == "ok"


def test_execute_matplotlib_png(kernel):
    r = kernel.execute("import matplotlib.pyplot as plt\n"
                       "plt.plot([1, 4, 2, 8]); plt.show()")
    imgs = [o for o in r["outputs"] if o["output_type"] == "display_data"
            and "image/png" in o.get("data", {})]
    assert imgs and len(imgs[0]["data"]["image/png"]) > 1000


def test_execute_error(kernel):
    r = kernel.execute("1/0")
    errs = [o for o in r["outputs"] if o["output_type"] == "error"]
    assert errs and errs[0]["ename"] == "ZeroDivisionError"
    assert r["status"] == "error"


def test_streaming_callback(kernel):
    snapshots = []
    r = kernel.execute("for i in range(3):\n    print('s', i, flush=True)",
                       on_output=snapshots.append)
    assert len(snapshots) >= 1  # partial snapshots arrived
    final = "".join(o.get("text", "") for o in r["outputs"]
                    if o["output_type"] == "stream")
    assert "s 0" in final and "s 2" in final


def test_sessions_isolated_and_resumable(kernel):
    kernel.execute("session_marker = 'A'")
    started = kernel.switch("pytest-other")
    assert started
    r = kernel.execute("try:\n    session_marker\nexcept NameError:\n    print('isolated')")
    txt = "".join(o.get("text", "") for o in r["outputs"] if o["output_type"] == "stream")
    assert "isolated" in txt
    resumed_fresh = kernel.switch("pytest-session")
    assert not resumed_fresh  # resumed, not restarted
    r = kernel.execute("session_marker")
    assert r["outputs"][0]["data"]["text/plain"] == "'A'"


# ---------------------------------------------------------------- notebook IO

def test_save_load_roundtrip_with_chat(tmp_path):
    name = "pytest_roundtrip.ipynb"
    chat = [{"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello\n```python\nx=1\n```"}]
    cells = [{"cell_type": "markdown", "source": "# t"},
             {"cell_type": "code", "source": "x=1", "execution_count": 3,
              "outputs": [{"output_type": "stream", "name": "stdout", "text": "hi\n"}]}]
    try:
        save_notebook_file(name, cells, chat)
        nb = nbformat.read(str(NOTEBOOK_DIR / name), as_version=4)
        nbformat.validate(nb)
        loaded = load_notebook_file(name)
        assert loaded["chat"] == chat
        assert loaded["cells"][1]["execution_count"] == 3
    finally:
        (NOTEBOOK_DIR / name).unlink(missing_ok=True)


def test_path_traversal_rejected():
    with pytest.raises(NotebookPathError):
        safe_notebook_path("../../etc/passwd.ipynb")
    with pytest.raises(NotebookPathError):
        safe_notebook_path("not_a_notebook.txt")


def test_strategy_scaffold_is_bias_safe():
    name = "pytest_scaffold.ipynb"
    try:
        path = create_strategy_notebook(name)
        nb = load_notebook_file(path)
        srcs = " ".join(c["source"] for c in nb["cells"])
        assert "shift(1)" in srcs          # no look-ahead
        assert "COST_BPS" in srcs          # transaction costs
        assert "quantlab_stats" in srcs
    finally:
        (NOTEBOOK_DIR / name).unlink(missing_ok=True)


# ---------------------------------------------------------------- AI context

def test_notebook_context_digest():
    cells = [{"cell_type": "code", "source": "x=1", "execution_count": 2,
              "outputs": [{"output_type": "error", "ename": "NameError",
                           "evalue": "boom", "traceback": ["NameError: boom"]}]}]
    ctx = notebook_context(cells)
    assert "Cell 1 [code]" in ctx and "NameError: boom" in ctx


def test_notebook_context_total_cap():
    cells = [{"cell_type": "code", "source": "x" * 5000, "execution_count": None,
              "outputs": []} for _ in range(20)]
    ctx = notebook_context(cells)
    assert len(ctx) < 30_000
    assert "truncated" in ctx


def test_build_chat_messages_order():
    msgs = build_chat_messages([{"role": "user", "content": "q"}],
                               [{"cell_type": "code", "source": "x=1"}])
    assert msgs[0]["role"] == "system"
    assert "CURRENT NOTEBOOK STATE" in msgs[1]["content"]
    assert msgs[-1]["content"] == "q"


def test_expand_file_references(tmp_path):
    f = tmp_path / "ref_target.py"
    f.write_text("unique_marker_zz = 1\n")
    out = expand_file_references(f"read @{f}")
    assert "unique_marker_zz" in out
    assert "file not found" in expand_file_references("read @missing_zz.py")
    email = "mail me@example.com ok"
    assert expand_file_references(email) == email
    plain = "no refs here"
    assert expand_file_references(plain) == plain


# ---------------------------------------------------------------- stats

def test_quantlab_stats_metrics():
    import numpy as np
    import pandas as pd
    from quantlab_stats import metrics

    idx = pd.bdate_range("2015-01-01", periods=1500)
    r = pd.Series(np.random.default_rng(1).normal(0.0004, 0.01, 1500), index=idx)
    m = metrics(r)
    assert abs(m["ann_vol"] - 0.01 * np.sqrt(252)) < 0.01
    assert -1 < m["max_drawdown"] <= 0
    m2 = metrics(r.abs())
    assert m2["max_drawdown"] == 0 and m2["hit_rate"] == 1.0


def test_quantlab_data_loader():
    from quantlab_data import available, closes, load_index

    av = available()
    assert "sp500" in av and len(av) >= 8
    spx = load_index("sp500", start="2020-01-01")
    assert {"Open", "High", "Low", "Close"} <= set(spx.columns)
    panel = closes("sp500", "dax", start="2015-01-01")
    assert list(panel.columns) == ["sp500", "dax"]
