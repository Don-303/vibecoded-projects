# QuantLab

A completely offline, desktop-friendly **Jupyter (`.ipynb`) editor and live
execution environment** for Linux, with an integrated side-by-side **AI
Quantitative Finance Reviewer** powered by a local Ollama model.

- **Backend:** FastAPI + Jinja2 single-page app, `jupyter_client`
  (`MultiKernelManager`) driving a real local `ipykernel`, `nbformat` for
  structurally-safe `.ipynb` read/write.
- **Frontend:** one HTML page — Tailwind CSS, Monaco Editor per cell, rich
  output rendering (stdout/stderr, Pandas HTML tables, base64 Matplotlib PNGs).
- **AI:** streaming chat against `http://localhost:11434` with a hardcoded
  *Quantitative Finance Research Mentor* system prompt. Highlight-to-Ask-AI,
  "Apply to Cell" refactoring, and "Fix with AI" on failed executions.

Nothing leaves your machine.

---

## 1. Setup (Linux)

### 1.1 Python environment

Use **Python 3.10-3.13** — TensorFlow and `pandas-ta`'s `numba` dependency
don't have wheels for the newest Python yet, so a very new interpreter
(3.14+) will fail on those two even though everything else installs fine.
If your system `python3` is already too new, point at another one (e.g. via
`mise`, `pyenv`, or a distro package): `mise install python@3.12`.

```bash
cd ~/Projects/quantlab
python3.12 -m venv .venv     # or: python3 -m venv .venv, if that's 3.10-3.13
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install torch --index-url https://download.pytorch.org/whl/cpu
```

> `torch` is installed separately from the CPU-only index — without it,
> pip resolves a multi-GB CUDA build even on machines with no GPU.
> `pyportfolioopt` pulls in `cvxpy`, which may take a few minutes to build.

### 1.2 Register the kernel

The execution engine launches the `python3` kernelspec. Register the venv's
interpreter so cells run with the quant stack installed above:

```bash
.venv/bin/python -m ipykernel install --user --name python3 --display-name "QuantLab (Python 3)"
```

### 1.3 Vendor the frontend assets (one-time, needs network)

```bash
.venv/bin/python download_assets.py
```

This drops Tailwind, marked, and Monaco into `static/vendor/`. After this step
the app runs with **zero** network access. (Without the assets the app still
works, falling back to plain-text cell editors.)

### 1.4 Local AI model (Ollama)

```bash
# install Ollama natively on Linux if you haven't:
curl -fsSL https://ollama.com/install.sh | sh

ollama pull qwen2.5-coder:3b-instruct-q8_0   # fast default; or ollama pull deepseek-coder
ollama serve                  # usually already running as a systemd service
```

## 2. Run

### 2.1 Terminal UI — the default (no browser, no GUI)

```bash
quantlab                     # opens the first notebook in the notebook dir
quantlab research.ipynb      # open a specific notebook
```

A full Jupyter-style notebook editor in your terminal (Textual): syntax-
highlighted cell editors, live kernel execution, Pandas/text outputs, inline
Matplotlib plots rendered as unicode pixels, and the AI Quant Reviewer chat
in a side panel. Press `?` inside the app for all shortcuts:

| | |
|---|---|
| `↑`/`k` · `↓`/`j` | select cell |
| `Enter` / `Esc` | edit mode / command mode |
| `Shift+Enter` · `Ctrl+Enter` · `r` | run (+advance · stay · stay) |
| `a` / `b` · `dd` · `z` | insert above/below · delete · undo delete |
| `x` / `c` / `v` | cut / copy / paste cell |
| `m` / `y` | to markdown / to code |
| `00` · `ii` | restart · interrupt kernel |
| `Ctrl+S` · `F5` | save · run all |
| `e` / `f` | AI: explain cell / fix cell's error |
| `Ctrl+Y` | apply AI's last code block to selected cell |
| `Ctrl+E` | **agent step**: insert AI's code below, run it, report result back to the mentor |
| `o` / `O` | collapse output / full-screen output viewer (scrollable DataFrame table) |
| `/` | search across all cells, jump to match |
| `F2` | variable explorer (live kernel variables: name/type/shape/value) |
| `Ctrl+T` | switch AI model (lists your installed Ollama models) |
| `Ctrl+G` | new **strategy scaffold** (bundled data → signal → costed backtest → report) |
| `Ctrl+L` · `Ctrl+O` · `Ctrl+N` | focus chat · open · new notebook |

DataFrames render as aligned tables inline (press `O` for the full scrollable
view). `quantlab_stats` ships `metrics` / `report` / `tearsheet` for instant
backtest evaluation — the `Ctrl+G` scaffold wires it all together with
transaction costs and look-ahead-safe execution built in.

