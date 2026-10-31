//
//  ChatView.swift
//  PersonalOps
//
//  Chat surface for the Hermes agent, wired to the network bridge
//  (HermesTransport → hermes_server.py on the user's PC). Sending a
//  message persists it locally, POSTs it with a context snapshot, then
//  persists the reply and applies any returned envelope actions. The gear
//  sheet holds the server address, a connection test, and manual backup.
//

import GRDB
import GRDBQuery
import SwiftUI
import UIKit

/// Observes the full chat transcript, oldest first.
struct ChatHistoryRequest: ValueObservationQueryable {
    static var defaultValue: [ChatMessage] { [] }

    func fetch(_ db: Database) throws -> [ChatMessage] {
        try ChatMessage
            .order(ChatMessage.Columns.timestamp)
            .fetchAll(db)
    }
}

/// Observes durable Hermes memory entries, most recently added first.
struct HermesMemoryRequest: ValueObservationQueryable {
    static var defaultValue: [HermesMemory] { [] }

    func fetch(_ db: Database) throws -> [HermesMemory] {
        try HermesMemory
            .order(HermesMemory.Columns.createdAt.desc)
            .fetchAll(db)
    }
}

struct ChatView: View {
    @Query(ChatHistoryRequest()) private var messages: [ChatMessage]

    @State private var draft = ""
    @State private var isSending = false
    @State private var showingSettings = false
    @FocusState private var inputFocused: Bool

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                if messages.isEmpty {
                    Spacer()
                    ConsoleEmptyState(
                        code: "NO_HERMES_TRANSCRIPT_FOUND",
                        detail: "Set the server address via the gear icon, then send a message.")
                    Spacer()
                } else {
                    ScrollViewReader { proxy in
                        ScrollView {
                            LazyVStack(alignment: .leading, spacing: 10) {
                                ForEach(messages) { message in
                                    ChatBubble(message: message)
                                        .id(message.id)
                                        .transition(.move(edge: .bottom).combined(with: .opacity))
                                }
                                if isSending {
                                    HStack(spacing: 8) {
                                        Text("HERMES_PROCESSING")
                                            .font(.system(.caption2, design: .monospaced).weight(.bold))
                                            .foregroundStyle(NousTheme.statusActive)
                                            .tracking(1)
                                        BlinkingCursor(color: NousTheme.statusActive)
                                    }
                                    .id("thinking")
                                    .transition(.opacity)
                                }
                            }
                            .padding()
                            .animation(.spring(response: 0.35, dampingFraction: 0.8), value: messages.map(\.id))
                            .animation(.easeOut(duration: 0.2), value: isSending)
                        }
                        .onChange(of: messages.count) {
                            if let last = messages.last {
                                withAnimation {
                                    proxy.scrollTo(last.id, anchor: .bottom)
                                }
                            }
                        }
                        .onChange(of: isSending) {
                            if isSending {
                                withAnimation { proxy.scrollTo("thinking", anchor: .bottom) }
                            }
                        }
                        .onAppear {
                            if let last = messages.last {
                                proxy.scrollTo(last.id, anchor: .bottom)
                            }
                        }
                    }
                }

