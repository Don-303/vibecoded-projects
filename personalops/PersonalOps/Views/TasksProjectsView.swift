//
//  TasksProjectsView.swift
//  PersonalOps
//
//  Combines the Tasks and Projects screens under one tab (freeing a tab
//  slot for Journal) via a segmented switcher above each screen's own
//  NavigationStack. TasksView/ProjectsView are otherwise untouched — only
//  one is ever in the view hierarchy at a time, so their independent
//  NavigationStacks/toolbars never nest.
//

import SwiftUI

struct TasksProjectsView: View {
    private enum Segment: String, CaseIterable {
        case tasks = "Tasks"
        case projects = "Projects"
        case calendar = "Calendar"
    }

    @State private var segment: Segment = .tasks

    var body: some View {
        VStack(spacing: 0) {
            Picker("Section", selection: $segment) {
                ForEach(Segment.allCases, id: \.self) {
                    Text($0.rawValue.uppercased()).tag($0)
                }
            }
            .pickerStyle(.segmented)
            .padding(.horizontal)
            .padding(.top, 6)
            .padding(.bottom, 8)
            .background(NousTheme.background)

            Rectangle()
                .fill(NousTheme.accent.opacity(0.15))
                .frame(height: 1)

            Group {
                switch segment {
                case .tasks: TasksView()
                case .projects: ProjectsView()
                case .calendar: DeadlineCalendarView()
                }
            }
        }
        .background(NousTheme.background.ignoresSafeArea())
    }
}
