//
//  TasksView.swift
//  PersonalOps
//
//  Task engine. Segmented picker partitions incomplete tasks:
//    Today's Focus — targetDate exists and is today or earlier (overdue counts)
//    Backlog       — no targetDate, or targetDate in the future
//  Swipe leading = complete, trailing = delete. Completed tasks from today
//  stay visible (struck through) in Today's Focus for the satisfaction hit.
//

import Combine
import GRDB
import GRDBQuery
import SwiftUI

/// Observes incomplete tasks whose target date is today or earlier
/// (overdue counts), plus tasks completed today, priority-first.
struct FocusTasksRequest: ValueObservationQueryable {
    static var defaultValue: [TaskItem] { [] }
    var day: Date

    func fetch(_ db: Database) throws -> [TaskItem] {
        let endOfToday = Calendar.current.date(byAdding: .day, value: 1, to: day)!
        return try TaskItem
            .filter(
                (TaskItem.Columns.isCompleted == false
                    && TaskItem.Columns.targetDate != nil
                    && TaskItem.Columns.targetDate < endOfToday)
                || (TaskItem.Columns.isCompleted == true
                    && TaskItem.Columns.completedAt >= day))
            .order(
                TaskItem.Columns.priorityRaw.desc,
                TaskItem.Columns.createdAt)
            .fetchAll(db)
    }
}

/// Observes incomplete tasks with no target date or a future one.
struct BacklogTasksRequest: ValueObservationQueryable {
    static var defaultValue: [TaskItem] { [] }
    var day: Date

    func fetch(_ db: Database) throws -> [TaskItem] {
        let endOfToday = Calendar.current.date(byAdding: .day, value: 1, to: day)!
        return try TaskItem
            .filter(TaskItem.Columns.isCompleted == false)
            .filter(
                TaskItem.Columns.targetDate == nil
                || TaskItem.Columns.targetDate >= endOfToday)
            .order(
                TaskItem.Columns.priorityRaw.desc,
                TaskItem.Columns.createdAt)
            .fetchAll(db)
    }
}

struct TasksView: View {
    @State private var day = Calendar.current.startOfDay(for: .now)

    var body: some View {
        TasksContent(day: day)
            .id(day)
            .onReceive(NotificationCenter.default.publisher(
                for: .NSCalendarDayChanged)) { _ in
                day = Calendar.current.startOfDay(for: .now)
            }
    }
}

private struct TasksContent: View {
    private enum Partition: String, CaseIterable {
        case today = "Today's Focus"
        case backlog = "Backlog"
    }

    @Query<FocusTasksRequest> private var focusTasks: [TaskItem]
    @Query<BacklogTasksRequest> private var backlogTasks: [TaskItem]
    @Query(ProjectsRequest()) private var projects: [ProjectInfo]

    @State private var partition: Partition = .today
    @State private var showingNewTask = false
    @State private var editingTask: TaskItem?

    private let startOfToday: Date

    private var projectsByID: [UUID: Project] {
        Dictionary(uniqueKeysWithValues: projects.map { ($0.id, $0.project) })
    }

    init(day: Date) {
        self.startOfToday = day
        _focusTasks = Query(constant: FocusTasksRequest(day: day))
        _backlogTasks = Query(constant: BacklogTasksRequest(day: day))
    }

    private var visibleTasks: [TaskItem] {
        switch partition {
        case .today:
            // Open tasks first, completed sink to the bottom.
            return focusTasks.sorted { !$0.isCompleted && $1.isCompleted }
        case .backlog:
            return backlogTasks
        }
    }

