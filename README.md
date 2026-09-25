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
  - Floating tasks that don't fit before the day ends show as **overflow**.
  - Unfinished tasks from past days (including overdue Todoist tasks) and Todoist tasks
    with no date go in an **Overdue** list at the bottom of Today instead of on the timeline. Select one in Tasks and press
    `t` to put it on today's plan, which also makes it due today in Todoist. Pinning it
    to a time does the same.
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
| Add a task with no date | Goes in the Overdue list on today, marked "no date" |
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

## Stats

Press `3` to see the last 7 days: focus time per day (with the change from the previous
week), tasks completed per day, your current and longest streak, and focus time per tag.
Free focus has its own bar.

## AI agent (Hermes on another machine)

Press `4` or `/` to chat with a [Hermes Agent](https://hermes-agent.nousresearch.com/)
running on another machine. Each message you send includes today's plan (the timeline,
overdue and undated tasks, and your focus stats), so you can ask "what should I do next?"
or "plan my afternoon". Replies stream in as Markdown, and a status line shows what the
agent is doing while it works. The agent can't change your tasks yet; it tells you which
keys to press.

**On the Hermes machine**, in `~/.hermes/.env`:
```
API_SERVER_ENABLED=true
API_SERVER_HOST=0.0.0.0          # listen beyond localhost
API_SERVER_KEY=<long random secret>
```
Then run `hermes gateway`. It listens on port 8642.

**Here**, in `~/.config/terminus/config.toml`:
```toml
[hermes]
host = "100.64.0.10"   # Tailscale IP or hostname; port 8642 is assumed
api_key = "<the same secret>"
# model = "hermes-agent" # or your Hermes profile name
```
Connect over Tailscale, which encrypts traffic between your devices. Hermes can run
terminal commands on its machine, so don't expose port 8642 to the open internet. The
sidebar shows `hermes ● online` or `● offline`, based on a check of `/health` every
minute.

## Morning brief (`5`)

On the first launch each day after 06:00, Terminus prepares a brief with three sections:
- **Today:** your plan, and what to start with.
- **Email:** the Gmail messages from the last day (Primary tab) that need attention.
- **Markets & finance:** headlines from CNBC, the FT and Investing.com, with each feed
  getting a fair share.

Hermes writes it up as a short summary, streamed onto the page. Without Hermes you get
the raw digest instead. A "☀ Morning brief ready" line on Today stays until you open the
brief. Press `g` on the Brief page to make it again.

**Gmail is read-only.** Terminus opens your inbox with IMAP `EXAMINE` and reads messages
with `BODY.PEEK`, so it can't send, delete, move or even mark anything as read. It signs
in with a Google **app password**, not your real password:

1. Turn on 2-Step Verification for your Google account, if it isn't on already.
2. Go to <https://myaccount.google.com/apppasswords>, create one called "Terminus",
   and copy the 16 characters.
3. Add it to `~/.config/terminus/config.toml` (keep the file `chmod 600`):
   ```toml
   [gmail]
   address = "you@gmail.com"
   app_password = "abcd efgh ijkl mnop"
   # query = "newer_than:1d category:primary"   # any Gmail search

   [brief]
   # time = "06:00"                 # "" = only when you press g
   # [brief.feeds]                  # replace the default finance feeds
   # "FT Markets" = "https://www.ft.com/markets?format=rss"
   ```

Emails and headlines go to Hermes marked as untrusted data, and it's told never to follow
instructions inside them. Hermes can still run commands on its machine, so consider a
Hermes profile with the terminal tool turned off for the brief (set `model = "<profile>"`
under `[hermes]`).

## Keys

| Key | Where | Action |
|---|---|---|
| `1`–`4` | anywhere | Today / Tasks / Stats / Agent |
| `n` | anywhere | New task form |
| `space` | anywhere | Toggle done: the selected task in Tasks, the running or up-next task in Today |
| `f` | anywhere | Start or pause focus on the selected or next task. With no tasks, starts free focus |
| `F` | anywhere | Start or pause free focus (not tied to any task) |
| `/` | anywhere | Agent view, cursor in the chat box |
| `ctrl+l` | Agent | Start a new conversation |
| `5` | anywhere | Morning brief |
| `g` | Brief | Make the brief again |
| `r` | anywhere | Sync with Todoist now (only when a token is set) |
| `q` | anywhere | Quit |
| `a` | Tasks | Quick-add bar |
| `s` | Tasks | Search (text, or `#tag`) |
| `o` | Tasks | Cycle sort: plan → title → estimate → tag |
| `e` / `d d` | Tasks | Edit / delete (press `d` twice) |
| `t` | Tasks | Move an overdue task onto today's plan |
| `J` `K` / `shift+↑↓` | Tasks | Reorder (plan sort only) |
| `esc` | inputs | Back to the table |
| `ctrl+p` | anywhere | Command palette |

Quick-add syntax: `Write report #work #deep ~2 @14:30`. `#tag` adds a tag, `~N` sets the
estimate in pomodoros, and `@HH:MM` pins the task to a start time (`@9` and `@930` work too).

## Configuration

Settings come from `~/.config/terminus/config.toml`, and the environment variables below
override it:

```toml
[planner]
day_start = "04:00"
day_end = "22:00"
# pomodoro_minutes = 25

[todoist]
project = "Inbox"
```

| Variable | Default | |
|---|---|---|
| `TERMINUS_DB` | `$XDG_DATA_HOME/terminus/terminus.db` (`~/.local/share/terminus/terminus.db`) | SQLite file |
| `TERMINUS_DAY_START` | `08:00` | Timeline start |
| `TERMINUS_DAY_END` | `20:00` | Timeline end; floating tasks that don't fit by then overflow |
| `TERMINUS_POMODORO_MIN` | `25` | Pomodoro length in minutes |
| `TODOIST_API_TOKEN` | contents of `~/.config/terminus/todoist-token` | Turns on Todoist sync |
| `TODOIST_PROJECT` | `Terminus` | Project to sync (`Inbox` works too) |
| `TODOIST_SYNC_SECONDS` | `60` | Seconds between background syncs (minimum 15) |
| `HERMES_HOST` | `127.0.0.1` | Hermes machine (IP, hostname or URL; port 8642 assumed) |
| `HERMES_API_KEY` | none | Hermes `API_SERVER_KEY`; without it the chat shows setup help |
| `HERMES_MODEL` | `hermes-agent` | Model name sent to Hermes (your Hermes profile name) |
| `GMAIL_ADDRESS` / `GMAIL_APP_PASSWORD` | none | Gmail for the morning brief (read-only IMAP) |

## Tests

```bash
.venv/bin/python -m pytest
```

The scheduler, database, quick-add parser and Todoist sync have unit tests. The sync is
tested against an in-memory fake of Todoist's Sync API (`tests/fake_todoist.py`), so
tests never touch your real account. The UI is tested
headlessly with Textual's Pilot, using an injectable fake clock.

## Roadmap

- **v2** (done): the Stats view, and Hermes agent chat with streamed replies and an
  online/offline indicator.
- **v3**: The agent proposes structured changes (add, reschedule, split), and nothing is
  applied until you confirm.
