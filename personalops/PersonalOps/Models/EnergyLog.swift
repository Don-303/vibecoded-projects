//
//  EnergyLog.swift
//  PersonalOps
//
//  Nightly 1-5 energy/focus self-rating — the Energy Audit Log. One row per
//  day; logging again the same day overwrites that day's rating rather than
//  duplicating it.
//

import Foundation
import GRDB

struct EnergyLog: Codable, Equatable, Identifiable, Sendable,
    FetchableRecord, PersistableRecord {

    static let databaseTableName = "energyLog"

    var id: UUID
    var day: Date
    var rating: Int

    init(day: Date = .now, rating: Int) {
        self.id = UUID()
        self.day = Calendar.current.startOfDay(for: day)
        self.rating = rating
    }
}

extension EnergyLog {
    enum Columns {
        static let id = Column(CodingKeys.id)
        static let day = Column(CodingKeys.day)
        static let rating = Column(CodingKeys.rating)
    }
}