    var body: some View {
        NavigationStack {
            List {
                ForEach(visibleTasks) { task in
                    TaskRow(task: task, project: task.projectId.flatMap { projectsByID[$0] })
                        .listRowBackground(NousTheme.panel)
                        .listRowSeparatorTint(NousTheme.accent.opacity(0.2))
                        .contentShape(Rectangle())
                        .onTapGesture { editingTask = task }
                        .contextMenu {
                            if !task.isCompleted {
                                if partition == .backlog {
                                    Button {
                                        reschedule(task, to: startOfToday)
                                    } label: {
                                        Label("Do today", systemImage: "sun.max")
                                    }
                                } else {
                                    Button {
                                        reschedule(task, to: nil)
                                    } label: {
                                        Label("Move to backlog", systemImage: "tray")
                                    }
                                }
                                Button {
                                    reschedule(task, to: Calendar.current.date(
                                        byAdding: .day, value: 1, to: startOfToday))
                                } label: {
                                    Label("Push to tomorrow", systemImage: "arrow.turn.up.right")
                                }
                            }
                            Button(role: .destructive) {
                                delete(task)
                            } label: {
                                Label("Delete", systemImage: "trash")
                            }
                        }
                        .swipeActions(edge: .leading, allowsFullSwipe: true) {
                            Button {
                                toggleComplete(task)
                            } label: {
                                Label(
                                    task.isCompleted ? "Reopen" : "Done",
                                    systemImage: task.isCompleted
                                        ? "arrow.uturn.backward" : "checkmark")
                            }
                            .tint(task.isCompleted ? NousTheme.statusPending : NousTheme.statusActive)
                        }
                        .swipeActions(edge: .trailing, allowsFullSwipe: false) {
                            Button(role: .destructive) {
                                delete(task)
                            } label: {
                                Label("Delete", systemImage: "trash")
                            }
                            .tint(NousTheme.alert)
                            if !task.isCompleted {
                                Button {
                                    reschedule(task, to: Calendar.current.date(
                                        byAdding: .day, value: 1, to: startOfToday))
                                } label: {
                                    Label("Tomorrow", systemImage: "arrow.turn.up.right")
                                }
                                .tint(NousTheme.statusPending)
                            }
                        }
                }
            }
            .listStyle(.plain)
            .scrollContentBackground(.hidden)
            .background(NousTheme.background)
            .animation(
                .spring(response: 0.35, dampingFraction: 0.8),
                value: visibleTasks.map(\.id))
            .toolbarBackground(NousTheme.background, for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
            .toolbarColorScheme(.dark, for: .navigationBar)
            .navigationBarTitleDisplayMode(.inline)
            .overlay {
                if visibleTasks.isEmpty {
                    ConsoleEmptyState(
                        code: partition == .today
                            ? "NO_TASKS_SCHEDULED_FOR_CURRENT_CYCLE"
                            : "BACKLOG_QUEUE_EMPTY",
                        detail: partition == .today
                            ? "Pull tasks from the backlog or add one with +."
                            : "New unscheduled tasks land here.")
                }
            }
            .safeAreaInset(edge: .top) {
                Picker("Partition", selection: $partition) {
                    ForEach(Partition.allCases, id: \.self) {
                        Text($0.rawValue.uppercased()).tag($0)
                    }
                }
                .pickerStyle(.segmented)
                .padding(.horizontal)
                .padding(.bottom, 8)
                .background(NousTheme.background)
            }
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Button {
                        showingNewTask = true
                    } label: {
                        Image(systemName: "plus")
                    }
                    .tint(NousTheme.onBackground)
                }
            }
            .sheet(isPresented: $showingNewTask) {
                NewTaskSheet(defaultToToday: partition == .today)
                    .presentationDetents([.medium])
            }
            .sheet(item: $editingTask) { task in
                EditTaskSheet(task: task)
                    .presentationDetents([.medium])
            }
        }
    }

    private func reschedule(_ task: TaskItem, to date: Date?) {
        withAnimation(.spring(response: 0.35, dampingFraction: 0.8)) {
            DataController.shared.rescheduleTask(task.id, to: date)
        }
    }

    private func toggleComplete(_ task: TaskItem) {
        withAnimation(.spring(response: 0.35, dampingFraction: 0.8)) {
            DataController.shared.setTaskCompleted(task.id, completed: !task.isCompleted)
        }
    }

    private func delete(_ task: TaskItem) {
        withAnimation(.spring(response: 0.35, dampingFraction: 0.8)) {
            DataController.shared.deleteTask(task.id)
        }
    }
}

// MARK: - Row

private struct TaskRow: View {
    let task: TaskItem
    var project: Project?

    private var priorityColor: Color {
        switch task.priority {
        case .low: return NousTheme.dimText
        case .normal: return NousTheme.accent
        case .high: return NousTheme.alert
        }
    }

    private var isOverdue: Bool {
        guard !task.isCompleted, let target = task.targetDate else { return false }
        return target < Calendar.current.startOfDay(for: .now)
    }

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Image(systemName: task.isCompleted
                ? "checkmark.circle.fill" : "circle")
                .foregroundStyle(task.isCompleted ? NousTheme.statusActive : priorityColor)
                .consoleGlow(task.isCompleted ? NousTheme.statusActive : .clear, radius: 3)

