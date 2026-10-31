//
//  SoberItem.swift
//  PersonalOps
//
//  A single thing being tracked (e.g. "Alcohol", "Smoking", "Sugar") — the
//  Sober Tracker supports multiple concurrent items, each with its own
//  SoberPeriod history/streak.
//

import Foundation
import GRDB

struct SoberItem: Codable, Equatable, Identifiable, Sendable,
    FetchableRecord, PersistableRecord {

    static let databaseTableName = "soberItem"

    var id: UUID
    var name: String
    var sortOrder: Int
    var createdAt: Date

    init(name: String, sortOrder: Int = 0, createdAt: Date = .now) {
        self.id = UUID()
        self.name = name
        self.sortOrder = sortOrder
        self.createdAt = createdAt
    }
}

extension SoberItem {
    enum Columns {
        static let id = Column(CodingKeys.id)
        static let sortOrder = Column(CodingKeys.sortOrder)
        static let createdAt = Column(CodingKeys.createdAt)
    }
}

// MARK: - View-facing aggregate

struct SoberItemInfo: Equatable, Identifiable, Sendable {
    var item: SoberItem
    var periods: [SoberPeriod]

    var id: UUID { item.id }

    var active: SoberPeriod? { periods.first { $0.endDate == nil } }
    var bestStreak: Int { periods.map(\.days).max() ?? 0 }
}
