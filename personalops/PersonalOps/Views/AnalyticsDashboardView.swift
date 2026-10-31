//
//  AnalyticsDashboardView.swift
//  PersonalOps
//
//  14-day telemetry history rendered with Swift Charts in the Nous Blue
//  console language: translucent cyan BarMarks for steps with a solid cyan
//  stroke, and a glowing LineMark overlay for sleep hours (normalized onto
//  the steps scale so both series share one plot; the sleep axis is printed
//  on the trailing edge). All axis/grid labels are monospaced.
//

import Charts
import GRDB
import GRDBQuery
import SwiftUI

/// Observes the trailing 14 days of DailyMetric rows, oldest first.
struct FourteenDayMetricsRequest: ValueObservationQueryable {
    static var defaultValue: [DailyMetric] { [] }

    func fetch(_ db: Database) throws -> [DailyMetric] {
        let calendar = Calendar.current
        let today = calendar.startOfDay(for: .now)
        let start = calendar.date(byAdding: .day, value: -13, to: today)!
        return try DailyMetric
            .filter(DailyMetric.Columns.day >= start)
            .order(DailyMetric.Columns.day)
            .fetchAll(db)
    }
}

/// A day's habit-completion rate over the trailing 14 days — 1.0 means every
/// active habit was completed that day.
struct HabitConsistencyPoint: Identifiable, Sendable {
    var day: Date
    var rate: Double
    var id: Date { day }
}

/// Observes non-archived habits joined with completions across the trailing
/// 14 days, and reduces them into one completion-rate figure per day.
struct HabitConsistencyRequest: ValueObservationQueryable {
    static var defaultValue: [HabitConsistencyPoint] { [] }

    func fetch(_ db: Database) throws -> [HabitConsistencyPoint] {
        let calendar = Calendar.current
        let today = calendar.startOfDay(for: .now)
        let start = calendar.date(byAdding: .day, value: -13, to: today)!

        let habitCount = try Habit.filter(Habit.Columns.isArchived == false).fetchCount(db)
        guard habitCount > 0 else { return [] }

        let completions = try HabitCompletion
            .filter(HabitCompletion.Columns.date >= start)
            .fetchAll(db)
        var countByDay: [Date: Int] = [:]
        for completion in completions {
            countByDay[calendar.startOfDay(for: completion.date), default: 0] += 1
        }

        return (0..<14).compactMap { offset in
            guard let day = calendar.date(byAdding: .day, value: offset, to: start) else { return nil }
            return HabitConsistencyPoint(
                day: day, rate: Double(countByDay[day] ?? 0) / Double(habitCount))
        }
    }
}

/// Completed-task count per day over the trailing 14 days.
struct TaskThroughputPoint: Identifiable, Sendable {
    var day: Date
    var count: Int
    var id: Date { day }
}

struct TaskThroughputRequest: ValueObservationQueryable {
    static var defaultValue: [TaskThroughputPoint] { [] }

    func fetch(_ db: Database) throws -> [TaskThroughputPoint] {
        let calendar = Calendar.current
        let today = calendar.startOfDay(for: .now)
        let start = calendar.date(byAdding: .day, value: -13, to: today)!

        let completedTasks = try TaskItem
            .filter(TaskItem.Columns.isCompleted == true)
            .filter(TaskItem.Columns.completedAt >= start)
            .fetchAll(db)
        var countByDay: [Date: Int] = [:]
        for task in completedTasks {
            guard let completedAt = task.completedAt else { continue }
            countByDay[calendar.startOfDay(for: completedAt), default: 0] += 1
        }

        return (0..<14).compactMap { offset in
            guard let day = calendar.date(byAdding: .day, value: offset, to: start) else { return nil }
            return TaskThroughputPoint(day: day, count: countByDay[day] ?? 0)
        }
    }
}

