//
//  DataController.swift
//  PersonalOps
//
//  Single owner of the GRDB database. DailyMetric fields are populated
//  manually via the dashboard's override sheet (no HealthKit dependency —
//  removed to avoid the entitlement/capability setup it requires).
//
//  Hermes  → JSON "envelope" files dropped into an App Group inbox
//  directory by the agent's terminal skill; processHermesInbox() drains
//  them into DailyLogItem rows. ingest/export methods are also public
//  so later phases (URL scheme, local endpoint) reuse the same path.
//
//  The database file lives inside the App Group so the on-disk location is
//  stable and discoverable by external tooling. Views observe it through
//  GRDBQuery's @Query, fed by the .databaseContext(...) environment set up
//  in PersonalOpsApp.
//

import Foundation
import GRDB
import OSLog

@MainActor
final class DataController {

    private let logger = Logger(
        subsystem: "com.yourteam.personalops", category: "DataController")

    /// Must match the App Group enabled on the target in Signing & Capabilities.
    static let appGroupID = "group.com.yourteam.personalops"
    static let hermesInboxFolder = "HermesInbox"

    static let shared = DataController()

    /// DatabasePool for the on-disk store, DatabaseQueue for in-memory
    /// (previews/tests). Both are DatabaseWriter, which is all callers need.
    let writer: any DatabaseWriter

    /// Pass inMemory: true from previews and unit tests.
    init(inMemory: Bool = false) {
        do {
            if inMemory {
                writer = try DatabaseQueue()
            } else {
                let directory: URL
                if let group = FileManager.default.containerURL(
                    forSecurityApplicationGroupIdentifier: Self.appGroupID) {
                    // Store inside the App Group so the path is predictable.
                    directory = group
                } else {
                    // App Group not configured yet — fall back to app support.
                    directory = try FileManager.default.url(
                        for: .applicationSupportDirectory,
                        in: .userDomainMask,
                        appropriateFor: nil,
                        create: true)
                }
                let dbURL = directory.appendingPathComponent("PersonalOps.sqlite")
                writer = try DatabasePool(path: dbURL.path)
            }
            try Self.migrator.migrate(writer)
        } catch {
            fatalError("Failed to open database: \(error)")
        }
    }

    // MARK: - Schema

    private static var migrator: DatabaseMigrator {
        var migrator = DatabaseMigrator()

        migrator.registerMigration("v1") { db in
            try db.create(table: DailyLogItem.databaseTableName) { t in
                t.primaryKey("id", .blob)
                t.column("timestamp", .datetime).notNull().indexed()
                t.column("content", .text).notNull()
                t.column("sourceRaw", .text).notNull()
                t.column("tags", .text).notNull()
                t.column("processedByHermes", .boolean).notNull()
            }

            try db.create(table: Habit.databaseTableName) { t in
                t.primaryKey("id", .blob)
                t.column("name", .text).notNull()
                t.column("segmentRaw", .text).notNull()
                t.column("sortOrder", .integer).notNull()
                t.column("createdAt", .datetime).notNull()
                t.column("isArchived", .boolean).notNull()
            }

            try db.create(table: HabitCompletion.databaseTableName) { t in
                t.primaryKey("id", .blob)
                t.column("date", .datetime).notNull()
                t.belongsTo("habit", onDelete: .cascade).notNull()
                t.uniqueKey(["habitId", "date"])
            }

            try db.create(table: DailyMetric.databaseTableName) { t in
                t.primaryKey("id", .blob)
                t.column("day", .datetime).notNull().unique()
                t.column("sleepHours", .double)
                t.column("steps", .integer)
                t.column("activeEnergyKcal", .double)
                t.column("weightKg", .double)
                t.column("sleepIsManual", .boolean).notNull()
                t.column("stepsIsManual", .boolean).notNull()
                t.column("energyIsManual", .boolean).notNull()
                t.column("weightIsManual", .boolean).notNull()
                t.column("lastSyncedAt", .datetime)
            }

            try db.create(table: TaskItem.databaseTableName) { t in
                t.primaryKey("id", .blob)
                t.column("title", .text).notNull()
                t.column("details", .text).notNull()
                t.column("priorityRaw", .integer).notNull()
                t.column("createdAt", .datetime).notNull()
                t.column("targetDate", .datetime)
                t.column("completedAt", .datetime)
                t.column("isCompleted", .boolean).notNull().indexed()
                t.column("sourceRaw", .text).notNull()
            }
        }

        migrator.registerMigration("v2_projects") { db in
            try db.create(table: Project.databaseTableName) { t in
                t.primaryKey("id", .blob)
                t.column("name", .text).notNull()
                t.column("colorRaw", .text).notNull()
                t.column("sortOrder", .integer).notNull()
                t.column("createdAt", .datetime).notNull()
                t.column("isArchived", .boolean).notNull()
            }
            try db.alter(table: TaskItem.databaseTableName) { t in
                t.add(column: "projectId", .blob)
                    .indexed()
                    .references(Project.databaseTableName, onDelete: .setNull)
            }
        }

        migrator.registerMigration("v3_chat") { db in
            try db.create(table: ChatMessage.databaseTableName) { t in
                t.primaryKey("id", .blob)
                t.column("roleRaw", .text).notNull()
                t.column("content", .text).notNull()
                t.column("timestamp", .datetime).notNull().indexed()
            }
        }

        migrator.registerMigration("v4_news") { db in
            try db.create(table: NewsItem.databaseTableName) { t in
                t.primaryKey("id", .blob)
                t.column("categoryRaw", .text).notNull()
                t.column("title", .text).notNull()
                t.column("link", .text).notNull()
                t.column("publishedAt", .datetime).notNull().indexed()
            }
        }

        migrator.registerMigration("v5_focus_screentime_protected") { db in
            try db.create(table: FocusSession.databaseTableName) { t in
                t.primaryKey("id", .blob)
                t.column("startedAt", .datetime).notNull()
                t.column("durationMinutes", .integer).notNull()
                t.column("completedAt", .datetime).notNull().indexed()
            }
            try db.alter(table: DailyMetric.databaseTableName) { t in
                t.add(column: "screenTimeMinutes", .double)
                t.add(column: "screenTimeIsManual", .boolean).notNull().defaults(to: false)
            }
            try db.alter(table: Project.databaseTableName) { t in
                t.add(column: "isProtected", .boolean).notNull().defaults(to: false)
            }
        }

        migrator.registerMigration("v6_project_goals") { db in
            try db.alter(table: Project.databaseTableName) { t in
                t.add(column: "goalDescription", .text).notNull().defaults(to: "")
                t.add(column: "goalTargetDate", .datetime)
            }
        }

        migrator.registerMigration("v7_systems_layer") { db in
            try db.create(table: EnergyLog.databaseTableName) { t in
                t.primaryKey("id", .blob)
                t.column("day", .datetime).notNull().unique()
                t.column("rating", .integer).notNull()
            }
            try db.create(table: WeeklyRetro.databaseTableName) { t in
                t.primaryKey("id", .blob)
                t.column("weekStart", .datetime).notNull().unique()
                t.column("wentRight", .text).notNull()
                t.column("systemBroke", .text).notNull()
                t.column("nextFix", .text).notNull()
                t.column("updatedAt", .datetime).notNull()
            }
        }

        migrator.registerMigration("v8_reminders_sync") { db in
            try db.alter(table: TaskItem.databaseTableName) { t in
                t.add(column: "reminderIdentifier", .text)
            }
        }

        migrator.registerMigration("v9_books") { db in
            try db.create(table: Book.databaseTableName) { t in
                t.primaryKey("id", .blob)
                t.column("title", .text).notNull()
                t.column("author", .text).notNull()
                t.column("statusRaw", .text).notNull()
                t.column("addedAt", .datetime).notNull()
                t.column("finishedAt", .datetime)
            }
        }

        migrator.registerMigration("v10_recurrence_sober") { db in
            try db.alter(table: TaskItem.databaseTableName) { t in
                t.add(column: "recurrenceRaw", .text)
            }
            try db.create(table: SoberPeriod.databaseTableName) { t in
                t.primaryKey("id", .blob)
                t.column("startDate", .datetime).notNull()
                t.column("endDate", .datetime)
            }
        }

        migrator.registerMigration("v11_gym") { db in
            try db.create(table: WorkoutPlan.databaseTableName) { t in
                t.primaryKey("id", .blob)
                t.column("name", .text).notNull()
                t.column("sortOrder", .integer).notNull()
                t.column("createdAt", .datetime).notNull()
            }
            try db.create(table: Exercise.databaseTableName) { t in
                t.primaryKey("id", .blob)
                t.belongsTo("workoutPlan", onDelete: .cascade).notNull()
                t.column("name", .text).notNull()
                t.column("targetSets", .integer).notNull()
                t.column("targetReps", .integer).notNull()
                t.column("sortOrder", .integer).notNull()
            }
            try db.create(table: WorkoutSession.databaseTableName) { t in
                t.primaryKey("id", .blob)
                t.belongsTo("workoutPlan", onDelete: .cascade).notNull()
                t.column("date", .datetime).notNull()
                t.uniqueKey(["workoutPlanId", "date"])
            }
        }

        migrator.registerMigration("v12_exercise_completion") { db in
            try db.create(table: ExerciseCompletion.databaseTableName) { t in
                t.primaryKey("id", .blob)
                t.belongsTo("exercise", onDelete: .cascade).notNull()
                t.column("date", .datetime).notNull()
                t.uniqueKey(["exerciseId", "date"])
            }
        }

        migrator.registerMigration("v13_workout_schedule") { db in
            try db.alter(table: WorkoutPlan.databaseTableName) { t in
                t.add(column: "scheduledWeekday", .integer)
            }
        }

        migrator.registerMigration("v14_protected_habits") { db in
            try db.alter(table: Habit.databaseTableName) { t in
                t.add(column: "isProtected", .boolean).notNull().defaults(to: false)
            }
        }

        migrator.registerMigration("v15_workout_day_assignment") { db in
            try db.create(table: WorkoutDayAssignment.databaseTableName) { t in
                t.primaryKey("id", .blob)
                t.column("date", .datetime).notNull().unique()
                t.belongsTo("workoutPlan", onDelete: .cascade).notNull()
            }
        }

        migrator.registerMigration("v16_sober_items") { db in
            try db.create(table: SoberItem.databaseTableName) { t in
                t.primaryKey("id", .blob)
                t.column("name", .text).notNull()
                t.column("sortOrder", .integer).notNull()
                t.column("createdAt", .datetime).notNull()
            }
            try db.alter(table: SoberPeriod.databaseTableName) { t in
                t.add(column: "soberItemId", .blob)
            }
            // Pre-existing installs had one unnamed tracker — fold its
            // history into a single "Sobriety" item so it isn't lost.
            let existingPeriodCount = try Int.fetchOne(
                db, sql: "SELECT COUNT(*) FROM \(SoberPeriod.databaseTableName)") ?? 0
            if existingPeriodCount > 0 {
                let defaultItem = SoberItem(name: "Sobriety")
                try defaultItem.insert(db)
                try db.execute(
                    sql: "UPDATE \(SoberPeriod.databaseTableName) SET soberItemId = ?",
                    arguments: [defaultItem.id])
            }
        }

        migrator.registerMigration("v17_calendar_sync") { db in
            try db.alter(table: TaskItem.databaseTableName) { t in
                t.add(column: "calendarEventIdentifier", .text)
            }
        }

        migrator.registerMigration("v18_book_category") { db in
            try db.alter(table: Book.databaseTableName) { t in
                t.add(column: "categoryRaw", .text).notNull().defaults(to: BookCategory.personal.rawValue)
            }
        }

        migrator.registerMigration("v19_task_calendar_visibility") { db in
            try db.alter(table: TaskItem.databaseTableName) { t in
                t.add(column: "showOnCalendar", .boolean).notNull().defaults(to: true)
            }
        }

        migrator.registerMigration("v20_hermes_memory") { db in
            try db.create(table: HermesMemory.databaseTableName) { t in
                t.primaryKey("id", .blob)
                t.column("content", .text).notNull()
                t.column("createdAt", .datetime).notNull()
            }
        }

        return migrator
    }

