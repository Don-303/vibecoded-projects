//
//  Book.swift
//  PersonalOps
//
//  Entry in the permanent Reading List project: title/author plus a
//  queued → reading → finished pipeline. finishedAt drives the Quarterly
//  Velocity tracker's books-finished count.
//

import Foundation
import GRDB

enum BookStatus: String, Codable, CaseIterable, Sendable {
    case reading
    case queued
    case finished

    var displayName: String { rawValue.capitalized }

    /// Swipe-to-advance order: queued → reading → finished.
    var next: BookStatus? {
        switch self {
        case .queued: return .reading
        case .reading: return .finished
        case .finished: return nil
        }
    }
}

enum BookCategory: String, Codable, CaseIterable, Sendable {
    case personal
    case textbook

    var displayName: String { self == .textbook ? "Textbooks" : "Personal Reading" }
}

struct Book: Codable, Equatable, Identifiable, Sendable,
    FetchableRecord, PersistableRecord {

    static let databaseTableName = "book"

    var id: UUID
    var title: String
    var author: String
    var statusRaw: String
    var categoryRaw: String
    var addedAt: Date
    var finishedAt: Date?

    var status: BookStatus {
        get { BookStatus(rawValue: statusRaw) ?? .queued }
        set { statusRaw = newValue.rawValue }
    }

    var category: BookCategory {
        get { BookCategory(rawValue: categoryRaw) ?? .personal }
        set { categoryRaw = newValue.rawValue }
    }

    init(
        title: String,
        author: String,
        status: BookStatus = .queued,
        category: BookCategory = .personal
    ) {
        self.id = UUID()
        self.title = title
        self.author = author
        self.statusRaw = status.rawValue
        self.categoryRaw = category.rawValue
        self.addedAt = .now
        self.finishedAt = nil
    }
}

extension Book {
    enum Columns {
        static let id = Column(CodingKeys.id)
        static let statusRaw = Column(CodingKeys.statusRaw)
        static let categoryRaw = Column(CodingKeys.categoryRaw)
        static let addedAt = Column(CodingKeys.addedAt)
        static let finishedAt = Column(CodingKeys.finishedAt)
    }
}
