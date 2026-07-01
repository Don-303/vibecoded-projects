# quant-tutor

Offline, terminal-based interactive tutor for learning Python, machine
learning, econometrics, and quantitative finance. The tutor is a local LLM
(Qwen2.5-7B-Instruct via Ollama) — no internet connection needed once the
model is downloaded.

## Setup

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt

# pull the model once (needs internet the first time only)
OLLAMA_HOST=http://127.0.0.1:11434 ollama pull qwen2.5:7b-instruct
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
  capstone projects).
- Chat with the tutor in the "Tutor Chat" tab — it introduces the concept
  and gives you an exercise.
- Write code in the "Code Editor" tab, hit Run to execute it locally, then
  "Send output to tutor" to get it reviewed.
- Mark a lesson complete and move to the next one when ready.
- The "Progress" tab shows overall/per-module completion, total time spent,
  and your current/longest day streak. A live session timer sits next to
  the lesson header.

Progress, time spent per lesson, activity streaks, and per-lesson chat
history are stored locally in `data/progress.json` (not committed to git).
