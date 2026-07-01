# quant-tutor

Offline, terminal-based interactive tutor for learning Python, machine
learning, econometrics, and quantitative finance. The tutor is a local LLM
(Qwen2.5-Coder-7B via Ollama) — no internet connection needed once the
model is downloaded.

## Setup

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt

# pull the model once (needs internet the first time only)
OLLAMA_HOST=http://127.0.0.1:11434 ollama pull qwen2.5-coder:7b
```

## Run

```bash
.venv/bin/python -m app.main
```

## How it works

- Pick a lesson from the tree on the left (curriculum runs Python/NumPy/
  Pandas → statistics → ML foundations → econometrics → ML-for-finance →
  capstone projects).
- Chat with the tutor in the "Tutor Chat" tab — it introduces the concept
  and gives you an exercise.
- Write code in the "Code Editor" tab, hit Run to execute it locally, then
  "Send output to tutor" to get it reviewed.
- Mark a lesson complete and move to the next one when ready.

Progress and per-lesson chat history are stored locally in
`data/progress.json` (not committed to git).