            VStack(alignment: .leading, spacing: 3) {
                Text(task.title)
                    .strikethrough(task.isCompleted)
                    .foregroundStyle(task.isCompleted ? NousTheme.dimText : NousTheme.textPrimary)

                HStack(spacing: 6) {
                    if isOverdue {
                        Text("OVERDUE")
                            .font(.system(.caption2, design: .monospaced).weight(.bold))
                            .padding(.horizontal, 6)
                            .padding(.vertical, 2)
                            .background(NousTheme.alert)
                            .foregroundStyle(NousTheme.background)
                    }
                    if task.priority == .high {
                        Text("HIGH")
                            .font(.system(.caption2, design: .monospaced).weight(.bold))
                            .padding(.horizontal, 6)
                            .padding(.vertical, 2)
                            .overlay(Rectangle().stroke(NousTheme.alert, lineWidth: 1))
                            .foregroundStyle(NousTheme.alert)
                    }
                    if task.recurrence != nil {
                        Image(systemName: "repeat")
                            .font(.caption2)
                            .foregroundStyle(NousTheme.accent)
                    }
                    if let target = task.targetDate {
                        Label(
                            target.formatted(.dateTime.month(.abbreviated).day()),
                            systemImage: "calendar")
                            .font(.system(.caption2, design: .monospaced))
                            .foregroundStyle(isOverdue ? NousTheme.alert : NousTheme.dimText)
                    }
                    if task.source == .hermes {
                        Image(systemName: "sparkles")
                            .font(.caption2)
                            .foregroundStyle(NousTheme.accent)
                    }
                    if let project {
                        Text(project.name.uppercased())
                            .font(.system(.caption2, design: .monospaced).weight(.semibold))
                            .padding(.horizontal, 6)
                            .padding(.vertical, 2)
                            .overlay(Rectangle().stroke(project.color.color, lineWidth: 1))
                            .foregroundStyle(project.color.color)
                    }
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
        .accessibilityLabel(
            "\(task.title), \(task.priority.displayName) priority"
            + (task.isCompleted ? ", completed" : "")
            + (isOverdue ? ", overdue" : ""))
    }
}

// MARK: - New task sheet

private struct NewTaskSheet: View {
    @Environment(\.dismiss) private var dismiss
    @Query(ProjectsRequest()) private var projects: [ProjectInfo]

    let defaultToToday: Bool

    @State private var title = ""
    @State private var priority: TaskPriority = .normal
    @State private var hasDeadline = false
    @State private var deadline: Date = .now
    @State private var projectId: UUID?
    @State private var recurrence: TaskRecurrence?
    @State private var showOnCalendar = true

    var body: some View {
        NavigationStack {
            Form {
                TextField("What needs doing?", text: $title)
                    .foregroundStyle(NousTheme.textPrimary)
                    .listRowBackground(NousTheme.panel)

                Picker("Priority", selection: $priority) {
                    ForEach(TaskPriority.allCases, id: \.self) {
                        Text($0.displayName.uppercased()).tag($0)
                    }
                }
                .pickerStyle(.segmented)
                .listRowBackground(NousTheme.panel)

                if !projects.isEmpty {
                    Picker("Project", selection: $projectId) {
                        Text("NONE").tag(UUID?.none)
                        ForEach(projects) { info in
                            Text(info.project.name.uppercased()).tag(Optional(info.id))
                        }
                    }
                    .listRowBackground(NousTheme.panel)
                }

                Toggle("Deadline", isOn: $hasDeadline.animation())
                    .tint(NousTheme.accent)
                    .listRowBackground(NousTheme.panel)
                if hasDeadline {
                    DatePicker(
                        "Due", selection: $deadline,
                        displayedComponents: .date)
                        .listRowBackground(NousTheme.panel)
                    Picker("Repeat", selection: $recurrence) {
                        Text("NEVER").tag(TaskRecurrence?.none)
                        ForEach(TaskRecurrence.allCases, id: \.self) {
                            Text($0.displayName.uppercased()).tag(Optional($0))
                        }
                    }
                    .listRowBackground(NousTheme.panel)
                    Toggle("Show on Calendar", isOn: $showOnCalendar)
                        .tint(NousTheme.accent)
                        .listRowBackground(NousTheme.panel)
                }
            }
            .scrollContentBackground(.hidden)
            .background(NousTheme.background)
            .navigationTitle("")
            .toolbar {
                ToolbarItem(placement: .principal) {
                    Text("NEW TASK")
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
                        .disabled(title.trimmingCharacters(
                            in: .whitespaces).isEmpty)
                }
            }
            .toolbarBackground(NousTheme.background, for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
            .toolbarColorScheme(.dark, for: .navigationBar)
            .navigationBarTitleDisplayMode(.inline)
            .onAppear {
                if defaultToToday { hasDeadline = true }
            }
        }
    }

    private func save() {
        let task = TaskItem(
            title: title.trimmingCharacters(in: .whitespaces),
            priority: priority,
            targetDate: hasDeadline
                ? Calendar.current.startOfDay(for: deadline) : nil,
            projectId: projectId,
            recurrence: hasDeadline ? recurrence : nil,
            showOnCalendar: showOnCalendar)
        DataController.shared.addTask(task)
        dismiss()
    }
}

// MARK: - Edit task sheet

private struct EditTaskSheet: View {
    @Environment(\.dismiss) private var dismiss
    @Query(ProjectsRequest()) private var projects: [ProjectInfo]

