//
//  Habit.swift
//  PersonalOps
//
//  A routine item that belongs to a segment of the day (Morning / Afternoon /
//  Evening). Completions are stored as separate rows (one per habit per day)
//  so streaks can be computed from history and the Hermes Agent can query
//  "what did/didn't get done today" cheaply.
//
//  `HabitInfo` is the view-facing aggregate: a habit joined with its set of
//  completed days, from which today's status and streaks are derived. It is
//  produced by `HabitsRequest` (see HabitsView) in a single database read.
//

import Foundation
import GRDB

enum RoutineSegment: String, Codable, CaseIterable, Sendable {
    case morning
    case afternoon
    case evening

    var displayName: String { rawValue.capitalized }
}

struct Habit: Codable, Equatable, Identifiable, Sendable,
    FetchableRecord, PersistableRecord {

    static let databaseTableName = "habit"

    var id: UUID
    var name: String
    var segmentRaw: String
    /// Manual ordering within a segment's checklist.
    var sortOrder: Int
    var createdAt: Date
    var isArchived: Bool
    /// Permanent habits (e.g. the seeded Gym tracker) can't be deleted from
    /// the UI — DataController.deleteHabit refuses on this flag.
    var isProtected: Bool

    var segment: RoutineSegment {
        get { RoutineSegment(rawValue: segmentRaw) ?? .morning }
        set { segmentRaw = newValue.rawValue }
    }

    init(
        name: String,
        segment: RoutineSegment,
        sortOrder: Int = 0,
        createdAt: Date = .now,
        isProtected: Bool = false
    ) {
        self.id = UUID()
        self.name = name
        self.segmentRaw = segment.rawValue
        self.sortOrder = sortOrder
        self.createdAt = createdAt
        self.isArchived = false
        self.isProtected = isProtected
    }
}

extension Habit {
    enum Columns {
        static let id = Column(CodingKeys.id)
        static let name = Column(CodingKeys.name)
        static let sortOrder = Column(CodingKeys.sortOrder)
        static let createdAt = Column(CodingKeys.createdAt)
        static let isArchived = Column(CodingKeys.isArchived)
        static let isProtected = Column(CodingKeys.isProtected)
    }
}

struct HabitCompletion: Codable, Equatable, Identifiable, Sendable,
    FetchableRecord, PersistableRecord {

    static let databaseTableName = "habitCompletion"

    var id: UUID
    /// Normalized to start-of-day by `DataController.toggleHabit`.
    var date: Date
    var habitId: UUID

    init(date: Date = .now, habitId: UUID) {
        self.id = UUID()
        self.date = Calendar.current.startOfDay(for: date)
        self.habitId = habitId
    }
}

extension HabitCompletion {
    enum Columns {
        static let id = Column(CodingKeys.id)
        static let date = Column(CodingKeys.date)
        static let habitId = Column(CodingKeys.habitId)
    }
}

// MARK: - View-facing aggregate

struct HabitInfo: Equatable, Identifiable, Sendable {
    var habit: Habit
    /// Start-of-day dates on which this habit was completed.
    var completedDays: Set<Date>

    var id: UUID { habit.id }

    func isCompleted(on date: Date = .now, calendar: Calendar = .current) -> Bool {
        completedDays.contains(calendar.startOfDay(for: date))
    }

    /// Consecutive completed days ending today (or yesterday, so an
    /// unfinished "today" doesn't zero the streak before the day is over).
    var currentStreak: Int {
        let calendar = Calendar.current
        guard !completedDays.isEmpty else { return 0 }

        let today = calendar.startOfDay(for: .now)
        // Anchor on today if completed, otherwise yesterday.
        var cursor = completedDays.contains(today)
            ? today
            : calendar.date(byAdding: .day, value: -1, to: today)!

        var streak = 0
        while completedDays.contains(cursor) {
            streak += 1
            cursor = calendar.date(byAdding: .day, value: -1, to: cursor)!
        }
        return streak
    }

    /// Longest consecutive-day run in the habit's history.
    var bestStreak: Int {
        let calendar = Calendar.current
        let days = completedDays.sorted()
        var best = 0
        var run = 0
        var previous: Date?
        for date in days {
            if let previous,
               calendar.date(byAdding: .day, value: 1, to: previous) == date {
                run += 1
            } else {
                run = 1
            }
            best = max(best, run)
            previous = date
        }
        return best
    }
}
