//
//  JournalView.swift
//  PersonalOps
//
//  Cognitive & Reflection Logs — the "Systems" layer. Energy Audit Log
//  (nightly 1-5 self-rating, 30-day trend) and the Weekly Retrospective
//  (three fixed Sunday prompts). Split out of Analytics into its own tab so
//  reflection stays separate from raw telemetry.
//

import Charts
import GRDB
import GRDBQuery
import SwiftUI

/// A night's 1-5 energy/focus self-rating over the trailing 30 days.
/// `rating` is nil for days with no entry.
struct EnergyPoint: Identifiable, Sendable {
    var day: Date
    var rating: Int?
    var id: Date { day }
}

struct ThirtyDayEnergyRequest: ValueObservationQueryable {
    static var defaultValue: [EnergyPoint] { [] }

    func fetch(_ db: Database) throws -> [EnergyPoint] {
        let calendar = Calendar.current
        let today = calendar.startOfDay(for: .now)
        let start = calendar.date(byAdding: .day, value: -29, to: today)!

        let logs = try EnergyLog.filter(EnergyLog.Columns.day >= start).fetchAll(db)
        var ratingByDay: [Date: Int] = [:]
        for log in logs {
            ratingByDay[calendar.startOfDay(for: log.day)] = log.rating
        }

        return (0..<30).compactMap { offset in
            guard let day = calendar.date(byAdding: .day, value: offset, to: start) else { return nil }
            return EnergyPoint(day: day, rating: ratingByDay[day])
        }
    }
}

/// Observes every tracked item joined with its period history, current
/// (endDate == nil) included.
struct SoberItemsRequest: ValueObservationQueryable {
    static var defaultValue: [SoberItemInfo] { [] }

    func fetch(_ db: Database) throws -> [SoberItemInfo] {
        let items = try SoberItem
            .order(SoberItem.Columns.sortOrder, SoberItem.Columns.createdAt)
            .fetchAll(db)
        let periods = try SoberPeriod.fetchAll(db)
        var periodsByItem: [UUID: [SoberPeriod]] = [:]
        for period in periods {
            periodsByItem[period.soberItemId, default: []].append(period)
        }
        return items.map {
            SoberItemInfo(item: $0, periods: periodsByItem[$0.id] ?? [])
        }
    }
}

struct JournalView: View {
    @Query(ThirtyDayEnergyRequest()) private var energyPoints: [EnergyPoint]
    @Query(SoberItemsRequest()) private var soberItems: [SoberItemInfo]

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 24) {
                    EnergyAuditSection(points: energyPoints)
                    WeeklyRetroSection()
                    SoberTrackerSection(items: soberItems)
                }
                .padding(.horizontal)
                .padding(.top, 12)
                .padding(.bottom, 32)
            }
            .consoleBackground()
            .toolbar {
                ToolbarItem(placement: .principal) {
                    Text("JOURNAL")
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
}

// MARK: - Sober tracker

private struct SoberTrackerSection: View {
    let items: [SoberItemInfo]
    @State private var showingAddItem = false

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                ConsoleSectionLabel(text: "Sober Tracker")
                Spacer()
                Button {
                    showingAddItem = true
                } label: {
                    Image(systemName: "plus")
                }
                .buttonStyle(.plain)
                .foregroundStyle(NousTheme.accent)
            }

            if items.isEmpty {
                VStack(alignment: .leading, spacing: 8) {
                    Text("// NO_ITEMS_TRACKED")
                        .font(.system(.footnote, design: .monospaced).weight(.semibold))
                        .foregroundStyle(NousTheme.statusPending)
                    Text("Add something you've quit (alcohol, smoking, etc.) to start a counter.")
                        .font(.system(.caption2, design: .monospaced))
                        .foregroundStyle(NousTheme.dimText)
                    Button("ADD ITEM") { showingAddItem = true }
                        .buttonStyle(.shell(NousTheme.statusActive))
                }
                .padding(12)
                .frame(maxWidth: .infinity, alignment: .leading)
                .consolePanel(opacity: 0.25)
            } else {
                VStack(spacing: 12) {
                    ForEach(items) { info in
                        SoberItemCard(info: info)
                    }
                }
            }
        }
        .sheet(isPresented: $showingAddItem) {
            AddSoberItemSheet()
                .presentationDetents([.medium])
        }
        .accessibilityElement(children: .contain)
    }
}