    // MARK: - Generic helpers

    /// Fetch-or-create the single DailyMetric row for a given day.
    func metric(for date: Date = .now) -> DailyMetric {
        let day = Calendar.current.startOfDay(for: date)
        do {
            return try writer.write { db in
                if let existing = try DailyMetric
                    .filter(DailyMetric.Columns.day == day)
                    .fetchOne(db) {
                    return existing
                }
                let fresh = DailyMetric(day: day)
                try fresh.insert(db)
                return fresh
            }
        } catch {
            assertionFailure("DailyMetric fetch-or-create failed: \(error)")
            return DailyMetric(day: day)
        }
    }

    private func saveMetric(_ metric: DailyMetric) {
        do {
            try writer.write { db in
                try metric.save(db)
            }
        } catch {
            assertionFailure("DailyMetric save failed: \(error)")
        }
    }

    /// Self-reported daily total — see DailyMetric.screenTimeMinutes.
    func setScreenTimeMinutes(_ minutes: Double, for date: Date = .now) {
        var metric = metric(for: date)
        metric.screenTimeMinutes = minutes
        metric.screenTimeIsManual = true
        saveMetric(metric)
    }

    func setSleepHours(_ hours: Double, for date: Date = .now) {
        var metric = metric(for: date)
        metric.sleepHours = hours
        metric.sleepIsManual = true
        saveMetric(metric)
    }

    func setSteps(_ steps: Int, for date: Date = .now) {
        var metric = metric(for: date)
        metric.steps = steps
        metric.stepsIsManual = true
        saveMetric(metric)
    }

    func setWeightKg(_ kg: Double, for date: Date = .now) {
        var metric = metric(for: date)
        metric.weightKg = kg
        metric.weightIsManual = true
        saveMetric(metric)
    }

    func setActiveEnergyKcal(_ kcal: Double, for date: Date = .now) {
        var metric = metric(for: date)
        metric.activeEnergyKcal = kcal
        metric.energyIsManual = true
        saveMetric(metric)
    }

    // MARK: - Reading list books

    func addBook(_ book: Book) {
        do {
            try writer.write { db in
                try book.insert(db)
            }
        } catch {
            assertionFailure("Book insert failed: \(error)")
        }
    }

    func setBookStatus(_ id: UUID, status: BookStatus) {
        do {
            try writer.write { db in
                guard var book = try Book.fetchOne(db, key: id) else { return }
                book.status = status
                book.finishedAt = status == .finished ? .now : nil
                try book.update(db)
            }
        } catch {
            assertionFailure("Book status update failed: \(error)")
        }
    }

    func deleteBook(_ id: UUID) {
        do {
            _ = try writer.write { db in
                try Book.deleteOne(db, key: id)
            }
        } catch {
            assertionFailure("Book delete failed: \(error)")
        }
    }

    // MARK: - Hermes memory

    /// Durable fact Hermes chose to remember — see HermesMemory's header
    /// comment. Deduplicates on exact content match so re-telling Hermes
    /// something it already knows doesn't pile up repeats.
    func addHermesMemory(_ content: String) {
        let trimmed = content.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        do {
            try writer.write { db in
                let exists = try HermesMemory
                    .filter(Column("content") == trimmed)
                    .fetchCount(db) > 0
                guard !exists else { return }
                try HermesMemory(content: trimmed).insert(db)
            }
        } catch {
            assertionFailure("HermesMemory insert failed: \(error)")
        }
    }

    func deleteHermesMemory(_ id: UUID) {
        do {
            _ = try writer.write { db in
                try HermesMemory.deleteOne(db, key: id)
            }
        } catch {
            assertionFailure("HermesMemory delete failed: \(error)")
        }
    }

    func fetchAllHermesMemories() -> [HermesMemory] {
        (try? writer.read { db in
            try HermesMemory.order(HermesMemory.Columns.createdAt).fetchAll(db)
        }) ?? []
    }

