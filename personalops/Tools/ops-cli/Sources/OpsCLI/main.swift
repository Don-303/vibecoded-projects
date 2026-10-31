//
//  main.swift — ops-cli
//
//  Headless bridge into the PersonalOps SwiftData store, for the Hermes
//  Agent and other terminal skills. Opens the app's SQLite store directly
//  (simulator / local dev) via a ModelConfiguration(url:) that reuses the
//  app's exact @Model classes (symlinked into this target).
//
//  Commands (see HERMES_SKILL.md for the full contract):
//    ops-cli log "text" [--tags a,b] [--category work] [--via-inbox]
//    ops-cli task "title" [--priority low|normal|high] [--due YYYY-MM-DD]
//    ops-cli tasks                     # print pending tasks as JSON
//    ops-cli done <task-id-prefix>     # mark a pending task completed
//    ops-cli today                     # print today's log as JSON
//    ops-cli ingest                    # read a HermesEnvelope JSON from stdin
//    ops-cli where                     # print resolved store/group paths
//
//  Store resolution order:
//    --store <path> | $PERSONALOPS_STORE
//    else --group-dir <path> | $PERSONALOPS_GROUP_DIR, searched for default.store
//    else scan booted-simulator App Group containers for the group ID.
//

import Foundation
import SwiftData

let appGroupID = "group.com.yourteam.personalops"

// MARK: - Small helpers

func fail(_ message: String, code: Int32 = 1) -> Never {
    FileHandle.standardError.write(Data(("ops-cli: " + message + "\n").utf8))
    exit(code)
}

func isoEncoder() -> JSONEncoder {
    let e = JSONEncoder()
    e.dateEncodingStrategy = .iso8601
    e.outputFormatting = [.prettyPrinted, .sortedKeys]
    return e
}

func parseDate(_ raw: String) -> Date? {
    if let d = ISO8601DateFormatter().date(from: raw) { return d }
    let f = DateFormatter()
    f.dateFormat = "yyyy-MM-dd"
    f.locale = Locale(identifier: "en_US_POSIX")
    f.timeZone = .current
    return f.date(from: raw)
}

// MARK: - Argument parsing (dependency-free)

var args = Array(CommandLine.arguments.dropFirst())
var flags: [String: String] = [:]
var boolFlags: Set<String> = []
var positionals: [String] = []

let valueFlags: Set<String> = [
    "--tags", "--category", "--priority", "--due", "--store", "--group-dir", "--time",
]

var i = 0
while i < args.count {
    let arg = args[i]
    if valueFlags.contains(arg) {
        guard i + 1 < args.count else { fail("missing value for \(arg)") }
        flags[arg] = args[i + 1]
        i += 2
    } else if arg.hasPrefix("--") {
        boolFlags.insert(arg)
        i += 1
    } else {
        positionals.append(arg)
        i += 1
    }
}

guard let command = positionals.first else {
    fail("""
    usage: ops-cli <command> [options]
      log "text"     append a note to the Daily Log
      task "title"   create a task (backlog unless --due)
      tasks          print pending tasks as JSON
      done <id>      mark a pending task completed (UUID prefix ok)
      today          print today's log entries as JSON
      ingest         read a HermesEnvelope JSON object from stdin
      where          print resolved store paths
    """)
}

// MARK: - Store discovery

func simulatorGroupDirs() -> [URL] {
    let fm = FileManager.default
    let devices = fm.homeDirectoryForCurrentUser
        .appendingPathComponent("Library/Developer/CoreSimulator/Devices")
    guard let deviceIDs = try? fm.contentsOfDirectory(
        at: devices, includingPropertiesForKeys: nil) else { return [] }

    var matches: [URL] = []
    for device in deviceIDs {
        let groupsDir = device.appendingPathComponent("data/Containers/Shared/AppGroup")
        guard let groups = try? fm.contentsOfDirectory(
            at: groupsDir, includingPropertiesForKeys: [.contentModificationDateKey])
        else { continue }
        for group in groups {
            let plist = group.appendingPathComponent(
                ".com.apple.mobile_container_manager.metadata.plist")
            guard let data = try? Data(contentsOf: plist),
                  let dict = try? PropertyListSerialization.propertyList(
                    from: data, format: nil) as? [String: Any],
                  dict["MCMMetadataIdentifier"] as? String == appGroupID
            else { continue }
            matches.append(group)
        }
    }
    // Most recently modified container first (= most recently used simulator).
    return matches.sorted {
        let a = (try? $0.resourceValues(forKeys: [.contentModificationDateKey]))?
            .contentModificationDate ?? .distantPast
        let b = (try? $1.resourceValues(forKeys: [.contentModificationDateKey]))?
            .contentModificationDate ?? .distantPast
        return a > b
    }
}

