import AppKit
import ApplicationServices

/// Runs the dashboard server (dashboard/server.py) as FunOura's own child, so there's no terminal to start.
/// macOS then credits FunOura with the ring's Bluetooth and the ring mouse's cursor control, which is
/// why FunOura asks for both on first launch.
final class Server {
    static let shared = Server()

    private let dashboard = Server.home.appendingPathComponent("dashboard")

    /// The FunOura folder (the git clone). build.sh stamps it into Info.plist as FunOuraHome; without
    /// that, it's the folder two levels up from FunOura.app (funoura/FunOura.app).
    static let home: URL = {
        if let stamped = Bundle.main.object(forInfoDictionaryKey: "FunOuraHome") as? String, !stamped.isEmpty {
            return URL(fileURLWithPath: stamped)
        }
        return Bundle.main.bundleURL.deletingLastPathComponent().deletingLastPathComponent()
    }()
    private let log = URL(fileURLWithPath: NSHomeDirectory()).appendingPathComponent("Library/Logs/FunOura/server.log")
    private var process: Process?
    private var lastLaunch = Date.distantPast
    private var quitting = false  // the stream drops while we stop the server; don't start a new one

    var owned: Bool { process?.isRunning == true }

    /// Start the server unless it's already up (ours, or one started in a terminal).
    /// Returns a problem to show, if any.
    func launchIfNeeded() -> String? {
        if quitting || owned || Date().timeIntervalSince(lastLaunch) < 10 { return nil }  // give a fresh one time to come up
        guard let python = ["/usr/local/bin/python3", "/opt/homebrew/bin/python3", "/usr/bin/python3"]
            .first(where: { FileManager.default.isExecutableFile(atPath: $0) }) else {
            return "FunOura couldn't find python3 to run the dashboard server."
        }
        let p = Process()
        p.executableURL = URL(fileURLWithPath: python)
        p.arguments = ["-u", "server.py"]
        p.currentDirectoryURL = dashboard
        var env = ProcessInfo.processInfo.environment
        env["OURA_PLAY_NO_BROWSER"] = "1"
        env["OURA_PLAY_HOST_APP"] = "FunOura"
        p.environment = env
        try? FileManager.default.createDirectory(at: log.deletingLastPathComponent(), withIntermediateDirectories: true)
        FileManager.default.createFile(atPath: log.path, contents: nil)
        if let out = try? FileHandle(forWritingTo: log) {
            p.standardOutput = out
            p.standardError = out
        }
        lastLaunch = Date()
        do {
            try p.run()
        } catch {
            return "FunOura couldn't start the dashboard server: \(error.localizedDescription)"
        }
        process = p
        return nil
    }

    /// Stop the server we started. It stops ScreenOura and switches the ring's stream off on the way
    /// out, which can take several seconds, so this waits (off the main thread) before calling done.
    func stop(then done: @escaping () -> Void) {
        quitting = true
        guard let p = process, p.isRunning else { return done() }
        p.terminate()
        DispatchQueue.global().async {
            let deadline = Date().addingTimeInterval(20)
            while p.isRunning && Date() < deadline { usleep(100_000) }
            if p.isRunning { kill(p.processIdentifier, SIGKILL) }
            DispatchQueue.main.async(execute: done)
        }
    }

    /// Put FunOura in the Accessibility list (with the system prompt) if it isn't allowed yet.
    static func askForCursorControl() {
        let key = kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String
        _ = AXIsProcessTrustedWithOptions([key: true] as CFDictionary)
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        if !AXIsProcessTrusted() { Server.askForCursorControl() }
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard Server.shared.owned else { return .terminateNow }
        Server.shared.stop { sender.reply(toApplicationShouldTerminate: true) }
        return .terminateLater
    }
}
