//
//  ProjectsView.swift
//  PersonalOps
//
//  Project management. A project is just a named grouping over TaskItem —
//  progress is always derived live from its linked tasks (see ProjectInfo),
//  never stored. Tapping a card drills into that project's own task list,
//  which reuses the same complete/reschedule/delete actions as TasksView.
//

import GRDB
import GRDBQuery
import SwiftUI

/// Observes all non-archived projects joined with their tasks, in manual
/// order.
struct ProjectsRequest: ValueObservationQueryable {
    static var defaultValue: [ProjectInfo] { [] }

    func fetch(_ db: Database) throws -> [ProjectInfo] {
        let projects = try Project
            .filter(Project.Columns.isArchived == false)
            .order(Project.Columns.sortOrder, Project.Columns.createdAt)
            .fetchAll(db)
        let tasks = try TaskItem.fetchAll(db)

        var tasksByProject: [UUID: [TaskItem]] = [:]
        for task in tasks {
            guard let projectId = task.projectId else { continue }
            tasksByProject[projectId, default: []].append(task)
        }
        return projects.map {
            ProjectInfo(project: $0, tasks: tasksByProject[$0.id] ?? [])
        }
    }
}

/// Observes archived projects, in manual order.
struct ArchivedProjectsRequest: ValueObservationQueryable {
    static var defaultValue: [ProjectInfo] { [] }

    func fetch(_ db: Database) throws -> [ProjectInfo] {
        let projects = try Project
            .filter(Project.Columns.isArchived == true)
            .order(Project.Columns.sortOrder, Project.Columns.createdAt)
            .fetchAll(db)
        let tasks = try TaskItem.fetchAll(db)

        var tasksByProject: [UUID: [TaskItem]] = [:]
        for task in tasks {
            guard let projectId = task.projectId else { continue }
            tasksByProject[projectId, default: []].append(task)
        }
        return projects.map {
            ProjectInfo(project: $0, tasks: tasksByProject[$0.id] ?? [])
        }
    }
}

/// Observes all tasks belonging to one project.
struct ProjectTasksRequest: ValueObservationQueryable {
    static var defaultValue: [TaskItem] { [] }
    var projectId: UUID

    func fetch(_ db: Database) throws -> [TaskItem] {
        try TaskItem
            .filter(TaskItem.Columns.projectId == projectId)
            .order(
                TaskItem.Columns.isCompleted,
                TaskItem.Columns.priorityRaw.desc,
                TaskItem.Columns.createdAt)
            .fetchAll(db)
    }
}

struct ProjectsView: View {
    @Query(ProjectsRequest()) private var projects: [ProjectInfo]
    @Query(ArchivedProjectsRequest()) private var archivedProjects: [ProjectInfo]
    @Query(BooksRequest()) private var books: [Book]
    @State private var showingNewProject = false
    @State private var showingArchive = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 20) {
                    QuarterlyVelocitySection(projects: projects, books: books)
                        .padding(.horizontal)
                        .padding(.top, 12)

                    if projects.isEmpty {
                        ConsoleEmptyState(
                            code: "NO_PROJECTS_REGISTERED",
                            detail: "Create a project to start grouping tasks.")
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 80)
                    } else {
                        VStack(spacing: 12) {
                            ForEach(projects) { info in
                                NavigationLink {
                                    // The protected Reading List gets the
                                    // dedicated book pipeline instead of a
                                    // generic task list.
                                    if info.project.isProtected,
                                       info.project.name == "Reading List" {
                                        ReadingListView()
                                    } else {
                                        ProjectDetailView(info: info)
                                    }
                                } label: {
                                    ProjectCard(info: info)
                                }
                                .buttonStyle(.plain)
                                .contextMenu {
                                    if !info.project.isProtected {
                                        Button {
                                            DataController.shared.setProjectArchived(info.id, archived: true)
                                        } label: {
                                            Label("Archive Project", systemImage: "archivebox")
                                        }
                                        Button(role: .destructive) {
                                            DataController.shared.deleteProject(info.id)
                                        } label: {
                                            Label("Delete Project", systemImage: "trash")
                                        }
                                    }
                                }
                            }
                        }
                        .padding(.horizontal)
                    }
                }
                .padding(.bottom, 32)
            }
            .consoleBackground()
            .toolbar {
                if !archivedProjects.isEmpty {
                    ToolbarItem(placement: .primaryAction) {
                        Button {
                            showingArchive = true
                        } label: {
                            Image(systemName: "archivebox")
                                .contentShape(Rectangle())
                        }
                        .tint(NousTheme.onBackground)
                    }
                }
                ToolbarItem(placement: .primaryAction) {
                    Button {
                        showingNewProject = true
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
            .sheet(isPresented: $showingNewProject) {
                NewProjectSheet()
                    .presentationDetents([.medium])
            }
            .sheet(isPresented: $showingArchive) {
                ArchivedProjectsSheet(projects: archivedProjects)
            }
        }
    }
}

