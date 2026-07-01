# CLAUDE.md

## What this is

An offline, terminal-based interactive tutor for learning Python, ML,
econometrics, and quantitative finance. The tutor is a local LLM
(Qwen2.5-7B-Instruct) served by Ollama — no network required after the model
is pulled.

## Run it

```
.venv/bin/python -m app.main
```

(Run from the project root so the `app` package resolves; add project root
to `PYTHONPATH` if invoking from elsewhere.)

Requires the Ollama service running locally with the model pulled:

```
OLLAMA_HOST=http://127.0.0.1:11434 ollama pull qwen2.5:7b-instruct
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

10 modules, 62 lessons, covering the full breadth of what a quantitative
analyst needs. Several modules were shaped directly off real MSc Financial
Engineering coursework (CE889 Neural Networks, CE802 Machine Learning,
CF969 Machine Learning in Finance — see
`~/GoogleDrive/MSc Financial Engineering/Courses`), plus a few concepts
borrowed from QuantConnect's algorithm framework (universe selection →
alpha → portfolio construction → risk → execution; realistic fill/slippage
modeling):

1. Python foundations (core/NumPy/pandas/viz)
2. Math foundations (linear algebra, probability/stochastic processes,
   optimization)
3. Statistics & probability
4. ML foundations — classical ML from CE802: bias-variance, Bayesian
   learning, k-NN, SVM, clustering, ensembles, evaluation, reinforcement
   learning
5. Neural Networks & Deep Learning (new dedicated module, from CE889 +
   CF969): gradient descent, perceptron/XOR, MLP/backprop, regularization,
   CNNs, RNN/LSTM, transformers, SOM/ART
6. Time series & econometrics (ARIMA, cointegration, GARCH, factor models)
7. Financial theory (MPT/CAPM, portfolio construction models, derivatives
   pricing, fixed income, market microstructure, risk management)
8. ML applied to finance (features, lookahead bias, LSTM fusion of
   technical/fundamental data, NLP for alt-data, ML-based trade execution,
   walk-forward validation, backtesting)
9. Engineering practices (SQL/large datasets, algorithm framework design,
   realistic fill/slippage/fee models, backtesting discipline,
   reproducibility/production)
10. Capstone projects tied to real strategies (momentum, volatility
    forecasting, pairs trading, options pricing, portfolio risk models,
    LSTM fusion model, QuantConnect-style algorithm framework)
