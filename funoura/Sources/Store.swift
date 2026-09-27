import Foundation
import SwiftUI

/// Everything FunOura knows, mirrored from the dashboard server (dashboard/server.py).
/// FunOura never talks to the ring itself: it starts the server (Server.swift), which runs the ring
/// tools, and drives it over localhost.

struct Point: Identifiable {
    let id = UUID()
    let at: Date
    let value: Double
}

struct Reading {
    var value: Double?
    var unit: String
    var at: Date?
    var logged: Date?
    var hrv: Double?
    var note: String?
}

struct Tuning {
    var speed = 800.0
    var deadzone = 8.0
    var scrollPx = 400.0
    var flipScroll = false
    var steerOnStart = true
}

/// Where the five-pose setup (calibration) is, as the ring mouse reports it.
struct Calib {
    var stage: String  // prompt, countdown, measure, got
    var step: Int
    var of: Int
    var key: String
    var text: String
    var countdown: Int?
    var problem: String?
}

struct Screen {
    var running = false
    var remote: String?  // "slides" or "media" while the ring runs as a remote rather than the cursor
    var connected = false
    var steering = false
    var stopping = false
    var turned = false
    var calibrated = true
    var lastActivity: String?
}

@MainActor
final class Store: ObservableObject {
    @Published var online = false
    @Published var busy: String?
    @Published var readings: [String: Reading] = [:]
    @Published var tuning = Tuning()
    @Published var screen = Screen()
    @Published var heart: [Point] = []
    @Published var temperature: [Point] = []
    @Published var oxygen: [Point] = []
    @Published var syncedAt: Date?
    @Published var health: HealthSummary?
    @Published var liveBeats: [Point] = []
    @Published var calib: Calib?
    @Published var settingUp = false  // Calibrate was pressed and setup hasn't finished
    private var afterStop: (() -> Void)?  // what to start once the ring mouse has stopped
    @Published var remoteProfile: String {
        didSet { UserDefaults.standard.set(remoteProfile, forKey: "remoteProfile") }
    }
    @Published var message: String? {  // the latest problem the server reported
        didSet {  // the server and web dashboard call Hands Free by its old name, ScreenOura
            if let m = message, m.contains("ScreenOura") { message = m.replacingOccurrences(of: "ScreenOura", with: "Hands Free") }
        }
    }

    let base: URL
    let autoStart: Bool  // FunOura runs the server itself unless pointed at another one

    init() {
        let configured = ProcessInfo.processInfo.environment["FUNOURA_URL"]
            ?? UserDefaults.standard.string(forKey: "serverURL")
        base = URL(string: configured ?? "http://127.0.0.1:8787")!
        autoStart = configured == nil
        remoteProfile = UserDefaults.standard.string(forKey: "remoteProfile") ?? "slides"
        Task { await listen() }
    }

    var dashboardURL: URL { base }

    // MARK: - live updates

    private func listen() async {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 60  // the server says "still here" every 15 s
        config.timeoutIntervalForResource = .infinity
        let session = URLSession(configuration: config)
        var wait: UInt64 = 1
        while true {
            do {
                let (bytes, response) = try await session.bytes(from: base.appendingPathComponent("api/stream"))
                guard (response as? HTTPURLResponse)?.statusCode == 200 else { throw URLError(.badServerResponse) }
                online = true
                wait = 1
                await loadHistory()
                for try await line in bytes.lines {
                    guard line.hasPrefix("data: "),
                          let data = line.dropFirst(6).data(using: .utf8),
                          let event = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
                    else { continue }
                    handle(event)
                }
            } catch {}
            online = false
            if autoStart, let problem = Server.shared.launchIfNeeded() { message = problem }
            screen = Screen()
            busy = nil
            try? await Task.sleep(nanoseconds: wait * 1_000_000_000)
            wait = min(wait * 2, 5)
        }
    }

    private func handle(_ e: [String: Any]) {
        switch e["type"] as? String {
        case "state":
            busy = e["busy"] as? String
            if let r = e["readings"] as? [String: Any] {
                for (name, v) in r { if let v = v as? [String: Any] { readings[name] = reading(v) } }
            }
            if let s = e["settings"] as? [String: Any] { applySettings(s) }
            if let s = e["screenoura"] as? [String: Any] {
                screen.running = s["running"] as? Bool ?? false
                calib = (s["calib"] as? [String: Any]).map(parseCalib)
                settingUp = calib != nil
                applyScreen(s)
                screen.calibrated = s["calibrated"] as? Bool ?? true
            }
        case "busy":
            busy = e["what"] as? String
            screen.running = busy == "screenoura"
            if !screen.running {
                calib = nil
                settingUp = false
                screen.remote = nil
                if let next = afterStop {
                    afterStop = nil
                    next()
                }
            }
        case "calib":
            if e["stage"] as? String == "done" {
                calib = nil
                settingUp = false
            } else {
                calib = parseCalib(e)
            }
        case "screen":
            applyScreen(e)
        case "settings":
            applySettings(e)
        case "reading":
            if let name = e["name"] as? String {
                readings[name] = reading(e)
                if name == "heart" { liveBeats = [] }
            }
        case "history":
            applyHistory(e)
        case "heart_start":
            liveBeats = []
        case "beat":
            if let bpm = e["bpm"] as? Double { liveBeats.append(Point(at: Date(), value: bpm)) }
        case "error":
            message = e["message"] as? String
            if e["name"] as? String == "heart" { liveBeats = [] }
        default:
            break  // "aim" arrives many times a second; the menu doesn't draw it
        }
    }

