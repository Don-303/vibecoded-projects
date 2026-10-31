//
//  NewsItem.swift
//  PersonalOps
//
//  A single headline pulled from the Daily Briefing's RSS feeds (BBC
//  Business + BBC Top Stories/breaking). Rows are wholly replaced on each
//  successful refresh (see DataController.refreshBriefing) — this is a
//  cache of "latest known headlines," not an accumulating history. The
//  briefing is weighted toward financial news (see BriefingService).
//

import Foundation
import GRDB

enum NewsCategory: String, Codable, CaseIterable, Sendable {
    case business
    case breaking

    var displayName: String {
        switch self {
        case .business: return "Business"
        case .breaking: return "Breaking"
        }
    }
}

struct NewsItem: Codable, Equatable, Identifiable, Sendable,
    FetchableRecord, PersistableRecord {

    static let databaseTableName = "newsItem"

    var id: UUID
    var categoryRaw: String
    var title: String
    var link: String
    var publishedAt: Date

    var category: NewsCategory {
        get { NewsCategory(rawValue: categoryRaw) ?? .breaking }
        set { categoryRaw = newValue.rawValue }
    }

    init(category: NewsCategory, title: String, link: String, publishedAt: Date) {
        self.id = UUID()
        self.categoryRaw = category.rawValue
        self.title = title
        self.link = link
        self.publishedAt = publishedAt
    }
}

extension NewsItem {
    enum Columns {
        static let id = Column(CodingKeys.id)
        static let publishedAt = Column(CodingKeys.publishedAt)
    }
}
