//
//  FocusSession.swift
//  PersonalOps
//
//  A completed Deep Work Matrix countdown block (25/50 min preset). Only
//  completed sessions are logged — aborting a session logs nothing.
//

import Foundation
import GRDB

struct FocusSession: Codable, Equatable, Identifiable, Sendable,
    FetchableRecord, PersistableRecord {

    static let databaseTableName = "focusSession"

    var id: UUID
    var startedAt: Date
    var durationMinutes: Int
    var completedAt: Date

    init(startedAt: Date, durationMinutes: Int, completedAt: Date = .now) {
        self.id = UUID()
        self.startedAt = startedAt
        self.durationMinutes = durationMinutes
        self.completedAt = completedAt
    }
}

extension FocusSession {
    enum Columns {
        static let id = Column(CodingKeys.id)
        static let completedAt = Column(CodingKeys.completedAt)
    }
}
