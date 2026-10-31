# HERMES_SKILL — PersonalOps terminal interface

This file is the capability manifest for a Hermes Agent (or any terminal
skill) interacting with the PersonalOps iOS app's local data store. Read it
top-to-bottom before issuing commands.

## What you can do

| Capability | Command | Effect |
|---|---|---|
| Append a note to the Daily Log | `ops-cli log "…"` | New `DailyLogItem`, source=`hermes` |
| Create a task | `ops-cli task "…"` | New `TaskItem` (backlog, or Today's Focus if `--due` ≤ today) |
| Complete a task | `ops-cli done <id>` | Marks a pending `TaskItem` completed (UUID prefix accepted) |
| Read pending tasks | `ops-cli tasks` | JSON array on stdout |
| Read today's log | `ops-cli today` | JSON array on stdout |
| Raw envelope from stdin | `ops-cli ingest` | Applies a HermesEnvelope JSON object |
| Locate the data store | `ops-cli where` | Prints resolved group/store/inbox paths |

## Building the utility (one-time, on the Mac)

```bash
cd Tools/ops-cli
swift build -c release
# binary at: .build/release/ops-cli — put it on PATH if you like:
install .build/release/ops-cli /usr/local/bin/ops-cli
```

Requires macOS 14+ and Xcode 15+ command line tools (SwiftData). The CLI
compiles the app's own model files via symlinks in
`Sources/OpsCLI/Models/`, so it always matches the app schema — rebuild the
CLI after any model change.

## Store resolution

`ops-cli` needs to find the app's SQLite store (`default.store`). Resolution
order:

1. `--store /path/to/default.store` or env `PERSONALOPS_STORE`
2. `--group-dir /path/to/appgroup` or env `PERSONALOPS_GROUP_DIR`
   (searched recursively for `default.store`)
3. Automatic: scans `~/Library/Developer/CoreSimulator/Devices/*/data/`
   `Containers/Shared/AppGroup/*` for the container whose metadata matches
   `group.com.yourteam.personalops`, most-recently-used first.

Run `ops-cli where` first and cache the printed paths in your session; pass
`--store` explicitly afterwards to skip the scan.

**Scope**: automatic discovery targets the iOS **Simulator** (local dev). A
physical device's container is not reachable from the Mac filesystem; for
device workflows, use inbox mode and a file-sync transport, or wait for the
network endpoint phase.

## Write modes — choose deliberately

- **Direct mode (default)**: `ops-cli` opens the SQLite store itself and
  inserts rows. Instant, works while the app is closed. SQLite WAL makes
  concurrent access safe, **but the running app does not live-refresh from
  cross-process writes** — the user sees the new row on next app foreground.
- **Inbox mode (`--via-inbox`)**: writes a JSON envelope file into
  `<app-group>/HermesInbox/`; the app ingests it next time it becomes
  active, through the same code path as every other write. Prefer this when
  the app is likely open. Malformed envelopes are renamed `*.rejected`, not
  deleted — check for those when debugging.

## Command reference

### Write a log note

```bash
ops-cli log "Reviewed the quarterly numbers. **Margin up 2%.**" \
  --tags finance,review --category work
# markdown allowed in the text; --category is folded into tags
ops-cli log "Note while app is open" --via-inbox
ops-cli log "Backdated note" --time 2026-07-03T09:15:00Z
```

### Create a task

```bash
ops-cli task "Renew passport" --priority high --due 2026-07-10
ops-cli task "Read SwiftData migration docs"          # no due date → Backlog
# priority: low | normal | high (default normal)
# --due YYYY-MM-DD; due ≤ today surfaces in "Today's Focus"
```

### Complete a task

```bash
ops-cli tasks                 # find the task's "id"
ops-cli done 9B2E             # any unambiguous UUID prefix
ops-cli done 9B2E --via-inbox # queue for the app to apply on foreground
```

Fails with exit 1 if the prefix matches zero or multiple pending tasks.

### Read pending tasks

```bash
ops-cli tasks
```

Output — JSON array, highest priority first, ISO-8601 dates, `priority`
0=low 1=normal 2=high, `targetDate` null = backlog:

```json
[
  {
    "createdAt" : "2026-07-03T18:04:11Z",
    "details" : "",
    "id" : "9B2E…",
    "priority" : 2,
    "source" : "user",
    "targetDate" : "2026-07-10T07:00:00Z",
    "title" : "Renew passport"
  }
]
```

### Read today's log

```bash
ops-cli today
# → JSON array of {timestamp, source, content, tags}, chronological
```

The app also exports snapshots on every foreground to the App Group root:
`hermes_state_today.json` and `hermes_pending_tasks.json` — read those files
directly if you want zero-cost polling without invoking ops-cli.

### Raw envelope via stdin

```bash
echo '{
  "action": "add_task",
  "content": "Prepare Monday brief",
  "priority": 2,
  "targetDate": "2026-07-06T07:00:00Z",
  "details": "Pull metrics from the dashboard export first."
}' | ops-cli ingest --via-inbox
```

Envelope schema (`action` is required; unknown actions are rejected):

| Field | Type | Used by | Notes |
|---|---|---|---|
| `action` | string | all | `"log_note"`, `"add_task"`, `"complete_task"`, `"toggle_habit"`, `"add_habit"`, `"create_project"`, or `"assign_task_to_project"` |
| `content` | string | most | note markdown / task title / habit name / project name (informational for complete_task, toggle_habit, assign_task_to_project) |
| `timestamp` | ISO-8601 | log_note | default now |
| `tags` | [string] | log_note | optional |
| `priority` | int 0–2 | add_task | default 1 |
| `targetDate` | ISO-8601 | add_task | omit for backlog |
| `details` | string | add_task | optional body |
| `projectId` | UUID | add_task, assign_task_to_project | omit/null clears assignment |
| `id` | UUID | complete_task, toggle_habit, assign_task_to_project | required — task/habit UUID |
| `date` | ISO-8601 | toggle_habit | which day to toggle; default today |
| `segment` | string | add_habit | `"morning"`, `"afternoon"`, or `"evening"` |
| `projectColor` | string | create_project | `"cyan"`, `"green"`, `"amber"`, or `"alert"`; default cyan |

Malformed or unresolvable rows (e.g. `toggle_habit` for a habit UUID that no
longer exists) don't stop the batch — that one file is renamed `*.rejected`
and the rest of the inbox still drains normally.

## Exit codes

- `0` success (writes print `ok` or `queued <path>`; reads print JSON)
- `1` usage error (bad flags, invalid JSON on stdin)
- `2` store/container not found — launch the app once in the simulator, or
  pass `--store` explicitly

## Rules for agents

1. Never edit `default.store*` files directly; only write through `ops-cli`
   or the `HermesInbox` envelope format.
2. Mark provenance honestly: everything written through this interface is
   recorded with `source: "hermes"` and surfaces with a ✨ badge in the UI.
   Do not impersonate user entries.
3. Treat exported JSON (`hermes_state_today.json`, tasks output) as
   read-only snapshots; they are regenerated by the app on foreground.
4. One envelope per file in inbox mode; filename must be unique and end
   `.json` (ops-cli handles this for you).