    // MARK: - Gym

    func addWorkoutPlan(_ plan: WorkoutPlan) {
        do {
            try writer.write { db in try plan.insert(db) }
        } catch {
            assertionFailure("WorkoutPlan insert failed: \(error)")
        }
    }

    func deleteWorkoutPlan(_ id: UUID) {
        do {
            _ = try writer.write { db in try WorkoutPlan.deleteOne(db, key: id) }
        } catch {
            assertionFailure("WorkoutPlan delete failed: \(error)")
        }
    }

    func updateWorkoutPlan(_ id: UUID, name: String, scheduledWeekday: Int?) {
        do {
            try writer.write { db in
                guard var plan = try WorkoutPlan.fetchOne(db, key: id) else { return }
                plan.name = name
                plan.scheduledWeekday = scheduledWeekday
                try plan.update(db)
            }
        } catch {
            assertionFailure("WorkoutPlan update failed: \(error)")
        }
    }

    /// Assigns a plan as "today's workout" independent of its regular
    /// scheduledWeekday — a one-off override. Replaces any existing
    /// assignment for that day.
    func assignWorkoutPlan(_ planId: UUID, forDay date: Date = .now) {
        let day = Calendar.current.startOfDay(for: date)
        do {
            try writer.write { db in
                try WorkoutDayAssignment
                    .filter(WorkoutDayAssignment.Columns.date == day)
                    .deleteAll(db)
                try WorkoutDayAssignment(date: day, workoutPlanId: planId).insert(db)
            }
        } catch {
            assertionFailure("WorkoutDayAssignment insert failed: \(error)")
        }
    }

    func clearWorkoutAssignment(forDay date: Date = .now) {
        let day = Calendar.current.startOfDay(for: date)
        do {
            _ = try writer.write { db in
                try WorkoutDayAssignment
                    .filter(WorkoutDayAssignment.Columns.date == day)
                    .deleteAll(db)
            }
        } catch {
            assertionFailure("WorkoutDayAssignment clear failed: \(error)")
        }
    }

    /// Seeds four renameable/reassignable starter plans on first launch,
    /// unassigned to any day so the user picks the split themselves.
    func seedDefaultWorkoutPlansIfNeeded() {
        do {
            let exists = try writer.read { db in try WorkoutPlan.fetchCount(db) > 0 }
            guard !exists else { return }
            try writer.write { db in
                for (index, name) in ["Chest 1", "Chest 2", "Back 1", "Back 2"].enumerated() {
                    try WorkoutPlan(name: name, sortOrder: index).insert(db)
                }
            }
        } catch {
            assertionFailure("Default workout plan seed failed: \(error)")
        }
    }

    func addExercise(_ exercise: Exercise) {
        do {
            try writer.write { db in try exercise.insert(db) }
        } catch {
            assertionFailure("Exercise insert failed: \(error)")
        }
    }

    func deleteExercise(_ id: UUID) {
        do {
            _ = try writer.write { db in try Exercise.deleteOne(db, key: id) }
        } catch {
            assertionFailure("Exercise delete failed: \(error)")
        }
    }

    /// Toggles today's completion for a plan — same on/off pattern as
    /// toggleHabit. Used directly for plans with no exercises yet; plans
    /// with exercises get this automatically from toggleExerciseCompletion.
    func toggleWorkoutSession(_ planId: UUID, on date: Date = .now) {
        let day = Calendar.current.startOfDay(for: date)
        do {
            try writer.write { db in
                if let existing = try WorkoutSession
                    .filter(WorkoutSession.Columns.workoutPlanId == planId)
                    .filter(WorkoutSession.Columns.date == day)
                    .fetchOne(db) {
                    try existing.delete(db)
                } else {
                    try WorkoutSession(workoutPlanId: planId, date: day).insert(db)
                }
            }
        } catch {
            assertionFailure("WorkoutSession toggle failed: \(error)")
        }
    }

    /// Checks/unchecks one exercise off today's session, then keeps the
    /// plan-level WorkoutSession in sync: completing the last unchecked
    /// exercise auto-completes the plan for today, unchecking any exercise
    /// un-completes it.
    func toggleExerciseCompletion(_ exerciseId: UUID, workoutPlanId: UUID, on date: Date = .now) {
        let day = Calendar.current.startOfDay(for: date)
        do {
            try writer.write { db in
                if let existing = try ExerciseCompletion
                    .filter(ExerciseCompletion.Columns.exerciseId == exerciseId)
                    .filter(ExerciseCompletion.Columns.date == day)
                    .fetchOne(db) {
                    try existing.delete(db)
                } else {
                    try ExerciseCompletion(exerciseId: exerciseId, date: day).insert(db)
                }

                let exerciseIds = try Exercise
                    .filter(Exercise.Columns.workoutPlanId == workoutPlanId)
                    .fetchAll(db)
                    .map(\.id)
                guard !exerciseIds.isEmpty else { return }

                let completedCount = try ExerciseCompletion
                    .filter(exerciseIds.contains(ExerciseCompletion.Columns.exerciseId))
                    .filter(ExerciseCompletion.Columns.date == day)
                    .fetchCount(db)
                let allDone = completedCount == exerciseIds.count

                let existingSession = try WorkoutSession
                    .filter(WorkoutSession.Columns.workoutPlanId == workoutPlanId)
                    .filter(WorkoutSession.Columns.date == day)
                    .fetchOne(db)
                if allDone, existingSession == nil {
                    try WorkoutSession(workoutPlanId: workoutPlanId, date: day).insert(db)
                } else if !allDone, let existingSession {
                    try existingSession.delete(db)
                }
            }
        } catch {
            assertionFailure("ExerciseCompletion toggle failed: \(error)")
        }
    }

    // MARK: - Sober tracker

    /// Adds a new tracked item (e.g. "Alcohol", "Smoking") with no active
    /// period yet — the caller follows up with startSoberTracking.
    func addSoberItem(_ name: String) -> UUID {
        let item = SoberItem(name: name.trimmingCharacters(in: .whitespaces))
        do {
            try writer.write { db in try item.insert(db) }
        } catch {
            assertionFailure("SoberItem insert failed: \(error)")
        }
        return item.id
    }

    func deleteSoberItem(_ id: UUID) {
        do {
            try writer.write { db in
                try SoberPeriod.filter(SoberPeriod.Columns.soberItemId == id).deleteAll(db)
                try SoberItem.deleteOne(db, key: id)
            }
        } catch {
            assertionFailure("SoberItem delete failed: \(error)")
        }
    }

    /// Starts tracking one item from `date`. Replaces any active period for
    /// that item (used for both first-time setup and correcting the start
    /// date).
    func startSoberTracking(itemId: UUID, from date: Date) {
        do {
            try writer.write { db in
                try SoberPeriod
                    .filter(SoberPeriod.Columns.soberItemId == itemId)
                    .filter(SoberPeriod.Columns.endDate == nil)
                    .deleteAll(db)
                try SoberPeriod(soberItemId: itemId, startDate: date).insert(db)
            }
        } catch {
            assertionFailure("SoberPeriod start failed: \(error)")
        }
    }

    /// Records a relapse for one item: closes its active period and
    /// immediately opens a new one starting today, so history (and the
    /// best streak) is kept.
    func resetSoberStreak(itemId: UUID) {
        do {
            try writer.write { db in
                if var active = try SoberPeriod
                    .filter(SoberPeriod.Columns.soberItemId == itemId)
                    .filter(SoberPeriod.Columns.endDate == nil)
                    .fetchOne(db) {
                    active.endDate = .now
                    try active.update(db)
                }
                try SoberPeriod(soberItemId: itemId, startDate: .now).insert(db)
            }
        } catch {
            assertionFailure("SoberPeriod reset failed: \(error)")
        }
    }

    // MARK: - Energy audit log