/// Focus-session minutes logged per day over the trailing 14 days.
struct FocusMinutesPoint: Identifiable, Sendable {
    var day: Date
    var minutes: Int
    var id: Date { day }
}

struct FocusMinutesRequest: ValueObservationQueryable {
    static var defaultValue: [FocusMinutesPoint] { [] }

    func fetch(_ db: Database) throws -> [FocusMinutesPoint] {
        let calendar = Calendar.current
        let today = calendar.startOfDay(for: .now)
        let start = calendar.date(byAdding: .day, value: -13, to: today)!

        let sessions = try FocusSession
            .filter(FocusSession.Columns.completedAt >= start)
            .fetchAll(db)
        var minutesByDay: [Date: Int] = [:]
        for session in sessions {
            minutesByDay[calendar.startOfDay(for: session.completedAt), default: 0] += session.durationMinutes
        }

        return (0..<14).compactMap { offset in
            guard let day = calendar.date(byAdding: .day, value: offset, to: start) else { return nil }
            return FocusMinutesPoint(day: day, minutes: minutesByDay[day] ?? 0)
        }
    }
}

struct AnalyticsDashboardView: View {
    @Query(FourteenDayMetricsRequest()) private var metrics: [DailyMetric]
    @Query(HabitConsistencyRequest()) private var habitConsistency: [HabitConsistencyPoint]
    @Query(TaskThroughputRequest()) private var taskThroughput: [TaskThroughputPoint]
    @Query(FocusMinutesRequest()) private var focusMinutes: [FocusMinutesPoint]
    @Query(ProjectsRequest()) private var projects: [ProjectInfo]

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                AnalyticsGroupHeader(text: "Health")
                if metrics.isEmpty {
                    ConsoleEmptyState(
                        code: "NO_ACTIVE_DATA_PAYLOADS_FOUND",
                        detail: "Telemetry accumulates here once metrics are logged or synced.")
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 80)
                } else {
                    SummaryStrip(metrics: metrics)
                    StepsSleepChart(metrics: metrics)
                    EnergyChart(metrics: metrics)
                    WeightChart(metrics: metrics)
                    ScreenTimeChart(metrics: metrics)
                }
                DailyMetricsEntrySection()
                ScreenTimeSection()

                if !habitConsistency.isEmpty || !taskThroughput.isEmpty
                    || focusMinutes.contains(where: { $0.minutes > 0 }) {
                    AnalyticsGroupHeader(text: "Productivity")
                    if !habitConsistency.isEmpty {
                        HabitConsistencyChart(points: habitConsistency)
                    }
                    if !taskThroughput.isEmpty {
                        TaskThroughputChart(points: taskThroughput)
                    }
                    if focusMinutes.contains(where: { $0.minutes > 0 }) {
                        FocusMinutesChart(points: focusMinutes)
                    }
                }

                AnalyticsGroupHeader(text: "Goals")
                LongTermGoalsSection(projects: projects)
            }
            .padding(.horizontal)
            .padding(.top, 8)
            .padding(.bottom, 32)
        }
        .consoleBackground()
        .toolbar {
            ToolbarItem(placement: .principal) {
                Text("ANALYTICS")
                    .font(NousTheme.pixelFontBold(size: 17, relativeTo: .headline))
                    .tracking(3)
                    .foregroundStyle(NousTheme.onBackground)
            }
        }
        .toolbarBackground(NousTheme.background, for: .navigationBar)
        .toolbarBackground(.visible, for: .navigationBar)
        .toolbarColorScheme(.dark, for: .navigationBar)
        .navigationBarTitleDisplayMode(.inline)
    }
}

// MARK: - Group header

/// Super-header dividing Analytics into Health / Productivity / Goals —
/// distinct from ConsoleSectionLabel (used for the individual charts
/// underneath) via a full-width rule line, signalling a topic change rather
/// than just another chart title.
private struct AnalyticsGroupHeader: View {
    let text: String

