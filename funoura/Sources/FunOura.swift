import Charts
import SwiftUI

#if !SNAPSHOT
@main
#endif
struct FunOuraApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @StateObject private var store = Store()
    @StateObject private var coach = Coach()

    var body: some Scene {
        MenuBarExtra {
            Panel().environmentObject(store).environmentObject(coach)
        } label: {
            Image(systemName: coach.running ? "wind" : store.screen.remote != nil ? "av.remote"
                  : store.screen.steering ? "cursorarrow.rays" : "circle.circle")
        }
        .menuBarExtraStyle(.window)
    }
}

enum Tab: String, CaseIterable {
    case screen = "Hands Free"
    case remote = "Remote"
    case health = "Health"
    case games = "Games"
    case exercises = "Exercises"
    case more = "More"

    var icon: String {
        switch self {
        case .screen: "cursorarrow.rays"
        case .remote: "av.remote"
        case .health: "heart"
        case .games: "gamecontroller"
        case .exercises: "figure.mind.and.body"
        case .more: "ellipsis"
        }
    }
}

struct Panel: View {
    @EnvironmentObject var store: Store
    @State var tab = Tab.screen

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("FunOura").font(.headline)
                Text(tab.rawValue).font(.headline).foregroundStyle(.secondary)
                Spacer()
                Circle().fill(store.online ? Color.green : Color.secondary.opacity(0.5)).frame(width: 7, height: 7)
                Text(store.online ? "Connected" : "Offline").font(.caption).foregroundStyle(.secondary)
            }
            Picker("", selection: $tab) {
                ForEach(Tab.allCases, id: \.self) { t in
                    Image(systemName: t.icon).accessibilityLabel(t.rawValue).help(t.rawValue).tag(t)
                }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .frame(maxWidth: .infinity)

            Group {
                if tab == .more {
                    ComingSoon()
                } else if tab == .games {
                    GamesTab()
                } else if tab == .exercises {
                    ExercisesTab()
                } else if !store.online && store.autoStart {
                    Starting()
                } else if !store.online {
                    Offline()
                } else if tab == .screen {
                    ScreenTab()
                } else if tab == .remote {
                    RemoteTab()
                } else {
                    HealthTab()
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            if let message = store.message, store.online, [.screen, .remote, .health].contains(tab) {
                HStack(alignment: .top, spacing: 6) {
                    Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.orange)
                    Text(message).font(.caption).fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: 0)
                    Button { store.message = nil } label: { Image(systemName: "xmark") }
                        .buttonStyle(.borderless).font(.caption)
                }
                .padding(8)
                .background(RoundedRectangle(cornerRadius: 6).fill(Color.orange.opacity(0.12)))
            }

            Divider()
            HStack {
                Button("Open dashboard") { NSWorkspace.shared.open(store.dashboardURL) }
                    .disabled(!store.online)
                Spacer()
                Link("via open_oura", destination: URL(string: "https://github.com/Th0rgal/open_oura")!)
                    .foregroundStyle(.secondary)
                Spacer()
                Button("Quit FunOura") { NSApp.terminate(nil) }
            }
            .buttonStyle(.borderless)
            .font(.caption)
            .lineLimit(1)
        }
        .padding(14)
        .frame(width: 340)
    }
}

// MARK: - Hands Free (ScreenOura: the ring as a mouse)

struct ScreenTab: View {
    @EnvironmentObject var store: Store

    var body: some View {
        if store.settingUp {
            CalibrationCard()
        } else {
            controls
        }
    }

