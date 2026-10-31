//
//  HermesTransport.swift
//  PersonalOps
//
//  Network bridge to the Hermes server (HermesServer/hermes_server.py)
//  running on the user's PC. Plain HTTP on the local network — the
//  Info.plist carries NSAllowsLocalNetworking for exactly this. The server
//  address lives in UserDefaults ("hermesServerURL", edited via the gear
//  sheet on the Hermes tab); every call is a no-op error until it's set.
//
//  Endpoints:
//    POST /chat    {message, context} → {reply, actions?}
//    POST /backup  full JSON export   → {ok}
//    GET  /health  → {ok, brain}
//

import Foundation

enum HermesTransport {
    static let serverURLKey = "hermesServerURL"
    static let lastBackupKey = "lastBackupAt"
    /// Automatic (app-backgrounding) backups only run this often; the
    /// manual "BACK UP NOW" button in the gear sheet always bypasses this.
    static let autoBackupInterval: TimeInterval = 7 * 24 * 60 * 60

    /// Whether enough time has passed since the last backup (of any kind,
    /// manual or automatic) to justify another automatic one.
    static var shouldAutoBackup: Bool {
        let lastBackupAt = UserDefaults.standard.double(forKey: lastBackupKey)
        guard lastBackupAt > 0 else { return true } // never backed up yet
        return Date.now.timeIntervalSince1970 - lastBackupAt >= autoBackupInterval
    }

    struct Reply {
        var reply: String
        /// Actions that decoded cleanly and are safe to apply.
        var actions: [DataController.HermesEnvelope]
        /// Human-readable reasons any action was dropped — either flagged
        /// by the server's own validator, or (belt-and-suspenders) one this
        /// client failed to decode. Surfaced to the user as a chat receipt
        /// rather than silently vanishing.
        var warnings: [String]

        /// Parses the /chat response by hand rather than via `Decodable`.
        /// Previously `actions` was `[HermesEnvelope]?` decoded directly —
        /// if ANY single action in that array failed to decode (missing
        /// field, bad date string), JSONDecoder failed the WHOLE array,
        /// which failed the WHOLE Reply, which meant a perfectly good reply
        /// with 90% valid actions got thrown away entirely and replaced
        /// with a generic "TRANSMISSION_FAILED" error — losing the reply
        /// text the user should have seen. Decoding each action
        /// independently means one bad action can never take down the rest
        /// of the response again.
        static func parse(_ data: Data) throws -> Reply {
            guard let json = try JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let replyText = json["reply"] as? String
            else {
                throw TransportError.malformedResponse
            }

            let decoder = JSONDecoder()
            decoder.dateDecodingStrategy = .iso8601

            var actions: [DataController.HermesEnvelope] = []
            var warnings = (json["warnings"] as? [String]) ?? []
            for raw in (json["actions"] as? [[String: Any]]) ?? [] {
                do {
                    let itemData = try JSONSerialization.data(withJSONObject: raw)
                    actions.append(try decoder.decode(DataController.HermesEnvelope.self, from: itemData))
                } catch {
                    let label = (raw["action"] as? String) ?? "unknown"
                    warnings.append("\(label): failed to decode (\(error.localizedDescription))")
                }
            }
            return Reply(reply: replyText, actions: actions, warnings: warnings)
        }
    }

    enum TransportError: LocalizedError {
        case noServerConfigured
        case serverError(Int)
        case malformedResponse

        var errorDescription: String? {
            switch self {
            case .noServerConfigured:
                return "No Hermes server configured — set the address via the gear icon."
            case .serverError(let code):
                return "Hermes server returned status \(code)."
            case .malformedResponse:
                return "Hermes server returned a response without a reply."
            }
        }
    }

    static var serverURL: URL? {
        guard var raw = UserDefaults.standard.string(forKey: serverURLKey)?
            .trimmingCharacters(in: .whitespacesAndNewlines),
            !raw.isEmpty else { return nil }
        if !raw.lowercased().hasPrefix("http") {
            raw = "http://" + raw
        }
        return URL(string: raw)
    }

    static func send(message: String, context: String) async throws -> Reply {
        guard let base = serverURL else { throw TransportError.noServerConfigured }
        var request = URLRequest(url: base.appendingPathComponent("chat"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        // The server may be running a full agent turn — give it room.
        request.timeoutInterval = 180
        request.httpBody = try JSONSerialization.data(withJSONObject: [
            "message": message,
            "context": context,
        ])

        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
            throw TransportError.serverError(
                (response as? HTTPURLResponse)?.statusCode ?? -1)
        }
        return try Reply.parse(data)
    }

    static func uploadBackup(_ data: Data) async throws {
        guard let base = serverURL else { throw TransportError.noServerConfigured }
        var request = URLRequest(url: base.appendingPathComponent("backup"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.timeoutInterval = 60
        request.httpBody = data

        let (_, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
            throw TransportError.serverError(
                (response as? HTTPURLResponse)?.statusCode ?? -1)
        }
        UserDefaults.standard.set(
            Date.now.timeIntervalSince1970, forKey: lastBackupKey)
    }

    /// Downloads latest.json from the server for a full restore.
    static func fetchBackup() async throws -> Data {
        guard let base = serverURL else { throw TransportError.noServerConfigured }
        var request = URLRequest(url: base.appendingPathComponent("restore"))
        request.timeoutInterval = 60
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
            throw TransportError.serverError(
                (response as? HTTPURLResponse)?.statusCode ?? -1)
        }
        return data
    }

    /// Returns the server's "brain" descriptor ("claude-cli" / "api"), or
    /// nil if the server is unreachable.
    static func checkHealth() async -> String? {
        guard let base = serverURL else { return nil }
        var request = URLRequest(url: base.appendingPathComponent("health"))
        request.timeoutInterval = 5
        guard let (data, response) = try? await URLSession.shared.data(for: request),
              (response as? HTTPURLResponse)?.statusCode == 200,
              let payload = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else { return nil }
        return payload["brain"] as? String ?? "unknown"
    }
}