    /// Upserts today's (or `date`'s) energy rating — logging again the same
    /// day overwrites rather than duplicates.
    func logEnergyRating(_ rating: Int, for date: Date = .now) {
        let day = Calendar.current.startOfDay(for: date)
        do {
            try writer.write { db in
                if var existing = try EnergyLog.filter(EnergyLog.Columns.day == day).fetchOne(db) {
                    existing.rating = rating
                    try existing.update(db)
                } else {
                    try EnergyLog(day: day, rating: rating).insert(db)
                }
            }
        } catch {
            assertionFailure("EnergyLog upsert failed: \(error)")
        }
    }

    // MARK: - Weekly retrospective

    /// Fetch-or-create the single WeeklyRetro row for the week starting `weekStart`.
    func weeklyRetro(for weekStart: Date) -> WeeklyRetro {
        do {
            return try writer.read { db in
                if let existing = try WeeklyRetro
                    .filter(WeeklyRetro.Columns.weekStart == weekStart)
                    .fetchOne(db) {
                    return existing
                }
                return WeeklyRetro(weekStart: weekStart)
            }
        } catch {
            assertionFailure("WeeklyRetro fetch-or-create failed: \(error)")
            return WeeklyRetro(weekStart: weekStart)
        }
    }

    func saveWeeklyRetro(_ retro: WeeklyRetro) {
        var retro = retro
        retro.updatedAt = .now
        do {
            try writer.write { db in
                try retro.save(db)
            }
        } catch {
            assertionFailure("WeeklyRetro save failed: \(error)")
        }
    }

    // MARK: - Deep work

    /// Logs a completed Deep Work Matrix session. Aborted/cancelled sessions
    /// never reach here — only full-duration completions are logged.
    func logFocusSession(durationMinutes: Int, startedAt: Date) {
        let session = FocusSession(startedAt: startedAt, durationMinutes: durationMinutes)
        do {
            try writer.write { db in
                try session.insert(db)
            }
            addLogItem("Completed a \(durationMinutes)-minute deep work session", source: .system)
        } catch {
            assertionFailure("FocusSession insert failed: \(error)")
        }
    }

    // MARK: - Daily log

    @discardableResult
    func addLogItem(
        _ content: String,
        timestamp: Date = .now,
        source: LogSource = .user,
        tags: [String] = []
    ) -> DailyLogItem {
        let item = DailyLogItem(
            content: content,
            timestamp: timestamp,
            source: source,
            tags: tags)
        do {
            try writer.write { db in
                try item.insert(db)
            }
        } catch {
            assertionFailure("DailyLogItem insert failed: \(error)")
        }
        return item
    }

    func deleteLogItem(_ id: UUID) {
        do {
            try writer.write { db in
                _ = try DailyLogItem.deleteOne(db, key: id)
            }
        } catch {
            assertionFailure("DailyLogItem delete failed: \(error)")
        }
    }

    // MARK: - Tasks

    func addTask(_ task: TaskItem) {
        var task = task
        do {
            task.reminderIdentifier = EventKitReminderSync.syncReminder(
                title: task.title, targetDate: task.targetDate, existingIdentifier: nil)
            task.calendarEventIdentifier = EventKitReminderSync.syncCalendarEvent(
                title: task.title,
                targetDate: task.showOnCalendar ? task.targetDate : nil,
                existingIdentifier: nil)
            try writer.write { db in
                try task.insert(db)
            }
            NotificationScheduler.rescheduleAll(writer: writer)
        } catch {
            assertionFailure("TaskItem insert failed: \(error)")
        }
    }

    func setTaskCompleted(_ id: UUID, completed: Bool) {
        do {
            let completedTask = try writer.write { db -> TaskItem? in
                guard var task = try TaskItem.fetchOne(db, key: id) else { return nil }
                task.setCompleted(completed)
                try task.update(db)
                EventKitReminderSync.setCompleted(completed, identifier: task.reminderIdentifier)
                return task
            }
            // Completing a recurring task spawns its next occurrence, with
            // the recurrence carried forward. Reopening doesn't claw the
            // spawned copy back — deleting it by hand is cheaper than the
            // surprise of tasks vanishing.
            if completed,
               let task = completedTask,
               let recurrence = task.recurrence,
               let targetDate = task.targetDate {
                addTask(TaskItem(
                    title: task.title,
                    details: task.details,
                    priority: task.priority,
                    targetDate: recurrence.nextDate(after: targetDate),
                    source: task.source,
                    projectId: task.projectId,
                    recurrence: recurrence))
            }
            NotificationScheduler.rescheduleAll(writer: writer)
        } catch {
            assertionFailure("TaskItem completion update failed: \(error)")
        }
    }

    func rescheduleTask(_ id: UUID, to date: Date?) {
        do {
            try writer.write { db in
                guard var task = try TaskItem.fetchOne(db, key: id) else { return }
                task.targetDate = date
                task.reminderIdentifier = EventKitReminderSync.syncReminder(
                    title: task.title, targetDate: date, existingIdentifier: task.reminderIdentifier)
                task.calendarEventIdentifier = EventKitReminderSync.syncCalendarEvent(
                    title: task.title,
                    targetDate: task.showOnCalendar ? date : nil,
                    existingIdentifier: task.calendarEventIdentifier)
                try task.update(db)
            }
            NotificationScheduler.rescheduleAll(writer: writer)
        } catch {
            assertionFailure("TaskItem reschedule failed: \(error)")
        }
    }

    func updateTask(
        _ id: UUID,
        title: String,
        priority: TaskPriority,
        targetDate: Date?,
        projectId: UUID?,
        recurrence: TaskRecurrence? = nil,
        showOnCalendar: Bool = true
    ) {
        do {
            try writer.write { db in
                guard var task = try TaskItem.fetchOne(db, key: id) else { return }
                task.title = title
                task.priorityRaw = priority.rawValue
                task.targetDate = targetDate
                task.projectId = projectId
                task.recurrence = recurrence
                task.showOnCalendar = showOnCalendar
                task.reminderIdentifier = EventKitReminderSync.syncReminder(
                    title: title, targetDate: targetDate, existingIdentifier: task.reminderIdentifier)
                task.calendarEventIdentifier = EventKitReminderSync.syncCalendarEvent(
                    title: title,
                    targetDate: showOnCalendar ? targetDate : nil,
                    existingIdentifier: task.calendarEventIdentifier)
                try task.update(db)
            }
            NotificationScheduler.rescheduleAll(writer: writer)
        } catch {
            assertionFailure("TaskItem update failed: \(error)")
        }
    }

    func deleteTask(_ id: UUID) {
        do {
            try writer.write { db in
                guard let task = try TaskItem.fetchOne(db, key: id) else { return }
                if let reminderIdentifier = task.reminderIdentifier {
                    EventKitReminderSync.removeReminder(identifier: reminderIdentifier)
                }
                if let calendarEventIdentifier = task.calendarEventIdentifier {
                    EventKitReminderSync.removeCalendarEvent(identifier: calendarEventIdentifier)
                }
                try task.delete(db)
            }
            NotificationScheduler.rescheduleAll(writer: writer)
        } catch {
            assertionFailure("TaskItem delete failed: \(error)")
        }
    }

    // MARK: - Projects

    func addProject(_ project: Project) {
        do {
            try writer.write { db in
                try project.insert(db)
            }
        } catch {
            assertionFailure("Project insert failed: \(error)")
        }
    }

    func deleteProject(_ id: UUID) {
        do {
            try writer.write { db in
                // Permanent projects (e.g. Reading List) refuse deletion.
                // The projectId foreign key is ON DELETE SET NULL, so tasks
                // survive the project's deletion and fall back to unassigned.
                guard let project = try Project.fetchOne(db, key: id), !project.isProtected else { return }
                try project.delete(db)
            }
        } catch {
            assertionFailure("Project delete failed: \(error)")
        }
    }

    func setProjectArchived(_ id: UUID, archived: Bool) {
        do {
            try writer.write { db in
                guard var project = try Project.fetchOne(db, key: id) else { return }
                project.isArchived = archived
                try project.update(db)
            }
        } catch {
            assertionFailure("Project archive toggle failed: \(error)")
        }
    }

