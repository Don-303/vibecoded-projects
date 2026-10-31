# PersonalOps

Personal Operations iOS app — daily log, habits, tasks, and HealthKit metrics,
with a local-first ingestion interface for the Hermes Agent. 100% SwiftUI +
SwiftData, iOS 17+.

**Visual style:** terminal/cyberpunk — true black backgrounds, SF Mono
everywhere (`.fontDesign(.monospaced)` set once at the app root), neon green
primary accent, cyan/amber secondary accents, deep red for alerts, sharp
4pt-max corners, and CRT-style glow shadows on titles and active readouts.
See `PersonalOps/Theme/CyberTheme.swift` for the shared tokens/modifiers
(`.terminalCard()`, `.neonGlow()`, `.terminal()` button style) — reuse those
rather than hand-rolling colors in new views.

## Repo layout

```
PersonalOps/
├── PersonalOps/
│   ├── PersonalOpsApp.swift            # @main, container wiring, foreground hooks
│   ├── Models/
│   │   ├── DailyLogItem.swift          # markdown timeline entry (+ LogSource)
│   │   ├── Habit.swift                 # Habit + HabitCompletion, streak logic
│   │   └── DailyMetric.swift           # one row/day, per-field manual flags
│   └── Services/
│       ├── HealthKitManager.swift      # async auth + last-24h queries
│       └── DataController.swift        # ModelContainer, HK sync, Hermes inbox
└── Scripts/
    └── hermes_inject.sh                # Hermes terminal skill (simulator)
```

## Xcode setup (one-time)

1. **Create the project**: Xcode → New Project → iOS App, name `PersonalOps`,
   interface SwiftUI, storage *None* (we build the container ourselves).
   Delete the generated `ContentView.swift`/`Item.swift` and drag the
   `PersonalOps/` sources in.
2. **Minimum deployment**: iOS 17.0 (SwiftData + `@Observable`).
3. **Capabilities** (target → Signing & Capabilities → +):
   - **HealthKit** (leave "Clinical Health Records" off).
   - **App Groups** — add `group.com.yourteam.personalops` and update
     `DataController.appGroupID` to match your team's identifier.
4. **Info.plist** (target → Info → Custom iOS Target Properties):

   | Key | Value |
   |---|---|
   | `NSHealthShareUsageDescription` | `PersonalOps reads your sleep, step count, active energy, and weight from Apple Health to fill in your daily metrics dashboard automatically. Data never leaves your device.` |

   (No `NSHealthUpdateUsageDescription` needed yet — Phase 1 is read-only.)

## Architecture notes

- **`DataController`** is the single owner of the `ModelContainer`, stored in
  the App Group so the SQLite location is stable. Both write paths converge
  here: HealthKit sync (`syncTodayFromHealthKit`) and Hermes ingestion
  (`processHermesInbox` / `ingestHermesNote`).
- **Manual overrides win**: `DailyMetric` carries a per-field `isManual` flag;
  the UI sliders call `setManualOverride`, and subsequent HealthKit syncs skip
  pinned fields. HealthKit `nil`s also never erase existing values.
- **HealthKit reads** use the modern async descriptors
  (`HKSampleQueryDescriptor` / `HKStatisticsQueryDescriptor`). Steps and
  energy use `.cumulativeSum` statistics (correct multi-source dedup); sleep
  merges overlapping "asleep" intervals before summing; weight takes the most
  recent sample regardless of age.
- **Hermes interface** is file-based JSON over the App Group — no server, no
  network entitlement:
  - *Inject*: drop `{"action": "log_note", "content": "…", "tags": […]}` into
    `<app-group>/HermesInbox/*.json`; the app drains it on foreground.
    Malformed files become `*.rejected` rather than vanishing.
  - *Read*: the app exports `hermes_state_today.json` (today's log) into the
    App Group on foreground; pending-tasks export joins when the task model
    lands in Phase 3.
  - On the simulator, `Scripts/hermes_inject.sh "note text" tag1,tag2`
    locates the booted device's App Group container and writes the envelope.

## Phase plan

- [x] **Phase 1** — models, HealthKitManager, DataController, Hermes inbox
- [x] **Phase 2** — MainTabView + DashboardView (metrics grid, quick log, markdown timeline)
- [x] **Phase 3** — TaskItem model + TasksView (Today's Focus/Backlog, swipes, new-task sheet); HabitsView (segment blocks, toggle, streaks)
- [x] **Phase 4** — `Tools/ops-cli` headless SwiftData bridge + `HERMES_SKILL.md` manifest
- [x] **Phase 5** — polish pass: spring animations, HealthKit fail-safes, manual-override
  slider sheet (tap the metrics grid), concurrency audit, os.Logger instead of prints

Post-phase improvement pass:
- HealthKit steps/energy now measured **since midnight** (calendar-day
  semantics matching DailyMetric); sleep keeps the trailing-24h window.
- Dashboard and Tasks re-key their day-scoped queries on
  `NSCalendarDayChanged` — midnight rollover works without a relaunch.
- Design refresh: 2×2 metric grid with an Active Energy card, gradient icon
  chips, card shadows, source-tinted timeline capsules, segment progress
  bars and streak capsules in Habits, accessibility labels throughout.
- Task context menus: "Do today" / "Move to backlog"; habit rows show the
  best-ever streak.
- Hermes can now close tasks: `ops-cli done <id-prefix>` /
  `complete_task` envelope action.

## ops-cli (Hermes bridge)

See `HERMES_SKILL.md` for the full agent-facing contract. Quick start:

```bash
cd Tools/ops-cli && swift build -c release
.build/release/ops-cli where
.build/release/ops-cli log "Hello from the terminal" --tags demo
.build/release/ops-cli tasks
```

Model files are symlinked into the CLI target from `PersonalOps/Models/` —
edit them once, rebuild both. In Xcode, add `TaskItem.swift` to the app
target like the other models (already registered in DataController's schema).
