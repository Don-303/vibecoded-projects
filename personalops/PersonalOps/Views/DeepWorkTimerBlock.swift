//
//  DeepWorkTimerBlock.swift
//  PersonalOps
//
//  Deep Work Matrix: a Pomodoro cycle. Pick a 25/50 min work preset; on
//  completion it logs a FocusSession (see AnalyticsDashboardView's Focus
//  Minutes chart) and auto-starts a break — 5 min after a normal round, 15
//  min after every 4th. Only completed *work* rounds are logged; breaks
//  never are, and aborting anything logs nothing. Driven by TimelineView
//  rather than a Combine Timer so remaining time is derived from wall-clock
//  `endDate`, staying correct across app backgrounding/suspension.
//

import SwiftUI

private enum PomodoroPhase {
    case idle, working, shortBreak, longBreak

    var label: String {
        switch self {
        case .idle: return ""
        case .working: return "WORK"
        case .shortBreak: return "SHORT BREAK"
        case .longBreak: return "LONG BREAK"
        }
    }

    var tint: Color {
        switch self {
        case .idle: return NousTheme.accent
        case .working: return NousTheme.accent
        case .shortBreak, .longBreak: return NousTheme.statusActive
        }
    }
}

struct DeepWorkTimerBlock: View {
    private static let presets = [25, 50]
    private static let shortBreakMinutes = 5
    private static let longBreakMinutes = 15
    private static let roundsPerCycle = 4

    @State private var workMinutes = 25
    @State private var phase: PomodoroPhase = .idle
    @State private var sessionStart: Date?
    @State private var endDate: Date?
    @State private var completedRounds = 0
    @State private var justFinishedBreak = false

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                ConsoleSectionLabel(text: "Deep Work Matrix")
                Spacer()
                if completedRounds > 0 {
                    Text("\(completedRounds) POMODORO\(completedRounds == 1 ? "" : "S") TODAY")
                        .font(.system(.caption2, design: .monospaced).weight(.semibold))
                        .foregroundStyle(NousTheme.dimText)
                }
            }

            if let endDate {
                TimelineView(.periodic(from: .now, by: 1)) { context in
                    let remaining = max(0, Int(endDate.timeIntervalSince(context.date).rounded()))
                    HStack(spacing: 10) {
                        Text(phase.label)
                            .font(.system(.caption2, design: .monospaced).weight(.bold))
                            .foregroundStyle(phase.tint)
                            .frame(width: 84, alignment: .leading)
                        Text(Self.timeString(remaining))
                            .font(.system(.title3, design: .monospaced).weight(.bold))
                            .foregroundStyle(phase.tint)
                            .consoleGlow(phase.tint, radius: 4)
                        ProgressView(value: progress(remaining: remaining))
                            .progressViewStyle(.linear)
                            .tint(phase.tint)
                        Button("ABORT") { reset() }
                            .buttonStyle(.shell(NousTheme.alert))
                    }
                    .padding(.horizontal, 10)
                    .padding(.vertical, 8)
                    .consolePanel(border: phase.tint, opacity: 0.5)
                    .onChange(of: remaining) { _, newValue in
                        if newValue <= 0 { advance() }
                    }
                    .accessibilityElement(children: .combine)
                    .accessibilityLabel("\(phase.label): \(Self.timeString(remaining)) remaining")
                }
            } else {
                HStack(spacing: 10) {
                    ForEach(Self.presets, id: \.self) { minutes in
                        Button("\(minutes) MIN") { startWork(minutes: minutes) }
                            .buttonStyle(.shell())
                    }
                    Spacer()
                    if justFinishedBreak {
                        Label("BREAK OVER", systemImage: "checkmark.seal.fill")
                            .font(.system(.caption2, design: .monospaced).weight(.bold))
                            .foregroundStyle(NousTheme.statusActive)
                    }
                }
                .padding(.horizontal, 10)
                .padding(.vertical, 8)
                .consolePanel(opacity: 0.25)
            }
        }
    }

    private static func timeString(_ seconds: Int) -> String {
        String(format: "%02d:%02d", seconds / 60, seconds % 60)
    }

    private func progress(remaining: Int) -> Double {
        guard let sessionStart, let endDate else { return 0 }
        let total = endDate.timeIntervalSince(sessionStart)
        guard total > 0 else { return 0 }
        return 1 - Double(remaining) / total
    }

    private func startWork(minutes: Int) {
        workMinutes = minutes
        justFinishedBreak = false
        phase = .working
        sessionStart = .now
        endDate = Calendar.current.date(byAdding: .minute, value: minutes, to: .now)
        if let endDate {
            NotificationScheduler.schedulePomodoroEnd(
                at: endDate,
                title: "Deep Work Complete",
                body: "\(minutes)-minute session logged — break time.")
        }
    }

    private func startBreak() {
        let isLong = completedRounds > 0 && completedRounds % Self.roundsPerCycle == 0
        phase = isLong ? .longBreak : .shortBreak
        let minutes = isLong ? Self.longBreakMinutes : Self.shortBreakMinutes
        sessionStart = .now
        endDate = Calendar.current.date(byAdding: .minute, value: minutes, to: .now)
        if let endDate {
            NotificationScheduler.schedulePomodoroEnd(
                at: endDate,
                title: "Break Over",
                body: "Back to work — start your next round.")
        }
    }

    private func reset() {
        phase = .idle
        sessionStart = nil
        endDate = nil
        NotificationScheduler.cancelPomodoroEnd()
    }

    /// Called when the running phase's countdown hits zero.
    private func advance() {
        switch phase {
        case .working:
            if let sessionStart {
                DataController.shared.logFocusSession(durationMinutes: workMinutes, startedAt: sessionStart)
            }
            completedRounds += 1
            startBreak()
        case .shortBreak, .longBreak:
            reset()
            justFinishedBreak = true
        case .idle:
            break
        }
    }
}