                ChatInputBar(
                    text: $draft,
                    isFocused: $inputFocused,
                    onSend: sendMessage)
                    .padding(.horizontal)
                    .padding(.vertical, 10)
                    .background(NousTheme.backgroundAlt)
            }
            .scrollDismissesKeyboard(.interactively)
            .consoleBackground()
            .toolbar {
                ToolbarItem(placement: .navigation) {
                    // Hermes is a top-level tab, not a pushed screen, so
                    // "back" means switching tabs — see .switchToDashboardTab.
                    Button {
                        NotificationCenter.default.post(name: .switchToDashboardTab, object: nil)
                    } label: {
                        Image(systemName: "chevron.left")
                            .contentShape(Rectangle())
                    }
                    .tint(NousTheme.onBackground)
                    .accessibilityLabel("Back to Dashboard")
                }
                ToolbarItem(placement: .principal) {
                    Text("HERMES CHAT")
                        .font(NousTheme.pixelFontBold(size: 17, relativeTo: .headline))
                        .tracking(3)
                        .foregroundStyle(NousTheme.onBackground)
                }
                ToolbarItem(placement: .primaryAction) {
                    Button {
                        showingSettings = true
                    } label: {
                        Image(systemName: "gearshape")
                            .contentShape(Rectangle())
                    }
                    .tint(NousTheme.onBackground)
                    .accessibilityLabel("Hermes server settings")
                }
            }
            .toolbarBackground(NousTheme.background, for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
            .toolbarColorScheme(.dark, for: .navigationBar)
            .navigationBarTitleDisplayMode(.inline)
            .sheet(isPresented: $showingSettings) {
                HermesSettingsSheet()
                    .presentationDetents([.medium])
            }
        }
    }

    private func sendMessage() {
        let trimmed = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, !isSending else {
            if trimmed.isEmpty { inputFocused = false }
            return
        }
        DataController.shared.sendChatMessage(trimmed)
        draft = ""
        isSending = true

        // A background task assertion buys the in-flight request extra
        // time to finish even if the user locks the phone or switches apps
        // mid-reply — without this, iOS suspends the app ~30s after
        // backgrounding and the (up to 3-minute) Hermes request would just
        // die silently. This can't survive the app being force-quit, and
        // iOS may still cut it short under memory pressure, but it covers
        // the common "sent a message, then locked my phone" case. The
        // reply is persisted via addChatMessage regardless, so it's there
        // waiting next time the app opens either way.
        var backgroundTask: UIBackgroundTaskIdentifier = .invalid
        backgroundTask = UIApplication.shared.beginBackgroundTask(withName: "HermesChatReply") {
            UIApplication.shared.endBackgroundTask(backgroundTask)
            backgroundTask = .invalid
        }

        Task {
            await DataController.shared.transmitToHermes(trimmed)
            isSending = false
            if backgroundTask != .invalid {
                UIApplication.shared.endBackgroundTask(backgroundTask)
                backgroundTask = .invalid
            }
        }
    }
}

// MARK: - Server settings + backup sheet

private struct HermesSettingsSheet: View {
    @Environment(\.dismiss) private var dismiss
    @AppStorage(HermesTransport.serverURLKey) private var serverAddress = ""
    @AppStorage(HermesTransport.lastBackupKey) private var lastBackupAt: Double = 0

    @State private var healthStatus: String?
    @State private var isTesting = false
    @State private var isBackingUp = false
    @State private var backupResult: Bool?
    @State private var isRestoring = false
    @State private var restoreResult: Bool?
    @State private var confirmingRestore = false

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("192.168.x.x:8787", text: $serverAddress)
                        .keyboardType(.URL)
                        .autocorrectionDisabled()
                        .textInputAutocapitalization(.never)
                        .font(.system(.subheadline, design: .monospaced))
                        .foregroundStyle(NousTheme.textPrimary)
                        .listRowBackground(NousTheme.panel)