    func updateProjectGoal(_ id: UUID, goalDescription: String, goalTargetDate: Date?) {
        do {
            try writer.write { db in
                guard var project = try Project.fetchOne(db, key: id) else { return }
                project.goalDescription = goalDescription
                project.goalTargetDate = goalTargetDate
                try project.update(db)
            }
        } catch {
            assertionFailure("Project goal update failed: \(error)")
        }
    }

    /// Seeds the permanent "Reading List" project on first launch. Safe to
    /// call every launch — a no-op once the project exists.
    func seedReadingListProjectIfNeeded() {
        do {
            let exists = try writer.read { db in
                try Project.filter(Project.Columns.name == "Reading List").fetchCount(db) > 0
            }
            guard !exists else { return }
            let project = Project(name: "Reading List", color: .amber, isProtected: true)
            try writer.write { db in
                try project.insert(db)
            }
        } catch {
            assertionFailure("Reading List project seed failed: \(error)")
        }
    }

    func assignTask(_ taskId: UUID, toProject projectId: UUID?) {
        do {
            try writer.write { db in
                guard var task = try TaskItem.fetchOne(db, key: taskId) else { return }
                task.projectId = projectId
                try task.update(db)
            }
        } catch {
            assertionFailure("TaskItem project assignment failed: \(error)")
        }
    }

    // MARK: - Habits

    func addHabit(_ habit: Habit) {
        do {
            try writer.write { db in
                try habit.insert(db)
            }
            NotificationScheduler.rescheduleAll(writer: writer)
        } catch {
            assertionFailure("Habit insert failed: \(error)")
        }
    }

    func updateHabit(_ id: UUID, name: String?, segment: RoutineSegment?) {
        do {
            try writer.write { db in
                guard var habit = try Habit.fetchOne(db, key: id) else { return }
                if let name { habit.name = name }
                if let segment { habit.segment = segment }
                try habit.update(db)
            }
            NotificationScheduler.rescheduleAll(writer: writer)
        } catch {
            assertionFailure("Habit update failed: \(error)")
        }
    }

    func deleteHabit(_ id: UUID) {
        do {
            try writer.write { db in
                // Permanent habits (e.g. Gym) refuse deletion.
                guard let habit = try Habit.fetchOne(db, key: id), !habit.isProtected else { return }
                try habit.delete(db)
            }
            NotificationScheduler.rescheduleAll(writer: writer)
        } catch {
            assertionFailure("Habit delete failed: \(error)")
        }
    }

    /// Seeds the permanent "Gym" habit on first launch. Safe to call every
    /// launch — a no-op once the habit exists.
    func seedGymHabitIfNeeded() {
        do {
            let exists = try writer.read { db in
                try Habit.filter(Habit.Columns.name == "Gym").fetchCount(db) > 0
            }
            guard !exists else { return }
            try writer.write { db in
                try Habit(name: "Gym", segment: .morning, isProtected: true).insert(db)
            }
        } catch {
            assertionFailure("Gym habit seed failed: \(error)")
        }
    }

    func toggleHabit(_ habitID: UUID, on date: Date = .now) {
        let day = Calendar.current.startOfDay(for: date)
        do {
            try writer.write { db in
                let existing = try HabitCompletion
                    .filter(HabitCompletion.Columns.habitId == habitID)
                    .filter(HabitCompletion.Columns.date == day)
                    .fetchOne(db)
                if let existing {
                    try existing.delete(db)
                } else {
                    try HabitCompletion(date: day, habitId: habitID).insert(db)
                }
            }
            NotificationScheduler.rescheduleAll(writer: writer)
        } catch {
            assertionFailure("Habit toggle failed: \(error)")
        }
    }

    // MARK: - Manual metrics

    /// UI entry point for the manual override sliders: sets the value and
    /// pins the field so future syncs won't overwrite it.
    func setManualOverride(
        sleepHours: Double? = nil,
        steps: Int? = nil,
        activeEnergyKcal: Double? = nil,
        weightKg: Double? = nil,
        for date: Date = .now
    ) {
        var m = metric(for: date)
        if let sleepHours { m.sleepHours = sleepHours; m.sleepIsManual = true }
        if let steps { m.steps = steps; m.stepsIsManual = true }
        if let activeEnergyKcal { m.activeEnergyKcal = activeEnergyKcal; m.energyIsManual = true }
        if let weightKg { m.weightKg = weightKg; m.weightIsManual = true }
        saveMetric(m)
    }

    /// Un-pins fields so the next HealthKit sync owns them again.
    func clearManualOverrides(
        sleep: Bool = false,
        steps: Bool = false,
        energy: Bool = false,
        weight: Bool = false,
        for date: Date = .now
    ) {
        var m = metric(for: date)
        if sleep { m.sleepIsManual = false }
        if steps { m.stepsIsManual = false }
        if energy { m.energyIsManual = false }
        if weight { m.weightIsManual = false }
        saveMetric(m)
    }

    // MARK: - Daily Briefing

    /// Fetches the latest headlines and wholly replaces the cached set —
    /// this is "latest known headlines," not an accumulating history.
    /// Safe to call from a `.task`/`.refreshable`; a failed fetch (offline)
    /// just leaves the existing cached rows in place.
    func refreshBriefing() async {
        let items = await BriefingService.fetchBriefing()
        guard !items.isEmpty else { return }
        do {
            try await writer.write { db in
                try NewsItem.deleteAll(db)
                for item in items {
                    try item.insert(db)
                }
            }
            // Read by DailyBriefingSection's "UPD HH:MM" stamp via @AppStorage.
            UserDefaults.standard.set(
                Date.now.timeIntervalSince1970, forKey: "briefingLastFetchAt")
        } catch {
            assertionFailure("NewsItem replace failed: \(error)")
        }
    }

    // MARK: - Hermes chat
    //
    // Local-only transcript today: sendChatMessage persists the user's
    // message and returns immediately. There is no live network bridge yet
    // wiring a reply back from a running Hermes/Claude session — see
    // ChatView. A future transport would call addChatMessage(_:role: .hermes)
    // once a real reply arrives.

    @discardableResult
    func sendChatMessage(_ content: String) -> ChatMessage {
        addChatMessage(content, role: .user)
    }

    @discardableResult
    func addChatMessage(_ content: String, role: ChatRole) -> ChatMessage {
        let message = ChatMessage(content: content, role: role)
        do {
            try writer.write { db in
                try message.insert(db)
            }
        } catch {
            assertionFailure("ChatMessage insert failed: \(error)")
        }
        return message
    }

    // MARK: - Hermes ingestion

    /// Wire format for a file dropped into the inbox by the Hermes Agent.
    ///
    /// Supported `action` values:
    ///   - "log_note"               content, timestamp?, tags?
    ///   - "add_task"               content(title), details?, priority?, targetDate?, projectId?
    ///   - "update_task"            id, content(new title)?, priority?, targetDate?, projectId?,
    ///                               recurrence? ("daily"|"weekly"|""=none), showOnCalendar?.
    ///                               Any field omitted keeps its current value.
    ///   - "delete_task"            id
    ///   - "complete_task"          id
    ///   - "toggle_habit"           id (Habit.id), date? (defaults to today)
    ///   - "add_habit"              content(name), segment ("morning"|"afternoon"|"evening")
    ///   - "update_habit"           id, content(new name)?, segment?
    ///   - "delete_habit"           id
    ///   - "create_project"         content(name), projectColor? ("cyan"|"green"|"amber"|"alert")
    ///   - "set_project_goal"       id, content(goal description), targetDate? (nil clears it)
    ///   - "archive_project"        id, archived? (default true; false to unarchive)
    ///   - "delete_project"         id (protected projects like Reading List silently refuse)
    ///   - "assign_task_to_project" id (TaskItem.id), projectId? (nil clears assignment)
    ///   - "add_book"               content(title), author?, category? ("personal"|"textbook").
    ///                               Use this — never "add_task" — for the Reading List: it's a
    ///                               dedicated book tracker, not generic tasks under a project.
    ///   - "update_book_status"     id, status ("queued"|"reading"|"finished")
    ///   - "delete_book"            id
    ///   - "remember"               content(the fact/preference to store durably). Deduplicates
    ///                               on exact match. This is what makes Hermes's memory persist
    ///                               across sessions rather than resetting every conversation.
    ///   - "forget"                 id (memory uuid from context's `memory` array)
    struct HermesEnvelope: Codable {
        var action: String
        var content: String           // note markdown / task title / habit name / project name / goal text
        var timestamp: Date?          // defaults to now (log_note)
        var tags: [String]?
        // add_task / update_task extras
        var priority: Int?            // TaskPriority rawValue (0/1/2)
        var targetDate: Date?         // nil = backlog/inbox (or clears set_project_goal's date)
        var details: String?
        var projectId: UUID?          // add_task / assign_task_to_project
        // complete_task / toggle_habit / assign_task_to_project / update_*/delete_* actions
        var id: UUID?
        // toggle_habit
        var date: Date?               // defaults to today
        // add_habit / update_habit
        var segment: String?          // "morning" | "afternoon" | "evening"
        // create_project
        var projectColor: String?     // "cyan" | "green" | "amber" | "alert"
        // add_book
        var author: String?
        var category: String?         // "personal" | "textbook"
        // update_task
        var recurrence: String?       // "daily" | "weekly" | "" (clears recurrence)
        var showOnCalendar: Bool?
        // archive_project
        var archived: Bool?
        // update_book_status
        var status: String?           // "queued" | "reading" | "finished"
    }