// MARK: - Archived projects

private struct ArchivedProjectsSheet: View {
    @Environment(\.dismiss) private var dismiss
    let projects: [ProjectInfo]

    var body: some View {
        NavigationStack {
            List {
                ForEach(projects) { info in
                    HStack {
                        Rectangle()
                            .fill(info.project.color.color)
                            .frame(width: 8, height: 8)
                        Text(info.project.name.uppercased())
                            .font(.system(.subheadline, design: .monospaced).weight(.bold))
                            .foregroundStyle(NousTheme.textPrimary)
                        Spacer()
                        Text("\(info.doneCount)/\(info.tasks.count)")
                            .font(.system(.caption, design: .monospaced))
                            .foregroundStyle(NousTheme.dimText)
                    }
                    .listRowBackground(NousTheme.panel)
                    .swipeActions(edge: .leading, allowsFullSwipe: true) {
                        Button {
                            DataController.shared.setProjectArchived(info.id, archived: false)
                        } label: {
                            Label("Unarchive", systemImage: "arrow.uturn.up")
                        }
                        .tint(NousTheme.statusActive)
                    }
                    .swipeActions(edge: .trailing, allowsFullSwipe: false) {
                        Button(role: .destructive) {
                            DataController.shared.deleteProject(info.id)
                        } label: {
                            Label("Delete", systemImage: "trash")
                        }
                        .tint(NousTheme.alert)
                    }
                }
            }
            .listStyle(.plain)
            .scrollContentBackground(.hidden)
            .background(NousTheme.background)
            .overlay {
                if projects.isEmpty {
                    ConsoleEmptyState(
                        code: "NO_ARCHIVED_PROJECTS",
                        detail: "Archived projects will show up here.")
                }
            }
            .toolbar {
                ToolbarItem(placement: .principal) {
                    Text("ARCHIVED PROJECTS")
                        .font(NousTheme.pixelFontBold(size: 14, relativeTo: .subheadline))
                        .tracking(2)
                        .foregroundStyle(NousTheme.onBackground)
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("DONE") { dismiss() }
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

// MARK: - Quarterly velocity tracker

private struct QuarterlyVelocitySection: View {
    let projects: [ProjectInfo]
    let books: [Book]

    private var quarterLabel: String {
        let calendar = Calendar.current
        let now = Date.now
        let quarter = (calendar.component(.month, from: now) - 1) / 3 + 1
        return "Q\(quarter) \(calendar.component(.year, from: now))"
    }

    private var booksFinishedThisQuarter: Int {
        let calendar = Calendar.current
        let now = Date.now
        let month = calendar.component(.month, from: now)
        let year = calendar.component(.year, from: now)
        let quarterStartMonth = ((month - 1) / 3) * 3 + 1
        guard let start = calendar.date(
            from: DateComponents(year: year, month: quarterStartMonth, day: 1))
        else { return 0 }
        return books.filter {
            $0.status == .finished && ($0.finishedAt ?? .distantPast) >= start
        }.count
    }

    private var daysLeftInQuarter: Int {
        let calendar = Calendar.current
        let now = Date.now
        let month = calendar.component(.month, from: now)
        let year = calendar.component(.year, from: now)
        let quarterStartMonth = ((month - 1) / 3) * 3 + 1
        let start = calendar.date(from: DateComponents(year: year, month: quarterStartMonth, day: 1))!
        let end = calendar.date(byAdding: DateComponents(month: 3, day: -1), to: start)!
        let days = calendar.dateComponents(
            [.day],
            from: calendar.startOfDay(for: now),
            to: calendar.startOfDay(for: end)).day ?? 0
        return max(0, days)
    }

    /// Projects treated as this quarter's objectives — anything with a
    /// stated goal or at least one task attached.
    private var objectives: [ProjectInfo] {
        projects.filter { !$0.project.goalDescription.isEmpty || !$0.tasks.isEmpty }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                ConsoleSectionLabel(text: "Quarterly Velocity")
                Spacer()
                Text("\(quarterLabel) — \(daysLeftInQuarter)D LEFT")
                    .font(.system(.caption2, design: .monospaced).weight(.bold))
                    .foregroundStyle(NousTheme.statusPending)
            }

            if booksFinishedThisQuarter > 0 {
                Text("BOOKS FINISHED THIS QUARTER: \(booksFinishedThisQuarter)")
                    .font(.system(.caption2, design: .monospaced).weight(.semibold))
                    .foregroundStyle(NousTheme.statusActive)
            }

            if objectives.isEmpty {
                ConsoleEmptyState(
                    code: "NO_QUARTERLY_OBJECTIVES",
                    detail: "Set a goal on a project to track it here.")
                    .frame(maxWidth: .infinity)
                    .padding(16)
                    .consolePanel(opacity: 0.2)
            } else {
                VStack(spacing: 0) {
                    ForEach(objectives) { info in
                        HStack(spacing: 8) {
                            Rectangle()
                                .fill(info.project.color.color)
                                .frame(width: 6, height: 6)
                            Text(info.project.name.uppercased())
                                .font(.system(.caption, design: .monospaced).weight(.semibold))
                                .foregroundStyle(NousTheme.textPrimary)
                            Spacer()
                            ProgressView(value: info.progress)
                                .progressViewStyle(.linear)
                                .tint(info.project.color.color)
                                .frame(width: 60)
                            Text("\(Int(info.progress * 100))%")
                                .font(.system(.caption2, design: .monospaced))
                                .foregroundStyle(NousTheme.dimText)
                        }
                        .padding(8)
                        if info.id != objectives.last?.id {
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

// MARK: - Project card

private struct ProjectCard: View {
    let info: ProjectInfo

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Rectangle()
                    .fill(info.project.color.color)
                    .frame(width: 8, height: 8)
                Text(info.project.name.uppercased())
                    .font(.system(.subheadline, design: .monospaced).weight(.bold))
                    .foregroundStyle(NousTheme.textPrimary)
                if info.project.isProtected {
                    Image(systemName: "lock.fill")
                        .font(.caption2)
                        .foregroundStyle(NousTheme.dimText)
                }
                Spacer()
                Text("\(info.doneCount)/\(info.tasks.count)")
                    .font(.system(.caption, design: .monospaced))
                    .foregroundStyle(NousTheme.dimText)
            }

            ProgressView(value: info.progress)
                .progressViewStyle(.linear)
                .tint(info.project.color.color)

            if !info.project.goalDescription.isEmpty {
                Text(info.project.goalDescription)
                    .font(.system(.caption2, design: .monospaced))
                    .foregroundStyle(NousTheme.dimText)
                    .lineLimit(2)
            }

            HStack {
                if info.tasks.isEmpty {
                    Text("// NO TASKS ASSIGNED")
                        .font(.system(.caption2, design: .monospaced))
                        .foregroundStyle(NousTheme.dimText)
                } else {
                    Text("\(Int(info.progress * 100))% COMPLETE")
                        .font(.system(.caption2, design: .monospaced))
                        .foregroundStyle(info.project.color.color.opacity(0.8))
                }
                Spacer()
                if let target = info.project.goalTargetDate {
                    Label(
                        target.formatted(.dateTime.month(.abbreviated).day()),
                        systemImage: "target")
                        .font(.system(.caption2, design: .monospaced))
                        .foregroundStyle(
                            target < Calendar.current.startOfDay(for: .now)
                                ? NousTheme.alert : NousTheme.dimText)
                }
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .contentShape(Rectangle())
        .consolePanel(border: info.project.color.color, opacity: 0.35)
        .accessibilityElement(children: .combine)
        .accessibilityLabel(
            "\(info.project.name), \(info.doneCount) of \(info.tasks.count) tasks complete")
    }
}

// MARK: - Project detail (task list scoped to one project)

private struct ProjectDetailView: View {
    let info: ProjectInfo
    @Query<ProjectTasksRequest> private var tasks: [TaskItem]
    @State private var showingEditGoal = false

    init(info: ProjectInfo) {
        self.info = info
        _tasks = Query(constant: ProjectTasksRequest(projectId: info.id))
    }

    var body: some View {
        List {
            Section {
                Button {
                    showingEditGoal = true
                } label: {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("LONG-TERM GOAL")
                            .font(.system(.caption2, design: .monospaced).weight(.bold))
                            .foregroundStyle(NousTheme.accent.opacity(0.85))
                        Text(info.project.goalDescription.isEmpty
                            ? "// TAP TO SET A GOAL" : info.project.goalDescription)
                            .font(.system(.footnote, design: .monospaced))
                            .foregroundStyle(
                                info.project.goalDescription.isEmpty
                                    ? NousTheme.dimText : NousTheme.textPrimary)
                        if let target = info.project.goalTargetDate {
                            Label(
                                "Target \(target.formatted(.dateTime.month(.abbreviated).day().year()))",
                                systemImage: "target")
                                .font(.system(.caption2, design: .monospaced))
                                .foregroundStyle(NousTheme.statusPending)
                        }
                    }
                }
                .buttonStyle(.plain)
            }
            .listRowBackground(NousTheme.panel)

            ForEach(tasks) { task in
                ProjectTaskRow(task: task)
                    .listRowBackground(NousTheme.panel)
                    .swipeActions(edge: .leading, allowsFullSwipe: true) {
                        Button {
                            DataController.shared.setTaskCompleted(
                                task.id, completed: !task.isCompleted)
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
                            DataController.shared.deleteTask(task.id)
                        } label: {
                            Label("Delete", systemImage: "trash")
                        }
                        .tint(NousTheme.alert)
                    }
            }
        }
        .listStyle(.plain)
        .scrollContentBackground(.hidden)
        .background(NousTheme.background)
        .overlay {
            if tasks.isEmpty {
                ConsoleEmptyState(
                    code: "PROJECT_QUEUE_EMPTY",
                    detail: "Assign tasks to this project from the Tasks tab.")
            }
        }
        .navigationTitle(info.project.name)
        .toolbarBackground(NousTheme.background, for: .navigationBar)
        .toolbarBackground(.visible, for: .navigationBar)
        .toolbarColorScheme(.dark, for: .navigationBar)
        .navigationBarTitleDisplayMode(.inline)
        .sheet(isPresented: $showingEditGoal) {
            EditGoalSheet(project: info.project)
                .presentationDetents([.medium])
        }
    }
}

private struct ProjectTaskRow: View {
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
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
        .accessibilityLabel(
            "\(task.title), \(task.isCompleted ? "completed" : "not completed")")
    }
}

// MARK: - New project sheet

private struct NewProjectSheet: View {
    @Environment(\.dismiss) private var dismiss

    @State private var name = ""
    @State private var color: ProjectColor = .cyan
    @State private var goalDescription = ""
    @State private var hasTargetDate = false
    @State private var targetDate: Date = .now

    var body: some View {
        NavigationStack {
            Form {
                TextField("Project name", text: $name)
                    .foregroundStyle(NousTheme.textPrimary)
                    .listRowBackground(NousTheme.panel)
                Picker("Color tag", selection: $color) {
                    ForEach(ProjectColor.allCases, id: \.self) { c in
                        Label(c.rawValue.uppercased(), systemImage: "circle.fill")
                            .foregroundStyle(c.color)
                            .tag(c)
                    }
                }
                .listRowBackground(NousTheme.panel)

                TextField("Long-term goal (optional)", text: $goalDescription, axis: .vertical)
                    .lineLimit(2...4)
                    .foregroundStyle(NousTheme.textPrimary)
                    .listRowBackground(NousTheme.panel)

                Toggle("Target date", isOn: $hasTargetDate.animation())
                    .tint(NousTheme.accent)
                    .listRowBackground(NousTheme.panel)
                if hasTargetDate {
                    DatePicker("Target", selection: $targetDate, displayedComponents: .date)
                        .listRowBackground(NousTheme.panel)
                }
            }
            .scrollContentBackground(.hidden)
            .background(NousTheme.background)
            .navigationTitle("")
            .toolbar {
                ToolbarItem(placement: .principal) {
                    Text("NEW PROJECT")
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
        let project = Project(
            name: name.trimmingCharacters(in: .whitespaces),
            color: color,
            goalDescription: goalDescription.trimmingCharacters(in: .whitespacesAndNewlines),
            goalTargetDate: hasTargetDate ? Calendar.current.startOfDay(for: targetDate) : nil)
        DataController.shared.addProject(project)
        dismiss()
    }
}

// MARK: - Edit goal sheet

private struct EditGoalSheet: View {
    @Environment(\.dismiss) private var dismiss
    let project: Project

    @State private var goalDescription: String
    @State private var hasTargetDate: Bool
    @State private var targetDate: Date

    init(project: Project) {
        self.project = project
        _goalDescription = State(initialValue: project.goalDescription)
        _hasTargetDate = State(initialValue: project.goalTargetDate != nil)
        _targetDate = State(initialValue: project.goalTargetDate ?? .now)
    }

    var body: some View {
        NavigationStack {
            Form {
                TextField("Long-term goal", text: $goalDescription, axis: .vertical)
                    .lineLimit(3...6)
                    .foregroundStyle(NousTheme.textPrimary)
                    .listRowBackground(NousTheme.panel)

                Toggle("Target date", isOn: $hasTargetDate.animation())
                    .tint(NousTheme.accent)
                    .listRowBackground(NousTheme.panel)
                if hasTargetDate {
                    DatePicker("Target", selection: $targetDate, displayedComponents: .date)
                        .listRowBackground(NousTheme.panel)
                }
            }
            .scrollContentBackground(.hidden)
            .background(NousTheme.background)
            .navigationTitle("")
            .toolbar {
                ToolbarItem(placement: .principal) {
                    Text("PROJECT GOAL")
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
                }
            }
            .toolbarBackground(NousTheme.background, for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
            .toolbarColorScheme(.dark, for: .navigationBar)
            .navigationBarTitleDisplayMode(.inline)
        }
    }

    private func save() {
        DataController.shared.updateProjectGoal(
            project.id,
            goalDescription: goalDescription.trimmingCharacters(in: .whitespacesAndNewlines),
            goalTargetDate: hasTargetDate ? Calendar.current.startOfDay(for: targetDate) : nil)
        dismiss()
    }
}
