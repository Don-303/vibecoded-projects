//
//  GymView.swift
//  PersonalOps
//
//  Gym tracker: workout plans (e.g. "Push Day") each holding an ordered
//  list of exercises with target sets/reps. Tapping the checkbox logs
//  today's session — same completion-toggle pattern as Habits.
//

import GRDB
import GRDBQuery
import SwiftUI

/// Observes all workout plans joined with their exercises and completion
/// history.
struct WorkoutPlansRequest: ValueObservationQueryable {
    static var defaultValue: [WorkoutPlanInfo] { [] }

    func fetch(_ db: Database) throws -> [WorkoutPlanInfo] {
        let plans = try WorkoutPlan
            .order(WorkoutPlan.Columns.sortOrder, WorkoutPlan.Columns.createdAt)
            .fetchAll(db)
        let exercises = try Exercise.order(Exercise.Columns.sortOrder).fetchAll(db)
        let sessions = try WorkoutSession.fetchAll(db)

        let calendar = Calendar.current
        var exercisesByPlan: [UUID: [Exercise]] = [:]
        for exercise in exercises {
            exercisesByPlan[exercise.workoutPlanId, default: []].append(exercise)
        }
        var daysByPlan: [UUID: Set<Date>] = [:]
        for session in sessions {
            daysByPlan[session.workoutPlanId, default: []]
                .insert(calendar.startOfDay(for: session.date))
        }

        return plans.map {
            WorkoutPlanInfo(
                plan: $0,
                exercises: exercisesByPlan[$0.id] ?? [],
                completedDays: daysByPlan[$0.id] ?? [])
        }
    }
}

/// Observes whether a plan has been one-off assigned for today (see
/// WorkoutDayAssignment) — takes priority over any regular scheduledWeekday.
struct TodayWorkoutAssignmentRequest: ValueObservationQueryable {
    static var defaultValue: UUID? { nil }

    func fetch(_ db: Database) throws -> UUID? {
        let day = Calendar.current.startOfDay(for: .now)
        return try WorkoutDayAssignment
            .filter(WorkoutDayAssignment.Columns.date == day)
            .fetchOne(db)?
            .workoutPlanId
    }
}

struct GymView: View {
    @Query(WorkoutPlansRequest()) private var plans: [WorkoutPlanInfo]
    @Query(TodayWorkoutAssignmentRequest()) private var todayAssignedPlanId: UUID?
    @State private var showingNewPlan = false
    @State private var editingPlan: WorkoutPlan?

    private var todayWeekday: Int { Calendar.current.component(.weekday, from: .now) }

    /// Today's scheduled plan(s) first, then the rest in manual order.
    private var orderedPlans: [WorkoutPlanInfo] {
        plans.sorted { a, b in
            let aToday = a.plan.scheduledWeekday == todayWeekday
            let bToday = b.plan.scheduledWeekday == todayWeekday
            if aToday != bToday { return aToday }
            return a.plan.sortOrder < b.plan.sortOrder
        }
    }

    /// A one-off "do this today" assignment beats the regular weekly
    /// schedule, so an ad-hoc swap doesn't require editing the recurring plan.
    private var todaysPlan: WorkoutPlanInfo? {
        if let todayAssignedPlanId, let assigned = plans.first(where: { $0.id == todayAssignedPlanId }) {
            return assigned
        }
        return plans.first { $0.plan.scheduledWeekday == todayWeekday }
    }