    var body: some View {
        HStack(spacing: 8) {
            Text(text.uppercased())
                .font(NousTheme.pixelFontBold(size: 13, relativeTo: .subheadline))
                .foregroundStyle(NousTheme.onBackground)
                .tracking(3)
            Rectangle()
                .fill(NousTheme.onBackground.opacity(0.2))
                .frame(height: 1)
        }
    }
}

// MARK: - Summary readouts

private struct SummaryStrip: View {
    let metrics: [DailyMetric]

    private var avgSteps: Int? {
        let values = metrics.compactMap(\.steps)
        guard !values.isEmpty else { return nil }
        return values.reduce(0, +) / values.count
    }

    private var avgSleep: Double? {
        let values = metrics.compactMap(\.sleepHours)
        guard !values.isEmpty else { return nil }
        return values.reduce(0, +) / Double(values.count)
    }

    private var avgEnergy: Int? {
        let values = metrics.compactMap(\.activeEnergyKcal)
        guard !values.isEmpty else { return nil }
        return Int((values.reduce(0, +) / Double(values.count)).rounded())
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            ConsoleSectionLabel(text: "14-Day Averages")
            HStack(spacing: 10) {
                ReadoutCell(
                    label: "STEPS.AVG",
                    value: avgSteps.map { "\($0)" })
                ReadoutCell(
                    label: "SLEEP.AVG",
                    value: avgSleep.map { String(format: "%.1fH", $0) })
                ReadoutCell(
                    label: "KCAL.AVG",
                    value: avgEnergy.map { "\($0)" })
            }
        }
    }
}

private struct ReadoutCell: View {
    let label: String
    let value: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label)
                .font(.system(.caption2, design: .monospaced).weight(.semibold))
                .foregroundStyle(NousTheme.accent.opacity(0.85))
                .tracking(1)
            Text(value ?? "--")
                .font(.system(.title3, design: .monospaced).weight(.bold))
                .foregroundStyle(value == nil ? NousTheme.dimText : NousTheme.textPrimary)
                .consoleGlow(value == nil ? .clear : NousTheme.accent, radius: 4)
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .consolePanel()
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(label): \(value ?? "no data")")
    }
}

// MARK: - Steps + sleep chart

private struct StepsSleepChart: View {
    let metrics: [DailyMetric]

    private var maxSteps: Double {
        Double(metrics.compactMap(\.steps).max() ?? 10_000)
    }

    private var maxSleep: Double {
        max(metrics.compactMap(\.sleepHours).max() ?? 10, 1)
    }

    /// Sleep hours mapped onto the steps axis so both series share a plot.
    private func scaledSleep(_ hours: Double) -> Double {
        hours / maxSleep * maxSteps
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                ConsoleSectionLabel(text: "Steps × Sleep — 14D")
                Spacer()
                LegendDot(color: NousTheme.accent, label: "STEPS")
                LegendDot(color: NousTheme.statusActive, label: "SLEEP.H")
            }