    private func reading(_ v: [String: Any]) -> Reading {
        func date(_ key: String) -> Date? { (v[key] as? Double).map { Date(timeIntervalSince1970: $0) } }
        return Reading(value: v["value"] as? Double, unit: v["unit"] as? String ?? "",
                       at: date("at"), logged: date("logged"), hrv: v["hrv"] as? Double,
                       note: v["note"] as? String)
    }

    private func parseCalib(_ e: [String: Any]) -> Calib {
        Calib(stage: e["stage"] as? String ?? "", step: e["step"] as? Int ?? 1, of: e["of"] as? Int ?? 5,
              key: e["key"] as? String ?? "", text: e["text"] as? String ?? "", countdown: e["n"] as? Int,
              problem: e["problem"] as? String)
    }

    private func applySettings(_ s: [String: Any]) {
        if let v = s["speed"] as? Double { tuning.speed = v }
        if let v = s["deadzone"] as? Double { tuning.deadzone = v }
        if let v = s["scroll_px"] as? Double { tuning.scrollPx = v }
        if let v = s["flip_scroll"] as? Bool { tuning.flipScroll = v }
        if let v = s["steer_on_start"] as? Bool { tuning.steerOnStart = v }
    }

    private func applyScreen(_ s: [String: Any]) {
        screen.connected = s["connected"] as? Bool ?? false
        screen.steering = s["steering"] as? Bool ?? false
        screen.stopping = s["stopping"] as? Bool ?? false
        screen.turned = s["turned"] as? Bool ?? false
        screen.remote = s["remote"] as? String
        if let activity = s["activity"] as? [[String: Any]] {
            screen.lastActivity = activity.first?["text"] as? String
            if screen.lastActivity == "Stopped" { screen.running = false }
        }
    }

    private func applyHistory(_ h: [String: Any]) {
        func series(_ key: String) -> [Point] {
            (h[key] as? [[Double]] ?? []).compactMap { pair in
                pair.count == 2 ? Point(at: Date(timeIntervalSince1970: pair[0]), value: pair[1]) : nil
            }
        }
        heart = series("heart")
        temperature = series("temperature")
        oxygen = series("oxygen")
        syncedAt = (h["synced_at"] as? Double).map { Date(timeIntervalSince1970: $0) }
        if let summary = h["health"] as? [String: Any], !summary.isEmpty {
            health = HealthSummary.decode(summary)
        }
    }

    private func loadHistory() async {
        guard let (data, _) = try? await URLSession.shared.data(from: base.appendingPathComponent("api/history")),
              let h = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else { return }
        applyHistory(h)
    }

    // MARK: - actions

    func startScreen() { post("api/screenoura/start") }
    func stopScreen() { post("api/screenoura/stop") }
    func steer(_ on: Bool) { post("api/screenoura/steer", ["on": on]) }
    func nextSetupStep() { post("api/screenoura/next") }

    /// Redo the five setup poses. If Hands Free is running, it stops first and setup starts once it has.
    func calibrate() {
        message = nil
        settingUp = true
        if screen.running {
            afterStop = { [weak self] in self?.calibrate() }
            stopScreen()
        } else {
            post("api/screenoura/calibrate")
        }
    }

    /// Run the ring as a remote. If Hands Free has the ring, it stops first.
    func startRemote() {
        message = nil
        if screen.running && screen.remote == nil {
            afterStop = { [weak self] in self?.startRemote() }
            stopScreen()
        } else if !screen.running {
            post("api/remote/start", ["profile": remoteProfile])
        }
    }

    /// Hands Free back on; if the remote has the ring, it stops first.
    func startHandsFree() {
        if screen.running && screen.remote != nil {
            afterStop = { [weak self] in self?.startHandsFree() }
            stopScreen()
        } else if !screen.running {
            startScreen()
        }
    }

    func chooseRemote(_ profile: String) {
        remoteProfile = profile
        if screen.remote != nil { post("api/remote/profile", ["profile": profile]) }
    }

    func cancelSetup() {
        afterStop = nil
        settingUp = false
        stopScreen()
    }
    func measure(_ what: String) {
        message = nil
        post("api/measure/\(what)")
    }

    /// Settings go to the server only when a slider is let go: each change rewrites
    /// screenoura.json and reaches the running ring mouse at once.
    func save(_ changes: [String: Any]) {
        applySettings(changes)  // show it at once; the server's "settings" event corrects any clamping
        post("api/screenoura/settings", changes)
    }

    private func post(_ path: String, _ body: [String: Any] = [:]) {
        var request = URLRequest(url: base.appendingPathComponent(path))
        request.httpMethod = "POST"
        request.setValue("1", forHTTPHeaderField: "X-Oura-Play")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try? JSONSerialization.data(withJSONObject: body)
        Task {
            do {
                let (data, response) = try await URLSession.shared.data(for: request)
                if (response as? HTTPURLResponse)?.statusCode != 202 {
                    let reply = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
                    message = reply?["error"] as? String ?? "The dashboard didn't accept that."
                } else if path.hasPrefix("api/screenoura") {
                    message = nil
                }
            } catch {
                message = "Couldn't reach the dashboard server."
            }
        }
    }
}
