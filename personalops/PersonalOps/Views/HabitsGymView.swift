//
//  HabitsGymView.swift
//  PersonalOps
//
//  Combines Habits and the Gym tracker under one tab via a segmented
//  switcher, the same pattern TasksProjectsView uses — only one of
//  HabitsView/GymView is ever in the hierarchy at a time, so their
//  independent NavigationStacks/toolbars never nest.
//

import SwiftUI

struct HabitsGymView: View {
    private enum Segment: String, CaseIterable {
        case habits = "Habits"
        case gym = "Gym"
    }

    @State private var segment: Segment = .habits

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
                case .habits: HabitsView()
                case .gym: GymView()
                }
            }
        }
        .background(NousTheme.background.ignoresSafeArea())
    }
}