            Chart {
                ForEach(metrics) { metric in
                    if let steps = metric.steps {
                        BarMark(
                            x: .value("Day", metric.day, unit: .day),
                            y: .value("Steps", steps))
                            .foregroundStyle(NousTheme.accent.opacity(0.3))
                            .cornerRadius(2)
                    }
                }
                ForEach(metrics) { metric in
                    if let steps = metric.steps {
                        // Solid cyan stroke capping each translucent bar.
                        RectangleMark(
                            x: .value("Day", metric.day, unit: .day),
                            y: .value("Steps", steps),
                            height: 2)
                            .foregroundStyle(NousTheme.accent)
                    }
                }
                ForEach(metrics) { metric in
                    if let sleep = metric.sleepHours {
                        LineMark(
                            x: .value("Day", metric.day, unit: .day),
                            y: .value("Sleep", scaledSleep(sleep)),
                            series: .value("Series", "Sleep"))
                            .foregroundStyle(NousTheme.statusActive)
                            .lineStyle(StrokeStyle(lineWidth: 2))
                            .interpolationMethod(.monotone)
                            .symbol {
                                Circle()
                                    .fill(NousTheme.statusActive)
                                    .frame(width: 5, height: 5)
                            }
                    }
                }
            }
            .chartYAxis {
                AxisMarks(position: .leading) { value in
                    AxisGridLine()
                        .foregroundStyle(NousTheme.textPrimary.opacity(0.08))
                    AxisValueLabel {
                        if let steps = value.as(Double.self) {
                            Text("\(Int(steps / 1000))K")
                                .font(.system(.caption2, design: .monospaced))
                                .foregroundStyle(NousTheme.accent.opacity(0.7))
                        }
                    }
                }
                AxisMarks(position: .trailing) { value in
                    AxisValueLabel {
                        if let scaled = value.as(Double.self) {
                            Text(String(format: "%.0fH", scaled / maxSteps * maxSleep))
                                .font(.system(.caption2, design: .monospaced))
                                .foregroundStyle(NousTheme.statusActive.opacity(0.7))
                        }
                    }
                }
            }
            .chartXAxis {
                AxisMarks(values: .stride(by: .day, count: 2)) { value in
                    AxisGridLine()
                        .foregroundStyle(NousTheme.textPrimary.opacity(0.06))
                    AxisValueLabel {
                        if let date = value.as(Date.self) {
                            Text(date.formatted(.dateTime.day()))
                                .font(.system(.caption2, design: .monospaced))
                                .foregroundStyle(NousTheme.dimText)
                        }
                    }
                }
            }
            .frame(height: 220)
            // Glow shadow under the whole plot lifts the sleep line off the
            // navy backdrop, echoing the console's emissive language.
            .shadow(color: NousTheme.statusActive.opacity(0.35), radius: 6)
            .padding(12)
            .consolePanel()
            .accessibilityLabel("14-day chart of daily steps and sleep hours")
        }
    }
}

// MARK: - Energy chart

private struct EnergyChart: View {
    let metrics: [DailyMetric]

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                ConsoleSectionLabel(text: "Active Energy — 14D")
                Spacer()
                LegendDot(color: NousTheme.statusPending, label: "KCAL")
            }

            Chart {
                ForEach(metrics) { metric in
                    if let energy = metric.activeEnergyKcal {
                        BarMark(
                            x: .value("Day", metric.day, unit: .day),
                            y: .value("kcal", energy))
                            .foregroundStyle(NousTheme.statusPending.opacity(0.3))
                            .cornerRadius(2)
                    }
                }
            }
            .chartYAxis {
                AxisMarks(position: .leading) { value in
                    AxisGridLine()
                        .foregroundStyle(NousTheme.textPrimary.opacity(0.08))
                    AxisValueLabel {
                        if let kcal = value.as(Double.self) {
                            Text("\(Int(kcal))")
                                .font(.system(.caption2, design: .monospaced))
                                .foregroundStyle(NousTheme.statusPending.opacity(0.7))
                        }
                    }
                }
            }
            .chartXAxis {
                AxisMarks(values: .stride(by: .day, count: 2)) { value in
                    AxisGridLine()
                        .foregroundStyle(NousTheme.textPrimary.opacity(0.06))
                    AxisValueLabel {
                        if let date = value.as(Date.self) {
                            Text(date.formatted(.dateTime.day()))
                                .font(.system(.caption2, design: .monospaced))
                                .foregroundStyle(NousTheme.dimText)
                        }
                    }
                }
            }
            .frame(height: 160)
            .padding(12)
            .consolePanel(border: NousTheme.statusPending, opacity: 0.3)
            .accessibilityLabel("14-day chart of active energy burned")
        }
    }
}

// MARK: - Weight chart

private struct WeightChart: View {
    let metrics: [DailyMetric]

