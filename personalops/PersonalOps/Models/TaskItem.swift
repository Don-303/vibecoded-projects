//
//  TaskItem.swift
//  PersonalOps
//
//  Lightweight task. Partitioning rule used by TasksView and the Hermes
//  export:
//    "Today's Focus" = incomplete AND targetDate != nil AND targetDate <= end of today
//    "Backlog/Inbox" = incomplete AND (targetDate == nil OR targetDate is future)
//
//  Named TaskItem (not Task) to avoid colliding with Swift Concurrency's Task.
//

import Foundation
import GRDB

enum TaskRecurrence: String, Codable, CaseIterable, Sendable {
    case daily
    case weekly

    var displayName: String { rawValue.capitalized }

    /// Next occurrence strictly after today, stepping from `date`.
    func nextDate(after date: Date) -> Date {
        let calendar = Calendar.current
        let today = calendar.startOfDay(for: .now)
        let step = DateComponents(day: self == .daily ? 1 : 7)
        var next = calendar.date(byAdding: step, to: calendar.startOfDay(for: date)) ?? date
        while next <= today {
            next = calendar.date(byAdding: step, to: next) ?? next.addingTimeInterval(86400)
        }
        return next
    }
}

enum TaskPriority: Int, Codable, CaseIterable, Comparable, Sendable {
    case low = 0
    case normal = 1
    case high = 2

    var displayName: String {
        switch self {
        case .low: return "Low"
        case .normal: return "Normal"
        case .high: return "High"
        }
    }

    static func < (lhs: TaskPriority, rhs: TaskPriority) -> Bool {
        lhs.rawValue < rhs.rawValue
    }
}

struct TaskItem: Codable, Equatable, Identifiable, Sendable,
    FetchableRecord, PersistableRecord {

    static let databaseTableName = "taskItem"

    var id: UUID
    var title: String
    var details: String
    var priorityRaw: Int
    var createdAt: Date
    /// The day this task should surface in "Today's Focus". Nil = inbox item
    /// with no committed date.
    var targetDate: Date?
    var completedAt: Date?
    var isCompleted: Bool
    /// Same provenance scheme as DailyLogItem (user / hermes / system).
    var sourceRaw: String
    /// Nil = unassigned (shows up in Tasks/Today's Focus but not under any
    /// project card).
    var projectId: UUID?
    /// EKReminder.calendarItemIdentifier for the mirrored Reminders app
    /// entry — nil when there's no targetDate or Reminders access hasn't
    /// been granted. See EventKitReminderSync.
    var reminderIdentifier: String?
    /// EKEvent.eventIdentifier for the mirrored Calendar app all-day event —
    /// same nil conditions as reminderIdentifier. See EventKitReminderSync.
    var calendarEventIdentifier: String?
    /// User opt-in for the Calendar tab + iOS Calendar app sync — a task
    /// can have a deadline (and still remind via Reminders) without
    /// cluttering the calendar. Only meaningful when targetDate != nil.
    var showOnCalendar: Bool
    /// Nil = one-off. Completing a recurring task spawns the next
    /// occurrence (see DataController.setTaskCompleted).
    var recurrenceRaw: String?

    var recurrence: TaskRecurrence? {
        get { recurrenceRaw.flatMap(TaskRecurrence.init) }
        set { recurrenceRaw = newValue?.rawValue }
    }

    var priority: TaskPriority {
        get { TaskPriority(rawValue: priorityRaw) ?? .normal }
        set { priorityRaw = newValue.rawValue }
    }

    var source: LogSource {
        get { LogSource(rawValue: sourceRaw) ?? .user }
        set { sourceRaw = newValue.rawValue }
    }

    init(
        title: String,
        details: String = "",
        priority: TaskPriority = .normal,
        targetDate: Date? = nil,
        source: LogSource = .user,
        projectId: UUID? = nil,
        recurrence: TaskRecurrence? = nil,
        showOnCalendar: Bool = true
    ) {
        self.id = UUID()
        self.title = title
        self.details = details
        self.priorityRaw = priority.rawValue
        self.createdAt = .now
        self.targetDate = targetDate
        self.completedAt = nil
        self.isCompleted = false
        self.sourceRaw = source.rawValue
        self.projectId = projectId
        self.reminderIdentifier = nil
        self.calendarEventIdentifier = nil
        self.recurrenceRaw = recurrence?.rawValue
        self.showOnCalendar = showOnCalendar
    }

    mutating func setCompleted(_ completed: Bool) {
        isCompleted = completed
        completedAt = completed ? .now : nil
    }
}

extension TaskItem {
    enum Columns {
        static let id = Column(CodingKeys.id)
        static let priorityRaw = Column(CodingKeys.priorityRaw)
        static let createdAt = Column(CodingKeys.createdAt)
        static let targetDate = Column(CodingKeys.targetDate)
        static let completedAt = Column(CodingKeys.completedAt)
        static let isCompleted = Column(CodingKeys.isCompleted)
        static let projectId = Column(CodingKeys.projectId)
        static let showOnCalendar = Column(CodingKeys.showOnCalendar)
    }
}
