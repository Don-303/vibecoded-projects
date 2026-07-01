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
  full lesson text). Some lessons have a `sources:` list ({repo, file})
  pointing at a real lecture file in `corpus/` -- see "Grounding" below.
- `app/curriculum.py` — loads the syllabus; `Lesson.sources` is a list of
  `SourceRef(repo, file)`.
- `app/agent.py` — wraps the Ollama chat API, builds a per-lesson system
  prompt, streams tokens. If the lesson has `sources`, injects their full
  text and instructs the model to walk through it interactively (see
  `SOURCE_LESSON_TEMPLATE`); otherwise falls back to fuzzy retrieval via
  `app/rag.py` (see `REFERENCE_TEMPLATE`). Sets `num_ctx=8192` explicitly
  (Ollama's runtime default is much smaller than the model's 32k max).
- `app/rag.py` — the corpus registry (`SOURCES`), the local embedding index
  (`retrieve()`, backed by `nomic-embed-text` + a numpy cosine-similarity
  search over `data/rag_index/`), and full-lecture-text loading
  (`load_source_text()`) for sourced lessons.
- `scripts/build_rag_index.py` — one-time (re-runnable) setup: clones all
  corpora into `corpus/` (gitignored) and builds the embedding index.
  Needs internet only for this step.
- `app/sandbox.py` — runs student code in a subprocess (using this venv's
  interpreter) with a timeout, capturing stdout/stderr.
- `app/progress.py` — JSON-backed persistence (`data/progress.json`,
  gitignored) for lesson completion + per-lesson chat history.
- `app/main.py` — the Textual TUI: sidebar lesson tree, chat tab, code
  editor tab with run/send-to-tutor.

## Grounding the tutor in real lecture material

See README.md's "Grounding the tutor in real lecture material" section for
the user-facing explanation. Implementation notes:

- `agent._build_source_lesson_block()` takes priority over
  `agent._build_reference_block()` (retrieval) whenever `lesson.sources` is
  non-empty and at least one file is found in `corpus/`.
- `rag.load_source_text()` caps each source at 3500 chars (keeps 2-3 sources
  comfortably within the 8192-token context budget alongside conversation
  history); it strips notebook boilerplate implicitly by just taking cells
  in order, so the QuantRocket copyright/nav header at the top of every
  lecture eats a small, fixed amount of that budget.
- Mapping QuantRocket's 45-lecture series onto lesson ids was done by hand
  in `syllabus.yaml` (see git history) -- most lessons in
  math-foundations/stats-foundations/econometrics/financial-theory have a
  direct 1:1 or 1:2 mapping; ml-foundations/neural-networks/ml-finance
  mostly don't (those came from different MSc coursework, not this corpus)
  and rely on retrieval + the model's own knowledge instead.

## Curriculum order

10 modules, 65 lessons, covering the full breadth of what a quantitative
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
