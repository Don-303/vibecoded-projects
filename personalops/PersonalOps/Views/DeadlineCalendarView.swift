//
//  DeadlineCalendarView.swift
//  PersonalOps
//
//  Month-grid calendar of task deadlines. Days with a task due show a dot;
//  tapping a day lists what's due. Deadlines with a target date already
//  sync out to the iOS Reminders + Calendar apps (see EventKitReminderSync)
//  — this view is the in-app read side of the same data.
//

import GRDB
import GRDBQuery
import SwiftUI

/// Observes every task with a target date, incomplete or not — the
/// calendar shows the whole deadline picture, not just what's outstanding.
struct DeadlineTasksRequest: ValueObservationQueryable {
    static var defaultValue: [TaskItem] { [] }

    func fetch(_ db: Database) throws -> [TaskItem] {
        try TaskItem
            .filter(TaskItem.Columns.targetDate != nil)
            .filter(TaskItem.Columns.showOnCalendar == true)
            .order(TaskItem.Columns.targetDate)
            .fetchAll(db)
    }
}

struct DeadlineCalendarView: View {
    @Query(DeadlineTasksRequest()) private var tasks: [TaskItem]
    @State private var displayedMonth: Date = Calendar.current.startOfDay(for: .now)
    @State private var selectedDay: Date = Calendar.current.startOfDay(for: .now)

    private var tasksByDay: [Date: [TaskItem]] {
        let calendar = Calendar.current
        var result: [Date: [TaskItem]] = [:]
        for task in tasks {
            guard let target = task.targetDate else { continue }
            result[calendar.startOfDay(for: target), default: []].append(task)
        }
        return result
    }

    private var selectedDayTasks: [TaskItem] {
        tasksByDay[selectedDay] ?? []
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    MonthHeader(month: $displayedMonth)
                    MonthGrid(
                        month: displayedMonth,
                        selectedDay: $selectedDay,
                        tasksByDay: tasksByDay)

                    VStack(alignment: .leading, spacing: 10) {
                        ConsoleSectionLabel(
                            text: selectedDay.formatted(.dateTime.month(.wide).day().year()))

                        if selectedDayTasks.isEmpty {
                            ConsoleEmptyState(
                                code: "NO_DEADLINES_THIS_DAY",
                                detail: "Nothing due — pick another day or add a task with a deadline.")
                                .frame(maxWidth: .infinity)
                                .padding(16)
                                .consolePanel(opacity: 0.2)
                        } else {
                            VStack(spacing: 0) {
                                ForEach(selectedDayTasks) { task in
                                    DeadlineRow(task: task)
                                    if task.id != selectedDayTasks.last?.id {
                                        Rectangle()
                                            .fill(NousTheme.accent.opacity(0.12))
                                            .frame(height: 1)
                                    }
                                }
                            }
                            .consolePanel(opacity: 0.25)
                        }
                    }
                }
                .padding(.horizontal)
                .padding(.top, 12)
                .padding(.bottom, 32)
            }
            .consoleBackground()
            .toolbarBackground(NousTheme.background, for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
            .toolbarColorScheme(.dark, for: .navigationBar)
            .navigationBarTitleDisplayMode(.inline)
        }
    }
}

// MARK: - Month header

private struct MonthHeader: View {
    @Binding var month: Date

    var body: some View {
        HStack {
            Button {
                shift(-1)
            } label: {
                Image(systemName: "chevron.left")
            }
            .buttonStyle(.shell())

            Spacer()

            Text(month.formatted(.dateTime.month(.wide).year()).uppercased())
                .font(NousTheme.pixelFontBold(size: 16, relativeTo: .headline))
                .foregroundStyle(NousTheme.textPrimary)
                .tracking(2)

            Spacer()

            Button {
                shift(1)
            } label: {
                Image(systemName: "chevron.right")
            }
            .buttonStyle(.shell())
        }
    }

    private func shift(_ months: Int) {
        withAnimation(.easeOut(duration: 0.2)) {
            month = Calendar.current.date(byAdding: .month, value: months, to: month) ?? month
        }
    }
}

