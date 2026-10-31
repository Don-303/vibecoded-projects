//
//  MockTelemetryProvider.swift
//  PersonalOps
//
//  Seeds 14 days of back-dated metrics, tasks, and habit history into a
//  completely empty store so the analytics charts and streak grids render
//  meaningfully on first launch. Never touches a store that already has any
//  user data (a single metric, task, habit, or log row disables seeding).
//

import Foundation
import GRDB

enum MockTelemetryProvider {

    /// Seeds only when every table is empty (i.e. a fresh install).
    static func seedIfEmpty(into writer: any DatabaseWriter) {
        do {
            try writer.write { db in
                let isEmpty = try DailyMetric.fetchCount(db) == 0
                    && TaskItem.fetchCount(db) == 0
                    && Habit.fetchCount(db) == 0
                    && DailyLogItem.fetchCount(db) == 0
                guard isEmpty else { return }
                try seed(db)
            }
        } catch {
            // Non-fatal: an unseeded store just shows empty states.
        }
    }

    private static func seed(_ db: Database) throws {
        let calendar = Calendar.current
        let today = calendar.startOfDay(for: .now)

        // Deterministic pseudo-random so the demo data looks organic but
        // renders identically across launches.
        var rng = SeededGenerator(seed: 0x5EED_2026)

        // 14 days of metrics (today stays sparse so HealthKit/manual entry
        // visibly takes over the live row).
        for offset in stride(from: 13, through: 1, by: -1) {
            let day = calendar.date(byAdding: .day, value: -offset, to: today)!
            var metric = DailyMetric(day: day)
            metric.steps = 4_000 + Int(rng.next() % 9_000)
            metric.sleepHours = (5.5 + Double(rng.next() % 40) / 10).rounded(toPlaces: 2)
            metric.activeEnergyKcal = 250 + Double(rng.next() % 550)
            metric.weightKg = (76.0 + Double(rng.next() % 30) / 10 - 1.5).rounded(toPlaces: 1)
            try metric.insert(db)
        }

        // Habits with organic completion history.
        let habitSpecs: [(String, RoutineSegment, Double)] = [
            ("Hydrate 500ml", .morning, 0.9),
            ("Deep work block", .morning, 0.7),
            ("Walk outside", .afternoon, 0.6),
            ("Read 20 pages", .evening, 0.75),
            ("Journal", .evening, 0.5),
        ]
        for (index, spec) in habitSpecs.enumerated() {
            let created = calendar.date(byAdding: .day, value: -13, to: today)!
            let habit = Habit(
                name: spec.0, segment: spec.1,
                sortOrder: index, createdAt: created)
            try habit.insert(db)
            for offset in stride(from: 13, through: 1, by: -1) {
                let day = calendar.date(byAdding: .day, value: -offset, to: today)!
                if Double(rng.next() % 100) / 100 < spec.2 {
                    try HabitCompletion(date: day, habitId: habit.id).insert(db)
                }
            }
        }

        // A mix of open and completed tasks across the window.
        let doneTitles = [
            "Wire telemetry export", "Review Q3 checklist",
            "Renew gym membership", "Clear photo backlog",
        ]
        for (index, title) in doneTitles.enumerated() {
            var task = TaskItem(
                title: title,
                priority: index % 2 == 0 ? .normal : .high,
                targetDate: calendar.date(byAdding: .day, value: -(index + 2), to: today))
            task.isCompleted = true
            task.completedAt = calendar.date(byAdding: .day, value: -(index + 1), to: today)
            try task.insert(db)
        }
        try TaskItem(
            title: "Calibrate sleep schedule", priority: .high,
            targetDate: today).insert(db)
        try TaskItem(
            title: "Draft weekly review", priority: .normal,
            targetDate: today).insert(db)
        try TaskItem(title: "Research standing desk", priority: .low).insert(db)

        // A few historical log lines so FILTER_QUERY has something to match.
        let notes: [(Int, String, LogSource)] = [
            (3, "Morning run felt easier than last week", .user),
            (2, "Synced 7.2h sleep from overnight wearable", .healthKit),
            (1, "Inbox drained; two tasks promoted to today", .hermes),
        ]
        for (offset, content, source) in notes {
            let stamp = calendar.date(byAdding: .day, value: -offset, to: today)!
                .addingTimeInterval(9 * 3600)
            try DailyLogItem(
                content: content, timestamp: stamp,
                source: source, tags: ["mock"]).insert(db)
        }
    }
}

// MARK: - Deterministic RNG

private struct SeededGenerator {
    private var state: UInt64

    init(seed: UInt64) {
        state = seed == 0 ? 0x9E3779B97F4A7C15 : seed
    }

    mutating func next() -> UInt64 {
        // xorshift64* — quality is irrelevant here, determinism is the point.
        state ^= state >> 12
        state ^= state << 25
        state ^= state >> 27
        return state &* 0x2545F4914F6CDD1D
    }
}

private extension Double {
    func rounded(toPlaces places: Int) -> Double {
        let factor = pow(10, Double(places))
        return (self * factor).rounded() / factor
    }
}
