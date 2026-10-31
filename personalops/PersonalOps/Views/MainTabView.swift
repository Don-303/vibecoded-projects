//
//  MainTabView.swift
//  PersonalOps
//
//  Root tab structure: Dashboard (Daily Log + Metrics), Analytics, Tasks,
//  Habits. Styled as a telemetry-console chrome bar — deep navy, electric
//  cyan active state, dimmed silver idle icons.
//

import GRDB
import GRDBQuery
import SwiftUI
import UIKit

private enum AppTab: Hashable {
    case tasks, habits, dashboard, hermes, journal
}

extension Notification.Name {
    /// Posted by ChatView's back button — Hermes is a top-level tab, not a
    /// pushed screen, so "going back to Dashboard" means switching tabs
    /// rather than popping a NavigationStack.
    static let switchToDashboardTab = Notification.Name("switchToDashboardTab")
}

/// Count of incomplete tasks due today or earlier — the Tasks tab badge.
private struct DueTodayCountRequest: ValueObservationQueryable {
    static var defaultValue: Int { 0 }

    func fetch(_ db: Database) throws -> Int {
        let day = Calendar.current.startOfDay(for: .now)
        let endOfToday = Calendar.current.date(byAdding: .day, value: 1, to: day)!
        return try TaskItem
            .filter(
                TaskItem.Columns.isCompleted == false
                    && TaskItem.Columns.targetDate != nil
                    && TaskItem.Columns.targetDate < endOfToday)
            .fetchCount(db)
    }
}

struct MainTabView: View {
    @State private var selectedTab: AppTab = .dashboard
    @Query(DueTodayCountRequest()) private var dueTodayCount: Int

    init() {
        let appearance = UITabBarAppearance()
        appearance.configureWithOpaqueBackground()
        appearance.backgroundColor = UIColor(NousTheme.backgroundAlt)
        appearance.shadowColor = UIColor(NousTheme.onBackground).withAlphaComponent(0.4)

        let idle = UIColor(NousTheme.onBackground).withAlphaComponent(0.35)
        let active = UIColor(NousTheme.onBackground)
        for itemAppearance in [appearance.stackedLayoutAppearance,
                               appearance.inlineLayoutAppearance,
                               appearance.compactInlineLayoutAppearance] {
            itemAppearance.normal.iconColor = idle
            itemAppearance.normal.titleTextAttributes = [
                .foregroundColor: idle,
                .font: UIFont.monospacedSystemFont(ofSize: 10, weight: .semibold),
            ]
            itemAppearance.selected.iconColor = active
            itemAppearance.selected.titleTextAttributes = [
                .foregroundColor: active,
                .font: UIFont.monospacedSystemFont(ofSize: 10, weight: .bold),
            ]
        }
        UITabBar.appearance().standardAppearance = appearance
        UITabBar.appearance().scrollEdgeAppearance = appearance
    }

    var body: some View {
        TabView(selection: $selectedTab) {
            // Exactly 5 tabs — iOS collapses a 6th+ into an overflow "More"
            // tab, so Analytics lives behind the Dashboard's KPI section
            // link instead of taking its own slot, and Tasks/Projects share
            // one tab via a segmented switcher (see TasksProjectsView) to
            // make room for Journal. Each tab carries an explicit, stable
            // `.tag` — without one, SwiftUI/UIKit can restore a *positional*
            // selected index from a previous launch, which points at the
            // wrong tab after the tab count/order changes.
            TasksProjectsView()
                .tabItem {
                    Label("TASKS", systemImage: "checklist")
                }
                .badge(dueTodayCount)
                .tag(AppTab.tasks)

            HabitsGymView()
                .tabItem {
                    Label("HABITS", systemImage: "repeat.circle")
                }
                .tag(AppTab.habits)

            DashboardView()
                .tabItem {
                    Label("DASHBOARD", systemImage: "rectangle.grid.2x2")
                }
                .tag(AppTab.dashboard)

            ChatView()
                .tabItem {
                    Label("HERMES", systemImage: "message")
                }
                .tag(AppTab.hermes)

            JournalView()
                .tabItem {
                    Label("JOURNAL", systemImage: "book.closed")
                }
                .tag(AppTab.journal)
        }
        .tint(NousTheme.onBackground)
        .onReceive(NotificationCenter.default.publisher(for: .switchToDashboardTab)) { _ in
            selectedTab = .dashboard
        }
    }
}