                    HStack {
                        Button(isTesting ? "TESTING..." : "TEST CONNECTION") {
                            testConnection()
                        }
                        .buttonStyle(.shell())
                        .disabled(isTesting || serverAddress.isEmpty)
                        Spacer()
                        if let healthStatus {
                            Text(healthStatus)
                                .font(.system(.caption2, design: .monospaced).weight(.bold))
                                .foregroundStyle(
                                    healthStatus.hasPrefix("ONLINE")
                                        ? NousTheme.statusActive : NousTheme.alert)
                        }
                    }
                    .listRowBackground(NousTheme.panel)
                } header: {
                    Text("SERVER ADDRESS")
                        .font(.system(.caption2, design: .monospaced))
                        .foregroundStyle(NousTheme.dimText)
                }

                Section {
                    HStack {
                        Button(isBackingUp ? "UPLOADING..." : "BACK UP NOW") {
                            runBackup()
                        }
                        .buttonStyle(.shell(NousTheme.statusActive))
                        .disabled(isBackingUp || serverAddress.isEmpty)
                        Spacer()
                        if let backupResult {
                            Text(backupResult ? "// SAVED" : "// FAILED")
                                .font(.system(.caption2, design: .monospaced).weight(.bold))
                                .foregroundStyle(
                                    backupResult ? NousTheme.statusActive : NousTheme.alert)
                        }
                    }
                    .listRowBackground(NousTheme.panel)

                    if lastBackupAt > 0 {
                        Text("LAST BACKUP: \(Date(timeIntervalSince1970: lastBackupAt).formatted(date: .abbreviated, time: .shortened))")
                            .font(.system(.caption2, design: .monospaced))
                            .foregroundStyle(NousTheme.dimText)
                            .listRowBackground(NousTheme.panel)
                    }

                    HStack {
                        Button(isRestoring ? "DOWNLOADING..." : "RESTORE FROM SERVER") {
                            confirmingRestore = true
                        }
                        .buttonStyle(.shell(NousTheme.alert))
                        .disabled(isRestoring || serverAddress.isEmpty)
                        Spacer()
                        if let restoreResult {
                            Text(restoreResult ? "// RESTORED" : "// FAILED")
                                .font(.system(.caption2, design: .monospaced).weight(.bold))
                                .foregroundStyle(
                                    restoreResult ? NousTheme.statusActive : NousTheme.alert)
                        }
                    }
                    .listRowBackground(NousTheme.panel)
                    .confirmationDialog(
                        "Replace everything on this phone with the server's latest backup?",
                        isPresented: $confirmingRestore,
                        titleVisibility: .visible
                    ) {
                        Button("RESTORE — REPLACE ALL LOCAL DATA", role: .destructive) {
                            runRestore()
                        }
                        Button("Cancel", role: .cancel) {}
                    }
                } header: {
                    Text("BACKUP")
                        .font(.system(.caption2, design: .monospaced))
                        .foregroundStyle(NousTheme.dimText)
                } footer: {
                    Text("Backups also run automatically once a week (whenever the app next goes to the background and the server is reachable). Restore pulls the newest backup from the server and replaces everything on this phone.")
                        .font(.system(.caption2, design: .monospaced))
                        .foregroundStyle(NousTheme.dimText)
                }

                Section {
                    NavigationLink {
                        HermesMemoryView()
                    } label: {
                        Label("Hermes's Memory", systemImage: "brain")
                            .foregroundStyle(NousTheme.textPrimary)
                    }
                    .listRowBackground(NousTheme.panel)
                } footer: {
                    Text("Durable facts Hermes has chosen to remember, or that you add yourself — these persist across every future conversation, unlike the rolling recent-chat window.")
                        .font(.system(.caption2, design: .monospaced))
                        .foregroundStyle(NousTheme.dimText)
                }
            }
            .scrollContentBackground(.hidden)
            .background(NousTheme.background)
            .navigationTitle("")
            .toolbar {
                ToolbarItem(placement: .principal) {
                    Text("HERMES SERVER")
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

    private func testConnection() {
        isTesting = true
        healthStatus = nil
        Task {
            let brain = await HermesTransport.checkHealth()
            healthStatus = brain.map { "ONLINE [\($0.uppercased())]" } ?? "UNREACHABLE"
            isTesting = false
        }
    }

    private func runBackup() {
        isBackingUp = true
        backupResult = nil
        Task {
            backupResult = await DataController.shared.backupToHermesServer()
            isBackingUp = false
        }
    }

    private func runRestore() {
        isRestoring = true
        restoreResult = nil
        Task {
            restoreResult = await DataController.shared.restoreFromHermesServer()
            isRestoring = false
        }
    }
}

// MARK: - Bubble

private struct ChatBubble: View {
    let message: ChatMessage

    private var isUser: Bool { message.role == .user }

    var body: some View {
        HStack {
            if isUser { Spacer(minLength: 40) }

            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 6) {
                    Text(isUser ? "YOU" : "HERMES")
                        .font(.system(.caption2, design: .monospaced).weight(.bold))
                        .foregroundStyle(isUser ? NousTheme.accent : NousTheme.statusActive)
                        .tracking(1)
                    Spacer(minLength: 0)
                    Text(message.timestamp.formatted(date: .omitted, time: .shortened))
                        .font(.system(.caption2, design: .monospaced))
                        .foregroundStyle(NousTheme.dimText)
                }
                Text(message.content)
                    .font(NousTheme.typewriterFont(size: 15, relativeTo: .subheadline))
                    .foregroundStyle(NousTheme.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
                    .textSelection(.enabled)
            }
            .padding(10)
            .consolePanel(
                border: isUser ? NousTheme.accent : NousTheme.statusActive,
                opacity: 0.4)
            .frame(maxWidth: 280, alignment: .leading)
            .contextMenu {
                Button {
                    UIPasteboard.general.string = message.content
                } label: {
                    Label("Copy", systemImage: "doc.on.doc")
                }
            }

            if !isUser { Spacer(minLength: 40) }
        }
        .frame(maxWidth: .infinity, alignment: isUser ? .trailing : .leading)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(isUser ? "You" : "Hermes"): \(message.content)")
    }
}

