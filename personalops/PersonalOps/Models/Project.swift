//
//  Project.swift
//  PersonalOps
//
//  Lightweight grouping for TaskItem rows. A project has no state of its
//  own beyond a name/color/archived flag — progress and task counts are
//  always derived live from its linked TaskItems (see ProjectInfo).
//

import Foundation
import GRDB
import SwiftUI

enum ProjectColor: String, Codable, CaseIterable, Sendable {
    case cyan, green, amber, alert

    var color: Color {
        switch self {
        case .cyan: return NousTheme.accent
        case .green: return NousTheme.statusActive
        case .amber: return NousTheme.statusPending
        case .alert: return NousTheme.alert
        }
    }
}

struct Project: Codable, Equatable, Identifiable, Sendable,
    FetchableRecord, PersistableRecord {

    static let databaseTableName = "project"

    var id: UUID
    var name: String
    var colorRaw: String
    var sortOrder: Int
    var createdAt: Date
    var isArchived: Bool
    /// Permanent projects (e.g. the seeded Reading List) can't be deleted
    /// from the UI — DataController.deleteProject refuses on this flag.
    var isProtected: Bool

    /// The long-term objective this project is grouping tasks toward —
    /// distinct from any single task, e.g. "Ship v2 of the trading system".
    var goalDescription: String
    /// Target date for the overall goal, not any one task's deadline.
    var goalTargetDate: Date?

    var color: ProjectColor {
        get { ProjectColor(rawValue: colorRaw) ?? .cyan }
        set { colorRaw = newValue.rawValue }
    }

    init(
        name: String,
        color: ProjectColor = .cyan,
        sortOrder: Int = 0,
        createdAt: Date = .now,
        isProtected: Bool = false,
        goalDescription: String = "",
        goalTargetDate: Date? = nil
    ) {
        self.id = UUID()
        self.name = name
        self.colorRaw = color.rawValue
        self.sortOrder = sortOrder
        self.createdAt = createdAt
        self.isArchived = false
        self.isProtected = isProtected
        self.goalDescription = goalDescription
        self.goalTargetDate = goalTargetDate
    }
}

extension Project {
    enum Columns {
        static let id = Column(CodingKeys.id)
        static let name = Column(CodingKeys.name)
        static let sortOrder = Column(CodingKeys.sortOrder)
        static let createdAt = Column(CodingKeys.createdAt)
        static let isArchived = Column(CodingKeys.isArchived)
        static let isProtected = Column(CodingKeys.isProtected)
        static let goalTargetDate = Column(CodingKeys.goalTargetDate)
    }
}

// MARK: - View-facing aggregate

struct ProjectInfo: Equatable, Identifiable, Sendable {
    var project: Project
    var tasks: [TaskItem]

    var id: UUID { project.id }

    var doneCount: Int { tasks.filter(\.isCompleted).count }
    var progress: Double {
        tasks.isEmpty ? 0 : Double(doneCount) / Double(tasks.count)
    }
}
