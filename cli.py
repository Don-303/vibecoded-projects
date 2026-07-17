#!/usr/bin/env python3
"""
QuantLab CLI — launch the offline notebook editor + AI quant reviewer.

    quantlab                    # terminal UI (default) — no browser needed
    quantlab research.ipynb     # open a specific notebook in the TUI
    quantlab --desktop          # native GTK desktop window
    quantlab --browser          # plain HTTP server, opens your default browser
    quantlab -d ~/research      # point at a different notebook directory
    quantlab -m deepseek-coder  # use a different Ollama chat model
    quantlab --list             # list notebooks + bundled datasets
    quantlab research.ipynb --export html   # share results (also: py, md)

Env vars read by the server (settable here as flags, or directly):
QUANTLAB_NOTEBOOK_DIR, OLLAMA_URL, OLLAMA_MODEL, QUANTLAB_KERNEL,
QUANTLAB_EXEC_TIMEOUT. See README.md for the full list.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="quantlab",
        description="QuantLab — offline Jupyter (.ipynb) editor with a live "
                    "kernel and a local-LLM Quantitative Finance Reviewer.",
    )
    parser.add_argument(
        "notebook", nargs="?", default=None, metavar="NOTEBOOK",
        help="notebook to open in the TUI (relative to the notebook dir)",
    )
    parser.add_argument(
        "-b", "--browser", action="store_true",
        help="run as a plain HTTP server and open your default browser, "
             "instead of the terminal UI",
    )
    parser.add_argument(
        "-D", "--desktop", action="store_true",
        help="run in a native GTK desktop window instead of the terminal UI",
    )
    parser.add_argument(
        "--host", default="127.0.0.1",
        help="host to bind (default: 127.0.0.1)",
    )
    parser.add_argument(
        "-p", "--port", type=int, default=None,
        help="port to bind (default: 8899 in --browser mode, a random free "
             "port in desktop mode)",
    )
    parser.add_argument(
        "-d", "--notebook-dir", metavar="DIR", default=None,
        help="directory containing .ipynb files (default: ./notebooks next "
             "to the app)",
    )
    parser.add_argument(
        "-m", "--model", metavar="NAME", default=None,
        help="Ollama chat model to use, e.g. qwen2.5-coder:7b, deepseek-coder "
             "(default: $OLLAMA_MODEL or qwen2.5-coder:3b-instruct-q8_0)",
    )
    parser.add_argument(
        "--ollama-url", metavar="URL", default=None,
        help="Ollama endpoint (default: http://localhost:11434)",
    )
    parser.add_argument(
        "--kernel", metavar="NAME", default=None,
        help="Jupyter kernelspec name to launch (default: python3)",
    )
    parser.add_argument(
        "--timeout", type=int, default=None,
        help="per-cell execution timeout in seconds (default: 300)",
    )
    parser.add_argument(
        "--no-open", action="store_true",
        help="in --browser mode, don't auto-open a browser tab",
    )
    parser.add_argument(
        "--export", metavar="FMT", choices=["html", "py", "md"], default=None,
        help="export NOTEBOOK to html/py/md next to the .ipynb and exit",
    )
    parser.add_argument(
        "-l", "--list", action="store_true",
        help="list notebooks and bundled datasets, then exit",
    )
    parser.add_argument(
        "-v", "--version", action="store_true",
        help="print the QuantLab version and exit",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.version:
        print("QuantLab 1.4.1")
        return 0

    if args.notebook_dir:
        os.environ["QUANTLAB_NOTEBOOK_DIR"] = str(Path(args.notebook_dir).expanduser())
    if args.model:
        os.environ["OLLAMA_MODEL"] = args.model
    if args.ollama_url:
        os.environ["OLLAMA_URL"] = args.ollama_url
    if args.kernel:
        os.environ["QUANTLAB_KERNEL"] = args.kernel
    if args.timeout:
        os.environ["QUANTLAB_EXEC_TIMEOUT"] = str(args.timeout)

    # Run from the project directory regardless of the caller's cwd, so the
    # default ./notebooks and ./templates resolve correctly.
    os.chdir(BASE_DIR)
    sys.path.insert(0, str(BASE_DIR))

    if args.list:
        return _run_list()
    if args.export:
        return _run_export(args.notebook, args.export)
    if args.browser:
        return _run_browser(args.host, args.port or 8899, open_browser=not args.no_open)
    if args.desktop:
        return _run_desktop(args.host, args.port)
    return _run_tui(args.notebook)


def _run_browser(host: str, port: int, open_browser: bool) -> int:
    import threading
    import time
    import webbrowser

    import uvicorn

    from app import app  # noqa: PLC0415 - deferred so env vars above take effect

    if open_browser:
        def _open() -> None:
            time.sleep(1.2)
            webbrowser.open(f"http://{host}:{port}")

        threading.Thread(target=_open, daemon=True).start()

    uvicorn.run(app, host=host, port=port)
    return 0


def _run_desktop(host: str, port: int | None) -> int:
    import desktop  # noqa: PLC0415 - deferred so env vars above take effect

    desktop.main(host=host, port=port)
    return 0


def _run_tui(notebook: str | None) -> int:
    import tui  # noqa: PLC0415 - deferred so env vars above take effect

    tui.main(notebook)
    return 0


def _run_list() -> int:
    from core import NOTEBOOK_DIR, list_notebooks

    print(f"Notebooks in {NOTEBOOK_DIR}:")
    for path in list_notebooks() or ["  (none)"]:
        print(f"  {path}")
    try:
        from quantlab_data import available
        print("\nBundled offline datasets:")
        for key, desc in available().items():
            print(f"  {key:<13} {desc}")
    except Exception:
        pass
    return 0


def _run_export(notebook: str | None, fmt: str) -> int:
    if not notebook:
        print("usage: quantlab <notebook.ipynb> --export html|py|md")
        return 2
    import nbformat
    from core import NotebookPathError, safe_notebook_path

    try:
        src = safe_notebook_path(notebook)
    except NotebookPathError as exc:
        print(f"Invalid notebook path: {exc}")
        return 2
    if not src.exists():
        print(f"Notebook not found: {notebook}")
        return 1
    nb = nbformat.read(str(src), as_version=4)

    if fmt == "html":
        from nbconvert import HTMLExporter
        exporter = HTMLExporter()
    elif fmt == "py":
        from nbconvert import PythonExporter
        exporter = PythonExporter()
    else:
        from nbconvert import MarkdownExporter
        exporter = MarkdownExporter()
    body, _ = exporter.from_notebook_node(nb)
    out = src.with_suffix(f".{fmt}")
    out.write_text(body if isinstance(body, str) else body.decode())
    print(f"Exported {out}  ({out.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