private struct SoberItemCard: View {
    let info: SoberItemInfo

    @State private var showingStartSheet = false
    @State private var confirmingReset = false
    @State private var confirmingDelete = false

    /// Standard recovery milestones; the next one drives the countdown line.
    private static let milestones = [7, 30, 60, 90, 180, 365, 730]

    private var nextMilestone: Int? {
        guard let days = info.active?.days else { return nil }
        return Self.milestones.first { $0 > days }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(info.item.name.uppercased())
                    .font(.system(.caption, design: .monospaced).weight(.bold))
                    .foregroundStyle(NousTheme.textPrimary)
                Spacer()
                if info.bestStreak > 0, info.active.map({ $0.days < info.bestStreak }) ?? true {
                    Text("BEST \(info.bestStreak)D")
                        .font(.system(.caption2, design: .monospaced))
                        .foregroundStyle(NousTheme.dimText)
                }
                Button {
                    confirmingDelete = true
                } label: {
                    Image(systemName: "trash")
                        .font(.caption2)
                }
                .buttonStyle(.plain)
                .foregroundStyle(NousTheme.alert.opacity(0.7))
            }

            if let active = info.active {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text("\(active.days)")
                        .font(NousTheme.pixelFontBold(size: 34, relativeTo: .largeTitle))
                        .foregroundStyle(NousTheme.statusActive)
                        .contentTransition(.numericText())
                    Text("DAYS")
                        .font(.system(.caption, design: .monospaced).weight(.bold))
                        .foregroundStyle(NousTheme.textPrimary)
                        .tracking(2)
                    Spacer()
                }

                Text("SINCE \(active.startDate.formatted(.dateTime.day().month(.abbreviated).year()))")
                    .font(.system(.caption2, design: .monospaced))
                    .foregroundStyle(NousTheme.dimText)

                if let next = nextMilestone {
                    let remaining = next - active.days
                    VStack(alignment: .leading, spacing: 4) {
                        ProgressView(value: Double(active.days), total: Double(next))
                            .progressViewStyle(.linear)
                            .tint(NousTheme.statusActive)
                        Text("\(remaining)D TO \(next)-DAY MILESTONE")
                            .font(.system(.caption2, design: .monospaced))
                            .foregroundStyle(NousTheme.statusPending)
                    }
                }

                HStack {
                    Button("EDIT START") { showingStartSheet = true }
                        .buttonStyle(.shell())
                    Spacer()
                    Button("RESET") { confirmingReset = true }
                        .buttonStyle(.shell(NousTheme.alert))
                }
            } else {
                Text("// NOT_STARTED")
                    .font(.system(.caption2, design: .monospaced).weight(.semibold))
                    .foregroundStyle(NousTheme.statusPending)
                Button("START TRACKING") { showingStartSheet = true }
                    .buttonStyle(.shell(NousTheme.statusActive))
            }
        }
        .padding(12)
        .consolePanel(
            border: info.active != nil ? NousTheme.statusActive : NousTheme.dimText,
            opacity: info.active != nil ? 0.4 : 0.25)
        .confirmationDialog(
            "Reset \(info.item.name)? Today becomes day zero — history and best streak are kept.",
            isPresented: $confirmingReset,
            titleVisibility: .visible
        ) {
            Button("RESET STREAK", role: .destructive) {
                withAnimation(.spring(response: 0.4, dampingFraction: 0.8)) {
                    DataController.shared.resetSoberStreak(itemId: info.item.id)
                }
            }
            Button("Cancel", role: .cancel) {}
        }
        .confirmationDialog(
            "Delete \(info.item.name) and all its history?",
            isPresented: $confirmingDelete,
            titleVisibility: .visible
        ) {
            Button("DELETE", role: .destructive) {
                DataController.shared.deleteSoberItem(info.item.id)
            }
            Button("Cancel", role: .cancel) {}
        }
        .sheet(isPresented: $showingStartSheet) {
            SoberStartSheet(itemId: info.item.id, initialDate: info.active?.startDate ?? .now)
                .presentationDetents([.medium])
        }
    }
}

