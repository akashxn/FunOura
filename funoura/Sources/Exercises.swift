import AppKit
import SwiftUI

/// Short guided exercises: paced breathing, a meditation timer, a body scan and desk breaks.
/// They run in the app itself and don't need the ring; heart rate can be checked afterwards.

struct Phase {
    let text: String
    let seconds: Double
    var scale: Double?  // where the circle ends up: 1 is full breath in, 0.45 all the way out; nil holds
}

struct Exercise: Identifiable {
    let id: String
    let title: String
    let icon: String
    let color: Color
    let blurb: String
    let phases: [Phase]
    var minutes: [Int] = []  // lengths to pick from; empty means one pass through the phases

    var repeats: Bool { !minutes.isEmpty }
    var cycle: Double { phases.reduce(0) { $0 + $1.seconds } }

    /// Seconds a session lasts: whole breathing cycles filling the minutes picked, or one pass.
    func length(minutes: Int) -> Double {
        repeats ? max(1, (Double(minutes * 60) / cycle).rounded()) * cycle : cycle
    }
}

private func inhale(_ s: Double) -> Phase { Phase(text: "Breathe in", seconds: s, scale: 1) }
private func exhale(_ s: Double) -> Phase { Phase(text: "Breathe out", seconds: s, scale: 0.45) }
private func hold(_ s: Double) -> Phase { Phase(text: "Hold", seconds: s) }
private func prompt(_ text: String, _ s: Double = 20) -> Phase { Phase(text: text, seconds: s) }

let exercises: [Exercise] = [
    Exercise(id: "calm", title: "Calm breathing", icon: "wind", color: .teal,
             blurb: "About 6 breaths a minute, the pace that tends to lift HRV",
             phases: [inhale(5.5), exhale(5.5)], minutes: [2, 5, 10]),
    Exercise(id: "box", title: "Box breathing", icon: "square", color: .blue,
             blurb: "In, hold, out, hold for 4 each. Steadies you before something big",
             phases: [inhale(4), hold(4), exhale(4), hold(4)], minutes: [2, 4, 8]),
    Exercise(id: "478", title: "4-7-8 breathing", icon: "moon.zzz", color: .indigo,
             blurb: "A long hold and a slow breath out, to wind down for sleep",
             phases: [inhale(4), hold(7), exhale(8)], minutes: [1, 2, 4]),
    Exercise(id: "meditate", title: "Meditation", icon: "brain.head.profile", color: .purple,
             blurb: "A quiet timer with a bell at the start, halfway and the end",
             phases: [Phase(text: "Rest your attention on your breath", seconds: 60)], minutes: [3, 5, 10, 20]),
    Exercise(id: "scan", title: "Body scan", icon: "figure.mind.and.body", color: .mint,
             blurb: "Five minutes moving your attention from your feet up",
             phases: ["your feet", "your ankles and calves", "your knees", "your thighs", "your hips",
                      "your lower back", "your belly", "your chest as it rises and falls", "your hands",
                      "your arms", "your shoulders, and let them drop", "your neck", "your jaw, and unclench it",
                      "your eyes and forehead", "your whole body at once"].map { prompt("Notice \($0)") }),
    Exercise(id: "stretch", title: "Desk stretch", icon: "figure.cooldown", color: .orange,
             blurb: "A few minutes to undo sitting",
             phases: [prompt("Tilt your head toward your left shoulder"), prompt("Now toward your right shoulder"),
                      prompt("Roll your shoulders backwards, slowly"), prompt("Reach both arms up and stretch tall"),
                      prompt("Twist gently to the left"), prompt("Now twist to the right"),
                      prompt("Hold one arm out and pull the fingers back gently, then swap"),
                      prompt("Stand up and shake it all out")]),
    Exercise(id: "eyes", title: "Eye break", icon: "eye", color: .green,
             blurb: "The 20-20-20 rule: look 20 feet away for 20 seconds",
             phases: [prompt("Look at something 20 feet away"), prompt("Blink slowly a few times", 8),
                      prompt("Close your eyes and relax them", 12)]),
]

// MARK: - the running session

@MainActor
final class Coach: ObservableObject {
    @Published private(set) var exercise: Exercise?
    @Published private(set) var finished: (title: String, seconds: Double, at: Date)?
    @Published var sounds: Bool { didSet { UserDefaults.standard.set(sounds, forKey: "exerciseSounds") } }
    @Published private(set) var mindfulToday: Double = 0

