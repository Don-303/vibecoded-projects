//
//  NotificationScheduler.swift
//  PersonalOps
//
//  Local reminders for incomplete habits (today) and tasks with a target
//  date. Every call to `rescheduleAll` fully re-derives pending
//  notifications from current database state — cheap and idempotent, so
//  it's safe to call after any task/habit mutation or on app foreground.
//  A habit already completed today, or a task already done/deleted/moved,
//  simply won't have a pending request the next time this runs.
//

import Foundation
import GRDB
import UserNotifications

enum NotificationScheduler {
    /// Representative time of day for each routine segment's reminder.
    private static let habitReminderTime: [RoutineSegment: (hour: Int, minute: Int)] = [
        .morning: (8, 0),
        .afternoon: (13, 0),
        .evening: (20, 0),
    ]
    /// Task deadline reminders fire once, this early in the due day.
    private static let taskReminderHour = 9

    static func requestAuthorizationIfNeeded() {
        Task {
            let center = UNUserNotificationCenter.current()
            let settings = await center.notificationSettings()
            guard settings.authorizationStatus == .notDetermined else { return }
            _ = try? await center.requestAuthorization(options: [.alert, .sound, .badge])
        }
    }

    /// Two fixed daily reminders, independent of any task/habit/routine data:
    /// 8am to plan the day's to-do list, 10pm to do the Weekly/nightly
    /// reflection. Repeating triggers, so this only needs scheduling once —
    /// safe to call on every launch since the identifiers are fixed.
    static func scheduleDailyRoutineReminders() {
        Task {
            let center = UNUserNotificationCenter.current()
            let settings = await center.notificationSettings()
            guard settings.authorizationStatus == .authorized else { return }

            center.removePendingNotificationRequests(withIdentifiers: [
                "daily-todo-reminder", "daily-reflection-reminder", "weekly-retro-reminder",
            ])

            let planContent = UNMutableNotificationContent()
            planContent.title = "Plan Today"
            planContent.body = "Build your daily to-do list."
            planContent.sound = .default
            var planTime = DateComponents()
            planTime.hour = 8
            planTime.minute = 0
            let planTrigger = UNCalendarNotificationTrigger(dateMatching: planTime, repeats: true)
            try? await center.add(UNNotificationRequest(
                identifier: "daily-todo-reminder", content: planContent, trigger: planTrigger))

            let reflectContent = UNMutableNotificationContent()
            reflectContent.title = "Reflect"
            reflectContent.body = "Log tonight's energy rating and reflections."
            reflectContent.sound = .default
            var reflectTime = DateComponents()
            reflectTime.hour = 22
            reflectTime.minute = 0
            let reflectTrigger = UNCalendarNotificationTrigger(dateMatching: reflectTime, repeats: true)
            try? await center.add(UNNotificationRequest(
                identifier: "daily-reflection-reminder", content: reflectContent, trigger: reflectTrigger))

            let retroContent = UNMutableNotificationContent()
            retroContent.title = "Weekly Retrospective"
            retroContent.body = "What went right? What system broke? What single change fixes it?"
            retroContent.sound = .default
            var retroTime = DateComponents()
            retroTime.weekday = 1 // Sunday
            retroTime.hour = 22
            retroTime.minute = 0
            let retroTrigger = UNCalendarNotificationTrigger(dateMatching: retroTime, repeats: true)
            try? await center.add(UNNotificationRequest(
                identifier: "weekly-retro-reminder", content: retroContent, trigger: retroTrigger))
        }
    }

