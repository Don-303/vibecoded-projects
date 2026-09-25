# terminus

A calm, keyboard-first day planner and focus timer for the terminal.
Local-first (SQLite), built on [Textual](https://textual.textualize.io/).

```
terminus      [1] Today            ✓ 1 done  ▶ 1 running  · 4 up next  ✗ 1 missed
              [2] Tasks      09:00 ┃ ✓ Standup           09:00–09:25  pinned
10:12         [3] Stats      09:40 ┃ ▶ Refactor          09:40–10:55  █████░░░ 43% · 43m left
streak 1 day  [4] AI Agent   10:12 ◀━━ now
focus  32m                   10:55 ┃ · Write design doc  10:55–11:45  up next
```

## Setup

```bash
cd ~/Projects/terminus
uv venv .venv && uv pip install --python .venv/bin/python -e ".[dev]"
.venv/bin/terminus            # or: .venv/bin/python -m terminus
```

Requires Python 3.11+.

## How it works

- **Tasks** are *what* to do: a title, tags, and an estimate in pomodoros.
- **The timeline** is *when*, and it is computed rather than stored. On every change and
  every minute, `terminus/scheduler.py` lays out the day again:
  - **Pinned** tasks (`@14:30`) stay at their time. They show as *running* during their
    window and as *missed* once it has passed.
  - **Floating** tasks queue from now, in list order, and move around pinned blocks.
    If you finish early or late, everything after shifts to match.
  - Floating tasks that don't fit before the day ends show as **overflow**. Unfinished
    floating tasks carry over to the next day automatically.
- **Focus sessions** (`f`) log real time against a task. Time already spent reduces the
  task's remaining block. A notification fires at the end of every pomodoro.

## Keys

| Key | Where | Action |
|---|---|---|
| `1`–`4` | anywhere | Today / Tasks / Stats / Agent |
| `n` | anywhere | New task form |
| `space` | anywhere | Toggle done: the selected task in Tasks, the running or up-next task in Today |
| `f` | anywhere | Start or pause focus on the same task |
| `/` | anywhere | Agent view |
| `q` | anywhere | Quit |
| `a` | Tasks | Quick-add bar |
| `s` | Tasks | Search (text, or `#tag`) |
| `o` | Tasks | Cycle sort: plan → title → estimate → tag |
| `e` / `d d` | Tasks | Edit / delete (press `d` twice) |
| `J` `K` / `shift+↑↓` | Tasks | Reorder (plan sort only) |
| `esc` | inputs | Back to the table |
| `ctrl+p` | anywhere | Command palette |

Quick-add syntax: `Write report #work #deep ~2 @14:30`. `#tag` adds a tag, `~N` sets the
estimate in pomodoros, and `@HH:MM` pins the task to a start time (`@9` and `@930` work too).

## Configuration

| Variable | Default | |
|---|---|---|
| `TERMINUS_DB` | `$XDG_DATA_HOME/terminus/terminus.db` (`~/.local/share/terminus/terminus.db`) | SQLite file |
| `TERMINUS_DAY_START` | `08:00` | Timeline start |
| `TERMINUS_DAY_END` | `20:00` | Timeline end; floating tasks that don't fit by then overflow |
| `TERMINUS_POMODORO_MIN` | `25` | Pomodoro length in minutes |

## Tests

```bash
.venv/bin/python -m pytest
```

The scheduler, database and quick-add parser have unit tests. The UI is tested
headlessly with Textual's Pilot, using an injectable fake clock.

## Roadmap

- **v2**: Stats view (weekly deep-work hours, streak history). Hermes agent chat over an
  OpenAI-compatible API (`HERMES_HOST`, `HERMES_API_KEY`), with streamed replies and an
  online/offline indicator.
- **v3**: The agent proposes structured changes (add, reschedule, split), and nothing is
  applied until you confirm.