    private(set) var startedAt = Date()
    private(set) var length: Double = 0
    private var lastPhase = -1
    private var rangHalfway = false
    private var timer: Timer?

    init() {
        sounds = UserDefaults.standard.object(forKey: "exerciseSounds") as? Bool ?? true
        mindfulToday = Self.loggedToday()
    }

    var running: Bool { exercise != nil }

    func start(_ e: Exercise, minutes: Int) {
        exercise = e
        finished = nil
        length = e.length(minutes: minutes)
        startedAt = Date()
        lastPhase = -1
        rangHalfway = false
        chime("Glass")
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: 0.1, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.tick() }
        }
    }

    func stop() { end(completed: false) }

    func dismiss() { finished = nil }

    /// Where the session is at time `t`: the phase, how far through it, and the circle's size.
    func position(at t: Date) -> (phase: Phase, index: Int, left: Double, scale: Double) {
        guard let e = exercise else { return (Phase(text: "", seconds: 1), 0, 0, 0.45) }
        let elapsed = min(t.timeIntervalSince(startedAt), length)
        var into = e.repeats ? elapsed.truncatingRemainder(dividingBy: e.cycle) : elapsed
        var from = e.phases.reversed().compactMap(\.scale).first ?? 0.7  // how the last cycle ended
        let cycles = e.repeats ? Int(elapsed / e.cycle) : 0
        for (i, p) in e.phases.enumerated() {
            if into < p.seconds || i == e.phases.count - 1 {
                let f = min(into / p.seconds, 1)
                let eased = 0.5 - cos(f * .pi) / 2
                let scale = p.scale.map { from + ($0 - from) * eased } ?? from
                return (p, cycles * e.phases.count + i, max(p.seconds - into, 0), scale)
            }
            into -= p.seconds
            from = p.scale ?? from
        }
        return (e.phases[0], 0, 0, from)
    }

    private func tick() {
        guard let e = exercise else { return }
        let now = Date()
        if now.timeIntervalSince(startedAt) >= length { return end(completed: true) }
        let index = position(at: now).index
        if index != lastPhase {
            if lastPhase >= 0 && e.id != "meditate" { chime("Tink") }
            lastPhase = index
        }
        if e.id == "meditate", !rangHalfway, now.timeIntervalSince(startedAt) >= length / 2 {
            rangHalfway = true
            chime("Glass")
        }
    }

    private func end(completed: Bool) {
        guard let e = exercise else { return }
        timer?.invalidate()
        timer = nil
        let seconds = min(Date().timeIntervalSince(startedAt), length)
        exercise = nil
        if completed { chime("Glass") }
        if seconds >= 30 {
            finished = (e.title, seconds, Date())
            Self.log(seconds)
            mindfulToday = Self.loggedToday()
        }
    }

    private func chime(_ name: String) {
        if sounds { NSSound(named: name)?.play() }
    }

    // mindful minutes, kept per day in the app's defaults
    private static func key(_ d: Date = Date()) -> String {
        "mindful-" + d.formatted(.iso8601.year().month().day())
    }

    private static func loggedToday() -> Double { UserDefaults.standard.double(forKey: key()) }

    private static func log(_ seconds: Double) {
        UserDefaults.standard.set(loggedToday() + seconds, forKey: key())
    }
}

// MARK: - views

struct ExercisesTab: View {
    @EnvironmentObject var coach: Coach
    @EnvironmentObject var store: Store

    var body: some View {
        if coach.running {
            SessionView()
        } else {
            VStack(alignment: .leading, spacing: 8) {
                if let done = coach.finished {
                    FinishedBanner(title: done.title, seconds: done.seconds, at: done.at)
                }
                HStack {
                    Label("Mindful today: \(Int((coach.mindfulToday / 60).rounded())) min", systemImage: "leaf")
                    Spacer()
                    Toggle("Sounds", isOn: $coach.sounds).toggleStyle(.checkbox)
                }
                .font(.caption).foregroundStyle(.secondary)
                ScrollView {
                    VStack(spacing: 6) {
                        ForEach(exercises) { ExerciseRow(exercise: $0) }
                    }
                }
                .frame(height: 400)
            }
        }
    }
}

struct ExerciseRow: View {
    @EnvironmentObject var coach: Coach
    let exercise: Exercise
    @State private var minutes: Int?