    private var controls: some View {
        let s = store.screen
        let otherJob = store.busy != nil && store.busy != "screenoura"
        return VStack(alignment: .leading, spacing: 10) {
            HandsFreeIntro()
            SwitchRow(title: "Cursor control", detail: status,
                      isOn: Binding(get: { s.running && s.remote == nil && !s.stopping },
                                    set: { $0 ? store.startHandsFree() : store.stopScreen() }))
                .disabled(s.stopping || otherJob || !s.calibrated)
            SwitchRow(title: "Steering", detail: "Same as holding your palm up for a second",
                      isOn: Binding(get: { s.steering && s.remote == nil }, set: { store.steer($0) }))
                .disabled(!s.connected || s.stopping || s.remote != nil)
            HStack {
                VStack(alignment: .leading, spacing: 1) {
                    Text("Calibration").font(.body.weight(.medium))
                    Text(s.calibrated ? "Redo it if the cursor feels off"
                                      : "Needed once before the first use")
                        .font(.caption).foregroundStyle(.secondary).lineLimit(1)
                }
                Spacer()
                Button("Calibrate") { store.calibrate() }
                    .controlSize(.small)
                    .disabled(otherJob || s.stopping)
            }

            if !s.calibrated {
                Text("Needs a one-time setup first: click Calibrate and follow the five poses.")
                    .font(.caption).foregroundStyle(.orange)
            } else if s.turned {
                Text("The ring seems turned around on your finger. Turn it back the way it sat during setup.")
                    .font(.caption).foregroundStyle(.orange)
            }

            Divider()
            Text("Tuning").font(.caption.weight(.semibold)).foregroundStyle(.secondary)
            SettingSlider(title: "Cursor speed", value: store.tuning.speed, range: 300...2000, step: 50,
                          format: { "\(Int($0))" }) { store.save(["speed": $0]) }
            SettingSlider(title: "Still zone", value: store.tuning.deadzone, range: 3...15, step: 0.5,
                          format: { String(format: "%.1f°", $0) }) { store.save(["deadzone": $0]) }
            SettingSlider(title: "Scroll per sweep", value: store.tuning.scrollPx, range: 100...1500, step: 50,
                          format: { "\(Int($0)) px" }) { store.save(["scroll_px": $0]) }
            Toggle("Reverse scroll direction", isOn: Binding(get: { store.tuning.flipScroll },
                                                             set: { store.save(["flip_scroll": $0]) }))
            Toggle("Steering on when it connects", isOn: Binding(get: { store.tuning.steerOnStart },
                                                                 set: { store.save(["steer_on_start": $0]) }))
        }
        .toggleStyle(.checkbox)
    }

    private var status: String {
        let s = store.screen
        if let busy = store.busy, busy != "screenoura" { return "Waiting: the ring is busy with \(busy)" }
        if s.stopping { return "Stopping…" }
        if s.remote != nil { return "Off · the ring is working as a remote" }
        if !s.running { return "Off" }
        if !s.connected { return s.lastActivity ?? "Connecting to the ring…" }
        guard s.steering else { return "Connected · steering paused" }
        let last = s.lastActivity ?? ""
        return last.hasPrefix("Clicked") || last.hasPrefix("Double") || last.hasPrefix("Triple") || last.hasPrefix("Scrolled")
            ? "Steering on · \(last)" : "Steering on"
    }
}

/// Walks through the five setup poses. The ring mouse measures each pose; Ready stands in for the
/// terminal version's Enter key.
struct CalibrationCard: View {
    @EnvironmentObject var store: Store
    private let titles = ["center": "Aim at the middle of the screen", "up": "Aim at the top edge",
                          "down": "Aim at the bottom edge", "right": "Tip your hand to the right",
                          "flip": "Turn your palm up"]

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("Calibration").font(.body.weight(.semibold))
                Spacer()
                if let c = store.calib {
                    HStack(spacing: 4) {
                        ForEach(1...c.of, id: \.self) { i in
                            Circle().fill(i < c.step || (i == c.step && c.stage == "got") ? Color.green
                                          : i == c.step ? Color.accentColor : Color.secondary.opacity(0.3))
                                .frame(width: 7, height: 7)
                        }
                    }
                }
            }
            if let c = store.calib {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Step \(c.step) of \(c.of)").font(.caption).foregroundStyle(.secondary)
                    Text(titles[c.key] ?? c.key).font(.title3.weight(.semibold))
                    Text(c.text).font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                }
                if let problem = c.problem {
                    Text("That didn't work: \(problem) Let's go through the poses again.")
                        .font(.caption).foregroundStyle(.orange).fixedSize(horizontal: false, vertical: true)
                }
                Group {
                    switch c.stage {
                    case "prompt":
                        VStack(spacing: 6) {
                            Button("Ready") { store.nextSetupStep() }
                                .keyboardShortcut(.defaultAction)
                                .controlSize(.large)
                            Text("Get into position, then click Ready or press Return with your other hand.")
                                .font(.caption2).foregroundStyle(.secondary).multilineTextAlignment(.center)
                        }
                    case "countdown":
                        Text("\(c.countdown ?? 3)").font(.system(size: 40, weight: .semibold)).monospacedDigit()
                    case "measure":
                        Label("Hold still…", systemImage: "hand.raised").font(.title3)
                    case "failed":
                        Label("Starting over", systemImage: "arrow.counterclockwise").font(.title3).foregroundStyle(.orange)
                    default:
                        Label("Got it", systemImage: "checkmark.circle.fill").font(.title3).foregroundStyle(.green)
                    }
                }
                .frame(maxWidth: .infinity, minHeight: 70)
            } else {
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small)
                    Text(store.screen.running ? "Stopping cursor control first…" : "Connecting to the ring…")
                        .foregroundStyle(.secondary)
                }
                .frame(maxWidth: .infinity, minHeight: 120)
            }
            HStack {
                Text("Wear the ring on the same finger, the same way round, as you'll use it.")
                    .font(.caption2).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                Spacer()
                Button("Cancel") { store.cancelSetup() }.controlSize(.small)
            }
        }
        .padding(12)
        .background(RoundedRectangle(cornerRadius: 10).fill(Color.accentColor.opacity(0.06)))
    }
}