    @discardableResult
    func ingestHermesNote(
        _ content: String,
        timestamp: Date = .now,
        tags: [String] = []
    ) -> DailyLogItem {
        addLogItem(content, timestamp: timestamp, source: .hermes, tags: tags)
    }

    /// App Group directory the Hermes terminal skill writes JSON files into.
    /// Nil if the App Group capability isn't configured yet.
    var hermesInboxURL: URL? {
        guard let group = FileManager.default.containerURL(
            forSecurityApplicationGroupIdentifier: Self.appGroupID) else {
            return nil
        }
        let inbox = group.appendingPathComponent(Self.hermesInboxFolder, isDirectory: true)
        try? FileManager.default.createDirectory(
            at: inbox, withIntermediateDirectories: true)
        return inbox
    }

    /// Drains pending envelope files. Call on app launch and on
    /// scenePhase == .active so notes injected while the app was backgrounded
    /// appear immediately. Files are deleted only after a successful ingest;
    /// malformed files are renamed *.rejected instead of silently dropped.
    func processHermesInbox() {
        guard let inbox = hermesInboxURL else { return }
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601

        let files = (try? FileManager.default.contentsOfDirectory(
            at: inbox, includingPropertiesForKeys: nil)) ?? []

        for file in files where file.pathExtension == "json" {
            do {
                let data = try Data(contentsOf: file)
                let envelope = try decoder.decode(HermesEnvelope.self, from: data)
                try applyHermesEnvelope(envelope)
                try FileManager.default.removeItem(at: file)
            } catch {
                let rejected = file.deletingPathExtension()
                    .appendingPathExtension("rejected")
                try? FileManager.default.moveItem(at: file, to: rejected)
                logger.error("Hermes envelope rejected (\(file.lastPathComponent)): \(error)")
            }
        }
    }

    /// Applies one Hermes action to the database. Shared by the file-drop
    /// inbox (Simulator) and the network bridge (real device) — see
    /// HermesEnvelope's doc comment for the action list.
    func applyHermesEnvelope(_ envelope: HermesEnvelope) throws {
        switch envelope.action {
        case "log_note":
            ingestHermesNote(
                envelope.content,
                timestamp: envelope.timestamp ?? .now,
                tags: envelope.tags ?? [])
        case "add_task":
            let task = TaskItem(
                title: envelope.content,
                details: envelope.details ?? "",
                priority: envelope.priority.flatMap(TaskPriority.init) ?? .normal,
                targetDate: envelope.targetDate,
                source: .hermes,
                projectId: envelope.projectId)
            addTask(task)
        case "update_task":
            guard let id = envelope.id else {
                throw CocoaError(.coderValueNotFound)
            }
            guard let existing = try writer.read({ db in try TaskItem.fetchOne(db, key: id) }) else {
                throw CocoaError(.fileNoSuchFile) // → rejected for triage
            }
            let recurrence: TaskRecurrence?
            if let raw = envelope.recurrence {
                recurrence = raw.isEmpty ? nil : TaskRecurrence(rawValue: raw)
            } else {
                recurrence = existing.recurrence
            }
            updateTask(
                id,
                title: envelope.content.isEmpty ? existing.title : envelope.content,
                priority: envelope.priority.flatMap(TaskPriority.init) ?? existing.priority,
                targetDate: envelope.targetDate ?? existing.targetDate,
                projectId: envelope.projectId ?? existing.projectId,
                recurrence: recurrence,
                showOnCalendar: envelope.showOnCalendar ?? existing.showOnCalendar)
        case "delete_task":
            guard let id = envelope.id else {
                throw CocoaError(.coderValueNotFound)
            }
            let found = try writer.read { db in
                try TaskItem.filter(TaskItem.Columns.id == id).fetchCount(db) > 0
            }
            guard found else {
                throw CocoaError(.fileNoSuchFile) // → rejected for triage
            }
            deleteTask(id)
        case "complete_task":
            guard let id = envelope.id else {
                throw CocoaError(.coderValueNotFound)
            }
            let found = try writer.write { db -> Bool in
                guard var task = try TaskItem.fetchOne(db, key: id) else {
                    return false
                }
                task.setCompleted(true)
                try task.update(db)
                return true
            }
            guard found else {
                throw CocoaError(.fileNoSuchFile) // → rejected for triage
            }
        case "toggle_habit":
            guard let id = envelope.id else {
                throw CocoaError(.coderValueNotFound)
            }
            let exists = try writer.read { db in
                try Habit.filter(Habit.Columns.id == id).fetchCount(db) > 0
            }
            guard exists else {
                throw CocoaError(.fileNoSuchFile) // → rejected for triage
            }
            toggleHabit(id, on: envelope.date ?? .now)
        case "add_habit":
            guard let segment = envelope.segment.flatMap(RoutineSegment.init) else {
                throw CocoaError(.coderValueNotFound)
            }
            addHabit(Habit(name: envelope.content, segment: segment))
        case "update_habit":
            guard let id = envelope.id else {
                throw CocoaError(.coderValueNotFound)
            }
            let exists = try writer.read { db in
                try Habit.filter(Habit.Columns.id == id).fetchCount(db) > 0
            }
            guard exists else {
                throw CocoaError(.fileNoSuchFile) // → rejected for triage
            }
            updateHabit(
                id,
                name: envelope.content.isEmpty ? nil : envelope.content,
                segment: envelope.segment.flatMap(RoutineSegment.init))
        case "delete_habit":
            guard let id = envelope.id else {
                throw CocoaError(.coderValueNotFound)
            }
            let exists = try writer.read { db in
                try Habit.filter(Habit.Columns.id == id).fetchCount(db) > 0
            }
            guard exists else {
                throw CocoaError(.fileNoSuchFile) // → rejected for triage
            }
            deleteHabit(id)
        case "create_project":
            let color = envelope.projectColor.flatMap(ProjectColor.init) ?? .cyan
            addProject(Project(name: envelope.content, color: color))
        case "set_project_goal":
            guard let id = envelope.id else {
                throw CocoaError(.coderValueNotFound)
            }
            let found = try writer.read { db in
                try Project.filter(Project.Columns.id == id).fetchCount(db) > 0
            }
            guard found else {
                throw CocoaError(.fileNoSuchFile) // → rejected for triage
            }
            updateProjectGoal(id, goalDescription: envelope.content, goalTargetDate: envelope.targetDate)
        case "archive_project":
            guard let id = envelope.id else {
                throw CocoaError(.coderValueNotFound)
            }
            let found = try writer.read { db in
                try Project.filter(Project.Columns.id == id).fetchCount(db) > 0
            }
            guard found else {
                throw CocoaError(.fileNoSuchFile) // → rejected for triage
            }
            setProjectArchived(id, archived: envelope.archived ?? true)
        case "delete_project":
            guard let id = envelope.id else {
                throw CocoaError(.coderValueNotFound)
            }
            deleteProject(id) // silently refuses protected projects (e.g. Reading List)
        case "assign_task_to_project":
            guard let id = envelope.id else {
                throw CocoaError(.coderValueNotFound)
            }
            let found = try writer.read { db in
                try TaskItem.filter(TaskItem.Columns.id == id).fetchCount(db) > 0
            }
            guard found else {
                throw CocoaError(.fileNoSuchFile) // → rejected for triage
            }
            assignTask(id, toProject: envelope.projectId)
        case "add_book":
            let category = envelope.category.flatMap(BookCategory.init) ?? .personal
            addBook(Book(
                title: envelope.content,
                author: envelope.author ?? "",
                category: category))
        case "update_book_status":
            guard let id = envelope.id, let status = envelope.status.flatMap(BookStatus.init) else {
                throw CocoaError(.coderValueNotFound)
            }
            setBookStatus(id, status: status)
        case "delete_book":
            guard let id = envelope.id else {
                throw CocoaError(.coderValueNotFound)
            }
            deleteBook(id)
        case "remember":
            addHermesMemory(envelope.content)
        case "forget":
            guard let id = envelope.id else {
                throw CocoaError(.coderValueNotFound)
            }
            deleteHermesMemory(id)
        default:
            throw CocoaError(.featureUnsupported)
        }
    }