private struct AddSoberItemSheet: View {
    @Environment(\.dismiss) private var dismiss
    @State private var name = ""
    @State private var startDate: Date = .now

    var body: some View {
        NavigationStack {
            Form {
                TextField("What are you quitting? (e.g. Alcohol)", text: $name)
                    .foregroundStyle(NousTheme.textPrimary)
                    .listRowBackground(NousTheme.panel)
                DatePicker(
                    "Sober since",
                    selection: $startDate,
                    in: ...Date.now,
                    displayedComponents: .date)
                    .listRowBackground(NousTheme.panel)
            }
            .scrollContentBackground(.hidden)
            .background(NousTheme.background)
            .navigationTitle("")
            .toolbar {
                ToolbarItem(placement: .principal) {
                    Text("NEW ITEM")
                        .font(NousTheme.pixelFontBold(size: 14, relativeTo: .subheadline))
                        .tracking(2)
                        .foregroundStyle(NousTheme.onBackground)
                }
                ToolbarItem(placement: .cancellationAction) {
                    Button("CANCEL") { dismiss() }
                        .buttonStyle(.shell(NousTheme.alert))
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("ADD") {
                        let itemId = DataController.shared.addSoberItem(name)
                        DataController.shared.startSoberTracking(itemId: itemId, from: startDate)
                        dismiss()
                    }
                    .buttonStyle(.shell(NousTheme.statusActive))
                    .disabled(name.trimmingCharacters(in: .whitespaces).isEmpty)
                }
            }
            .toolbarBackground(NousTheme.background, for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
            .toolbarColorScheme(.dark, for: .navigationBar)
            .navigationBarTitleDisplayMode(.inline)
        }
    }
}

private struct SoberStartSheet: View {
    @Environment(\.dismiss) private var dismiss
    let itemId: UUID
    @State private var startDate: Date

    init(itemId: UUID, initialDate: Date) {
        self.itemId = itemId
        _startDate = State(initialValue: initialDate)
    }

    var body: some View {
        NavigationStack {
            Form {
                DatePicker(
                    "Sober since",
                    selection: $startDate,
                    in: ...Date.now,
                    displayedComponents: .date)
                    .listRowBackground(NousTheme.panel)
            }
            .scrollContentBackground(.hidden)
            .background(NousTheme.background)
            .navigationTitle("")
            .toolbar {
                ToolbarItem(placement: .principal) {
                    Text("SOBER START DATE")
                        .font(NousTheme.pixelFontBold(size: 14, relativeTo: .subheadline))
                        .tracking(2)
                        .foregroundStyle(NousTheme.onBackground)
                }
                ToolbarItem(placement: .cancellationAction) {
                    Button("CANCEL") { dismiss() }
                        .buttonStyle(.shell(NousTheme.alert))
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("SET") {
                        DataController.shared.startSoberTracking(itemId: itemId, from: startDate)
                        dismiss()
                    }
                    .buttonStyle(.shell(NousTheme.statusActive))
                }
            }
            .toolbarBackground(NousTheme.background, for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
            .toolbarColorScheme(.dark, for: .navigationBar)
            .navigationBarTitleDisplayMode(.inline)
        }
    }
}

// MARK: - Energy audit log

private struct EnergyAuditSection: View {
    let points: [EnergyPoint]
    @State private var justLogged = false

