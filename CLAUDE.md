# CLAUDE.md

Terminus: a Textual TUI day planner and focus timer. User-facing docs are in README.md.

## Commands

```bash
.venv/bin/python -m pytest          # all tests (~6s)
.venv/bin/terminus                  # run the app
```

## Architecture

- `terminus/scheduler.py`: `plan_day()` is a **pure** function (tasks + worked time +
  now → `DayPlan`). All timeline and reflow logic lives here. Blocks are never stored.
  The app re-plans after every mutation and once a minute.
- `terminus/db.py`: stdlib `sqlite3`. Migrations are the `MIGRATIONS` list, applied via
  `PRAGMA user_version`; to change the schema, append to the list. Datetimes are naive
  local ISO strings. The tables are `tasks` (with `fixed_start` NULL = floating) and
  `sessions` (with `end` NULL = running; at most one runs at a time).
- `terminus/app.py`: `TerminusApp` owns the DB. The clock is injectable (`clock=`) so
  tests can drive time. `refresh_all()` re-reads the DB, re-plans and redraws. `_tick`
  runs every second for the live timer, the minute re-plan and midnight carry-over.
- `terminus/todoist.py`: two-way sync with one Todoist project via Sync API v1
  (`POST /api/v1/sync`). Local changes are queued in `todoist_outbox`, and commands carry
  stable uuids, so a retried batch is de-duplicated by Todoist. Temp ids map to real ids
  through `temp_id_mapping`. Remote items that still have a pending local command are
  skipped, so the local edit wins. The app calls `self._pushed(TodoistSync.queue_*, ...)`
  after every mutation. **Any new mutation in the app must do the same.** Tests use
  `tests/fake_todoist.py` (httpx MockTransport) and never reach the network.
- Overdue: `tasks.overdue_from` is set by `carry_over` and by Todoist items whose due
  date has passed. `tasks.no_due` is set for undated Todoist items. `Task.backlogged`
  covers both. `plan_day` lists these in `DayPlan.overdue` rather than placing them;
  `action_do_today` clears the flag and pushes due=today.
- Config: `~/.config/terminus/config.toml` (`[planner]`, `[todoist]`, `[hermes]`); env vars
  override it.
- `terminus/agent_client.py`: `HermesClient` for the OpenAI-compatible Hermes gateway (SSE
  streaming; named events become "tool" events; `/health` needs no auth). History is kept
  client-side in `TerminusApp._chat`, and a fresh system message from `build_context()` is
  sent every turn. Tests use `tests/fake_hermes.py`.
- `terminus/brief.py`: the morning brief. **Terminus must never read email itself**; the
  user wants Hermes to handle Gmail with its own tools. `gather()` fetches only public
  finance RSS headlines. `build_messages()` asks Hermes to check email and write the brief,
  and `raw_digest()` (headlines only) is the fallback when Hermes is unreachable. One row
  per day in `briefs`. It runs automatically after `config.brief_time`; tests set
  `brief_time=None` so they never touch the network.
- Jobs: the `jobs` and `job_history` tables (v6), `Job` in models.py, and
  `widgets/jobs_view.py`. `jobs_import.py` handles CSV import (`terminus import-jobs`). The
  email scan (`TerminusApp._scan_jobs`) is ONE Hermes call that returns labelled email
  JSON (`actions.parse_email_list`). `actions.job_entries_from_emails` then matches it to
  the tracker **deterministically**: gpt-oss:20b invented tracker entries when asked to do
  the matching itself, so keep that step in code. Matched emails are stored in
  `job_emails` (v7), holding Hermes' description only; `logged_gmail_ids()` stops
  re-proposals. Assessment emails (the `ASSESSMENT` regex) also propose a task.
- `pyproject.toml` turns RuntimeWarning into a test failure. An un-awaited `app.run_action`
  in a widget once silently broke `enter` on the Jobs page.
- `terminus/stats.py` computes the numbers and `widgets/stats_view.py` renders them. Stats
  are recomputed only while the Stats view is visible.
- Sessions: `task_id` NULL means free focus. `ON DELETE SET NULL` keeps logged time when a
  task is deleted.
- `terminus/widgets/`: TodayView (`render_timeline` is a pure Text renderer), TasksView,
  Sidebar, and TaskModal.
- Theme: `terminus/theme.py` registers the `terminus` Textual theme. It must be registered
  in `App.__init__` before CSS loads, because `styles.tcss` uses `$muted`/`$faint`.

## Conventions

- Single-key app bindings are fine: focused `Input`s consume printable keys first
  (see `tests/test_app.py::test_typing_in_inputs_does_not_trigger_hotkeys`).
- Actions that don't make sense while a modal is open go in `MAIN_SCREEN_ACTIONS`.
- v3: `terminus/actions.py` defines the protocol. Hermes ends a reply with a
  ```` ```terminus-actions ```` JSON block that refers to tasks by `[#id]` (ids are
  listed in `build_context(actions=True)`; the brief uses `actions=False`). `VisibleFilter`
  hides the block from the streamed text. `validate()` turns it into `Change`s or
  rejection reasons and never touches the DB. `ProposalPanel` asks for confirmation, and
  `TerminusApp._apply_change` applies ticked changes through the normal mutation paths,
  including `_pushed` for Todoist.
