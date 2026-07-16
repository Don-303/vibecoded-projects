# quant-tutor

Offline, terminal-based interactive tutor for learning Python, machine
learning, econometrics, and quantitative finance. The tutor is a local LLM
(Qwen3-4B-Instruct via Ollama) — no internet connection needed once the
model is downloaded.

## Setup

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt

# pull the model once (needs internet the first time only)
OLLAMA_HOST=http://127.0.0.1:11434 ollama pull qwen3:4b-instruct
```

## Run

```bash
.venv/bin/python -m app.main
```

Or launch it from your desktop's application menu as **Quant Tutor** — a
`.desktop` entry is installed at
`~/.local/share/applications/quant-tutor.desktop`, which runs `bin/quant-tutor`
in a terminal. That script checks the venv and local Ollama are ready before
starting the app.

## How it works

- Pick a lesson from the tree on the left (curriculum runs Python/NumPy/
  Pandas → statistics → ML foundations → econometrics → ML-for-finance →
  capstone projects). Modules collapse/expand; only the one you're studying
  starts open. Ctrl+B hides the sidebar entirely.
- The workspace shows the tutor chat and the code editor side by side
  (Ctrl+E hides/shows the editor pane). Opening a fresh lesson auto-starts
  it: the tutor introduces the concept and gives you an exercise.
- Beginner Python lessons use a playground-style loop: a tiny runnable
  example, a "what do you think this prints?" prediction, then a small
  challenge. Everywhere, hints escalate gradually (concept → line →
  solution shape → code) and errors are debugged one at a time.
- Chat action buttons: "Quiz me", "Practice exercise" (skeleton code with
  TODOs), "Worked example" (complete runnable code), and "Code → editor"
  (drops the tutor's last code block into the editor).
- Run code with Ctrl+R (or the Run button), then "Send output to tutor" to
  get it reviewed. Matplotlib figures are saved to `data/plots/` (the path
  is printed in the output pane). A Stop button cancels a slow reply.
- Your editor code is saved per lesson and restored when you come back.
- Mark a lesson complete and move to the next one (Ctrl+N) when ready.
- The "Progress" tab shows overall/per-module completion, total time spent,
  and your current/longest day streak. A live session timer sits next to
  the lesson header.
- Spaced repetition: a week after you complete a lesson it comes due for
  review (↻ in the tree and a "Review due" list in Progress); hitting
  "Quiz me" on it counts as a review and resets the clock.

Progress, time spent per lesson, activity streaks, saved code, and chat
history are stored locally in `data/progress.json` (not committed to git).

## Tests

```bash
.venv/bin/pytest
```

## Grounding the tutor in real lecture material

Two complementary mechanisms pull the tutor's answers away from pure
model-memory and toward real reference material:

1. **Sourced lessons.** Many lessons in `curriculum/syllabus.yaml` have a
   `sources:` list pointing at a specific real lecture file (e.g. the
   `fin-capm` lesson is backed by QuantRocket's
   `Lecture30-CAPM-and-Arbitrage-Pricing-Theory.ipynb`). When such a lesson
   is active, the tutor is given that lecture's full text and is instructed
   to walk through it with you interactively — explain a section, check
   understanding with a small question/exercise, then move on — citing the
   source by name, rather than freelancing its own generic explanation.
2. **Fuzzy retrieval fallback.** For lessons without a direct source
   mapping, the tutor instead retrieves the most relevant chunks from the
   full corpus (via local embeddings) and cites them as reference material,
   falling back to its own knowledge if nothing relevant is found.

### Corpus sources

- [QuantRocket's 45-lecture quant finance series](https://github.com/quantrocket-codeload/quant-finance-lectures)
  (stats, regression, CAPM, factor models, PCA, VaR/CVaR, cointegration,
  GARCH, Kalman filters, pairs trading, market impact models, and more)
- [QuantEcon's notebook gallery](https://github.com/QuantEcon/notebook-gallery)
  (community-submitted computational economics/finance notebooks)
- Six QuantEcon lecture series (`lecture-python-intro`,
  `lecture-python-programming`, `lecture-python.myst`,
  `lecture-python-advanced.myst`, `lecture-datascience.myst`,
  `continuous_time_mcs`)
- Ten [MoQuant](https://github.com/MoQuant) applied-project repos (options
  pricing, put-call parity, GARCH, VaR simulation, PCA, cointegration/stat-arb,
  PyTorch price & volatility forecasters, portfolio analysis) — these back
  the "Applied Projects (MoQuant)" curriculum module
- Your MSc CF969 (Machine Learning in Finance) lab/case-study notebooks,
  copied from the local Google Drive mirror by
  `scripts/add_local_corpus.py` (personal material, never committed)

### One-time setup (needs internet)

```bash
OLLAMA_HOST=http://127.0.0.1:11434 ollama pull nomic-embed-text
.venv/bin/python scripts/build_rag_index.py
```

This clones all corpora into `corpus/` (gitignored) and builds a local
embedding index at `data/rag_index/` (gitignored). Re-run it any time to
pull updates. Everything after this step runs fully offline. This is a
CPU-only, one-time cost — expect it to take a while (the full corpus is
several thousand chunks) depending on your hardware; sourced lessons work
immediately after cloning even before the embedding index finishes.