    // MARK: - Hermes network bridge

    /// Compact JSON snapshot sent with every chat message so the server-side
    /// agent can reference real IDs when emitting actions: pending tasks,
    /// habits (with today's completion state), projects, today's log, and
    /// the recent transcript.
    func buildHermesContext() -> String {
        struct ContextTask: Codable {
            let id: UUID, title: String, priority: Int, targetDate: Date?
        }
        struct ContextHabit: Codable {
            let id: UUID, name: String, segment: String, completedToday: Bool
        }
        struct ContextProject: Codable {
            let id: UUID, name: String, goal: String
        }
        struct ContextLogItem: Codable {
            let timestamp: Date, source: String, content: String
        }
        struct ContextMessage: Codable {
            let role: String, content: String
        }
        struct ContextMemory: Codable {
            let id: UUID, content: String
        }
        struct Context: Codable {
            let now: Date
            let pendingTasks: [ContextTask]
            let habits: [ContextHabit]
            let projects: [ContextProject]
            let todayLog: [ContextLogItem]
            let recentChat: [ContextMessage]
            /// Durable facts Hermes has chosen to remember (see "remember"/
            /// "forget" actions) — unlike recentChat, this ISN'T capped to a
            /// rolling window, so it's what gives Hermes continuity across
            /// sessions instead of starting over each conversation.
            let memory: [ContextMemory]
        }

        let today = Calendar.current.startOfDay(for: .now)
        let context: Context? = try? writer.read { db in
            let tasks = try TaskItem
                .filter(TaskItem.Columns.isCompleted == false)
                .order(TaskItem.Columns.priorityRaw.desc, TaskItem.Columns.createdAt)
                .fetchAll(db)
            let habits = try Habit
                .filter(Habit.Columns.isArchived == false)
                .fetchAll(db)
            let completions = try HabitCompletion
                .filter(HabitCompletion.Columns.date == today)
                .fetchAll(db)
            let completedToday = Set(completions.map(\.habitId))
            let projects = try Project
                .filter(Project.Columns.isArchived == false)
                .fetchAll(db)
            let log = try DailyLogItem
                .filter(DailyLogItem.Columns.timestamp >= today)
                .order(DailyLogItem.Columns.timestamp)
                .fetchAll(db)
            let chat = try ChatMessage
                .order(ChatMessage.Columns.timestamp.desc)
                .limit(10)
                .fetchAll(db)
                .reversed()
            let memories = try HermesMemory
                .order(HermesMemory.Columns.createdAt)
                .fetchAll(db)

            return Context(
                now: .now,
                pendingTasks: tasks.map {
                    ContextTask(id: $0.id, title: $0.title,
                                priority: $0.priorityRaw, targetDate: $0.targetDate)
                },
                habits: habits.map {
                    ContextHabit(id: $0.id, name: $0.name, segment: $0.segmentRaw,
                                 completedToday: completedToday.contains($0.id))
                },
                projects: projects.map {
                    ContextProject(id: $0.id, name: $0.name, goal: $0.goalDescription)
                },
                todayLog: log.map {
                    ContextLogItem(timestamp: $0.timestamp,
                                   source: $0.sourceRaw, content: $0.content)
                },
                recentChat: chat.map {
                    ContextMessage(role: $0.roleRaw, content: $0.content)
                },
                memory: memories.map {
                    ContextMemory(id: $0.id, content: $0.content)
                })
        }

        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        guard let context, let data = try? encoder.encode(context) else { return "{}" }
        return String(decoding: data, as: UTF8.self)
    }

    /// Sends one user message to the Hermes server and folds the response
    /// back into the local store: the reply becomes a hermes ChatMessage,
    /// and any returned envelope actions are applied. Failures surface as a
    /// TRANSMISSION_FAILED chat row rather than throwing.
    /// Short, human-readable summary of what an action did — used to build
    /// the visible receipt under Hermes's reply, so successes/failures are
    /// never something you have to go dig through a backup to discover.
    private func describeAction(_ envelope: HermesEnvelope) -> String {
        switch envelope.action {
        case "log_note": return "Logged note"
        case "add_task": return "Added task: \(envelope.content)"
        case "update_task": return "Updated task"
        case "delete_task": return "Deleted task"
        case "complete_task": return "Completed task"
        case "toggle_habit": return "Toggled habit"
        case "add_habit": return "Added habit: \(envelope.content)"
        case "update_habit": return "Updated habit"
        case "delete_habit": return "Deleted habit"
        case "create_project": return "Created project: \(envelope.content)"
        case "set_project_goal": return "Set project goal"
        case "archive_project": return (envelope.archived ?? true) ? "Archived project" : "Unarchived project"
        case "delete_project": return "Deleted project"
        case "assign_task_to_project": return "Reassigned task's project"
        case "add_book": return "Added book: \(envelope.content)"
        case "update_book_status": return "Updated book status"
        case "delete_book": return "Deleted book"
        case "remember": return "Remembered: \(envelope.content)"
        case "forget": return "Forgot a memory"
        default: return envelope.action
        }
    }

    func transmitToHermes(_ content: String) async {
        do {
            let reply = try await HermesTransport.send(
                message: content, context: buildHermesContext())

            var receipt: [String] = []
            for envelope in reply.actions {
                do {
                    try applyHermesEnvelope(envelope)
                    receipt.append("✓ \(describeAction(envelope))")
                } catch {
                    receipt.append("⚠ Failed: \(describeAction(envelope)) (\(error.localizedDescription))")
                }
            }
            for warning in reply.warnings {
                receipt.append("⚠ Skipped: \(warning)")
            }

            let fullText = receipt.isEmpty
                ? reply.reply
                : reply.reply + "\n\n" + receipt.joined(separator: "\n")
            addChatMessage(fullText, role: .hermes)
        } catch {
            addChatMessage(
                "// TRANSMISSION_FAILED: \(error.localizedDescription)",
                role: .hermes)
        }
    }

