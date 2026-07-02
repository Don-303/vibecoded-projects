from app import sandbox


def test_stdout_captured():
    r = sandbox.run_code("print(6 * 7)")
    assert r.stdout.strip() == "42"
    assert r.returncode == 0 and not r.timed_out


def test_stderr_and_returncode_on_error():
    r = sandbox.run_code("1/0")
    assert "ZeroDivisionError" in r.stderr
    assert r.returncode != 0


def test_braces_in_user_code_survive_wrapper():
    r = sandbox.run_code('d = {"a": 1}; print(f"{d[chr(97)]}")')
    assert r.stdout.strip() == "1"


def test_timeout(monkeypatch):
    monkeypatch.setattr(sandbox, "TIMEOUT_SECONDS", 1)
    r = sandbox.run_code("import time; time.sleep(30)")
    assert r.timed_out and "timed out" in r.stderr


def test_matplotlib_figures_saved(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox, "PLOTS_DIR", tmp_path / "plots")
    r = sandbox.run_code(
        "import matplotlib.pyplot as plt\nplt.plot([1, 2, 3])\nplt.show()"
    )
    assert "[saved plot:" in r.stdout
    saved = list((tmp_path / "plots").glob("figure_*.png"))
    assert len(saved) == 1 and saved[0].stat().st_size > 0