// MARK: - Input bar

private struct ChatInputBar: View {
    @Binding var text: String
    var isFocused: FocusState<Bool>.Binding
    let onSend: () -> Void

    var body: some View {
        HStack(spacing: 8) {
            Text(">")
                .font(.system(.body, design: .monospaced).weight(.bold))
                .foregroundStyle(NousTheme.statusActive)

            TextField("", text: $text, prompt:
                Text("message_hermes...")
                    .foregroundStyle(NousTheme.dimText), axis: .vertical)
                .font(.system(.body, design: .monospaced))
                .foregroundStyle(NousTheme.textPrimary)
                .tint(NousTheme.accent)
                .focused(isFocused)
                .lineLimit(1...4)
                .submitLabel(.send)
                .onSubmit(onSend)

            Button(action: onSend) {
                Image(systemName: "paperplane.fill")
            }
            .buttonStyle(.shell(NousTheme.statusActive))
            .disabled(text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            .accessibilityLabel("Send message")
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
        .consolePanel(border: NousTheme.statusActive, opacity: 0.5)
    }
}

// MARK: - Hermes memory viewer

private struct HermesMemoryView: View {
    @Query(HermesMemoryRequest()) private var memories: [HermesMemory]
    @State private var newMemory = ""
    @FocusState private var inputFocused: Bool

    var body: some View {
        List {
            Section {
                HStack(spacing: 8) {
                    TextField("", text: $newMemory, prompt:
                        Text("teach hermes something...")
                            .foregroundStyle(NousTheme.dimText), axis: .vertical)
                        .font(.system(.subheadline, design: .monospaced))
                        .foregroundStyle(NousTheme.textPrimary)
                        .lineLimit(1...4)
                        .focused($inputFocused)
                    Button("ADD") { addMemory() }
                        .buttonStyle(.shell(NousTheme.statusActive))
                        .disabled(newMemory.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
                .listRowBackground(NousTheme.panel)
            }

            if memories.isEmpty {
                Section {
                    ConsoleEmptyState(
                        code: "NO_MEMORIES_YET",
                        detail: "Hermes will add these itself over time, or add one above.")
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 24)
                }
                .listRowBackground(Color.clear)
            } else {
                Section {
                    ForEach(memories) { memory in
                        VStack(alignment: .leading, spacing: 4) {
                            Text(memory.content)
                                .font(.system(.subheadline, design: .monospaced))
                                .foregroundStyle(NousTheme.textPrimary)
                            Text(memory.createdAt.formatted(date: .abbreviated, time: .shortened))
                                .font(.system(.caption2, design: .monospaced))
                                .foregroundStyle(NousTheme.dimText)
                        }
                        .listRowBackground(NousTheme.panel)
                        .swipeActions(edge: .trailing, allowsFullSwipe: true) {
                            Button(role: .destructive) {
                                DataController.shared.deleteHermesMemory(memory.id)
                            } label: {
                                Label("Delete", systemImage: "trash")
                            }
                        }
                    }
                } header: {
                    Text("\(memories.count) MEMOR\(memories.count == 1 ? "Y" : "IES")")
                        .font(.system(.caption2, design: .monospaced))
                        .foregroundStyle(NousTheme.dimText)
                }
            }
        }
        .listStyle(.plain)
        .scrollContentBackground(.hidden)
        .background(NousTheme.background)
        .toolbar {
            ToolbarItem(placement: .principal) {
                Text("HERMES MEMORY")
                    .font(NousTheme.pixelFontBold(size: 14, relativeTo: .subheadline))
                    .tracking(2)
                    .foregroundStyle(NousTheme.onBackground)
            }
        }
        .toolbarBackground(NousTheme.background, for: .navigationBar)
        .toolbarBackground(.visible, for: .navigationBar)
        .toolbarColorScheme(.dark, for: .navigationBar)
        .navigationBarTitleDisplayMode(.inline)
    }

    private func addMemory() {
        DataController.shared.addHermesMemory(newMemory)
        newMemory = ""
        inputFocused = false
    }
}
