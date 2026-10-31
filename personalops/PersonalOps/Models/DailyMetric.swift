//
//  DailyMetric.swift
//  PersonalOps
//
//  One row per calendar day holding the four tracked metrics. Every field is
//  optional (nil = "no data yet") and carries a per-field `isManual` flag so
//  a manual override entered via the UI sliders is never clobbered by a later
//  HealthKit sync.
//

import Foundation
import GRDB

struct DailyMetric: Codable, Equatable, Identifiable, Sendable,
    FetchableRecord, PersistableRecord {

    static let databaseTableName = "dailyMetric"

    var id: UUID
    /// Start-of-day; exactly one DailyMetric should exist per day
    /// (enforced by a unique index + DataController's fetch-or-create upsert).
    var day: Date

    var sleepHours: Double?
    var steps: Int?
    var activeEnergyKcal: Double?
    var weightKg: Double?
    /// Self-reported only — iOS has no way to read Screen Time without the
    /// Family Controls entitlement, which Apple doesn't grant to
    /// personal/xtool-signed builds. See ScreenTimeSection in
    /// AnalyticsDashboardView for the manual entry field.
    var screenTimeMinutes: Double?

    var sleepIsManual: Bool
    var stepsIsManual: Bool
    var energyIsManual: Bool
    var weightIsManual: Bool
    var screenTimeIsManual: Bool

    /// When HealthKit last wrote into this row (nil = never synced).
    var lastSyncedAt: Date?

    init(day: Date = .now) {
        self.id = UUID()
        self.day = Calendar.current.startOfDay(for: day)
        self.sleepHours = nil
        self.steps = nil
        self.activeEnergyKcal = nil
        self.weightKg = nil
        self.screenTimeMinutes = nil
        self.sleepIsManual = false
        self.stepsIsManual = false
        self.energyIsManual = false
        self.weightIsManual = false
        self.screenTimeIsManual = false
        self.lastSyncedAt = nil
    }
}

extension DailyMetric {
    enum Columns {
        static let id = Column(CodingKeys.id)
        static let day = Column(CodingKeys.day)
    }
}