    var body: some View {
        NavigationStack {
            List {
                if let todaysPlan {
                    Section {
                        NavigationLink {
                            WorkoutPlanDetailView(info: todaysPlan)
                        } label: {
                            TodaysWorkoutCard(
                                info: todaysPlan,
                                isOverride: todayAssignedPlanId == todaysPlan.id
                                    && todaysPlan.plan.scheduledWeekday != todayWeekday,
                                onClearOverride: todayAssignedPlanId == todaysPlan.id
                                    ? { DataController.shared.clearWorkoutAssignment(forDay: .now) }
                                    : nil)
                        }
                        .buttonStyle(.plain)
                    }
                    .listRowBackground(Color.clear)
                    .listRowInsets(EdgeInsets())
                } else if !plans.isEmpty {
                    Section {
                        Text("// REST_DAY — NO PLAN SCHEDULED TODAY")
                            .font(.system(.caption, design: .monospaced).weight(.semibold))
                            .foregroundStyle(NousTheme.dimText)
                    }
                    .listRowBackground(Color.clear)
                    .listRowInsets(EdgeInsets())
                }

                // Today's featured plan already has its own hero card above.
                ForEach(orderedPlans.filter { $0.id != todaysPlan?.id }) { info in
                    NavigationLink {
                        WorkoutPlanDetailView(info: info)
                    } label: {
                        WorkoutPlanRow(info: info, isToday: info.plan.scheduledWeekday == todayWeekday)
                    }
                    .listRowBackground(NousTheme.panel)
                    .swipeActions(edge: .trailing, allowsFullSwipe: false) {
                        Button(role: .destructive) {
                            DataController.shared.deleteWorkoutPlan(info.id)
                        } label: {
                            Label("Delete", systemImage: "trash")
                        }
                        .tint(NousTheme.alert)
                    }
                    .contextMenu {
                        Button {
                            DataController.shared.assignWorkoutPlan(info.id, forDay: .now)
                        } label: {
                            Label("Do Today", systemImage: "checkmark.circle")
                        }
                        Button {
                            editingPlan = info.plan
                        } label: {
                            Label("Edit / Assign Weekday", systemImage: "calendar")
                        }
                    }
                }
            }
            .listStyle(.plain)
            .scrollContentBackground(.hidden)
            .background(NousTheme.background)
            .animation(.spring(response: 0.35, dampingFraction: 0.8), value: plans.map(\.id))
            .overlay {
                if plans.isEmpty {
                    ConsoleEmptyState(
                        code: "NO_WORKOUT_PLANS_REGISTERED",
                        detail: "Add a plan (e.g. Push Day) with +, then add its exercises.")
                }
            }
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Button {
                        showingNewPlan = true
                    } label: {
                        Image(systemName: "plus")
                            .contentShape(Rectangle())
                    }
                    .tint(NousTheme.onBackground)
                }
            }
            .toolbarBackground(NousTheme.background, for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
            .toolbarColorScheme(.dark, for: .navigationBar)
            .navigationBarTitleDisplayMode(.inline)
            .sheet(isPresented: $showingNewPlan) {
                NewWorkoutPlanSheet()
                    .presentationDetents([.medium])
            }
            .sheet(item: $editingPlan) { plan in
                EditWorkoutPlanSheet(plan: plan)
                    .presentationDetents([.medium])
            }
        }
    }
}

/// Calendar.weekday-indexed short labels (1 = Sunday ... 7 = Saturday).
private let weekdayShortNames = Calendar.current.shortWeekdaySymbols

// MARK: - Today's workout hero card

/// Prominent shortcut into today's scheduled plan — one tap from the Gym
/// tab straight into the exercise checklist, instead of hunting through
/// the list for whichever plan happens to be assigned to today.
private struct TodaysWorkoutCard: View {
    let info: WorkoutPlanInfo
    var isOverride: Bool = false
    var onClearOverride: (() -> Void)?

    var body: some View {
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 6) {
                HStack(spacing: 6) {
                    Text("TODAY'S WORKOUT")
                        .font(.system(.caption2, design: .monospaced).weight(.bold))
                        .foregroundStyle(NousTheme.statusActive)
                        .tracking(2)
                    if isOverride {
                        Text("SWAPPED")
                            .font(.system(.caption2, design: .monospaced).weight(.bold))
                            .foregroundStyle(NousTheme.background)
                            .padding(.horizontal, 5)
                            .padding(.vertical, 1)
                            .background(NousTheme.statusPending)
                    }
                }
                Text(info.plan.name.uppercased())
                    .font(NousTheme.pixelFontBold(size: 18, relativeTo: .title3))
                    .foregroundStyle(NousTheme.textPrimary)
                if info.isCompletedToday() {
                    Label("DONE", systemImage: "checkmark.seal.fill")
                        .font(.system(.caption, design: .monospaced).weight(.bold))
                        .foregroundStyle(NousTheme.statusActive)
                } else {
                    Text("\(info.exercises.count) EXERCISE\(info.exercises.count == 1 ? "" : "S") — TAP TO START")
                        .font(.system(.caption, design: .monospaced))
                        .foregroundStyle(NousTheme.dimText)
                }
                if let onClearOverride {
                    Button("UNDO SWAP", action: onClearOverride)
                        .buttonStyle(.shell(NousTheme.alert))
                }
            }
            Spacer()
            Image(systemName: "chevron.right")
                .foregroundStyle(NousTheme.statusActive)
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .consolePanel(border: NousTheme.statusActive, opacity: 0.6)
        .padding(.horizontal)
        .padding(.top, 8)
    }
}