    /// Tonight's rating, if already logged — its button renders filled.
    private var todayRating: Int? {
        points.last.flatMap {
            Calendar.current.isDateInToday($0.day) ? $0.rating : nil
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                ConsoleSectionLabel(text: "Energy Audit Log")
                Spacer()
                JournalLegendDot(color: NousTheme.statusActive, label: "1-5")
            }
            Text("Rate tonight's focus/energy before bed to map your peak hours over time.")
                .font(.system(.caption2, design: .monospaced))
                .foregroundStyle(NousTheme.dimText)

            HStack(spacing: 8) {
                ForEach(1...5, id: \.self) { rating in
                    let isSelected = todayRating == rating
                    Button {
                        log(rating)
                    } label: {
                        Text("\(rating)")
                            .font(.system(.subheadline, design: .monospaced).weight(.bold))
                            .foregroundStyle(
                                isSelected ? NousTheme.background : NousTheme.statusActive)
                            .padding(.horizontal, 14)
                            .padding(.vertical, 8)
                            .background(
                                isSelected ? NousTheme.statusActive : NousTheme.background)
                            .overlay(Rectangle().stroke(
                                NousTheme.statusActive, lineWidth: NousTheme.borderWidth))
                            .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel(
                        "Rate \(rating)\(isSelected ? ", selected" : "")")
                }
                Spacer()
                if justLogged {
                    Text("// LOGGED")
                        .font(.system(.caption2, design: .monospaced))
                        .foregroundStyle(NousTheme.statusActive)
                }
            }

            Chart {
                ForEach(points) { point in
                    BarMark(
                        x: .value("Day", point.day, unit: .day),
                        y: .value("Rating", point.rating ?? 0))
                        .foregroundStyle(
                            point.rating == nil
                                ? NousTheme.dimText.opacity(0.15)
                                : NousTheme.statusActive.opacity(0.75))
                        .cornerRadius(2)
                }
            }
            .chartYScale(domain: 0...5)
            .chartYAxis {
                AxisMarks(position: .leading, values: [0, 1, 2, 3, 4, 5]) { value in
                    AxisGridLine()
                        .foregroundStyle(NousTheme.textPrimary.opacity(0.08))
                    AxisValueLabel {
                        if let rating = value.as(Double.self) {
                            Text("\(Int(rating))")
                                .font(.system(.caption2, design: .monospaced))
                                .foregroundStyle(NousTheme.statusActive.opacity(0.7))
                        }
                    }
                }
            }
            .chartXAxis {
                AxisMarks(values: .stride(by: .day, count: 5)) { value in
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
            .accessibilityLabel("30-day chart of nightly energy ratings")
        }
    }

    private func log(_ rating: Int) {
        DataController.shared.logEnergyRating(rating)
        justLogged = true
    }
}

// MARK: - Weekly retrospective

private struct WeeklyRetroSection: View {
    @State private var wentRight = ""
    @State private var systemBroke = ""
    @State private var nextFix = ""
    @State private var loaded = false
    @State private var justSaved = false

    private var weekStart: Date {
        let calendar = Calendar.current
        let today = calendar.startOfDay(for: .now)
        let weekday = calendar.component(.weekday, from: today)
        return calendar.date(byAdding: .day, value: -(weekday - 1), to: today)!
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            ConsoleSectionLabel(text: "Weekly Retrospective")
            Text("Answer every Sunday.")
                .font(.system(.caption2, design: .monospaced))
                .foregroundStyle(NousTheme.dimText)

            RetroField(label: "WHAT WENT RIGHT?", text: $wentRight)
            RetroField(label: "WHAT SYSTEM BROKE?", text: $systemBroke)
            RetroField(label: "WHAT SINGLE CHANGE FIXES IT?", text: $nextFix)

            HStack(spacing: 10) {
                Button("SAVE") { save() }
                    .buttonStyle(.shell(NousTheme.statusActive))
                if justSaved {
                    Text("// SAVED")
                        .font(.system(.caption2, design: .monospaced))
                        .foregroundStyle(NousTheme.statusActive)
                }
            }
        }
        .onAppear { load() }
    }

    private func load() {
        guard !loaded else { return }
        let retro = DataController.shared.weeklyRetro(for: weekStart)
        wentRight = retro.wentRight
        systemBroke = retro.systemBroke
        nextFix = retro.nextFix
        loaded = true
    }

    private func save() {
        var retro = DataController.shared.weeklyRetro(for: weekStart)
        retro.wentRight = wentRight
        retro.systemBroke = systemBroke
        retro.nextFix = nextFix
        DataController.shared.saveWeeklyRetro(retro)
        justSaved = true
    }
}

private struct RetroField: View {
    let label: String
    @Binding var text: String

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label)
                .font(.system(.caption2, design: .monospaced).weight(.bold))
                .foregroundStyle(NousTheme.accent.opacity(0.85))
            TextEditor(text: $text)
                .scrollContentBackground(.hidden)
                .font(.system(.footnote, design: .monospaced))
                .foregroundStyle(NousTheme.textPrimary)
                .frame(height: 50)
                .padding(6)
                .consolePanel(opacity: 0.35)
        }
    }
}

private struct JournalLegendDot: View {
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
