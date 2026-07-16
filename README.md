# quant-tutor

Offline, terminal-based interactive tutor for learning Python, machine
learning, econometrics, and quantitative finance. The tutor is a local LLM
served by Ollama — no internet connection needed once a model is
downloaded. At launch you pick the model for the session from a small
catalog (Qwen3-4B-Instruct is the recommended default; a Q8 coder model,
Phi-4-mini, and others are listed with one-line descriptions and install
status); the choice is remembered for next time.

## Setup

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt

# pull at least the default model once (needs internet the first time only);
# the launch picker shows the full catalog and how to pull the others
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
  starts open. The filter box above the tree narrows it as you type
  (try "garch"). Ctrl+B hides the sidebar entirely.
- Ctrl+T switches the model mid-session (same picker as at launch; the
  new model is warmed and the lesson prompt prefilled in the background).
- The workspace shows the tutor chat and the code editor side by side
  (Ctrl+E hides/shows the editor pane). Opening a fresh lesson auto-starts
  it: the tutor introduces the concept and gives you an exercise.
- Beginner Python lessons use a playground-style loop: a tiny runnable
  example, a "what do you think this prints?" prediction, then a small
  challenge. Everywhere, hints escalate gradually (concept → line →
  solution shape → code) and errors are debugged one at a time.
- Chat action buttons: "Quiz me" (exam-blueprint style: mixed question
  types across sub-topics with a difficulty spread, one at a time, graded
  as you go), "Practice" (skeleton code with TODOs), "Example" (complete
  runnable code), "Cards" (an atomic Q/A + cloze flashcard deck from the
  lesson), and "Code → editor" (drops the tutor's last code block into
  the editor).
- The app follows your desktop theme, live: if your system theme has a
  Textual counterpart (nord, gruvbox, tokyo-night, catppuccin, …) it's
  used directly, otherwise the app just matches light/dark. Switching the
  system theme restyles the tutor within a few seconds; a theme picked
  manually in the command palette sticks until the system theme changes.
- Each reply ends with a dim speed line (time to first token · tok/s), so
  you can tell a cold prompt from a warm one. Very long lesson chats are
  trimmed automatically (oldest turns first) so the tutor never loses the
  lesson source material to context overflow.
- A per-lesson tutor style selector sits in the header: **standard**,
  **Socratic** (leads you to answers with guiding questions and a hint
  ladder), or **explainer** (layered explanations: one-liner → why →
  analogy → worked example → check). The choice is remembered per lesson.
- The editor runs on a persistent IPython kernel: run with Ctrl+R (or the
  Run button) and variables survive between runs — build up your analysis
  incrementally like a notebook. Selecting lines runs just the selection;
  a bare expression echoes its value like the REPL; errors come back as
  real tracebacks. Output streams into the pane live as it's printed
  (training loops show progress per epoch), ending with a run-time line.
  "Restart" wipes the kernel's variables. Then "Send output to tutor" to
  get the run reviewed. Matplotlib figures are saved to `data/plots/`
  (the "Plots" button opens the folder in your image viewer). A Stop
  button cancels a slow tutor reply.
- The tutor interacts with your environment: questions you type
  automatically include your editor code (when it changed since the tutor
  last saw it), and the tutor can run a small inspection snippet in your
  live kernel — checking shapes, dtypes, or asserting on your variables —
  with the output fed back to it automatically, so "verify my solution"
  is checked against reality rather than guessed at. One check per
  message, always visible in the chat.
- Your editor code is saved per lesson and restored when you come back.
- Mark a lesson complete and move to the next one (Ctrl+N) when ready.
  The app reopens on whatever lesson you had open last session, and
  Ctrl+D jumps straight to the next lesson due for review.
- The "Progress" tab shows overall/per-module completion, total time spent,
  and your current/longest day streak. A live session timer sits next to
  the lesson header.
- Spaced repetition with mastery tracking: a week after you complete a
  lesson it comes due for review (↻ in the tree and a "Review due" list in
  Progress); hitting "Quiz me" on it counts as a review and resets the
  clock. Quizzes end with a score line the app records — score under 60%
  and the lesson comes back for review in 3 days instead of 7. The
  Progress tab shows quizzes taken and your average score.

Progress, time spent per lesson, activity streaks, saved code, and chat
history are stored locally in `data/progress.json` (not committed to git).

Press **F1** in the app for the full list of shortcuts and chat actions.

## Development

```bash
.venv/bin/pytest              # test suite
.venv/bin/ruff check .        # lint (config in pyproject.toml)
```

## Teaching skills (DeepTutor EduHub)

The tutor's teaching methods are packaged as skills in the open
Agent-Skills format (`skills/<name>/SKILL.md` — YAML frontmatter plus a
markdown playbook), the same format used by
[DeepTutor's EduHub registry](https://eduhub.deeptutor.info/skills), where
these came from (MIT-0 licensed):

- **python-playground-coach** — applied automatically to every
  Python-foundations lesson (tiny goals, predict-what-it-prints, small
  challenges).
- **quant-project-mentor** (written for this app) — applied automatically
  to the MoQuant and capstone project modules, and behind the "Project"
  button everywhere: suggests three projects sized to your level, then
  walks the chosen one through five stages (data → exploration → model →
  evaluation → report), introducing each package of the quant stack
  (numpy, pandas, matplotlib, scipy, statsmodels, scikit-learn, arch) as
  the project needs it.
- **socratic-tutor** / **concept-explainer** — the header's tutor-style
  selector.
- **exam-blueprint** / **flashcard-deck** — power the "Quiz me" and
  "Cards" buttons.

Drop any other SKILL.md folder into `skills/` and it's loadable by name
(`app/skills.py`); wire it to a module in `agent.MODULE_SKILLS` or to the
style selector in `agent.MODE_SKILLS`.

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
