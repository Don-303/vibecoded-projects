//
//  EventKitReminderSync.swift
//  PersonalOps
//
//  Mirrors task deadlines into the iOS Reminders app AND the Calendar app
//  via EventKit, so iOS can alert the user even when PersonalOps isn't
//  open, and deadlines show up alongside the rest of the user's schedule.
//  Unlike HealthKit or Screen Time, EventKit needs only Info.plist usage
//  strings — no special Apple-granted entitlement — so this works from an
//  xtool/ad-hoc build. All calls are silent no-ops until the user grants
//  the relevant access.
//

@preconcurrency import EventKit
import Foundation

@MainActor
enum EventKitReminderSync {
    private static let store = EKEventStore()

    static func requestAccessIfNeeded() {
        Task { @MainActor in
            _ = try? await store.requestFullAccessToReminders()
            _ = try? await store.requestFullAccessToEvents()
        }
    }

    private static var isAuthorized: Bool {
        EKEventStore.authorizationStatus(for: .reminder) == .fullAccess
    }

    private static var isCalendarAuthorized: Bool {
        EKEventStore.authorizationStatus(for: .event) == .fullAccess
    }

    /// Creates/updates an all-day Calendar event on `targetDate` mirroring
    /// this task's title. Returns the event's identifier to store back on
    /// the TaskItem, or nil if there's no event to keep.
    static func syncCalendarEvent(
        title: String,
        targetDate: Date?,
        existingIdentifier: String?
    ) -> String? {
        guard isCalendarAuthorized else { return existingIdentifier }

        guard let targetDate else {
            if let existingIdentifier {
                removeCalendarEvent(identifier: existingIdentifier)
            }
            return nil
        }

        let event: EKEvent
        if let existingIdentifier,
           let existing = store.event(withIdentifier: existingIdentifier) {
            event = existing
        } else {
            event = EKEvent(eventStore: store)
            event.calendar = store.defaultCalendarForNewEvents
        }
        event.title = "\(title) (Due)"
        event.isAllDay = true
        event.startDate = Calendar.current.startOfDay(for: targetDate)
        event.endDate = event.startDate

        do {
            try store.save(event, span: .thisEvent, commit: true)
            return event.eventIdentifier
        } catch {
            return existingIdentifier
        }
    }

    static func removeCalendarEvent(identifier: String) {
        guard isCalendarAuthorized, let event = store.event(withIdentifier: identifier)
        else { return }
        try? store.remove(event, span: .thisEvent, commit: true)
    }

    /// Creates or updates the EKReminder mirroring this task's title/due
    /// date. Returns the reminder's identifier to store back on the
    /// TaskItem, or nil if there's no reminder to keep (no target date, or
    /// access hasn't been granted).
    static func syncReminder(
        title: String,
        targetDate: Date?,
        existingIdentifier: String?
    ) -> String? {
        guard isAuthorized else { return existingIdentifier }

        guard let targetDate else {
            if let existingIdentifier {
                removeReminder(identifier: existingIdentifier)
            }
            return nil
        }

        let reminder: EKReminder
        if let existingIdentifier,
           let existing = store.calendarItem(withIdentifier: existingIdentifier) as? EKReminder {
            reminder = existing
        } else {
            reminder = EKReminder(eventStore: store)
            reminder.calendar = store.defaultCalendarForNewReminders()
        }
        reminder.title = title
        reminder.dueDateComponents = Calendar.current.dateComponents(
            [.year, .month, .day], from: targetDate)

        do {
            try store.save(reminder, commit: true)
            return reminder.calendarItemIdentifier
        } catch {
            return existingIdentifier
        }
    }

    static func setCompleted(_ completed: Bool, identifier: String?) {
        guard isAuthorized, let identifier,
              let reminder = store.calendarItem(withIdentifier: identifier) as? EKReminder
        else { return }
        reminder.isCompleted = completed
        try? store.save(reminder, commit: true)
    }

    static func removeReminder(identifier: String) {
        guard isAuthorized,
              let reminder = store.calendarItem(withIdentifier: identifier) as? EKReminder
        else { return }
        try? store.remove(reminder, commit: true)
    }
}