    private var points: [DailyMetric] { metrics.filter { $0.weightKg != nil } }

    var body: some View {
        if !points.isEmpty {
            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    ConsoleSectionLabel(text: "Weight — 14D")
                    Spacer()
                    LegendDot(color: NousTheme.textPrimary, label: "KG")
                }

                Chart {
                    ForEach(points) { metric in
                        if let weight = metric.weightKg {
                            LineMark(
                                x: .value("Day", metric.day, unit: .day),
                                y: .value("kg", weight))
                                .foregroundStyle(NousTheme.textPrimary)
                                .lineStyle(StrokeStyle(lineWidth: 2))
                                .interpolationMethod(.monotone)
                                .symbol {
                                    Circle()
                                        .fill(NousTheme.textPrimary)
                                        .frame(width: 5, height: 5)
                                }
                        }
                    }
                }
                .chartYAxis {
                    AxisMarks(position: .leading) { value in
                        AxisGridLine()
                            .foregroundStyle(NousTheme.textPrimary.opacity(0.08))
                        AxisValueLabel {
                            if let weight = value.as(Double.self) {
                                Text(String(format: "%.0f", weight))
                                    .font(.system(.caption2, design: .monospaced))
                                    .foregroundStyle(NousTheme.dimText)
                            }
                        }
                    }
                }
                .chartXAxis {
                    AxisMarks(values: .stride(by: .day, count: 2)) { value in
                        AxisGridLine()
                            .foregroundStyle(NousTheme.textPrimary.opacity(0.06))
                        AxisValueLabel {
                            if let date = value.as(Date.self) {
                                Text(date.formatted(.dateTime.day()))
                                    .font(.system(.caption2, design: .monospaced))
                                    .foregroundStyle(NousTheme.dimText)
                            }
                        }
                    }
                }
                .frame(height: 140)
                .padding(12)
                .consolePanel()
                .accessibilityLabel("14-day chart of body weight")
            }
        }
    }
}

// MARK: - Habit consistency chart

private struct HabitConsistencyChart: View {
    let points: [HabitConsistencyPoint]

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                ConsoleSectionLabel(text: "Habit Consistency — 14D")
                Spacer()
                LegendDot(color: NousTheme.statusActive, label: "% DONE")
            }

            Chart {
                ForEach(points) { point in
                    BarMark(
                        x: .value("Day", point.day, unit: .day),
                        y: .value("Rate", point.rate))
                        .foregroundStyle(NousTheme.statusActive.opacity(0.75))
                        .cornerRadius(2)
                }
            }
            .chartYScale(domain: 0...1)
            .chartYAxis {
                AxisMarks(position: .leading, values: [0, 0.5, 1.0]) { value in
                    AxisGridLine()
                        .foregroundStyle(NousTheme.textPrimary.opacity(0.08))
                    AxisValueLabel {
                        if let rate = value.as(Double.self) {
                            Text("\(Int(rate * 100))%")
                                .font(.system(.caption2, design: .monospaced))
                                .foregroundStyle(NousTheme.statusActive.opacity(0.7))
                        }
                    }
                }
            }
            .chartXAxis {
                AxisMarks(values: .stride(by: .day, count: 2)) { value in
                    AxisGridLine()
                        .foregroundStyle(NousTheme.textPrimary.opacity(0.06))
                    AxisValueLabel {
                        if let date = value.as(Date.self) {
                            Text(date.formatted(.dateTime.day()))
                                .font(.system(.caption2, design: .monospaced))
                                .foregroundStyle(NousTheme.dimText)
                        }
                    }
                }
            }
            .frame(height: 140)
            .padding(12)
            .consolePanel(border: NousTheme.statusActive, opacity: 0.3)
            .accessibilityLabel("14-day chart of habit completion rate")
        }
    }
}

// MARK: - Task throughput chart