**Agentic research loop:** ask the mentor for something ("test a 6-month
reversal on the DAX"), then press `Ctrl+E` on its reply — QuantLab inserts the
code, runs it, and sends the result back so the mentor proposes the next step.
One keypress per iteration; you stay in control.

**Autonomous agent mode:** type `/auto <goal>` in the chat (or press `F6`) and
the mentor plans, writes cells, runs them, reads results, and iterates on its
own — up to 8 steps, deduplicating repeated code and stopping on DONE. Press
`F6` again to stop it. All cells it produced stay in the notebook for review.

**Live streaming output:** long-running cells (training loops, sweeps) stream
their stdout into the cell *while running* instead of dumping it at the end.

**Collapsible chat:** `F4` hides/shows the AI panel for full-width notebook
work; `Ctrl+L` brings it back and focuses the input.

**Per-notebook kernels:** each notebook gets its own kernel. `Ctrl+O` to
another notebook starts (or resumes) that notebook's interpreter — variables
never bleed between projects, and switching back restores your state.

**Export & inspect from the shell:**

```bash
quantlab --list                          # notebooks + bundled datasets
quantlab research.ipynb --export html    # shareable report (also: py, md)
```

Each cell shows its last run time (`✓ 1.96s`) under the output. The notebook
autosaves 30 s after the last change — disable with `QUANTLAB_AUTOSAVE=0`.

The AI mentor receives the **entire live notebook** (sources, execution
counts, outputs, errors) with every message — ask it about "cell 3" and it
answers from your actual code, no pasting needed. To have it read a file
that ISN'T in the notebook (a textbook script, a reference solution),
reference it directly in chat:

```
review @chapter4_momentum.py for look-ahead bias
```

Resolved against the notebook dir, then the project dir, then as given
(`~` and absolute paths work). The mentor gets the real file contents, not a
guess — works the same way in the terminal UI and the browser UI.

### 2.2 Native desktop window

```bash
quantlab --desktop
```

GTK/WebKit window via `pywebview` (needs system `webkit2gtk-4.1` +
`python-gobject` and a venv created with `--system-site-packages`). Also in
your app launcher as **QuantLab**.

### 2.3 In a browser

```bash
quantlab --browser           # serves on :8899 and opens your browser
```

All three modes start a live kernel and shut it down cleanly (no orphaned
`ipykernel` processes) on exit.

### Configuration (environment variables)

| Variable | Default | Purpose |
|---|---|---|
| `OLLAMA_URL` | `http://localhost:11434` | Ollama endpoint |
| `OLLAMA_MODEL` | `qwen2.5-coder:3b-instruct-q8_0` | chat model (e.g. `deepseek-coder`) |
| `OLLAMA_TEMPERATURE` | `0.3` | sampling temperature |
| `QUANTLAB_NOTEBOOK_DIR` | `./notebooks` | where `.ipynb` files live |
| `QUANTLAB_KERNEL` | `python3` | kernelspec name to launch |
| `QUANTLAB_EXEC_TIMEOUT` | `300` | per-cell timeout (seconds) |

Example:

```bash
OLLAMA_MODEL=deepseek-coder QUANTLAB_NOTEBOOK_DIR=~/research \
  .venv/bin/uvicorn app:app --port 8899
```

## 3. Using it

| Action | How |
|---|---|
| Run cell | ▶ button, or **Shift+Enter** (run + advance), **Ctrl+Enter** (run in place) |
| Save notebook | 💾 Save button or **Ctrl+S** |
| Add / delete / reorder / retype cells | per-cell toolbar (hover a cell) |
| Edit markdown cell | double-click the rendered text; Shift+Enter re-renders |
| Ask AI about a snippet | highlight code in a cell → floating **✨ Ask AI** button |
| Apply an AI suggestion | **⇤ Apply to Cell** button under any code block in the chat |
| Debug a failed cell | **🤖 Fix with AI** button inside the red error block |
| Restart / interrupt kernel | ↻ / ■ buttons in the header |

`notebooks/demo_quant_research.ipynb` ships as a fully offline demo — it
simulates GBM price paths and walks through vectorised momentum backtesting,
Sharpe Ratio, and Maximum Drawdown with Matplotlib plots. Run All and try the
reviewer on it.

## 4. Bundled offline market data

`data/` ships daily auto-adjusted OHLCV history for 8 major indices (~8 MB):
S&P 500 (back to **1927**), NASDAQ, Dow, Russell 2000, FTSE 100, DAX,
Nikkei 225 and the VIX. Use it from any notebook cell with zero network:

```python
from quantlab_data import available, load_index, closes

available()                       # what's bundled + date coverage
spx = load_index("sp500")         # OHLCV DataFrame, DatetimeIndex
px  = closes("sp500", "dax", start="2000-01-01")   # wide close panel
```

`notebooks/index_data_offline.ipynb` demonstrates cross-index momentum,
implied-vs-realised volatility, and a 7-index TSMOM basket — all offline.

Refresh the snapshot when you're online:

```bash
.venv/bin/python download_data.py            # all indices, max history
.venv/bin/python download_data.py sp500 vix  # just a subset
```

## 5. Tests

```bash
.venv/bin/pytest tests/          # full suite
.venv/bin/pytest tests/test_core.py tests/test_tui.py   # fast, no AI needed
```

`tests/test_ai.py` exercises the live Ollama chat and the autonomous agent;
those tests skip automatically when Ollama isn't running (and are slow —
minutes, not seconds — since they use the real local model).

## 6. Notes

- Files saved through the app are validated with `nbformat.validate()` before
  writing, so they always remain openable by JupyterLab/VS Code.
- One kernel serves the session (like a single Jupyter notebook); concurrent
  run requests are queued in order.
- Notebook paths are confined to `QUANTLAB_NOTEBOOK_DIR` — the API refuses
  path traversal.