    let task: TaskItem

    @State private var title: String
    @State private var priority: TaskPriority
    @State private var hasDeadline: Bool
    @State private var deadline: Date
    @State private var projectId: UUID?
    @State private var recurrence: TaskRecurrence?
    @State private var showOnCalendar: Bool

    init(task: TaskItem) {
        self.task = task
        _title = State(initialValue: task.title)
        _priority = State(initialValue: task.priority)
        _hasDeadline = State(initialValue: task.targetDate != nil)
        _deadline = State(initialValue: task.targetDate ?? .now)
        _projectId = State(initialValue: task.projectId)
        _recurrence = State(initialValue: task.recurrence)
        _showOnCalendar = State(initialValue: task.showOnCalendar)
    }

    var body: some View {
        NavigationStack {
            Form {
                TextField("What needs doing?", text: $title)
                    .foregroundStyle(NousTheme.textPrimary)
                    .listRowBackground(NousTheme.panel)

                Picker("Priority", selection: $priority) {
                    ForEach(TaskPriority.allCases, id: \.self) {
                        Text($0.displayName.uppercased()).tag($0)
                    }
                }
                .pickerStyle(.segmented)
                .listRowBackground(NousTheme.panel)

                if !projects.isEmpty {
                    Picker("Project", selection: $projectId) {
                        Text("NONE").tag(UUID?.none)
                        ForEach(projects) { info in
                            Text(info.project.name.uppercased()).tag(Optional(info.id))
                        }
                    }
                    .listRowBackground(NousTheme.panel)
                }

                Toggle("Deadline", isOn: $hasDeadline.animation())
                    .tint(NousTheme.accent)
                    .listRowBackground(NousTheme.panel)
                if hasDeadline {
                    DatePicker(
                        "Due", selection: $deadline,
                        displayedComponents: .date)
                        .listRowBackground(NousTheme.panel)
                    Picker("Repeat", selection: $recurrence) {
                        Text("NEVER").tag(TaskRecurrence?.none)
                        ForEach(TaskRecurrence.allCases, id: \.self) {
                            Text($0.displayName.uppercased()).tag(Optional($0))
                        }
                    }
                    .listRowBackground(NousTheme.panel)
                    Toggle("Show on Calendar", isOn: $showOnCalendar)
                        .tint(NousTheme.accent)
                        .listRowBackground(NousTheme.panel)
                }

                Button(role: .destructive) {
                    DataController.shared.deleteTask(task.id)
                    dismiss()
                } label: {
                    Text("DELETE TASK")
                        .frame(maxWidth: .infinity)
                }
                .listRowBackground(NousTheme.panel)
                .foregroundStyle(NousTheme.alert)
            }
            .scrollContentBackground(.hidden)
            .background(NousTheme.background)
            .navigationTitle("")
            .toolbar {
                ToolbarItem(placement: .principal) {
                    Text("EDIT TASK")
                        .font(NousTheme.pixelFontBold(size: 14, relativeTo: .subheadline))
                        .tracking(2)
                        .foregroundStyle(NousTheme.onBackground)
                }
                ToolbarItem(placement: .cancellationAction) {
                    Button("CANCEL") { dismiss() }
                        .buttonStyle(.shell(NousTheme.alert))
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("SAVE") { save() }
                        .buttonStyle(.shell(NousTheme.statusActive))
                        .disabled(title.trimmingCharacters(
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
        DataController.shared.updateTask(
            task.id,
            title: title.trimmingCharacters(in: .whitespaces),
            priority: priority,
            targetDate: hasDeadline
                ? Calendar.current.startOfDay(for: deadline) : nil,
            projectId: projectId,
            recurrence: hasDeadline ? recurrence : nil,
            showOnCalendar: showOnCalendar)
        dismiss()
    }
}

