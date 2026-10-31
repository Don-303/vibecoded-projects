//
//  HabitsView.swift
//  PersonalOps
//
//  Habits engine. Routines are partitioned into Morning / Afternoon / Evening
//  blocks. Tapping a row toggles today's completion (a HabitCompletion row
//  keyed to start-of-day); the streak count comes from HabitInfo.currentStreak.
//  Each row carries a Matrix Streak Grid: the trailing 7 days rendered as
//  glowing [X] (completed) / hollow [ ] (missed) console brackets.
//

import GRDB
import GRDBQuery
import SwiftUI

/// Observes all non-archived habits joined with their completion history,
/// in manual order.
struct HabitsRequest: ValueObservationQueryable {
    static var defaultValue: [HabitInfo] { [] }

    func fetch(_ db: Database) throws -> [HabitInfo] {
        let habits = try Habit
            .filter(Habit.Columns.isArchived == false)
            .order(Habit.Columns.sortOrder, Habit.Columns.createdAt)
            .fetchAll(db)
        let completions = try HabitCompletion.fetchAll(db)

        let calendar = Calendar.current
        var daysByHabit: [UUID: Set<Date>] = [:]
        for completion in completions {
            daysByHabit[completion.habitId, default: []]
                .insert(calendar.startOfDay(for: completion.date))
        }
        return habits.map {
            HabitInfo(habit: $0, completedDays: daysByHabit[$0.id] ?? [])
        }
    }
}

struct HabitsView: View {
    @Query(HabitsRequest()) private var habits: [HabitInfo]

    @State private var showingNewHabit = false

    private func habits(in segment: RoutineSegment) -> [HabitInfo] {
        habits.filter { $0.habit.segment == segment }
    }

    var body: some View {
        NavigationStack {
            List {
                ForEach(RoutineSegment.allCases, id: \.self) { segment in
                    let segmentHabits = habits(in: segment)
                    if !segmentHabits.isEmpty {
                        Section {
                            ForEach(segmentHabits) { info in
                                HabitRow(info: info) {
                                    toggle(info)
                                }
                                .listRowBackground(NousTheme.panel)
                            }
                            .onDelete { offsets in
                                delete(offsets, in: segmentHabits)
                            }
                        } header: {
                            SegmentHeader(
                                segment: segment,
                                habits: segmentHabits)
                        }
                    }
                }
            }
            .listStyle(.plain)
            .scrollContentBackground(.hidden)
            .background(NousTheme.background)
            .animation(.spring(response: 0.35, dampingFraction: 0.8), value: habits.map(\.id))
            .toolbarBackground(NousTheme.background, for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
            .toolbarColorScheme(.dark, for: .navigationBar)
            .navigationBarTitleDisplayMode(.inline)
            .overlay {
                if habits.isEmpty {
                    ConsoleEmptyState(
                        code: "NO_ROUTINE_PROTOCOLS_REGISTERED",
                        detail: "Add habits to your morning, afternoon, and evening blocks.")
                }
            }
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Button {
                        showingNewHabit = true
                    } label: {
                        Image(systemName: "plus")
                            .contentShape(Rectangle())
                    }
                    .tint(NousTheme.onBackground)
                }
            }
            .sheet(isPresented: $showingNewHabit) {
                NewHabitSheet()
                    .presentationDetents([.medium])
            }
        }
    }

    private func toggle(_ info: HabitInfo) {
        withAnimation(.spring(response: 0.3, dampingFraction: 0.7)) {
            DataController.shared.toggleHabit(info.id)
        }
    }

    private func delete(_ offsets: IndexSet, in segmentHabits: [HabitInfo]) {
        for index in offsets {
            DataController.shared.deleteHabit(segmentHabits[index].id)
        }
    }
}

// MARK: - Section header

private struct SegmentHeader: View {
    let segment: RoutineSegment
    let habits: [HabitInfo]

    private var icon: String {
        switch segment {
        case .morning: return "sunrise.fill"
        case .afternoon: return "sun.max.fill"
        case .evening: return "moon.stars.fill"
        }
    }

    private var doneCount: Int {
        habits.filter { $0.isCompleted() }.count
    }

    var body: some View {
        HStack(spacing: 8) {
            Label(segment.displayName.uppercased(), systemImage: icon)
                .font(.system(.caption, design: .monospaced).weight(.bold))
                .foregroundStyle(NousTheme.onBackground)
            Spacer()
            ProgressView(
                value: Double(doneCount),
                total: Double(max(habits.count, 1)))
                .progressViewStyle(.linear)
                .tint(NousTheme.onBackground)
                .frame(width: 56)
            Text("\(doneCount)/\(habits.count)")
                .font(.system(.caption, design: .monospaced))
                .foregroundStyle(
                    doneCount == habits.count
                        ? NousTheme.statusActive
                        : NousTheme.statusPending)
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel(
            "\(segment.displayName): \(doneCount) of \(habits.count) done")
    }
}

// MARK: - Matrix streak grid

/// The trailing 7 days as console brackets: glowing green [X] on completed
/// days, hollow [ ] on missed ones. Oldest on the left, today on the right.
private struct MatrixStreakGrid: View {
    let completedDays: Set<Date>

