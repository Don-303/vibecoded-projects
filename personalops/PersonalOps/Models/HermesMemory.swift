//
//  HermesMemory.swift
//  PersonalOps
//
//  Durable facts Hermes has chosen to remember — preferences, goals, recurring
//  context — that survive far longer than the rolling last-10-messages chat
//  window sent with every request (see DataController.buildHermesContext).
//  Written via the "remember" action, removed via "forget"; also editable
//  directly from the Hermes tab's gear sheet for transparency/control.
//

import Foundation
import GRDB

struct HermesMemory: Codable, Equatable, Identifiable, Sendable,
    FetchableRecord, PersistableRecord {

    static let databaseTableName = "hermesMemory"

    var id: UUID
    var content: String
    var createdAt: Date

    init(content: String, createdAt: Date = .now) {
        self.id = UUID()
        self.content = content
        self.createdAt = createdAt
    }
}

extension HermesMemory {
    enum Columns {
        static let id = Column(CodingKeys.id)
        static let createdAt = Column(CodingKeys.createdAt)
    }
}