// MARK: - Plan row

private struct WorkoutPlanRow: View {
    let info: WorkoutPlanInfo
    var isToday: Bool = false

    /// Plans with exercises derive completion from the checklist in
    /// WorkoutPlanDetailView (see toggleExerciseCompletion) — tapping here
    /// only navigates. Plans with no exercises yet have no other way to
    /// mark a day done, so the row itself stays a toggle for those.
    private var isDirectlyToggleable: Bool { info.exercises.isEmpty }

    var body: some View {
        Group {
            if isDirectlyToggleable {
                Button {
                    withAnimation(.spring(response: 0.3, dampingFraction: 0.7)) {
                        DataController.shared.toggleWorkoutSession(info.id)
                    }
                } label: {
                    rowContent
                }
                .buttonStyle(.plain)
            } else {
                rowContent
            }
        }
        .accessibilityLabel(
            "\(info.plan.name), \(info.isCompletedToday() ? "completed" : "not completed") today, streak \(info.currentStreak) days")
    }

    private var rowContent: some View {
        HStack(spacing: 12) {
            Image(systemName: info.isCompletedToday() ? "checkmark.square.fill" : "square")
                .foregroundStyle(info.isCompletedToday() ? NousTheme.statusActive : NousTheme.dimText)

            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 6) {
                    Text(info.plan.name)
                        .foregroundStyle(
                            info.isCompletedToday() ? NousTheme.dimText : NousTheme.textPrimary)
                        .font(.system(.subheadline, design: .monospaced).weight(.bold))
                    if isToday {
                        Text("TODAY")
                            .font(.system(.caption2, design: .monospaced).weight(.bold))
                            .foregroundStyle(NousTheme.background)
                            .padding(.horizontal, 5)
                            .padding(.vertical, 1)
                            .background(NousTheme.statusActive)
                    }
                }
                HStack(spacing: 6) {
                    if let weekday = info.plan.scheduledWeekday {
                        Text(weekdayShortNames[weekday - 1].uppercased())
                            .font(.system(.caption2, design: .monospaced))
                            .foregroundStyle(NousTheme.accent)
                    }
                    Text("\(info.exercises.count) EXERCISE\(info.exercises.count == 1 ? "" : "S")")
                        .font(.system(.caption2, design: .monospaced))
                        .foregroundStyle(NousTheme.dimText)
                }
            }

            Spacer()

            if info.currentStreak > 0 {
                Label("\(info.currentStreak)", systemImage: "flame.fill")
                    .font(.system(.caption, design: .monospaced).weight(.semibold))
                    .foregroundStyle(NousTheme.statusPending)
                    .labelStyle(.titleAndIcon)
            }
        }
        .contentShape(Rectangle())
    }
}

// MARK: - Plan detail (exercise checklist for today's session)

/// Observes which of a plan's exercises are checked off today.
struct TodayExerciseCompletionsRequest: ValueObservationQueryable {
    static var defaultValue: Set<UUID> { [] }
    var workoutPlanId: UUID

    func fetch(_ db: Database) throws -> Set<UUID> {
        let day = Calendar.current.startOfDay(for: .now)
        let exerciseIds = try Exercise
            .filter(Exercise.Columns.workoutPlanId == workoutPlanId)
            .fetchAll(db)
            .map(\.id)
        guard !exerciseIds.isEmpty else { return [] }
        let completions = try ExerciseCompletion
            .filter(exerciseIds.contains(ExerciseCompletion.Columns.exerciseId))
            .filter(ExerciseCompletion.Columns.date == day)
            .fetchAll(db)
        return Set(completions.map(\.exerciseId))
    }
}