private struct TaskThroughputChart: View {
    let points: [TaskThroughputPoint]

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                ConsoleSectionLabel(text: "Tasks Completed — 14D")
                Spacer()
                LegendDot(color: NousTheme.accent, label: "DONE")
            }

            Chart {
                ForEach(points) { point in
                    BarMark(
                        x: .value("Day", point.day, unit: .day),
                        y: .value("Count", point.count))
                        .foregroundStyle(NousTheme.accent.opacity(0.75))
                        .cornerRadius(2)
                }
            }
            .chartYAxis {
                AxisMarks(position: .leading) { value in
                    AxisGridLine()
                        .foregroundStyle(NousTheme.textPrimary.opacity(0.08))
                    AxisValueLabel {
                        if let count = value.as(Double.self) {
                            Text("\(Int(count))")
                                .font(.system(.caption2, design: .monospaced))
                                .foregroundStyle(NousTheme.accent.opacity(0.7))
                        }
                    }
                }
            }
            .chartXAxis {
                AxisMarks(values: .stride(by: .day, count: 2)) { value in
                    AxisGridLine()
                        .foregroundStyle(NousTheme.textPrimary.opacity(0.06))
                    AxisValueLabel {
                        if let date = value.as(Date.self) {
                            Text(date.formatted(.dateTime.day()))
                                .font(.system(.caption2, design: .monospaced))
                                .foregroundStyle(NousTheme.dimText)
                        }
                    }
                }
            }
            .frame(height: 140)
            .padding(12)
            .consolePanel()
            .accessibilityLabel("14-day chart of completed tasks per day")
        }
    }
}

// MARK: - Focus minutes chart

private struct FocusMinutesChart: View {
    let points: [FocusMinutesPoint]

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                ConsoleSectionLabel(text: "Deep Work Minutes — 14D")
                Spacer()
                LegendDot(color: NousTheme.accent, label: "MIN")
            }

            Chart {
                ForEach(points) { point in
                    BarMark(
                        x: .value("Day", point.day, unit: .day),
                        y: .value("Minutes", point.minutes))
                        .foregroundStyle(NousTheme.accent.opacity(0.75))
                        .cornerRadius(2)
                }
            }
            .chartYAxis {
                AxisMarks(position: .leading) { value in
                    AxisGridLine()
                        .foregroundStyle(NousTheme.textPrimary.opacity(0.08))
                    AxisValueLabel {
                        if let minutes = value.as(Double.self) {
                            Text("\(Int(minutes))")
                                .font(.system(.caption2, design: .monospaced))
                                .foregroundStyle(NousTheme.accent.opacity(0.7))
                        }
                    }
                }
            }
            .chartXAxis {
                AxisMarks(values: .stride(by: .day, count: 2)) { value in
                    AxisGridLine()
                        .foregroundStyle(NousTheme.textPrimary.opacity(0.06))
                    AxisValueLabel {
                        if let date = value.as(Date.self) {
                            Text(date.formatted(.dateTime.day()))
                                .font(.system(.caption2, design: .monospaced))
                                .foregroundStyle(NousTheme.dimText)
                        }
                    }
                }
            }
            .frame(height: 140)
            .padding(12)
            .consolePanel()
            .accessibilityLabel("14-day chart of deep work minutes logged")
        }
    }
}

// MARK: - Screen time

private struct ScreenTimeChart: View {
    let metrics: [DailyMetric]

    private var points: [DailyMetric] { metrics.filter { $0.screenTimeMinutes != nil } }

