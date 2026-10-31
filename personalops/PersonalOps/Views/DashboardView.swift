//
//  DashboardView.swift
//  PersonalOps
//
//  Telemetry-console dashboard: diagnostic gauge panels, a shell prompter
//  for quick log entry, a FILTER_QUERY console for searching log history,
//  and a console-printout timeline. The outer wrapper re-keys the content
//  on NSCalendarDayChanged so the day-scoped requests roll over at midnight
//  without a relaunch.
//

import Combine
import GRDB
import GRDBQuery
import SwiftUI

/// Observes the log stream. With an empty filter: today's entries, newest
/// first. With a filter string: matching entries across all history — the
/// Console Log Query Engine.
struct LogStreamRequest: ValueObservationQueryable {
    static var defaultValue: [DailyLogItem] { [] }
    var day: Date
    var filter: String = ""

    func fetch(_ db: Database) throws -> [DailyLogItem] {
        let trimmed = filter.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.isEmpty {
            return try DailyLogItem
                .filter(DailyLogItem.Columns.timestamp >= day)
                .order(DailyLogItem.Columns.timestamp.desc)
                .fetchAll(db)
        }
        return try DailyLogItem
            .filter(DailyLogItem.Columns.content.like("%\(trimmed)%"))
            .order(DailyLogItem.Columns.timestamp.desc)
            .limit(200)
            .fetchAll(db)
    }
}

/// Observes the cached Daily Briefing headlines, newest first.
struct BriefingRequest: ValueObservationQueryable {
    static var defaultValue: [NewsItem] { [] }

    func fetch(_ db: Database) throws -> [NewsItem] {
        try NewsItem
            .order(NewsItem.Columns.publishedAt.desc)
            .limit(8)
            .fetchAll(db)
    }
}

struct DashboardView: View {
    @State private var day = Calendar.current.startOfDay(for: .now)

    var body: some View {
        DashboardContent(day: day)
            .id(day)
            .onReceive(NotificationCenter.default.publisher(
                for: .NSCalendarDayChanged)) { _ in
                day = Calendar.current.startOfDay(for: .now)
            }
    }
}

private struct DashboardContent: View {
    @Query<LogStreamRequest> private var logItems: [DailyLogItem]
    @Query<FocusTasksRequest> private var focusTasks: [TaskItem]
    @Query(HabitsRequest()) private var habits: [HabitInfo]
    @Query(FourteenDayMetricsRequest()) private var kpiHistory: [DailyMetric]
    @Query(BriefingRequest()) private var briefing: [NewsItem]

    @State private var quickNote = ""
    @State private var captureAsTask = false
    @State private var noteSavedPulse = false
    @FocusState private var quickNoteFocused: Bool

    init(day: Date) {
        _logItems = Query(LogStreamRequest(day: day))
        _focusTasks = Query(constant: FocusTasksRequest(day: day))
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    TodayFocusChecklist(tasks: focusTasks)
                    TimelineSection(
                        items: logItems,
                        filter: $logItems.filter)
                    ShellPrompter(
                        text: $quickNote,
                        captureAsTask: $captureAsTask,
                        isFocused: $quickNoteFocused,
                        onSave: saveQuickNote)
                    HabitTrackerStrip(habits: habits)
                    DeepWorkTimerBlock()
                    MoreSection {
                        DailyBriefingSection(items: briefing)
                        KPIPreviewChart(history: kpiHistory)
                    }
                }
                .padding(.horizontal)
                .padding(.top, 12)
                .padding(.bottom, 32)
                .animation(
                    .spring(response: 0.35, dampingFraction: 0.8),
                    value: logItems.map(\.id))
            }
            .scrollDismissesKeyboard(.interactively)
            .refreshable {
                await DataController.shared.refreshBriefing()
            }
            .consoleBackground()
            .toolbar {
                ToolbarItem(placement: .principal) {
                    Text("DASHBOARD")
                        .font(NousTheme.pixelFontBold(size: 17, relativeTo: .headline))
                        .tracking(3)
                        .foregroundStyle(NousTheme.onBackground)
                }
            }
            .toolbarBackground(NousTheme.background, for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
            .toolbarColorScheme(.dark, for: .navigationBar)
            .navigationBarTitleDisplayMode(.inline)
            .sensoryFeedback(.success, trigger: noteSavedPulse)
            .task {
                await DataController.shared.refreshBriefing()
            }
        }
    }

    private func saveQuickNote() {
        let trimmed = quickNote.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            quickNoteFocused = false
            return
        }
        if captureAsTask {
            // Quick-captured tasks land in Today's Focus (due today).
            DataController.shared.addTask(TaskItem(
                title: trimmed,
                targetDate: Calendar.current.startOfDay(for: .now)))
        } else {
            DataController.shared.addLogItem(trimmed, source: .user)
        }
        quickNote = ""
        quickNoteFocused = false
        noteSavedPulse.toggle()
    }
}