/// What Hands Free is, up front: it's FunOura's headline feature.
struct HandsFreeIntro: View {
    private let gestures: [(icon: String, move: String, does: String)] = [
        ("hand.point.up.left", "Point", "Move the cursor"),
        ("hand.tap", "Tap thumb to finger", "Click"),
        ("hand.tap", "Tap twice", "Double-click"),
        ("arrow.up.arrow.down", "Flick hand up or down", "Scroll"),
        ("hand.raised", "Palm up for a second", "Pause or resume"),
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Image(systemName: "hand.point.up.left.fill").font(.title2).foregroundStyle(.tint)
                VStack(alignment: .leading, spacing: 1) {
                    Text("Control your Mac with the ring").font(.callout.weight(.semibold))
                    Text("Point at the screen to move the cursor, and tap your thumb against your finger to click.")
                        .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                }
            }
            Grid(alignment: .leading, horizontalSpacing: 6, verticalSpacing: 3) {
                ForEach(gestures, id: \.move) { g in
                    GridRow {
                        Image(systemName: g.icon).foregroundStyle(.secondary).frame(width: 14)
                        Text(g.move)
                        Text(g.does).foregroundStyle(.secondary)
                    }
                }
            }
            .font(.caption2)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(10)
        .background(RoundedRectangle(cornerRadius: 10).fill(Color.accentColor.opacity(0.08)))
    }
}

struct SwitchRow: View {
    let title: String
    let detail: String
    @Binding var isOn: Bool

    var body: some View {
        HStack {
            VStack(alignment: .leading, spacing: 1) {
                Text(title).font(.body.weight(.medium))
                Text(detail).font(.caption).foregroundStyle(.secondary).lineLimit(1)
            }
            Spacer()
            Toggle(title, isOn: $isOn).toggleStyle(.switch).labelsHidden()
        }
    }
}

/// A slider that only reports a value when it's let go, so dragging doesn't flood the server.
struct SettingSlider: View {
    let title: String
    let value: Double
    let range: ClosedRange<Double>
    let step: Double
    let format: (Double) -> String
    let commit: (Double) -> Void
    @State private var draft: Double?

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack {
                Text(title)
                Spacer()
                Text(format(draft ?? value)).monospacedDigit().foregroundStyle(.secondary)
            }
            .font(.callout)
            Slider(value: Binding(get: { draft ?? value }, set: { draft = ($0 / step).rounded() * step }),
                   in: range) { editing in
                if !editing, let d = draft {
                    commit(d)
                    draft = nil
                }
            }
            .controlSize(.small)
        }
    }
}

// MARK: - Health

struct HealthTab: View {
    @EnvironmentObject var store: Store