    var body: some View {
        if !points.isEmpty {
            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    ConsoleSectionLabel(text: "Screen Time — 14D")
                    Spacer()
                    LegendDot(color: NousTheme.alert, label: "HOURS")
                }

                Chart {
                    ForEach(points) { metric in
                        if let minutes = metric.screenTimeMinutes {
                            BarMark(
                                x: .value("Day", metric.day, unit: .day),
                                y: .value("Hours", minutes / 60))
                                .foregroundStyle(NousTheme.alert.opacity(0.65))
                                .cornerRadius(2)
                        }
                    }
                }
                .chartYAxis {
                    AxisMarks(position: .leading) { value in
                        AxisGridLine()
                            .foregroundStyle(NousTheme.textPrimary.opacity(0.08))
                        AxisValueLabel {
                            if let hours = value.as(Double.self) {
                                Text(String(format: "%.0fh", hours))
                                    .font(.system(.caption2, design: .monospaced))
                                    .foregroundStyle(NousTheme.alert.opacity(0.7))
                            }
                        }
                    }
                }
                .chartXAxis {
                    AxisMarks(values: .stride(by: .day, count: 2)) { value in
                        AxisGridLine()
                            .foregroundStyle(NousTheme.textPrimary.opacity(0.06))
                        AxisValueLabel {
                            if let date = value.as(Date.self) {
                                Text(date.formatted(.dateTime.day()))
                                    .font(.system(.caption2, design: .monospaced))
                                    .foregroundStyle(NousTheme.dimText)
                            }
                        }
                    }
                }
                .frame(height: 140)
                .padding(12)
                .consolePanel(border: NousTheme.alert, opacity: 0.3)
                .accessibilityLabel("14-day chart of screen time hours")
            }
        }
    }
}

/// Manual entry — iOS restricts automatic Screen Time reads to the Family
/// Controls entitlement, which Apple only grants through a special request
/// process, not available for a personal/xtool-signed build. This logs
/// today's total (from Settings > Screen Time) straight into DailyMetric.
// MARK: - Long term goals

/// All-time view of every project's stated goal (see Project.goalDescription)
/// — distinct from the Quarterly Velocity tracker on the Projects screen,
/// which only counts this quarter's progress and countdown.
private struct LongTermGoalsSection: View {
    let projects: [ProjectInfo]