// MARK: - Collapsible "more" wrapper

/// Keeps the Dashboard's top fold focused on same-day action items —
/// Briefing/KPI content lives behind a toggle, remembered across launches.
private struct MoreSection<Content: View>: View {
    @AppStorage("dashboardMoreExpanded") private var expanded = false
    @ViewBuilder let content: () -> Content

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Button {
                withAnimation(.easeOut(duration: 0.2)) {
                    expanded.toggle()
                }
            } label: {
                HStack(spacing: 6) {
                    Text(expanded ? "HIDE MORE" : "SHOW MORE")
                        .font(.system(.caption, design: .monospaced).weight(.bold))
                        .foregroundStyle(NousTheme.accent)
                        .tracking(1)
                    Image(systemName: expanded ? "chevron.up" : "chevron.down")
                        .font(.caption2)
                        .foregroundStyle(NousTheme.accent)
                    Spacer()
                }
                .padding(.vertical, 4)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)

            if expanded {
                VStack(alignment: .leading, spacing: 20) {
                    content()
                }
                .transition(.opacity.combined(with: .move(edge: .top)))
            }
        }
    }
}

// MARK: - Daily Briefing

private struct DailyBriefingSection: View {
    let items: [NewsItem]
    @AppStorage("briefingLastFetchAt") private var lastFetchAt: Double = 0

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                ConsoleSectionLabel(text: "Daily Briefing")
                Spacer()
                if lastFetchAt > 0 {
                    Text("UPD \(Date(timeIntervalSince1970: lastFetchAt).formatted(date: .omitted, time: .shortened))")
                        .font(.system(.caption2, design: .monospaced))
                        .foregroundStyle(NousTheme.dimText)
                }
            }

            if items.isEmpty {
                ConsoleEmptyState(
                    code: "BRIEFING_FEED_UNAVAILABLE",
                    detail: "No headlines cached yet — check your connection and pull to refresh.")
                    .frame(maxWidth: .infinity)
                    .padding(16)
                    .consolePanel(opacity: 0.4)
            } else {
                ScrollView {
                    VStack(alignment: .leading, spacing: 0) {
                        ForEach(items) { item in
                            BriefingRow(item: item)
                            if item.id != items.last?.id {
                                Rectangle()
                                    .fill(NousTheme.onBackground.opacity(0.15))
                                    .frame(height: 1)
                            }
                        }
                    }
                }
                .frame(height: 160)
                .consolePanel()
            }
        }
    }
}

private struct BriefingRow: View {
    let item: NewsItem

    private var categoryTag: (text: String, color: Color) {
        switch item.category {
        case .business: return ("BUSINESS", NousTheme.statusPending)
        case .breaking: return ("BREAKING", NousTheme.statusActive)
        }
    }