private struct WorkoutPlanDetailView: View {
    let info: WorkoutPlanInfo
    @Query<TodayExerciseCompletionsRequest> private var completedToday: Set<UUID>
    @State private var showingNewExercise = false

    init(info: WorkoutPlanInfo) {
        self.info = info
        _completedToday = Query(constant: TodayExerciseCompletionsRequest(workoutPlanId: info.id))
    }

    var body: some View {
        List {
            if !info.exercises.isEmpty {
                Section {
                    Text("\(completedToday.count)/\(info.exercises.count) CHECKED OFF TODAY")
                        .font(.system(.caption, design: .monospaced).weight(.semibold))
                        .foregroundStyle(
                            completedToday.count == info.exercises.count
                                ? NousTheme.statusActive : NousTheme.dimText)
                }
                .listRowBackground(Color.clear)
                .listRowInsets(EdgeInsets())
            }

            ForEach(info.exercises) { exercise in
                let done = completedToday.contains(exercise.id)
                Button {
                    withAnimation(.spring(response: 0.3, dampingFraction: 0.7)) {
                        DataController.shared.toggleExerciseCompletion(
                            exercise.id, workoutPlanId: info.id)
                    }
                } label: {
                    HStack {
                        Image(systemName: done ? "checkmark.square.fill" : "square")
                            .foregroundStyle(done ? NousTheme.statusActive : NousTheme.dimText)
                        Text(exercise.name)
                            .strikethrough(done)
                            .font(.system(.subheadline, design: .monospaced))
                            .foregroundStyle(done ? NousTheme.dimText : NousTheme.textPrimary)
                        Spacer()
                        Text("\(exercise.targetSets) x \(exercise.targetReps)")
                            .font(.system(.caption, design: .monospaced))
                            .foregroundStyle(NousTheme.dimText)
                    }
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .listRowBackground(NousTheme.panel)
                .swipeActions(edge: .trailing, allowsFullSwipe: false) {
                    Button(role: .destructive) {
                        DataController.shared.deleteExercise(exercise.id)
                    } label: {
                        Label("Delete", systemImage: "trash")
                    }
                    .tint(NousTheme.alert)
                }
                .accessibilityLabel(
                    "\(exercise.name), \(exercise.targetSets) sets of \(exercise.targetReps), \(done ? "checked" : "not checked")")
            }
        }
        .listStyle(.plain)
        .scrollContentBackground(.hidden)
        .background(NousTheme.background)
        .overlay {
            if info.exercises.isEmpty {
                ConsoleEmptyState(
                    code: "NO_EXERCISES_REGISTERED",
                    detail: "Add exercises with + — check them off during your session.")
            }
        }
        .navigationTitle(info.plan.name)
        .toolbarBackground(NousTheme.background, for: .navigationBar)
        .toolbarBackground(.visible, for: .navigationBar)
        .toolbarColorScheme(.dark, for: .navigationBar)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                Button {
                    showingNewExercise = true
                } label: {
                    Image(systemName: "plus")
                }
                .tint(NousTheme.onBackground)
            }
        }
        .sheet(isPresented: $showingNewExercise) {
            NewExerciseSheet(workoutPlanId: info.id, nextSortOrder: info.exercises.count)
                .presentationDetents([.medium])
        }
    }
}

// MARK: - New plan sheet

private struct NewWorkoutPlanSheet: View {
    @Environment(\.dismiss) private var dismiss
    @State private var name = ""
    @State private var weekday: Int?