    private var goalProjects: [ProjectInfo] {
        projects.filter { !$0.project.goalDescription.isEmpty }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            ConsoleSectionLabel(text: "Long Term Goals")

            if goalProjects.isEmpty {
                ConsoleEmptyState(
                    code: "NO_LONG_TERM_GOALS_SET",
                    detail: "Set a goal on a project (Tasks > Projects) to track it here.")
                    .frame(maxWidth: .infinity)
                    .padding(16)
                    .consolePanel(opacity: 0.2)
            } else {
                VStack(spacing: 0) {
                    ForEach(goalProjects) { info in
                        LongTermGoalRow(info: info)
                        if info.id != goalProjects.last?.id {
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

private struct LongTermGoalRow: View {
    let info: ProjectInfo

    private var daysRemaining: Int? {
        guard let target = info.project.goalTargetDate else { return nil }
        let calendar = Calendar.current
        return calendar.dateComponents(
            [.day],
            from: calendar.startOfDay(for: .now),
            to: calendar.startOfDay(for: target)).day
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                Rectangle()
                    .fill(info.project.color.color)
                    .frame(width: 6, height: 6)
                Text(info.project.name.uppercased())
                    .font(.system(.caption, design: .monospaced).weight(.bold))
                    .foregroundStyle(NousTheme.textPrimary)
                Spacer()
                if let days = daysRemaining {
                    Text(days >= 0 ? "\(days)D LEFT" : "\(-days)D OVERDUE")
                        .font(.system(.caption2, design: .monospaced).weight(.bold))
                        .foregroundStyle(days >= 0 ? NousTheme.statusPending : NousTheme.alert)
                }
            }
            Text(info.project.goalDescription)
                .font(.system(.caption, design: .monospaced))
                .foregroundStyle(NousTheme.dimText)
            ProgressView(value: info.progress)
                .progressViewStyle(.linear)
                .tint(info.project.color.color)
        }
        .padding(10)
    }
}

private struct DailyMetricsEntrySection: View {
    @State private var sleepText = ""
    @State private var stepsText = ""
    @State private var weightText = ""
    @State private var justLogged: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            ConsoleSectionLabel(text: "Log Today's Metrics")
            Text("No auto-sync on this build — log today's numbers by hand.")
                .font(.system(.caption2, design: .monospaced))
                .foregroundStyle(NousTheme.dimText)

            MetricEntryRow(
                label: "SLEEP (H)", text: $sleepText, justLogged: justLogged == "sleep"
            ) {
                guard let value = Double(sleepText) else { return }
                DataController.shared.setSleepHours(value)
                sleepText = ""
                justLogged = "sleep"
            }
            MetricEntryRow(
                label: "STEPS", text: $stepsText, justLogged: justLogged == "steps"
            ) {
                guard let value = Int(stepsText) else { return }
                DataController.shared.setSteps(value)
                stepsText = ""
                justLogged = "steps"
            }
            MetricEntryRow(
                label: "WEIGHT (KG)", text: $weightText, justLogged: justLogged == "weight"
            ) {
                guard let value = Double(weightText) else { return }
                DataController.shared.setWeightKg(value)
                weightText = ""
                justLogged = "weight"
            }
        }
    }
}

private struct MetricEntryRow: View {
    let label: String
    @Binding var text: String
    let justLogged: Bool
    let onSave: () -> Void

    var body: some View {
        HStack(spacing: 8) {
            Text(label)
                .font(.system(.caption2, design: .monospaced).weight(.bold))
                .foregroundStyle(NousTheme.accent.opacity(0.85))
                .frame(width: 90, alignment: .leading)
            TextField("", text: $text, prompt:
                Text("value...").foregroundStyle(NousTheme.dimText))
                .keyboardType(.decimalPad)
                .font(.system(.subheadline, design: .monospaced))
                .foregroundStyle(NousTheme.textPrimary)
            Button("LOG", action: onSave)
                .buttonStyle(.shell())
                .disabled(Double(text) == nil)
            if justLogged {
                Text("// OK")
                    .font(.system(.caption2, design: .monospaced))
                    .foregroundStyle(NousTheme.statusActive)
            }
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
        .consolePanel(opacity: 0.3)
    }
}

private struct ScreenTimeSection: View {
    @State private var hoursText = ""
    @State private var justLogged = false

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            ConsoleSectionLabel(text: "Log Screen Time")
            Text("Automatic Screen Time reads require an Apple entitlement this build can't obtain — log today's total hours from Settings > Screen Time.")
                .font(.system(.caption2, design: .monospaced))
                .foregroundStyle(NousTheme.dimText)

            HStack(spacing: 8) {
                TextField("", text: $hoursText, prompt:
                    Text("hours_today...")
                        .foregroundStyle(NousTheme.dimText))
                    .keyboardType(.decimalPad)
                    .font(.system(.subheadline, design: .monospaced))
                    .foregroundStyle(NousTheme.textPrimary)
                Button("LOG") { save() }
                    .buttonStyle(.shell())
                    .disabled(Double(hoursText) == nil)
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 8)
            .consolePanel(border: NousTheme.accent, opacity: 0.5)

            if justLogged {
                Text("// LOGGED")
                    .font(.system(.caption2, design: .monospaced))
                    .foregroundStyle(NousTheme.statusActive)
            }
        }
    }

    private func save() {
        guard let hours = Double(hoursText) else { return }
        DataController.shared.setScreenTimeMinutes(hours * 60)
        hoursText = ""
        justLogged = true
    }
}

// MARK: - Legend

private struct LegendDot: View {
    let color: Color
    let label: String

    var body: some View {
        HStack(spacing: 4) {
            Rectangle()
                .fill(color)
                .frame(width: 8, height: 8)
            Text(label)
                .font(.system(.caption2, design: .monospaced))
                .foregroundStyle(NousTheme.dimText)
        }
        .accessibilityHidden(true)
    }
}
