//
//  WeeklyRetro.swift
//  PersonalOps
//
//  The Weekly Retrospective Form — three fixed prompts answered once per
//  week, keyed to that week's Sunday (start-of-week). One row per week;
//  editing mid-week updates the same row rather than creating a new one.
//

import Foundation
import GRDB

struct WeeklyRetro: Codable, Equatable, Identifiable, Sendable,
    FetchableRecord, PersistableRecord {

    static let databaseTableName = "weeklyRetro"

    var id: UUID
    /// Start-of-week (Sunday) this retro belongs to.
    var weekStart: Date
    var wentRight: String
    var systemBroke: String
    var nextFix: String
    var updatedAt: Date

    init(weekStart: Date) {
        self.id = UUID()
        self.weekStart = weekStart
        self.wentRight = ""
        self.systemBroke = ""
        self.nextFix = ""
        self.updatedAt = .now
    }
}

extension WeeklyRetro {
    enum Columns {
        static let id = Column(CodingKeys.id)
        static let weekStart = Column(CodingKeys.weekStart)
    }
}