    private var lastSevenDays: [Date] {
        let calendar = Calendar.current
        let today = calendar.startOfDay(for: .now)
        return (0..<7).reversed().compactMap {
            calendar.date(byAdding: .day, value: -$0, to: today)
        }
    }

    var body: some View {
        HStack(spacing: 4) {
            ForEach(lastSevenDays, id: \.self) { day in
                let done = completedDays.contains(day)
                Text(done ? "[X]" : "[ ]")
                    .font(.system(.footnote, design: .monospaced)
                        .weight(done ? .bold : .regular))
                    .foregroundStyle(
                        done ? NousTheme.statusActive : NousTheme.dimText)
                    .consoleGlow(
                        done ? NousTheme.statusActive : .clear, radius: 3)
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(
            "Last 7 days: \(lastSevenDays.filter(completedDays.contains).count) completed")
    }
}

// MARK: - Row with completion toggle

private struct HabitRow: View {
    let info: HabitInfo
    let onToggle: () -> Void

    private var completedToday: Bool { info.isCompleted() }

    // The whole row is one Button — a nested Button plus a row-level tap
    // gesture would double-fire the toggle.
    var body: some View {
        Button(action: onToggle) {
            HStack(spacing: 12) {
                ZStack {
                    Rectangle()
                        .strokeBorder(
                            completedToday ? .clear : NousTheme.dimText,
                            lineWidth: 2)
                        .background(
                            Rectangle().fill(
                                completedToday ? NousTheme.statusActive : .clear))
                        .frame(width: 24, height: 24)
                        .consoleGlow(
                            completedToday ? NousTheme.statusActive : .clear,
                            radius: 4)
                    if completedToday {
                        Image(systemName: "checkmark")
                            .font(.caption.weight(.bold))
                            .foregroundStyle(NousTheme.background)
                            .transition(.scale.combined(with: .opacity))
                    }
                }

                VStack(alignment: .leading, spacing: 4) {
                    HStack(spacing: 6) {
                        Text(info.habit.name)
                            .foregroundStyle(
                                completedToday ? NousTheme.dimText : NousTheme.textPrimary)
                        if info.habit.isProtected {
                            Image(systemName: "lock.fill")
                                .font(.caption2)
                                .foregroundStyle(NousTheme.dimText)
                        }
                    }
                    MatrixStreakGrid(completedDays: info.completedDays)
                    if info.bestStreak > info.currentStreak {
                        Text("BEST \(info.bestStreak)D")
                            .font(.system(.caption2, design: .monospaced))
                            .foregroundStyle(NousTheme.dimText.opacity(0.7))
                    }
                }

                Spacer()

                if info.currentStreak > 0 {
                    Label("\(info.currentStreak)", systemImage: "flame.fill")
                        .font(.system(.caption, design: .monospaced).weight(.semibold))
                        .foregroundStyle(NousTheme.statusPending)
                        .labelStyle(.titleAndIcon)
                        .contentTransition(.numericText())
                        .padding(.horizontal, 8)
                        .padding(.vertical, 4)
                        .overlay(Rectangle().stroke(
                            NousTheme.statusPending.opacity(0.5), lineWidth: 1))
                }
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .sensoryFeedback(.success, trigger: completedToday) { _, new in new }
        .animation(
            .spring(response: 0.3, dampingFraction: 0.7),
            value: completedToday)
        .accessibilityLabel(
            "\(info.habit.name), \(completedToday ? "completed" : "not completed"), streak \(info.currentStreak) days")
        .accessibilityAddTraits(completedToday ? [.isSelected] : [])
    }
}

// MARK: - New habit sheet

private struct NewHabitSheet: View {
    @Environment(\.dismiss) private var dismiss

    @State private var name = ""
    @State private var segment: RoutineSegment = .morning

    var body: some View {
        NavigationStack {
            Form {
                TextField("Habit name", text: $name)
                    .foregroundStyle(NousTheme.textPrimary)
                    .listRowBackground(NousTheme.panel)
                Picker("Routine block", selection: $segment) {
                    ForEach(RoutineSegment.allCases, id: \.self) {
                        Text($0.displayName.uppercased()).tag($0)
                    }
                }
                .pickerStyle(.segmented)
                .listRowBackground(NousTheme.panel)
            }
            .scrollContentBackground(.hidden)
            .background(NousTheme.background)
            .navigationTitle("")
            .toolbar {
                ToolbarItem(placement: .principal) {
                    Text("NEW HABIT")
                        .font(NousTheme.pixelFontBold(size: 14, relativeTo: .subheadline))
                        .tracking(2)
                        .foregroundStyle(NousTheme.onBackground)
                }
                ToolbarItem(placement: .cancellationAction) {
                    Button("CANCEL") { dismiss() }
                        .buttonStyle(.shell(NousTheme.alert))
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("ADD") { save() }
                        .buttonStyle(.shell(NousTheme.statusActive))
                        .disabled(name.trimmingCharacters(
                            in: .whitespaces).isEmpty)
                }
            }
            .toolbarBackground(NousTheme.background, for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
            .toolbarColorScheme(.dark, for: .navigationBar)
            .navigationBarTitleDisplayMode(.inline)
        }
    }

    private func save() {
        let habit = Habit(
            name: name.trimmingCharacters(in: .whitespaces),
            segment: segment)
        DataController.shared.addHabit(habit)
        dismiss()
    }
}