    var body: some View {
        Link(destination: URL(string: item.link) ?? URL(string: "https://bbc.co.uk")!) {
            HStack(alignment: .top, spacing: 8) {
                let tag = categoryTag
                Text(tag.text)
                    .font(.system(.caption2, design: .monospaced).weight(.bold))
                    .foregroundStyle(tag.color)
                    .frame(width: 62, alignment: .leading)
                    .padding(.top, 2)
                Text(item.title)
                    .font(NousTheme.typewriterFont(size: 13, relativeTo: .footnote))
                    .foregroundStyle(NousTheme.textPrimary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                Image(systemName: "arrow.up.right.square")
                    .font(.caption2)
                    .foregroundStyle(NousTheme.dimText)
            }
            .padding(10)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(categoryTag.text): \(item.title)")
    }
}

// MARK: - Today's Focus checklist

private struct TodayFocusChecklist: View {
    let tasks: [TaskItem]

    private var doneCount: Int { tasks.filter(\.isCompleted).count }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                ConsoleSectionLabel(text: "Today's Focus")
                Spacer()
                Text("\(doneCount)/\(tasks.count)")
                    .font(.system(.caption, design: .monospaced))
                    .foregroundStyle(
                        !tasks.isEmpty && doneCount == tasks.count
                            ? NousTheme.statusActive
                            : NousTheme.dimText)
            }

            if tasks.isEmpty {
                ConsoleEmptyState(
                    code: "NO_TASKS_SCHEDULED_FOR_CURRENT_CYCLE",
                    detail: "Add a task from the Tasks tab.")
                    .frame(maxWidth: .infinity)
                    .padding(16)
                    .consolePanel(opacity: 0.2)
            } else {
                VStack(alignment: .leading, spacing: 0) {
                    ForEach(tasks) { task in
                        FocusChecklistRow(task: task)
                        if task.id != tasks.last?.id {
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
}

private struct FocusChecklistRow: View {
    let task: TaskItem

    var body: some View {
        Button {
            withAnimation(.spring(response: 0.3, dampingFraction: 0.7)) {
                DataController.shared.setTaskCompleted(task.id, completed: !task.isCompleted)
            }
        } label: {
            HStack(spacing: 10) {
                Image(systemName: task.isCompleted ? "checkmark.square.fill" : "square")
                    .foregroundStyle(task.isCompleted ? NousTheme.statusActive : NousTheme.dimText)
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
        }
        .buttonStyle(.plain)
        .accessibilityLabel(
            "\(task.title), \(task.isCompleted ? "completed" : "not completed")")
    }
}

// MARK: - Habit tracker strip

private struct HabitTrackerStrip: View {
    let habits: [HabitInfo]

    private var doneCount: Int { habits.filter { $0.isCompleted() }.count }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                ConsoleSectionLabel(text: "Habit Tracker")
                Spacer()
                ProgressView(
                    value: Double(doneCount),
                    total: Double(max(habits.count, 1)))
                    .progressViewStyle(.linear)
                    .tint(NousTheme.statusActive)
                    .frame(width: 64)
            }

            if habits.isEmpty {
                ConsoleEmptyState(
                    code: "NO_ROUTINE_PROTOCOLS_REGISTERED",
                    detail: "Add habits from the Habits tab.")
                    .frame(maxWidth: .infinity)
                    .padding(16)
                    .consolePanel(opacity: 0.2)
            } else {
                VStack(alignment: .leading, spacing: 0) {
                    ForEach(habits) { info in
                        HabitStripRow(info: info)
                        if info.id != habits.last?.id {
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
}

private struct HabitStripRow: View {
    let info: HabitInfo

    private var completedToday: Bool { info.isCompleted() }

    var body: some View {
        Button {
            withAnimation(.spring(response: 0.3, dampingFraction: 0.7)) {
                DataController.shared.toggleHabit(info.id)
            }
        } label: {
            HStack(spacing: 10) {
                Image(systemName: completedToday ? "checkmark.square.fill" : "square")
                    .foregroundStyle(completedToday ? NousTheme.statusActive : NousTheme.dimText)
                Text(info.habit.name)
                    .foregroundStyle(completedToday ? NousTheme.dimText : NousTheme.textPrimary)
                    .font(.system(.subheadline, design: .monospaced))
                Spacer()
                if info.currentStreak > 0 {
                    Label("\(info.currentStreak)", systemImage: "flame.fill")
                        .font(.system(.caption2, design: .monospaced).weight(.semibold))
                        .foregroundStyle(NousTheme.statusPending)
                        .labelStyle(.titleAndIcon)
                }
            }
            .padding(10)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(
            "\(info.habit.name), \(completedToday ? "completed" : "not completed"), streak \(info.currentStreak) days")
    }
}

// MARK: - KPI preview chart

private struct KPIPreviewChart: View {
    let history: [DailyMetric]

    private var latestSteps: Int? { history.last?.steps }
    private var latestSleep: Double? { history.last?.sleepHours }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                ConsoleSectionLabel(text: "Life Metrics / KPIs")
                Spacer()
                NavigationLink {
                    AnalyticsDashboardView()
                } label: {
                    Text("SEE ANALYTICS >")
                        .font(.system(.caption2, design: .monospaced).weight(.semibold))
                        .foregroundStyle(NousTheme.accent)
                }
            }

            if history.isEmpty {
                ConsoleEmptyState(
                    code: "NO_ACTIVE_DATA_PAYLOADS_FOUND",
                    detail: "KPI history builds up as metrics accumulate.")
                    .frame(maxWidth: .infinity)
                    .padding(16)
                    .consolePanel(opacity: 0.2)
            } else {
                MiniStepsBars(history: history)
                    .frame(height: 64)
                    .padding(12)
                    .consolePanel()
                    .accessibilityLabel(
                        "14 day steps trend, latest \(latestSteps.map(String.init) ?? "no data") steps, \(latestSleep.map { String(format: "%.1f", $0) } ?? "no data") hours sleep")
            }
        }
    }
}

private struct MiniStepsBars: View {
    let history: [DailyMetric]

    private var maxSteps: Double {
        Double(history.compactMap(\.steps).max() ?? 1)
    }

    var body: some View {
        HStack(alignment: .bottom, spacing: 3) {
            ForEach(history) { metric in
                RoundedRectangle(cornerRadius: 1)
                    .fill(NousTheme.accent.opacity(metric.steps == nil ? 0.08 : 0.75))
                    .frame(height: max(4, CGFloat((metric.steps.map(Double.init) ?? 0) / maxSteps) * 56))
                    .frame(maxWidth: .infinity)
            }
        }
        .frame(maxWidth: .infinity, alignment: .bottom)
    }
}

// MARK: - Shell prompter

private struct ShellPrompter: View {
    @Binding var text: String
    @Binding var captureAsTask: Bool
    var isFocused: FocusState<Bool>.Binding
    let onSave: () -> Void

    var body: some View {
        HStack(spacing: 8) {
            // Mode toggle: "$" logs a note, ">" captures a task due today.
            Button {
                withAnimation(.easeOut(duration: 0.12)) {
                    captureAsTask.toggle()
                }
            } label: {
                Text(captureAsTask ? "> TASK" : "$ LOG")
                    .font(.system(.caption2, design: .monospaced).weight(.bold))
                    .foregroundStyle(
                        captureAsTask ? NousTheme.statusPending : NousTheme.accent)
                    .padding(.horizontal, 6)
                    .padding(.vertical, 4)
                    .overlay(Rectangle().stroke(
                        captureAsTask ? NousTheme.statusPending : NousTheme.accent,
                        lineWidth: 1))
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel(
                captureAsTask ? "Capturing as task, tap to switch to log"
                              : "Capturing as log entry, tap to switch to task")

            TextField("", text: $text, prompt:
                Text(captureAsTask ? "new_task_today..." : "enter_log_entry...")
                    .foregroundStyle(NousTheme.dimText))
                .font(.system(.subheadline, design: .monospaced))
                .foregroundStyle(NousTheme.textPrimary)
                .tint(NousTheme.accent)
                .focused(isFocused)
                .submitLabel(.send)
                .onSubmit(onSave)

            if !isFocused.wrappedValue {
                BlinkingCursor()
            }

            Button(action: onSave) {
                Text("EXEC")
            }
            .buttonStyle(.shell())
            .disabled(text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            .accessibilityLabel("Save note")
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 8)
        .consolePanel(border: NousTheme.accent, opacity: 0.5)
    }
}

// MARK: - Console log query engine + terminal ingestion log

private struct TimelineSection: View {
    let items: [DailyLogItem]
    @Binding var filter: String

    private var isFiltering: Bool {
        !filter.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                ConsoleSectionLabel(
                    text: isFiltering ? "Query Results" : "Log Stream")
                Spacer()
                if isFiltering {
                    Text("\(items.count) HIT\(items.count == 1 ? "" : "S")")
                        .font(.system(.caption2, design: .monospaced))
                        .foregroundStyle(NousTheme.statusPending)
                }
            }

            FilterQueryBar(filter: $filter)

            if items.isEmpty {
                ConsoleEmptyState(
                    code: isFiltering
                        ? "QUERY_RETURNED_ZERO_ROWS"
                        : "NO_ACTIVE_DATA_PAYLOADS_FOUND",
                    detail: isFiltering
                        ? "No log entries match the filter query."
                        : "Log an entry from the shell prompter above.")
                    .frame(maxWidth: .infinity)
                    .padding(20)
                    .consolePanel(opacity: 0.2)
            } else {
                ScrollView {
                    VStack(alignment: .leading, spacing: 0) {
                        ForEach(items) { item in
                            TimelineRow(item: item, showDate: isFiltering) {
                                DataController.shared.deleteLogItem(item.id)
                            }
                            if item.id != items.last?.id {
                                Rectangle()
                                    .fill(NousTheme.accent.opacity(0.15))
                                    .frame(height: 1)
                            }
                        }
                    }
                }
                .frame(height: 140)
                .consolePanel(opacity: 0.3)
            }
        }
    }
}

private struct FilterQueryBar: View {
    @Binding var filter: String
    @FocusState private var focused: Bool

    var body: some View {
        HStack(spacing: 8) {
            Text("FILTER_QUERY >")
                .font(.system(.caption, design: .monospaced).weight(.bold))
                .foregroundStyle(
                    focused ? NousTheme.onBackground : NousTheme.dimText)

            TextField("", text: $filter, prompt:
                Text("match_content...")
                    .foregroundStyle(NousTheme.dimText.opacity(0.6)))
                .font(.system(.caption, design: .monospaced))
                .foregroundStyle(NousTheme.textPrimary)
                .tint(NousTheme.onBackground)
                .autocorrectionDisabled()
                .textInputAutocapitalization(.never)
                .focused($focused)

            if !filter.isEmpty {
                Button {
                    filter = ""
                    focused = false
                } label: {
                    Image(systemName: "xmark.square")
                        .font(.caption)
                        .foregroundStyle(NousTheme.statusPending)
                }
                .contentShape(Rectangle())
                .accessibilityLabel("Clear filter")
            }
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 8)
        .consolePanel(
            border: NousTheme.onBackground,
            opacity: focused ? 1 : 0.4)
        .animation(.easeOut(duration: 0.15), value: focused)
    }
}

private struct TimelineRow: View {
    let item: DailyLogItem
    var showDate = false
    var onDelete: () -> Void

    private var sourceTag: (text: String, color: Color) {
        switch item.source {
        case .user: return ("INPUT", NousTheme.accent)
        case .hermes: return ("HERMES", NousTheme.statusActive)
        case .healthKit: return ("BIOMETRIC", NousTheme.statusPending)
        case .system: return ("SYSTEM", NousTheme.dimText)
        }
    }

    private var timeString: String {
        let style: Date.FormatStyle = showDate
            ? .init(date: .abbreviated, time: .shortened)
            : .init(date: .omitted, time: .standard)
        return item.timestamp.formatted(
            style.locale(Locale(identifier: "en_US_POSIX")))
    }

    var body: some View {
        let tag = sourceTag
        HStack(alignment: .top, spacing: 6) {
            Text("[\(timeString)]")
                .foregroundStyle(NousTheme.dimText)
            Text("// \(tag.text):")
                .foregroundStyle(tag.color)
                .fontWeight(.semibold)
            Text(item.content)
                .font(NousTheme.typewriterFont(size: 12, relativeTo: .caption))
                .foregroundStyle(NousTheme.textPrimary.opacity(0.92))
                .frame(maxWidth: .infinity, alignment: .leading)
            Button(role: .destructive, action: onDelete) {
                Image(systemName: "xmark.square")
                    .foregroundStyle(NousTheme.alert.opacity(0.7))
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Delete log entry")
        }
        .font(.system(.caption, design: .monospaced))
        .padding(10)
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
        .accessibilityLabel(
            "\(timeString), \(tag.text): \(item.content)")
    }
}

