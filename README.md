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
  Free focus (`F`, or `f` with no tasks) logs time without a task, and still counts toward
  focus time and streaks. Deleting a task keeps its logged time.

## Todoist sync (add and remove tasks from your phone)

Terminus syncs two ways with **one Todoist project**: `Terminus` by default (created on the
first sync if it doesn't exist), or your `Inbox`, so anything you quick-add on your phone
shows up. Choose it in `~/.config/terminus/config.toml`:

```toml
[todoist]
project = "Inbox"
# sync_seconds = 60
```

When you switch projects, the next sync pulls the new project in full.

1. Get your API token: Todoist → Settings → Integrations → Developer → *API token*.
2. Save it where Terminus can read it:
   ```bash
   mkdir -p ~/.config/terminus
   echo 'YOUR_TOKEN' > ~/.config/terminus/todoist-token && chmod 600 ~/.config/terminus/todoist-token
   ```
   Or export `TODOIST_API_TOKEN` instead.
3. Start Terminus. The sidebar shows `todoist ✓ HH:MM` after each successful sync.

| On your phone (in the Terminus project) | In Terminus |
|---|---|
| Add a task with no date | Shows on today |
| Give it a date / a time | Goes on that day / is pinned at that time |
| Overdue task | Shows on today (the old time is dropped) |
| Labels | Tags |
| `~2` at the end of the title (or a duration, which may need Todoist Pro) | Estimate in pomodoros |
| Complete / uncomplete / delete | Done / reopened / removed |
| Move it to another project | Removed |

Tasks you add, edit, complete or delete in Terminus go to Todoist straight away, due on
their day. Terminus syncs on startup, every 60 seconds, after every change, and when
you press `r`. Changes made offline wait in a queue in the database and go out on the
next successful sync. When a task was changed in both places, the Terminus edit wins.

Not synced: task order within the day, focus sessions, and the move of unfinished
tasks to the next day (Todoist already shows those as overdue).

## Keys

| Key | Where | Action |
|---|---|---|
| `1`–`4` | anywhere | Today / Tasks / Stats / Agent |
| `n` | anywhere | New task form |
| `space` | anywhere | Toggle done: the selected task in Tasks, the running or up-next task in Today |
| `f` | anywhere | Start or pause focus on the selected or next task. With no tasks, starts free focus |
| `F` | anywhere | Start or pause free focus (not tied to any task) |
| `/` | anywhere | Agent view |
| `r` | anywhere | Sync with Todoist now (only when a token is set) |
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

Settings come from `~/.config/terminus/config.toml` (the `[todoist]` table), and the
environment variables below override it.

| Variable | Default | |
|---|---|---|
| `TERMINUS_DB` | `$XDG_DATA_HOME/terminus/terminus.db` (`~/.local/share/terminus/terminus.db`) | SQLite file |
| `TERMINUS_DAY_START` | `08:00` | Timeline start |
| `TERMINUS_DAY_END` | `20:00` | Timeline end; floating tasks that don't fit by then overflow |
| `TERMINUS_POMODORO_MIN` | `25` | Pomodoro length in minutes |
| `TODOIST_API_TOKEN` | contents of `~/.config/terminus/todoist-token` | Turns on Todoist sync |
| `TODOIST_PROJECT` | `Terminus` | Project to sync (`Inbox` works too) |
| `TODOIST_SYNC_SECONDS` | `60` | Seconds between background syncs (minimum 15) |

## Tests

```bash
.venv/bin/python -m pytest
```

The scheduler, database, quick-add parser and Todoist sync have unit tests. The sync is
tested against an in-memory fake of Todoist's Sync API (`tests/fake_todoist.py`), so
tests never touch your real account. The UI is tested
headlessly with Textual's Pilot, using an injectable fake clock.

## Roadmap

- **v2**: Stats view (weekly deep-work hours, streak history). Hermes agent chat over an
  OpenAI-compatible API (`HERMES_HOST`, `HERMES_API_KEY`), with streamed replies and an
  online/offline indicator.
- **v3**: The agent proposes structured changes (add, reschedule, split), and nothing is
  applied until you confirm.
