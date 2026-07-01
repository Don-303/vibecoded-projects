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

Python/ML foundations first, then statistics/econometrics, then ML applied
to finance, then capstone projects tied to real strategies (momentum,
volatility forecasting, pairs trading).
