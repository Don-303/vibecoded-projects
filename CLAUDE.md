# CLAUDE.md

## What this is

An offline, terminal-based interactive tutor for learning Python, ML,
econometrics, and quantitative finance. The tutor is a local LLM served by
Ollama — chosen per session from `agent.MODEL_CATALOG` via a launch-time
picker (default qwen3:4b-instruct; last choice persisted in
progress.json's `model` key). No network required after the models are
pulled.

## Run it

```
.venv/bin/python -m app.main
```

(Run from the project root so the `app` package resolves; add project root
to `PYTHONPATH` if invoking from elsewhere.)

Requires the Ollama service running locally with the model pulled:

```
OLLAMA_HOST=http://127.0.0.1:11434 ollama pull qwen3:4b-instruct
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
  CPU-latency measures that must not be regressed: `keep_alive=24h` +
  `warm_up()` at app start (avoids ~2 min cold loads), `num_thread=10`,
  `num_predict=640`, a total source budget of ~7000 chars split across a
  lesson's sources, and — most importantly — the per-lesson memoized
  prompt block (`_lesson_extra_block`): the system prompt must stay
  byte-identical across turns within a lesson so llama.cpp's prefix cache
  skips re-prefilling the conversation (measured: 81.5s → 1.7s to first
  token on the second message). Retrieval is therefore keyed on the lesson
  title only, never the user's message. Long chats are bounded by
  `_fit_history()` (12k/7k-char high/low watermarks): trims happen in
  chunks so the kept window stays byte-stable between trims -- a naive
  keep-the-last-N-that-fit would slide every turn and re-prefill each
  message; it also keeps the whole prompt inside `num_ctx` so Ollama never
  silently drops the lesson source. `agent.prewarm()` extends this to
  the *first* message too: opening a lesson with history fires a debounced
  background 1-token request that prefills the prompt cache, so the first
  real message also starts in ~2s (fresh lessons get the same effect from
  the auto-start generation).
- `app/skills.py` + `skills/` — teaching-skill playbooks in the
  Agent-Skills format (SKILL.md, from DeepTutor's EduHub registry, MIT-0).
  `agent.MODULE_SKILLS` maps modules to always-on skills
  (python-foundations → python-playground-coach; moquant-projects and
  capstone → quant-project-mentor, a staged project walkthrough written
  for this app that teaches the installed package stack — see the
  "Project" button's PROJECT_MESSAGE too); `agent.MODE_SKILLS` are
  the user-selectable tutor styles (socratic-tutor, concept-explainer),
  persisted per lesson in progress.json (`teaching_skill`). Skill bodies
  are appended to the system prompt via `SKILL_TEMPLATE`; skills.load() is
  cached so the prompt stays byte-identical (prefix-cache requirement).
  exam-blueprint and flashcard-deck shape the Quiz/Cards button messages
  in `app/main.py` (sent as user messages, so no cache invalidation).
- `app/rag.py` — the corpus registry (`SOURCES`), the local embedding index
  (`retrieve()`, backed by `nomic-embed-text` + a numpy cosine-similarity
  search over `data/rag_index/`), and full-lecture-text loading
  (`load_source_text()`) for sourced lessons.
- `scripts/build_rag_index.py` — one-time (re-runnable) setup: clones all
  corpora into `corpus/` (gitignored) and builds the embedding index.
  Needs internet only for this step.
- Tutor↔environment loop (`app/main.py`): typed questions attach the
  editor code when changed (`_with_editor_attached`, per-lesson snapshot
  dedupe); a reply containing a ```check fenced block (CHECK_BLOCK_RE,
  protocol defined in agent.SYSTEM_TEMPLATE) is executed in the student's
  kernel and the output sent back as an "[auto]" user turn, then answered
  with allow_check=False — exactly one round per student message.
- `app/kernel.py` — runs student code in a persistent IPython kernel
  (ipykernel/jupyter_client, pinned to this venv's interpreter): the
  namespace survives between runs, expression results echo like the REPL,
  timeouts interrupt (not kill) the kernel, matplotlib figures are saved
  to data/plots/ after each run, and restart() wipes state. Started
  lazily + warmed at app start; shut down on quit and via atexit.
  `list_variables()` inventories the user namespace (name/type/shape/
  value, via a marker-line JSON snippet) for the variable explorer.
- `app/notebook.py` — the cell-based notebook editor in the code panel
  (adapted from ~/Projects/quantlab's TUI): `NotebookEditor` is a
  focusable VerticalScroll of `Cell` widgets owning Jupyter's two-mode
  keymap (command mode on the panel: j/k, Enter, a/b, dd/z, x/c/v, m/y,
  o collapse, 00 restart, e/f tutor help; edit mode in `CellTextArea`:
  Esc, Shift+Enter, Ctrl+Enter). Cells execute via `kernel.run_code`
  with live streamed output, inline unicode-pixel plots (rich-pixels +
  Pillow, keyed off the kernel's "[saved plot: …]" stdout lines), In [n]
  gutters and per-cell run times. Persistence is a jupytext-style
  percent script (`cells_to_script`/`script_to_cells`; a single code
  cell stays a bare script, so old single-buffer saves round-trip) in
  progress.json's per-lesson `code` key. Talks to the app via messages:
  `CellRan` (updates `last_run_result`/`last_run_code` + vars refresh),
  `TutorHelp` (e explain / f fix → Ctrl+F flow), `RestartRequested`.
- Workbench extras in `app/main.py`: F2 toggles the variable-explorer
  DataTable (refreshed after every run, tutor check, and kernel restart
  — only when visible); Ctrl+F sends the last failed cell's code +
  traceback tail to the tutor as a coach-style debugging request; F5 /
  "Run all" runs every code cell top to bottom.
- `app/theming.py` — maps the desktop theme to a Textual theme (Omarchy
  theme.name → same-named/renamed builtin, light.mode marker or gsettings
  for light/dark fallback). Polled every 3s off-thread from `main.py`;
  only a detection *change* re-applies, so manual palette picks stick.
- `app/progress.py` — JSON-backed persistence (`data/progress.json`,
  gitignored) for lesson completion + per-lesson chat history. Also quiz
  scores (`quiz_scores`): QUIZ_MESSAGE instructs a final "Final score:
  N/5" line, `main.QUIZ_SCORE_RE` captures it best-effort, and a last
  score < 60% shortens that lesson's review interval from 7 to 3 days.
- `app/main.py` — the Textual TUI: sidebar lesson tree, chat panel, and
  the notebook panel side by side. Modal screens: ModelPicker (launch +
  Ctrl+T), HelpScreen (F1), ConfirmScreen (destructive actions). The
  `TabbedContent { height: 1fr }` CSS rule is load-bearing: without it the
  bottom action row overflows the screen on short terminals.
- Project metadata, pytest config, and ruff lint config live in
  `pyproject.toml` (version in `app/__init__.py`). `ruff check .` must
  stay clean.

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

11 modules, 78 lessons, covering the full breadth of what a quantitative
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