    var body: some View {
        let measuring = store.busy
        let h = store.health
        VStack(alignment: .leading, spacing: 10) {
            ScrollView {
                VStack(alignment: .leading, spacing: 10) {
                    ScoreRow(scores: h?.scores)

                    SectionTitle("Heart")
                    if measuring == "heart" {
                        MetricCard(title: "Heart rate · measuring", unit: "bpm", color: .red,
                                   latest: store.liveBeats.last.map { "\(Int($0.value))" } ?? "…",
                                   detail: "Keep your hand still for 30 seconds", points: store.liveBeats)
                    } else {
                        MetricCard(title: "Heart rate", unit: "bpm", color: .red,
                                   latest: store.readings["heart"]?.value.map { "\(Int($0))" }
                                       ?? store.heart.last.map { "\(Int($0.value))" },
                                   detail: heartDetail, points: store.heart)
                    }
                    HeartSection(health: h)

                    SectionTitle("Activity")
                    ActivitySection(health: h)

                    SectionTitle("Sleep")
                    SleepSection(sleep: h?.sleep, goal: h?.goals?.sleepH ?? 8)

                    SectionTitle("Body")
                    MetricCard(title: "Skin temperature", unit: "°C", color: .orange,
                               latest: (store.readings["temperature"]?.value ?? store.temperature.last?.value)
                                   .map { String(format: "%.1f", $0) },
                               detail: logged(store.temperature.last?.at), points: store.temperature)
                    MetricCard(title: "Blood oxygen", unit: "%", color: .blue,
                               latest: (store.readings["oxygen"]?.value ?? store.oxygen.last?.value).map { "\(Int($0.rounded()))" },
                               detail: store.oxygen.isEmpty ? "Measured while you sleep" : logged(store.oxygen.last?.at),
                               points: store.oxygen)
                    BodySection(health: h)

                    Text(footnote(h)).font(.caption2).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .frame(height: 440)

            HStack(spacing: 8) {
                Button(measuring == "heart" ? "Measuring…" : "Measure HR") { store.measure("heart") }
                Button(measuring == "temperature" || measuring == "oxygen" ? "Syncing…" : "Sync log") {
                    store.measure("temperature")
                }
                Spacer()
                if let battery = store.readings["battery"]?.value {
                    Label("\(Int(battery))%", systemImage: batteryIcon(battery))
                        .font(.caption).foregroundStyle(.secondary)
                } else {
                    Button("Battery") { store.measure("battery") }
                }
            }
            .controlSize(.small)
            .disabled(measuring != nil && measuring != "screenoura" ? true : false)
        }
    }

    private func footnote(_ h: HealthSummary?) -> String {
        var parts = ["Graphs show the last 12 hours the ring logged; totals are for today."]
        if let synced = store.syncedAt {
            parts.append("Synced \(synced.formatted(.relative(presentation: .named))).")
        }
        let a = h?.assume
        parts.append("≈ marks an estimate from the ring's heart, motion and activity logs"
                     + (a.map { ", assuming age \(Int($0.age)) and \(Int($0.weightKg)) kg." } ?? "."))
        return parts.joined(separator: " ")
    }

    private var heartDetail: String {
        if let r = store.readings["heart"], let at = r.at {
            let hrv = r.hrv.map { " · HRV \(Int($0)) ms" } ?? ""
            return "Measured \(at.formatted(date: .omitted, time: .shortened))\(hrv)"
        }
        return logged(store.heart.last?.at)
    }

    private func logged(_ at: Date?) -> String {
        guard let at else { return "Nothing logged yet" }
        return "Logged \(at.formatted(date: .omitted, time: .shortened))"
    }

    private func batteryIcon(_ level: Double) -> String {
        level > 75 ? "battery.100" : level > 40 ? "battery.50" : level > 15 ? "battery.25" : "battery.0"
    }
}

private func short(_ v: Double) -> String {
    v.rounded() == v || v >= 50 ? "\(Int(v.rounded()))" : String(format: "%.1f", v)
}

struct MetricCard: View {
    let title: String
    let unit: String
    let color: Color
    let latest: String?
    let detail: String
    let points: [Point]

    var body: some View {
        HStack(alignment: .center, spacing: 10) {
            VStack(alignment: .leading, spacing: 1) {
                Text(title).font(.caption).foregroundStyle(.secondary)
                HStack(alignment: .firstTextBaseline, spacing: 2) {
                    Text(latest ?? "–").font(.title3.weight(.semibold)).monospacedDigit()
                    Text(unit).font(.caption).foregroundStyle(.secondary)
                }
                Text(detail).font(.caption2).foregroundStyle(.secondary).lineLimit(1)
            }
            .frame(width: 130, alignment: .leading)

            if points.count > 1 {
                let low = points.map(\.value).min()!, high = points.map(\.value).max()!
                VStack(alignment: .trailing, spacing: 2) {
                    Chart(points) {
                        AreaMark(x: .value("Time", $0.at), yStart: .value("Low", low), yEnd: .value(title, $0.value))
                            .foregroundStyle(LinearGradient(colors: [color.opacity(0.25), color.opacity(0)],
                                                            startPoint: .top, endPoint: .bottom))
                        LineMark(x: .value("Time", $0.at), y: .value(title, $0.value))
                            .foregroundStyle(color)
                            .lineStyle(StrokeStyle(lineWidth: 1.2))
                    }
                    .chartYScale(domain: low...max(high, low + 0.1))
                    .chartXAxis(.hidden)
                    .chartYAxis(.hidden)
                    .frame(height: 40)
                    Text("\(short(low))–\(short(high)) \(unit) · 12 h").font(.system(size: 9)).foregroundStyle(.tertiary)
                }
            } else {
                Text("No graph yet").font(.caption2).foregroundStyle(.tertiary)
                    .frame(maxWidth: .infinity, minHeight: 44)
            }
        }
        .padding(8)
        .background(RoundedRectangle(cornerRadius: 8).fill(Color.primary.opacity(0.05)))
    }
}

// MARK: - Games

struct GamesTab: View {
    @EnvironmentObject var store: Store

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            GameRow(icon: "figure.table.tennis", color: .blue, title: "Ring Pong",
                    detail: store.online ? "Classic Pong against the computer. Tilt to move your paddle"
                                         : "Needs the dashboard server running",
                    url: store.base.appendingPathComponent("pong"))
                .disabled(!store.online)
            GameRow(icon: "arcade.stick", color: .pink, title: "Ring Invaders",
                    detail: store.online ? "Space Invaders. Tilt to move, tap to fire"
                                         : "Needs the dashboard server running",
                    url: store.base.appendingPathComponent("invaders"))
                .disabled(!store.online)
            GameRow(icon: "paperplane", color: .teal, title: "Flappy Ring",
                    detail: store.online ? "Tap to flap through the gaps. How far can you go?"
                                         : "Needs the dashboard server running",
                    url: store.base.appendingPathComponent("flappy"))
                .disabled(!store.online)
            GameRow(icon: "bird", color: .green, title: "Duck Hunt",
                    detail: "The classic, on CrazyGames. Aim with Hands Free",
                    url: URL(string: "https://www.crazygames.com/game/duck-hunt")!)
            GameRow(icon: "scope", color: .orange, title: "Quick Draw",
                    detail: store.online ? "A one-minute target range for getting the hang of the ring"
                                         : "Needs the dashboard server running",
                    url: store.base.appendingPathComponent("game"))
                .disabled(!store.online)
            HStack(spacing: 6) {
                Image(systemName: "sparkles")
                Text("More coming soon")
            }
            .font(.caption).foregroundStyle(.secondary)
            .frame(maxWidth: .infinity)
            .padding(.top, 4)
        }
    }
}

