"""Tests against a real (module-scoped) IPython kernel."""
from app import kernel


def teardown_module(module):
    kernel.shutdown()


def test_state_persists_between_runs():
    assert kernel.run_code("x = 41").returncode == 0
    result = kernel.run_code("print(x + 1)")
    assert "42" in result.stdout
    assert result.returncode == 0


def test_bare_expression_echoes_like_the_repl():
    result = kernel.run_code("2 + 2")
    assert "4" in result.stdout


def test_errors_come_back_as_tracebacks():
    result = kernel.run_code("1 / 0")
    assert "ZeroDivisionError" in result.stderr
    assert result.returncode == 1
    # ANSI escape codes are stripped
    assert "\x1b[" not in result.stderr
    # and the kernel survives the error
    assert "ok" in kernel.run_code("print('ok')").stdout


def test_timeout_interrupts_but_kernel_recovers(monkeypatch):
    monkeypatch.setattr(kernel, "TIMEOUT_SECONDS", 2)
    result = kernel.run_code("import time; time.sleep(60)")
    assert result.timed_out
    assert "interrupted" in result.stderr
    assert "ok" in kernel.run_code("print('ok')").stdout


def test_plots_are_saved(tmp_path, monkeypatch):
    monkeypatch.setattr(kernel, "PLOTS_DIR", tmp_path)
    result = kernel.run_code("import matplotlib.pyplot as plt\nplt.plot([1, 2, 3])")
    assert "[saved plot" in result.stdout
    assert list(tmp_path.glob("figure_*.png"))


def test_restart_clears_state():
    kernel.run_code("y = 123")
    kernel.restart()
    result = kernel.run_code("print(y)")
    assert "NameError" in result.stderr


def test_output_streams_via_callback():
    chunks: list[tuple[str, str]] = []
    result = kernel.run_code(
        "for i in range(3): print('line', i)",
        on_output=lambda name, text: chunks.append((name, text)),
    )
    streamed = "".join(text for _, text in chunks)
    assert "line 0" in streamed and "line 2" in streamed
    assert result.stdout  # full output still returned