    var body: some View {
        let e = exercise
        let chosen = minutes ?? e.minutes.first.map { e.minutes.count > 1 ? e.minutes[1] : $0 } ?? 0
        HStack(spacing: 10) {
            Image(systemName: e.icon).font(.body).foregroundStyle(e.color)
                .frame(width: 32, height: 32)
                .background(RoundedRectangle(cornerRadius: 8).fill(e.color.opacity(0.15)))
            VStack(alignment: .leading, spacing: 1) {
                Text(e.title).font(.callout.weight(.medium))
                Text(e.blurb).font(.caption2).foregroundStyle(.secondary).lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 4)
            VStack(alignment: .trailing, spacing: 3) {
                Button("Start") { coach.start(e, minutes: chosen) }.controlSize(.small)
                if e.repeats {
                    Menu("\(chosen) min") {
                        ForEach(e.minutes, id: \.self) { m in Button("\(m) min") { minutes = m } }
                    }
                    .menuStyle(.borderlessButton).fixedSize().font(.caption2)
                } else {
                    Text(duration(e.cycle)).font(.caption2).foregroundStyle(.secondary)
                }
            }
        }
        .padding(8)
        .background(RoundedRectangle(cornerRadius: 8).fill(Color.primary.opacity(0.05)))
    }
}

private func duration(_ seconds: Double) -> String {
    let s = Int(seconds.rounded())
    return s >= 60 ? "\(s / 60):\(String(format: "%02d", s % 60))" : "\(s) s"
}

struct SessionView: View {
    @EnvironmentObject var coach: Coach

    var body: some View {
        TimelineView(.animation) { context in
            let e = coach.exercise
            let at = coach.position(at: context.date)
            let elapsed = min(context.date.timeIntervalSince(coach.startedAt), coach.length)
            let color = e?.color ?? .teal
            VStack(spacing: 14) {
                HStack {
                    Text(e?.title ?? "").font(.body.weight(.medium))
                    Spacer()
                    Text("\(duration(coach.length - elapsed)) left").font(.caption).monospacedDigit()
                        .foregroundStyle(.secondary)
                }
                ZStack {
                    Circle().fill(color.opacity(0.08)).frame(width: 170, height: 170)
                    Circle().fill(RadialGradient(colors: [color.opacity(0.55), color.opacity(0.25)],
                                                 center: .center, startRadius: 5, endRadius: 85))
                        .frame(width: 170, height: 170)
                        .scaleEffect(at.scale)
                    if e?.phases.first?.scale != nil {
                        Text("\(Int(at.left.rounded(.up)))").font(.title.weight(.semibold)).monospacedDigit()
                            .foregroundStyle(.white)
                    }
                }
                .frame(height: 180)
                Text(at.phase.text).font(.title3.weight(.medium)).multilineTextAlignment(.center)
                    .frame(maxWidth: .infinity, minHeight: 50)
                ProgressView(value: elapsed, total: max(coach.length, 1)).tint(color)
                HStack {
                    Toggle("Sounds", isOn: Binding(get: { coach.sounds }, set: { coach.sounds = $0 }))
                        .toggleStyle(.checkbox).font(.caption)
                    Spacer()
                    Button("Stop") { coach.stop() }
                }
            }
        }
    }
}

struct FinishedBanner: View {
    @EnvironmentObject var coach: Coach
    @EnvironmentObject var store: Store
    let title: String
    let seconds: Double
    let at: Date

    var body: some View {
        let after = store.readings["heart"].flatMap { r in (r.at ?? .distantPast) > at ? r : nil }
        HStack(spacing: 8) {
            Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
            VStack(alignment: .leading, spacing: 1) {
                Text("\(title) · \(duration(seconds))").font(.caption.weight(.medium))
                Text(after?.value.map { v in "Heart rate now \(Int(v)) bpm" + (after?.hrv.map { " · HRV \(Int($0)) ms" } ?? "") }
                     ?? "See how your heart settled").font(.caption2).foregroundStyle(.secondary)
            }
            Spacer()
            Button(store.busy == "heart" ? "Measuring…" : "Measure HR") { store.measure("heart") }
                .controlSize(.small)
                .disabled(!store.online || store.busy != nil)
            Button { coach.dismiss() } label: { Image(systemName: "xmark") }.buttonStyle(.borderless)
        }
        .padding(8)
        .background(RoundedRectangle(cornerRadius: 8).fill(Color.green.opacity(0.1)))
    }
}