// MARK: - Month grid

private struct MonthGrid: View {
    let month: Date
    @Binding var selectedDay: Date
    let tasksByDay: [Date: [TaskItem]]

    private static let columns = Array(repeating: GridItem(.flexible()), count: 7)
    private let calendar = Calendar.current

    /// Every day cell to render, padded with nils so the first day lands
    /// under the correct weekday column.
    private var days: [Date?] {
        guard let monthInterval = calendar.dateInterval(of: .month, for: month) else { return [] }
        let firstWeekday = calendar.component(.weekday, from: monthInterval.start)
        let leadingBlanks = Array<Date?>(repeating: nil, count: firstWeekday - 1)
        let dayCount = calendar.range(of: .day, in: .month, for: month)?.count ?? 30
        let daysInMonth = (0..<dayCount).compactMap {
            calendar.date(byAdding: .day, value: $0, to: monthInterval.start)
        }
        return leadingBlanks + daysInMonth.map { Optional($0) }
    }

    var body: some View {
        VStack(spacing: 6) {
            HStack {
                ForEach(calendar.veryShortWeekdaySymbols, id: \.self) { symbol in
                    Text(symbol.uppercased())
                        .font(.system(.caption2, design: .monospaced).weight(.bold))
                        .foregroundStyle(NousTheme.dimText)
                        .frame(maxWidth: .infinity)
                }
            }
            LazyVGrid(columns: Self.columns, spacing: 8) {
                ForEach(Array(days.enumerated()), id: \.offset) { _, day in
                    if let day {
                        DayCell(
                            day: day,
                            isSelected: calendar.isDate(day, inSameDayAs: selectedDay),
                            isToday: calendar.isDateInToday(day),
                            deadlineCount: tasksByDay[calendar.startOfDay(for: day)]?.count ?? 0
                        ) {
                            withAnimation(.easeOut(duration: 0.15)) {
                                selectedDay = calendar.startOfDay(for: day)
                            }
                        }
                    } else {
                        Color.clear.frame(height: 40)
                    }
                }
            }
        }
        .padding(10)
        .consolePanel(opacity: 0.25)
    }
}

private struct DayCell: View {
    let day: Date
    let isSelected: Bool
    let isToday: Bool
    let deadlineCount: Int
    let onTap: () -> Void

    var body: some View {
        Button(action: onTap) {
            VStack(spacing: 3) {
                Text("\(Calendar.current.component(.day, from: day))")
                    .font(.system(.footnote, design: .monospaced).weight(isToday ? .bold : .regular))
                    .foregroundStyle(
                        isSelected ? NousTheme.background : (isToday ? NousTheme.statusActive : NousTheme.textPrimary))
                Circle()
                    .fill(deadlineCount > 0
                        ? (isSelected ? NousTheme.background : NousTheme.alert)
                        : .clear)
                    .frame(width: 4, height: 4)
            }
            .frame(maxWidth: .infinity, minHeight: 36)
            .background(isSelected ? NousTheme.accent : Color.clear)
        }
        .buttonStyle(.plain)
        .accessibilityLabel(
            "\(day.formatted(.dateTime.month().day())), \(deadlineCount) deadline\(deadlineCount == 1 ? "" : "s")")
    }
}

// MARK: - Deadline row

private struct DeadlineRow: View {
    let task: TaskItem

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: task.isCompleted ? "checkmark.circle.fill" : "circle")
                .foregroundStyle(task.isCompleted ? NousTheme.statusActive : NousTheme.accent)
            Text(task.title)
                .strikethrough(task.isCompleted)
                .foregroundStyle(task.isCompleted ? NousTheme.dimText : NousTheme.textPrimary)
                .font(.system(.subheadline, design: .monospaced))
            Spacer()
            if task.priority == .high && !task.isCompleted {
                Text("HIGH")
                    .font(.system(.caption2, design: .monospaced).weight(.bold))
                    .foregroundStyle(NousTheme.alert)
            }
        }
        .padding(10)
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
    }
}
