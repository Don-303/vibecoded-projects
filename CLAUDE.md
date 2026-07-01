# CLAUDE.md

## What this is

An offline, terminal-based interactive tutor for learning Python, ML,
econometrics, and quantitative finance. The tutor is a local LLM
(Qwen2.5-Coder-7B) served by Ollama — no network required after the model
is pulled.

## Run it

```
.venv/bin/python -m app.main
```

(Run from the project root so the `app` package resolves; add project root
to `PYTHONPATH` if invoking from elsewhere.)

Requires the Ollama service running locally with the model pulled:

```
OLLAMA_HOST=http://127.0.0.1:11434 ollama pull qwen2.5-coder:7b
```

Note: this machine's shell sets `OLLAMA_HOST` to a remote Tailscale peer.
`app/agent.py` hardcodes `http://127.0.0.1:11434` so this app always talks
to the local instance regardless of that env var.

## Architecture

- `curriculum/syllabus.yaml` — ordered modules/lessons (topics only, not
  full lesson text — the tutor generates explanations/exercises live).
- `app/curriculum.py` — loads the syllabus.
- `app/agent.py` — wraps the Ollama chat API, builds a per-lesson system
  prompt, streams tokens.
- `app/sandbox.py` — runs student code in a subprocess (using this venv's
  interpreter) with a timeout, capturing stdout/stderr.
- `app/progress.py` — JSON-backed persistence (`data/progress.json`,
  gitignored) for lesson completion + per-lesson chat history.
- `app/main.py` — the Textual TUI: sidebar lesson tree, chat tab, code
  editor tab with run/send-to-tutor.

## Curriculum order

9 modules, 41 lessons, covering the full breadth of what a quantitative
analyst needs:

1. Python foundations (core/NumPy/pandas/viz)
2. Math foundations (linear algebra, probability/stochastic processes,
   optimization)
3. Statistics & probability
4. ML foundations (through deep learning for sequences + NLP for alt-data)
5. Time series & econometrics (ARIMA, cointegration, GARCH, factor models)
6. Financial theory (MPT/CAPM, derivatives pricing, fixed income, market
   microstructure, risk management)
7. ML applied to finance (features, lookahead bias, walk-forward validation)
8. Engineering practices (SQL/large datasets, backtesting discipline,
   reproducibility/production)
9. Capstone projects tied to real strategies (momentum, volatility
   forecasting, pairs trading, options pricing, portfolio risk models)
