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
- `terminus/widgets/`: TodayView (`render_timeline` is a pure Text renderer), TasksView,
  Sidebar, and TaskModal.
- Theme: `terminus/theme.py` registers the `terminus` Textual theme. It must be registered
  in `App.__init__` before CSS loads, because `styles.tcss` uses `$muted`/`$faint`.

## Conventions

- Single-key app bindings are fine: focused `Input`s consume printable keys first
  (see `tests/test_app.py::test_typing_in_inputs_does_not_trigger_hotkeys`).
- Actions that don't make sense while a modal is open go in `MAIN_SCREEN_ACTIONS`.
- v2 (Stats and the Hermes agent client) is not built yet. The Stats and Agent views
  are placeholders.
