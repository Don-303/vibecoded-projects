//
//  PersonalOpsApp.swift
//  PersonalOps
//
//  App entry point. Wires the shared DataController's database into the
//  SwiftUI environment (GRDBQuery's databaseContext), drains the Hermes
//  inbox, and refreshes habit/task local-notification reminders whenever
//  the app becomes active.
//

import GRDBQuery
import SwiftUI

@main
@MainActor
struct PersonalOpsApp: App {
    @Environment(\.scenePhase) private var scenePhase
    private let dataController = DataController.shared

    init() {
        FontRegistrar.registerBundledFontsIfNeeded()
        NotificationScheduler.requestAuthorizationIfNeeded()
        EventKitReminderSync.requestAccessIfNeeded()

        // Fresh installs get 14 days of demo telemetry so the analytics
        // charts and streak grids render immediately; any existing user
        // data disables this entirely.
        MockTelemetryProvider.seedIfEmpty(into: dataController.writer)
        dataController.seedReadingListProjectIfNeeded()
        dataController.seedDefaultWorkoutPlansIfNeeded()
        dataController.seedGymHabitIfNeeded()
        NotificationScheduler.scheduleDailyRoutineReminders()
    }

    var body: some Scene {
        WindowGroup {
            MainTabView()
                .preferredColorScheme(.dark)
                .fontDesign(.monospaced)
                .tint(NousTheme.accent)
        }
        .databaseContext(.readWrite { dataController.writer })
        .onChange(of: scenePhase) { _, phase in
            switch phase {
            case .active:
                dataController.processHermesInbox()
                dataController.exportStateForHermes()
                NotificationScheduler.rescheduleAll(writer: dataController.writer)
                NotificationScheduler.scheduleDailyRoutineReminders()
            case .background:
                // Automatic backups only run weekly (see
                // HermesTransport.shouldAutoBackup) — the gear sheet's
                // "BACK UP NOW" button always bypasses this for an
                // on-demand backup. Fire-and-forget; a failure (away from
                // home Wi-Fi, server off) is fine, the next eligible
                // backgrounding retries.
                if HermesTransport.shouldAutoBackup {
                    Task { await dataController.backupToHermesServer() }
                }
            default:
                break
            }
        }
    }
}