func findStore(under root: URL) -> URL? {
    let fm = FileManager.default
    let direct = root.appendingPathComponent("default.store")
    if fm.fileExists(atPath: direct.path) { return direct }
    guard let enumerator = fm.enumerator(
        at: root, includingPropertiesForKeys: nil) else { return nil }
    for case let url as URL in enumerator
    where url.lastPathComponent == "default.store" {
        return url
    }
    return nil
}

func resolveGroupDir() -> URL? {
    if let dir = flags["--group-dir"] ?? ProcessInfo.processInfo
        .environment["PERSONALOPS_GROUP_DIR"] {
        return URL(fileURLWithPath: dir)
    }
    return simulatorGroupDirs().first
}

func resolveStoreURL() -> URL {
    if let path = flags["--store"] ?? ProcessInfo.processInfo
        .environment["PERSONALOPS_STORE"] {
        return URL(fileURLWithPath: path)
    }
    guard let group = resolveGroupDir() else {
        fail("could not locate the app's App Group container; pass --store or --group-dir", code: 2)
    }
    guard let store = findStore(under: group) else {
        fail("no default.store under \(group.path) — launch the app once first", code: 2)
    }
    return store
}

func openContext(at storeURL: URL) -> ModelContext {
    let schema = Schema([
        DailyLogItem.self,
        Habit.self,
        HabitCompletion.self,
        DailyMetric.self,
        TaskItem.self,
    ])
    do {
        let config = ModelConfiguration(schema: schema, url: storeURL)
        let container = try ModelContainer(for: schema, configurations: [config])
        return ModelContext(container)
    } catch {
        fail("could not open store at \(storeURL.path): \(error)", code: 2)
    }
}

// MARK: - Envelope (matches DataController.HermesEnvelope)

struct Envelope: Codable {
    var action: String
    var content: String
    var timestamp: Date?
    var tags: [String]?
    var priority: Int?
    var targetDate: Date?
    var details: String?
    var id: UUID?
}

func writeEnvelopeToInbox(_ envelope: Envelope) {
    guard let group = resolveGroupDir() else {
        fail("could not locate App Group container for --via-inbox", code: 2)
    }
    let inbox = group.appendingPathComponent("HermesInbox", isDirectory: true)
    try? FileManager.default.createDirectory(
        at: inbox, withIntermediateDirectories: true)
    let file = inbox.appendingPathComponent(
        "\(Int(Date.now.timeIntervalSince1970))-\(UUID().uuidString.prefix(8)).json")
    do {
        try isoEncoder().encode(envelope).write(to: file, options: .atomic)
        print("queued \(file.path)")
    } catch {
        fail("could not write envelope: \(error)")
    }
}

func priorityValue(_ raw: String?) -> Int {
    switch raw?.lowercased() {
    case "low": return 0
    case nil, "normal": return 1
    case "high": return 2
    default: fail("--priority must be low|normal|high")
    }
}

// MARK: - Commands

