//
//  WorkoutPlan.swift
//  PersonalOps
//
//  Gym tracker: a WorkoutPlan (e.g. "Push Day") holds an ordered list of
//  Exercise templates (target sets/reps). Completing a plan for the day
//  logs a WorkoutSession, the same completion-history pattern HabitCompletion
//  uses for the streak grid.
//

import Foundation
import GRDB

struct WorkoutPlan: Codable, Equatable, Identifiable, Sendable,
    FetchableRecord, PersistableRecord {

    static let databaseTableName = "workoutPlan"

    var id: UUID
    var name: String
    var sortOrder: Int
    var createdAt: Date
    /// Calendar.weekday convention: 1 = Sunday ... 7 = Saturday. Nil = not
    /// assigned to a specific day.
    var scheduledWeekday: Int?

    init(
        name: String,
        sortOrder: Int = 0,
        createdAt: Date = .now,
        scheduledWeekday: Int? = nil
    ) {
        self.id = UUID()
        self.name = name
        self.sortOrder = sortOrder
        self.createdAt = createdAt
        self.scheduledWeekday = scheduledWeekday
    }
}

extension WorkoutPlan {
    enum Columns {
        static let id = Column(CodingKeys.id)
        static let sortOrder = Column(CodingKeys.sortOrder)
        static let createdAt = Column(CodingKeys.createdAt)
    }
}

struct Exercise: Codable, Equatable, Identifiable, Sendable,
    FetchableRecord, PersistableRecord {

    static let databaseTableName = "exercise"

    var id: UUID
    var workoutPlanId: UUID
    var name: String
    var targetSets: Int
    var targetReps: Int
    var sortOrder: Int

    init(
        workoutPlanId: UUID,
        name: String,
        targetSets: Int = 3,
        targetReps: Int = 10,
        sortOrder: Int = 0
    ) {
        self.id = UUID()
        self.workoutPlanId = workoutPlanId
        self.name = name
        self.targetSets = targetSets
        self.targetReps = targetReps
        self.sortOrder = sortOrder
    }
}

extension Exercise {
    enum Columns {
        static let id = Column(CodingKeys.id)
        static let workoutPlanId = Column(CodingKeys.workoutPlanId)
        static let sortOrder = Column(CodingKeys.sortOrder)
    }
}

/// One exercise checked off during today's session — mirrors HabitCompletion.
/// Checking off every exercise for a plan on a given day auto-completes
/// (and unchecking one un-completes) that plan's WorkoutSession — see
/// DataController.toggleExerciseCompletion.
struct ExerciseCompletion: Codable, Equatable, Identifiable, Sendable,
    FetchableRecord, PersistableRecord {

    static let databaseTableName = "exerciseCompletion"

    var id: UUID
    var exerciseId: UUID
    var date: Date

    init(exerciseId: UUID, date: Date = .now) {
        self.id = UUID()
        self.exerciseId = exerciseId
        self.date = Calendar.current.startOfDay(for: date)
    }
}

extension ExerciseCompletion {
    enum Columns {
        static let id = Column(CodingKeys.id)
        static let exerciseId = Column(CodingKeys.exerciseId)
        static let date = Column(CodingKeys.date)
    }
}

/// One completed session of a plan on a given day — mirrors HabitCompletion.
struct WorkoutSession: Codable, Equatable, Identifiable, Sendable,
    FetchableRecord, PersistableRecord {

    static let databaseTableName = "workoutSession"

    var id: UUID
    var workoutPlanId: UUID
    var date: Date

    init(workoutPlanId: UUID, date: Date = .now) {
        self.id = UUID()
        self.workoutPlanId = workoutPlanId
        self.date = Calendar.current.startOfDay(for: date)
    }
}

extension WorkoutSession {
    enum Columns {
        static let id = Column(CodingKeys.id)
        static let workoutPlanId = Column(CodingKeys.workoutPlanId)
        static let date = Column(CodingKeys.date)
    }
}

// MARK: - View-facing aggregate

struct WorkoutPlanInfo: Equatable, Identifiable, Sendable {
    var plan: WorkoutPlan
    var exercises: [Exercise]
    var completedDays: Set<Date>

    var id: UUID { plan.id }

    func isCompletedToday() -> Bool {
        completedDays.contains(Calendar.current.startOfDay(for: .now))
    }

    /// Current consecutive-day streak, same rule as HabitInfo.currentStreak.
    var currentStreak: Int {
        let calendar = Calendar.current
        let today = calendar.startOfDay(for: .now)
        var cursor = completedDays.contains(today)
            ? today
            : calendar.date(byAdding: .day, value: -1, to: today)!
        guard completedDays.contains(cursor) else { return 0 }
        var streak = 0
        while completedDays.contains(cursor) {
            streak += 1
            guard let previous = calendar.date(byAdding: .day, value: -1, to: cursor) else { break }
            cursor = previous
        }
        return streak
    }
}
