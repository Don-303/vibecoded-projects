//
//  BriefingService.swift
//  PersonalOps
//
//  Fetches the Daily Briefing headlines from two free, no-signup BBC RSS
//  feeds — Business and Top Stories (breaking news) — and weights the
//  result toward financial news. Parsed with Foundation's XMLParser rather
//  than a dependency, since the feeds only need <item><title>/<link>/
//  <pubDate> extracted.
//

import Foundation
import OSLog

enum BriefingService {
    private static let logger = Logger(
        subsystem: "com.yourteam.personalops", category: "BriefingService")

    /// Several free, no-signup/no-API-key feeds per category — business gets
    /// the most sources since the briefing is "mainly financial" per spec.
    private static let feeds: [(NewsCategory, URL)] = [
        (.business, URL(string: "https://feeds.bbci.co.uk/news/business/rss.xml")!),
        (.business, URL(string: "http://feeds.marketwatch.com/marketwatch/topstories/")!),
        (.business, URL(string: "http://feeds.marketwatch.com/marketwatch/marketpulse/")!),
        (.business, URL(string: "https://finance.yahoo.com/news/rssindex")!),
        (.business, URL(string: "https://feeds.a.dj.com/rss/RSSMarketsMain.xml")!),
        (.business, URL(string: "https://www.nasdaq.com/feed/rssoutbound?category=Stocks")!),
        (.business, URL(string: "https://feeds.bloomberg.com/markets/news.rss")!),
        (.business, URL(string: "https://www.ft.com/rss/home")!),
        (.business, URL(string: "https://www.economist.com/finance-and-economics/rss.xml")!),
        (.breaking, URL(string: "https://feeds.bbci.co.uk/news/rss.xml")!),
    ]

    /// How many of each category's freshest items to keep, after merging
    /// every source feed for that category — biased toward financial news
    /// per the briefing's "mainly financial" brief.
    private static let perCategoryLimit: [NewsCategory: Int] = [
        .business: 14,
        .breaking: 3,
    ]

    /// Fetches every feed concurrently, merges same-category sources, and
    /// returns the combined, business-weighted result. A feed that fails to
    /// load (offline, DNS, etc.) is skipped rather than failing the whole
    /// briefing — partial headlines beat none.
    static func fetchBriefing() async -> [NewsItem] {
        let itemsByCategory = await withTaskGroup(
            of: (NewsCategory, [NewsItem]).self
        ) { group -> [NewsCategory: [NewsItem]] in
            for (category, url) in feeds {
                group.addTask {
                    (category, await fetchFeed(category: category, url: url))
                }
            }
            var merged: [NewsCategory: [NewsItem]] = [:]
            for await (category, feedItems) in group {
                merged[category, default: []].append(contentsOf: feedItems)
            }
            return merged
        }

        var items: [NewsItem] = []
        for (category, categoryItems) in itemsByCategory {
            let limit = perCategoryLimit[category] ?? categoryItems.count
            let deduped = Dictionary(
                categoryItems.map { ($0.title, $0) },
                uniquingKeysWith: { first, _ in first }
            ).values
            let freshest = deduped.sorted { $0.publishedAt > $1.publishedAt }
            items.append(contentsOf: freshest.prefix(limit))
        }
        return items.sorted { $0.publishedAt > $1.publishedAt }
    }

    private static func fetchFeed(category: NewsCategory, url: URL) async -> [NewsItem] {
        do {
            let (data, _) = try await URLSession.shared.data(from: url)
            let parser = RSSParser(category: category)
            return parser.parse(data)
        } catch {
            logger.notice("Briefing fetch failed for \(category.rawValue): \(error.localizedDescription)")
            return []
        }
    }
}

/// Minimal RSS 2.0 `<item>` extractor — pulls title/link/pubDate only.
private final class RSSParser: NSObject, XMLParserDelegate {
    private let category: NewsCategory
    private var items: [NewsItem] = []

    private var currentElement = ""
    private var currentTitle = ""
    private var currentLink = ""
    private var currentPubDate = ""
    private var insideItem = false

    private static let dateFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "EEE, dd MMM yyyy HH:mm:ss zzz"
        return formatter
    }()

    init(category: NewsCategory) {
        self.category = category
    }

    func parse(_ data: Data) -> [NewsItem] {
        let parser = XMLParser(data: data)
        parser.delegate = self
        parser.parse()
        return items
    }

    func parser(
        _ parser: XMLParser, didStartElement elementName: String,
        namespaceURI: String?, qualifiedName qName: String?,
        attributes attributeDict: [String: String] = [:]
    ) {
        currentElement = elementName
        if elementName == "item" {
            insideItem = true
            currentTitle = ""
            currentLink = ""
            currentPubDate = ""
        }
    }

    func parser(_ parser: XMLParser, foundCharacters string: String) {
        guard insideItem else { return }
        switch currentElement {
        case "title": currentTitle += string
        case "link": currentLink += string
        case "pubDate": currentPubDate += string
        default: break
        }
    }

    func parser(
        _ parser: XMLParser, didEndElement elementName: String,
        namespaceURI: String?, qualifiedName qName: String?
    ) {
        guard elementName == "item" else { return }
        insideItem = false

        let title = currentTitle.trimmingCharacters(in: .whitespacesAndNewlines)
        let link = currentLink.trimmingCharacters(in: .whitespacesAndNewlines)
        let pubDate = currentPubDate.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !title.isEmpty, !link.isEmpty else { return }

        let publishedAt = Self.dateFormatter.date(from: pubDate) ?? .now
        items.append(NewsItem(
            category: category, title: title, link: link, publishedAt: publishedAt))
    }
}