    var body: some View {
        NavigationStack {
            Form {
                TextField("Plan name (e.g. Push Day)", text: $name)
                    .foregroundStyle(NousTheme.textPrimary)
                    .listRowBackground(NousTheme.panel)
                Picker("Day", selection: $weekday) {
                    Text("UNASSIGNED").tag(Int?.none)
                    ForEach(1...7, id: \.self) { day in
                        Text(weekdayShortNames[day - 1].uppercased()).tag(Optional(day))
                    }
                }
                .listRowBackground(NousTheme.panel)
            }
            .scrollContentBackground(.hidden)
            .background(NousTheme.background)
            .navigationTitle("")
            .toolbar {
                ToolbarItem(placement: .principal) {
                    Text("NEW WORKOUT PLAN")
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
                        .disabled(name.trimmingCharacters(in: .whitespaces).isEmpty)
                }
            }
            .toolbarBackground(NousTheme.background, for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
            .toolbarColorScheme(.dark, for: .navigationBar)
            .navigationBarTitleDisplayMode(.inline)
        }
    }

    private func save() {
        DataController.shared.addWorkoutPlan(WorkoutPlan(
            name: name.trimmingCharacters(in: .whitespaces),
            scheduledWeekday: weekday))
        dismiss()
    }
}

// MARK: - Edit plan sheet

private struct EditWorkoutPlanSheet: View {
    @Environment(\.dismiss) private var dismiss
    let plan: WorkoutPlan

    @State private var name: String
    @State private var weekday: Int?

    init(plan: WorkoutPlan) {
        self.plan = plan
        _name = State(initialValue: plan.name)
        _weekday = State(initialValue: plan.scheduledWeekday)
    }

    var body: some View {
        NavigationStack {
            Form {
                TextField("Plan name", text: $name)
                    .foregroundStyle(NousTheme.textPrimary)
                    .listRowBackground(NousTheme.panel)
                Picker("Day", selection: $weekday) {
                    Text("UNASSIGNED").tag(Int?.none)
                    ForEach(1...7, id: \.self) { day in
                        Text(weekdayShortNames[day - 1].uppercased()).tag(Optional(day))
                    }
                }
                .listRowBackground(NousTheme.panel)
            }
            .scrollContentBackground(.hidden)
            .background(NousTheme.background)
            .navigationTitle("")
            .toolbar {
                ToolbarItem(placement: .principal) {
                    Text("EDIT PLAN")
                        .font(NousTheme.pixelFontBold(size: 14, relativeTo: .subheadline))
                        .tracking(2)
                        .foregroundStyle(NousTheme.onBackground)
                }
                ToolbarItem(placement: .cancellationAction) {
                    Button("CANCEL") { dismiss() }
                        .buttonStyle(.shell(NousTheme.alert))
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("SAVE") {
                        DataController.shared.updateWorkoutPlan(
                            plan.id,
                            name: name.trimmingCharacters(in: .whitespaces),
                            scheduledWeekday: weekday)
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

// MARK: - New exercise sheet

private struct NewExerciseSheet: View {
    @Environment(\.dismiss) private var dismiss
    let workoutPlanId: UUID
    let nextSortOrder: Int

    @State private var name = ""
    @State private var sets = 3
    @State private var reps = 10

    var body: some View {
        NavigationStack {
            Form {
                TextField("Exercise name", text: $name)
                    .foregroundStyle(NousTheme.textPrimary)
                    .listRowBackground(NousTheme.panel)
                Stepper("Sets: \(sets)", value: $sets, in: 1...10)
                    .foregroundStyle(NousTheme.textPrimary)
                    .listRowBackground(NousTheme.panel)
                Stepper("Reps: \(reps)", value: $reps, in: 1...50)
                    .foregroundStyle(NousTheme.textPrimary)
                    .listRowBackground(NousTheme.panel)
            }
            .scrollContentBackground(.hidden)
            .background(NousTheme.background)
            .navigationTitle("")
            .toolbar {
                ToolbarItem(placement: .principal) {
                    Text("NEW EXERCISE")
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
                        .disabled(name.trimmingCharacters(in: .whitespaces).isEmpty)
                }
            }
            .toolbarBackground(NousTheme.background, for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
            .toolbarColorScheme(.dark, for: .navigationBar)
            .navigationBarTitleDisplayMode(.inline)
        }
    }

    private func save() {
        DataController.shared.addExercise(Exercise(
            workoutPlanId: workoutPlanId,
            name: name.trimmingCharacters(in: .whitespaces),
            targetSets: sets,
            targetReps: reps,
            sortOrder: nextSortOrder))
        dismiss()
    }
}