    /// One-shot alert for the running Deep Work Matrix phase, so a work
    /// round or break that finishes while the app is backgrounded still
    /// announces itself. One fixed identifier — starting a new phase
    /// replaces the previous alert, and aborting cancels it.
    static func schedulePomodoroEnd(at date: Date, title: String, body: String) {
        Task {
            let center = UNUserNotificationCenter.current()
            let settings = await center.notificationSettings()
            guard settings.authorizationStatus == .authorized else { return }

            center.removePendingNotificationRequests(withIdentifiers: ["pomodoro-phase-end"])

            let content = UNMutableNotificationContent()
            content.title = title
            content.body = body
            content.sound = .default
            let comps = Calendar.current.dateComponents(
                [.year, .month, .day, .hour, .minute, .second], from: date)
            let trigger = UNCalendarNotificationTrigger(dateMatching: comps, repeats: false)
            try? await center.add(UNNotificationRequest(
                identifier: "pomodoro-phase-end", content: content, trigger: trigger))
        }
    }

    static func cancelPomodoroEnd() {
        UNUserNotificationCenter.current()
            .removePendingNotificationRequests(withIdentifiers: ["pomodoro-phase-end"])
    }

    /// Re-derives every pending habit/task reminder from current database
    /// state. Existing pending requests for a given habit/task are always
    /// cleared first, so completing/deleting/rescheduling something removes
    /// its stale reminder rather than leaving a dangling notification.
    static func rescheduleAll(writer: any DatabaseWriter) {
        Task {
            let center = UNUserNotificationCenter.current()
            let settings = await center.notificationSettings()
            guard settings.authorizationStatus == .authorized else { return }

            do {
                let (habits, completions, tasks) = try await writer.read { db in
                    (
                        try Habit.filter(Habit.Columns.isArchived == false).fetchAll(db),
                        try HabitCompletion.fetchAll(db),
                        try TaskItem.filter(TaskItem.Columns.isCompleted == false).fetchAll(db)
                    )
                }
                await rescheduleHabits(habits, completions: completions, center: center)
                await rescheduleTasks(tasks, center: center)
            } catch {
                // Non-fatal — reminders are a nice-to-have, not core function.
            }
        }
    }

    private static func rescheduleHabits(
        _ habits: [Habit], completions: [HabitCompletion], center: UNUserNotificationCenter
    ) async {
        let calendar = Calendar.current
        let today = calendar.startOfDay(for: .now)
        let completedTodayIDs = Set(
            completions
                .filter { calendar.startOfDay(for: $0.date) == today }
                .map(\.habitId))

        for habit in habits {
            let identifier = "habit-\(habit.id.uuidString)"
            center.removePendingNotificationRequests(withIdentifiers: [identifier])

            guard !completedTodayIDs.contains(habit.id),
                  let time = habitReminderTime[habit.segment] else { continue }

            var comps = calendar.dateComponents([.year, .month, .day], from: .now)
            comps.hour = time.hour
            comps.minute = time.minute
            guard let fireDate = calendar.date(from: comps), fireDate > .now else { continue }

            let content = UNMutableNotificationContent()
            content.title = "Habit Reminder"
            content.body = "\(habit.name) — \(habit.segment.displayName) routine"
            content.sound = .default

            let trigger = UNCalendarNotificationTrigger(dateMatching: comps, repeats: false)
            let request = UNNotificationRequest(identifier: identifier, content: content, trigger: trigger)
            try? await center.add(request)
        }
    }

    private static func rescheduleTasks(_ tasks: [TaskItem], center: UNUserNotificationCenter) async {
        let calendar = Calendar.current
        for task in tasks {
            let identifier = "task-\(task.id.uuidString)"
            center.removePendingNotificationRequests(withIdentifiers: [identifier])

            guard let targetDate = task.targetDate else { continue }

            var comps = calendar.dateComponents([.year, .month, .day], from: targetDate)
            comps.hour = taskReminderHour
            comps.minute = 0
            guard let fireDate = calendar.date(from: comps), fireDate > .now else { continue }

            let content = UNMutableNotificationContent()
            content.title = "Task Due Today"
            content.body = task.title
            content.sound = .default

            let trigger = UNCalendarNotificationTrigger(dateMatching: comps, repeats: false)
            let request = UNNotificationRequest(identifier: identifier, content: content, trigger: trigger)
            try? await center.add(request)
        }
    }
}