    /// Full JSON export of every user table — the payload POSTed to the
    /// Hermes server's /backup endpoint.
    func buildFullBackup() -> Data? {
        struct FullBackup: Codable {
            let exportedAt: Date
            let schemaVersion: Int
            let logs: [DailyLogItem]
            let habits: [Habit]
            let habitCompletions: [HabitCompletion]
            let metrics: [DailyMetric]
            let tasks: [TaskItem]
            let projects: [Project]
            let chatMessages: [ChatMessage]
            let focusSessions: [FocusSession]
            let energyLogs: [EnergyLog]
            let weeklyRetros: [WeeklyRetro]
            let books: [Book]
            let soberItems: [SoberItem]
            let soberPeriods: [SoberPeriod]
            let workoutPlans: [WorkoutPlan]
            let exercises: [Exercise]
            let workoutSessions: [WorkoutSession]
            let exerciseCompletions: [ExerciseCompletion]
            let workoutDayAssignments: [WorkoutDayAssignment]
            let hermesMemories: [HermesMemory]
        }

        let backup: FullBackup? = try? writer.read { db in
            FullBackup(
                exportedAt: .now,
                schemaVersion: 20,
                logs: try DailyLogItem.fetchAll(db),
                habits: try Habit.fetchAll(db),
                habitCompletions: try HabitCompletion.fetchAll(db),
                metrics: try DailyMetric.fetchAll(db),
                tasks: try TaskItem.fetchAll(db),
                projects: try Project.fetchAll(db),
                chatMessages: try ChatMessage.fetchAll(db),
                focusSessions: try FocusSession.fetchAll(db),
                energyLogs: try EnergyLog.fetchAll(db),
                weeklyRetros: try WeeklyRetro.fetchAll(db),
                books: try Book.fetchAll(db),
                soberItems: try SoberItem.fetchAll(db),
                soberPeriods: try SoberPeriod.fetchAll(db),
                workoutPlans: try WorkoutPlan.fetchAll(db),
                exercises: try Exercise.fetchAll(db),
                workoutSessions: try WorkoutSession.fetchAll(db),
                exerciseCompletions: try ExerciseCompletion.fetchAll(db),
                workoutDayAssignments: try WorkoutDayAssignment.fetchAll(db),
                hermesMemories: try HermesMemory.fetchAll(db))
        }

        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        encoder.outputFormatting = [.sortedKeys]
        return backup.flatMap { try? encoder.encode($0) }
    }

    /// Uploads a full backup to the Hermes server. Returns whether it
    /// succeeded — silently tolerable to fail (e.g. away from home Wi-Fi).
    @discardableResult
    func backupToHermesServer() async -> Bool {
        guard let data = buildFullBackup() else { return false }
        do {
            try await HermesTransport.uploadBackup(data)
            return true
        } catch {
            return false
        }
    }

    /// Pulls latest.json back from the Hermes server and REPLACES all local
    /// data with it, in one transaction — nothing is touched unless the
    /// download and decode both succeed. Arrays are optional so backups
    /// from older schema versions (missing newer tables) still restore.
    func restoreFromHermesServer() async -> Bool {
        struct RestorePayload: Decodable {
            var logs: [DailyLogItem]?
            var habits: [Habit]?
            var habitCompletions: [HabitCompletion]?
            var metrics: [DailyMetric]?
            var tasks: [TaskItem]?
            var projects: [Project]?
            var chatMessages: [ChatMessage]?
            var focusSessions: [FocusSession]?
            var energyLogs: [EnergyLog]?
            var weeklyRetros: [WeeklyRetro]?
            var books: [Book]?
            var soberItems: [SoberItem]?
            var soberPeriods: [SoberPeriod]?
            var workoutPlans: [WorkoutPlan]?
            var exercises: [Exercise]?
            var workoutSessions: [WorkoutSession]?
            var exerciseCompletions: [ExerciseCompletion]?
            var workoutDayAssignments: [WorkoutDayAssignment]?
            var hermesMemories: [HermesMemory]?
        }

        do {
            let data = try await HermesTransport.fetchBackup()
            let decoder = JSONDecoder()
            decoder.dateDecodingStrategy = .iso8601
            let payload = try decoder.decode(RestorePayload.self, from: data)

            try await writer.write { db in
                // Projects before tasks: TaskItem.projectId references it.
                try DailyLogItem.deleteAll(db)
                try HabitCompletion.deleteAll(db)
                try Habit.deleteAll(db)
                try DailyMetric.deleteAll(db)
                try TaskItem.deleteAll(db)
                try Project.deleteAll(db)
                try ChatMessage.deleteAll(db)
                try FocusSession.deleteAll(db)
                try EnergyLog.deleteAll(db)
                try WeeklyRetro.deleteAll(db)
                try Book.deleteAll(db)
                try SoberPeriod.deleteAll(db)
                try SoberItem.deleteAll(db)
                try WorkoutDayAssignment.deleteAll(db)
                try WorkoutSession.deleteAll(db)
                try ExerciseCompletion.deleteAll(db)
                try Exercise.deleteAll(db)
                try WorkoutPlan.deleteAll(db)
                try HermesMemory.deleteAll(db)

                for row in payload.projects ?? [] { try row.insert(db) }
                for row in payload.logs ?? [] { try row.insert(db) }
                for row in payload.habits ?? [] { try row.insert(db) }
                for row in payload.habitCompletions ?? [] { try row.insert(db) }
                for row in payload.metrics ?? [] { try row.insert(db) }
                for row in payload.tasks ?? [] { try row.insert(db) }
                for row in payload.chatMessages ?? [] { try row.insert(db) }
                for row in payload.focusSessions ?? [] { try row.insert(db) }
                for row in payload.energyLogs ?? [] { try row.insert(db) }
                for row in payload.weeklyRetros ?? [] { try row.insert(db) }
                for row in payload.books ?? [] { try row.insert(db) }
                for row in payload.soberItems ?? [] { try row.insert(db) }
                for row in payload.soberPeriods ?? [] { try row.insert(db) }
                for row in payload.workoutPlans ?? [] { try row.insert(db) }
                for row in payload.exercises ?? [] { try row.insert(db) }
                for row in payload.workoutSessions ?? [] { try row.insert(db) }
                for row in payload.exerciseCompletions ?? [] { try row.insert(db) }
                for row in payload.workoutDayAssignments ?? [] { try row.insert(db) }
                for row in payload.hermesMemories ?? [] { try row.insert(db) }
            }
            NotificationScheduler.rescheduleAll(writer: writer)
            return true
        } catch {
            logger.error("Restore failed: \(error)")
            return false
        }
    }

    /// Read side of the Hermes interface: exports today's log and all
    /// pending tasks as JSON to the App Group so the agent's terminal skill
    /// can read app state without touching the SQLite store directly.
    func exportStateForHermes() {
        guard let group = FileManager.default.containerURL(
            forSecurityApplicationGroupIdentifier: Self.appGroupID) else { return }

        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]

        // Today's log
        let today = Calendar.current.startOfDay(for: .now)
        let items = (try? writer.read { db in
            try DailyLogItem
                .filter(DailyLogItem.Columns.timestamp >= today)
                .order(DailyLogItem.Columns.timestamp)
                .fetchAll(db)
        }) ?? []

        struct ExportItem: Codable {
            let timestamp: Date
            let source: String
            let content: String
            let tags: [String]
        }
        let logPayload = items.map {
            ExportItem(
                timestamp: $0.timestamp,
                source: $0.sourceRaw,
                content: $0.content,
                tags: $0.tags)
        }
        if let data = try? encoder.encode(logPayload) {
            try? data.write(
                to: group.appendingPathComponent("hermes_state_today.json"),
                options: .atomic)
        }

        // Pending tasks
        let tasks = (try? writer.read { db in
            try TaskItem
                .filter(TaskItem.Columns.isCompleted == false)
                .order(
                    TaskItem.Columns.priorityRaw.desc,
                    TaskItem.Columns.createdAt)
                .fetchAll(db)
        }) ?? []

        struct ExportTask: Codable {
            let id: UUID
            let title: String
            let details: String
            let priority: Int
            let targetDate: Date?
            let createdAt: Date
            let source: String
        }
        let taskPayload = tasks.map {
            ExportTask(
                id: $0.id,
                title: $0.title,
                details: $0.details,
                priority: $0.priorityRaw,
                targetDate: $0.targetDate,
                createdAt: $0.createdAt,
                source: $0.sourceRaw)
        }
        if let data = try? encoder.encode(taskPayload) {
            try? data.write(
                to: group.appendingPathComponent("hermes_pending_tasks.json"),
                options: .atomic)
        }
    }
}
