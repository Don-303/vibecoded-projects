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
- v3 (the agent proposes task changes, applied only after confirmation) is next. Hook it
  into `TerminusApp._ask` and `agent_client.build_context`, whose system prompt currently
  says the agent can't change the planner.
