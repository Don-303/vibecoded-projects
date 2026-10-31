//
//  ChatMessage.swift
//  PersonalOps
//
//  Local transcript for the Hermes chat tab. Messages are persisted and
//  rendered immediately; there is currently no live transport wiring a
//  reply back from a running Hermes/Claude session (see ChatView's header
//  comment) — sending a message just appends a "TRANSMIT_PENDING" row that
//  a future network bridge would resolve.
//

import Foundation
import GRDB

enum ChatRole: String, Codable, CaseIterable, Sendable {
    case user
    case hermes
}

struct ChatMessage: Codable, Equatable, Identifiable, Sendable,
    FetchableRecord, PersistableRecord {

    static let databaseTableName = "chatMessage"

    var id: UUID
    var roleRaw: String
    var content: String
    var timestamp: Date

    var role: ChatRole {
        get { ChatRole(rawValue: roleRaw) ?? .user }
        set { roleRaw = newValue.rawValue }
    }

    init(content: String, role: ChatRole, timestamp: Date = .now) {
        self.id = UUID()
        self.roleRaw = role.rawValue
        self.content = content
        self.timestamp = timestamp
    }
}

extension ChatMessage {
    enum Columns {
        static let id = Column(CodingKeys.id)
        static let timestamp = Column(CodingKeys.timestamp)
    }
}