struct GameRow: View {
    let icon: String
    let color: Color
    let title: String
    let detail: String
    let url: URL

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: icon).font(.title3).foregroundStyle(color)
                .frame(width: 36, height: 36)
                .background(RoundedRectangle(cornerRadius: 8).fill(color.opacity(0.15)))
            VStack(alignment: .leading, spacing: 1) {
                Text(title).font(.body.weight(.medium))
                Text(detail).font(.caption).foregroundStyle(.secondary).lineLimit(2)
            }
            Spacer(minLength: 4)
            Button("Play") { NSWorkspace.shared.open(url) }.controlSize(.small)
        }
        .padding(8)
        .background(RoundedRectangle(cornerRadius: 8).fill(Color.primary.opacity(0.05)))
    }
}

// MARK: - More

struct ComingSoon: View {
    var body: some View {
        VStack(spacing: 8) {
            Image(systemName: "sparkles").font(.largeTitle).foregroundStyle(.secondary)
            Text("More features coming soon").font(.headline)
            Text("New ways to play with the ring will show up here.")
                .font(.caption).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, minHeight: 160)
    }
}

struct Starting: View {
    @EnvironmentObject var store: Store

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                ProgressView().controlSize(.small)
                Text("Starting the dashboard server…").font(.body.weight(.medium))
            }
            if let message = store.message {
                Text(message).font(.caption).foregroundStyle(.orange).fixedSize(horizontal: false, vertical: true)
            }
            Text("If this doesn't clear, its log is in ~/Library/Logs/FunOura/server.log.")
                .font(.caption2).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
        }
    }
}

struct Offline: View {
    @EnvironmentObject var store: Store
    private let command = Server.home.appendingPathComponent("dashboard/dashboard.sh").path

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("The dashboard server isn't running").font(.body.weight(.medium))
            Text("FunOura works through it. Start it in a terminal app that's allowed Bluetooth and Accessibility:")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            HStack {
                Text(command).font(.caption.monospaced()).textSelection(.enabled)
                Spacer()
                Button("Copy") {
                    NSPasteboard.general.clearContents()
                    NSPasteboard.general.setString(command, forType: .string)
                }
                .controlSize(.small)
            }
            .padding(6)
            .background(RoundedRectangle(cornerRadius: 6).fill(Color.primary.opacity(0.06)))
            Text("FunOura connects on its own once it's up.").font(.caption2).foregroundStyle(.secondary)
        }
    }
}