func applyEnvelope(_ envelope: Envelope) {
    if boolFlags.contains("--via-inbox") {
        writeEnvelopeToInbox(envelope)
        return
    }
    let context = openContext(at: resolveStoreURL())
    switch envelope.action {
    case "log_note":
        let item = DailyLogItem(
            content: envelope.content,
            timestamp: envelope.timestamp ?? .now,
            source: .hermes,
            tags: envelope.tags ?? [])
        context.insert(item)
    case "add_task":
        let task = TaskItem(
            title: envelope.content,
            details: envelope.details ?? "",
            priority: envelope.priority.flatMap(TaskPriority.init) ?? .normal,
            targetDate: envelope.targetDate,
            source: .hermes)
        context.insert(task)
    case "complete_task":
        guard let id = envelope.id else { fail("complete_task requires an id") }
        let descriptor = FetchDescriptor<TaskItem>(
            predicate: #Predicate { $0.id == id })
        guard let task = try? context.fetch(descriptor).first else {
            fail("no task with id \(id)", code: 1)
        }
        task.setCompleted(true)
    default:
        fail("unknown action \(envelope.action)")
    }
    do {
        try context.save()
        print("ok")
    } catch {
        fail("save failed: \(error)")
    }
}

switch command {
case "where":
    if let group = resolveGroupDir() {
        print("group: \(group.path)")
        print("store: \(findStore(under: group)?.path ?? "<none — launch the app once>")")
        print("inbox: \(group.appendingPathComponent("HermesInbox").path)")
    } else {
        fail("no App Group container found", code: 2)
    }

case "log":
    guard positionals.count > 1 else { fail("usage: ops-cli log \"text\"") }
    var tags = (flags["--tags"] ?? "")
        .split(separator: ",").map(String.init).filter { !$0.isEmpty }
    if let category = flags["--category"] { tags.append(category.lowercased()) }
    applyEnvelope(Envelope(
        action: "log_note",
        content: positionals[1],
        timestamp: flags["--time"].flatMap(parseDate) ?? .now,
        tags: tags))

case "task":
    guard positionals.count > 1 else { fail("usage: ops-cli task \"title\"") }
    let due: Date? = flags["--due"].map {
        guard let d = parseDate($0) else { fail("--due must be YYYY-MM-DD") }
        return Calendar.current.startOfDay(for: d)
    }
    applyEnvelope(Envelope(
        action: "add_task",
        content: positionals[1],
        priority: priorityValue(flags["--priority"]),
        targetDate: due))

case "done":
    guard positionals.count > 1 else { fail("usage: ops-cli done <task-id-prefix>") }
    let prefix = positionals[1].lowercased()
    // Resolve the prefix against pending tasks (works in both modes; inbox
    // mode still needs the full UUID in the envelope, so resolve first).
    let context = openContext(at: resolveStoreURL())
    let descriptor = FetchDescriptor<TaskItem>(
        predicate: #Predicate { !$0.isCompleted })
    let pending = (try? context.fetch(descriptor)) ?? []
    let matches = pending.filter {
        $0.id.uuidString.lowercased().hasPrefix(prefix)
    }
    guard matches.count == 1, let task = matches.first else {
        fail(matches.isEmpty
            ? "no pending task matches id prefix \(prefix)"
            : "id prefix \(prefix) is ambiguous (\(matches.count) matches)")
    }
    if boolFlags.contains("--via-inbox") {
        writeEnvelopeToInbox(Envelope(
            action: "complete_task", content: task.title, id: task.id))
    } else {
        task.setCompleted(true)
        do {
            try context.save()
            print("ok — completed \"\(task.title)\"")
        } catch {
            fail("save failed: \(error)")
        }
    }

case "ingest":
    let data = FileHandle.standardInput.readDataToEndOfFile()
    let decoder = JSONDecoder()
    decoder.dateDecodingStrategy = .iso8601
    guard let envelope = try? decoder.decode(Envelope.self, from: data) else {
        fail("stdin was not a valid HermesEnvelope JSON object")
    }
    applyEnvelope(envelope)

case "tasks":
    let context = openContext(at: resolveStoreURL())
    let descriptor = FetchDescriptor<TaskItem>(
        predicate: #Predicate { !$0.isCompleted },
        sortBy: [SortDescriptor(\.priorityRaw, order: .reverse),
                 SortDescriptor(\.createdAt)])
    let tasks = (try? context.fetch(descriptor)) ?? []
    struct Out: Codable {
        let id: UUID; let title: String; let details: String
        let priority: Int; let targetDate: Date?
        let createdAt: Date; let source: String
    }
    let payload = tasks.map {
        Out(id: $0.id, title: $0.title, details: $0.details,
            priority: $0.priorityRaw, targetDate: $0.targetDate,
            createdAt: $0.createdAt, source: $0.sourceRaw)
    }
    print(String(data: try! isoEncoder().encode(payload), encoding: .utf8)!)

case "today":
    let context = openContext(at: resolveStoreURL())
    let start = Calendar.current.startOfDay(for: .now)
    let descriptor = FetchDescriptor<DailyLogItem>(
        predicate: #Predicate { $0.timestamp >= start },
        sortBy: [SortDescriptor(\.timestamp)])
    let items = (try? context.fetch(descriptor)) ?? []
    struct Out: Codable {
        let timestamp: Date; let source: String
        let content: String; let tags: [String]
    }
    let payload = items.map {
        Out(timestamp: $0.timestamp, source: $0.sourceRaw,
            content: $0.content, tags: $0.tags)
    }
    print(String(data: try! isoEncoder().encode(payload), encoding: .utf8)!)

default:
    fail("unknown command \(command)")
}
