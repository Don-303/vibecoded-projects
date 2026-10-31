//
//  SoberPeriod.swift
//  PersonalOps
//
//  Sobriety tracker: one row per continuous sober stretch. The row with
//  endDate == nil is the active streak; a reset closes it (endDate = now)
//  and opens a fresh one, preserving history so the best streak survives.
//

import Foundation
import GRDB

struct SoberPeriod: Codable, Equatable, Identifiable, Sendable,
    FetchableRecord, PersistableRecord {

    static let databaseTableName = "soberPeriod"

    var id: UUID
    var soberItemId: UUID
    var startDate: Date
    var endDate: Date?

    init(soberItemId: UUID, startDate: Date, endDate: Date? = nil) {
        self.id = UUID()
        self.soberItemId = soberItemId
        self.startDate = Calendar.current.startOfDay(for: startDate)
        self.endDate = endDate
    }

    /// Whole days this period spans (to now while active).
    var days: Int {
        let calendar = Calendar.current
        let end = calendar.startOfDay(for: endDate ?? .now)
        let start = calendar.startOfDay(for: startDate)
        return max(0, calendar.dateComponents([.day], from: start, to: end).day ?? 0)
    }
}

extension SoberPeriod {
    enum Columns {
        static let id = Column(CodingKeys.id)
        static let soberItemId = Column(CodingKeys.soberItemId)
        static let startDate = Column(CodingKeys.startDate)
        static let endDate = Column(CodingKeys.endDate)
    }
}
