import Charts
import SwiftUI

/// The Health tab's metrics, worked out by the dashboard server (dashboard/health.py) from the ring's log.
/// Many are estimates until open_oura decodes the ring's own summaries; the tab marks those with ≈.
struct HealthSummary: Decodable {
    struct Heart: Decodable {
        var avg: Double
        var min: Double
        var max: Double
        var resting: Double?
        var maxPredicted: Double
    }

    struct Activity: Decodable {
        var steps: Double
        var stepsHourly: [[Double]]
        var activeKcal: Double
        var totalKcal: Double
        var activeMin: Double
        var vigorousMin: Double
        var lightMin: Double
        var sedentaryMin: Double
        var longestStillMin: Double
        var moveHours: Double
        var metAvg: Double
    }

    struct Stage: Decodable {
        var at: Double
        var stage: String
    }

    struct Sleep: Decodable {
        var start: Double
        var end: Double
        var asleepMin: Double
        var inBedMin: Double
        var efficiency: Double?
        var restlessMin: Double
        var stages: [Stage]
        var stageMin: [String: Double]
        var lowestHr: Double?
        var avgHrv: Double?
    }

    struct Scores: Decodable {
        var readiness: Double?
        var sleep: Double?
        var activity: Double?
    }

    struct Assume: Decodable {
        var age: Double
        var weightKg: Double
    }

    struct Goals: Decodable {
        var steps: Double
        var activeMin: Double
        var sleepH: Double
    }

    var hr: Heart?
    var hrZones: [Double]?
    var hrv: [[Double]]?
    var hrvAvg: Double?
    var hrvLatest: Double?
    var stress: [[Double]]?
    var stressAvg: Double?
    var stressHighMin: Double?
    var restoredMin: Double?
    var respRate: Double?
    var activity: Activity?
    var met: [[Double]]?
    var wearMin: Double?
    var tempDev: Double?
    var tempDevSeries: [[Double]]?
    var vo2max: Double?
    var sleep: Sleep?
    var scores: Scores?
    var assume: Assume?
    var goals: Goals?

    static func decode(_ json: [String: Any]) -> HealthSummary? {
        guard let data = try? JSONSerialization.data(withJSONObject: json) else { return nil }
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try? decoder.decode(HealthSummary.self, from: data)
    }
}

private func points(_ series: [[Double]]?) -> [Point] {
    (series ?? []).compactMap { $0.count == 2 ? Point(at: Date(timeIntervalSince1970: $0[0]), value: $0[1]) : nil }
}

private func duration(_ minutes: Double) -> String {
    let m = Int(minutes.rounded())
    return m >= 60 ? "\(m / 60)h \(m % 60)m" : "\(m)m"
}

private func clock(_ unix: Double) -> String {
    Date(timeIntervalSince1970: unix).formatted(date: .omitted, time: .shortened)
}

// MARK: - building blocks

struct SectionTitle: View {
    let text: String
    init(_ text: String) { self.text = text }

    var body: some View {
        Text(text).font(.caption.weight(.semibold)).foregroundStyle(.secondary).padding(.top, 2)
    }
}

/// A small value card; `estimated` puts ≈ after the title.
struct Tile: View {
    let title: String
    let value: String?
    var unit = ""
    var detail = ""
    var estimated = false
    var progress: Double?
    var color = Color.accentColor

    var body: some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(estimated ? "\(title) ≈" : title).font(.caption).foregroundStyle(.secondary).lineLimit(1)
            HStack(alignment: .firstTextBaseline, spacing: 2) {
                Text(value ?? "–").font(.body.weight(.semibold)).monospacedDigit()
                Text(unit).font(.caption2).foregroundStyle(.secondary)
            }
            if let progress {
                GeometryReader { geo in
                    Capsule().fill(color.opacity(0.15))
                        .overlay(alignment: .leading) {
                            Capsule().fill(color).frame(width: geo.size.width * min(max(progress, 0.02), 1))
                        }
                }
                .frame(height: 4)
                .padding(.vertical, 2)
            }
            if !detail.isEmpty {
                Text(detail).font(.system(size: 9)).foregroundStyle(.tertiary).lineLimit(1)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(7)
        .background(RoundedRectangle(cornerRadius: 8).fill(Color.primary.opacity(0.05)))
    }
}

struct Tiles<Content: View>: View {
    @ViewBuilder let content: Content

    var body: some View {
        LazyVGrid(columns: [GridItem(.flexible(), spacing: 8), GridItem(.flexible(), spacing: 8)], spacing: 8) {
            content
        }
    }
}

struct Placeholder: View {
    let text: String

    var body: some View {
        Text(text).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(8)
            .background(RoundedRectangle(cornerRadius: 8).fill(Color.primary.opacity(0.05)))
    }
}

// MARK: - scores

struct ScoreRow: View {
    let scores: HealthSummary.Scores?

    var body: some View {
        HStack(spacing: 0) {
            ScoreRing(title: "Readiness", value: scores?.readiness, color: .teal)
            ScoreRing(title: "Sleep", value: scores?.sleep, color: .indigo)
            ScoreRing(title: "Activity", value: scores?.activity, color: .green)
        }
        .padding(.vertical, 4)
    }
}

struct ScoreRing: View {
    let title: String
    let value: Double?
    let color: Color

    var body: some View {
        VStack(spacing: 4) {
            ZStack {
                Circle().stroke(color.opacity(0.15), lineWidth: 5)
                Circle().trim(from: 0, to: (value ?? 0) / 100)
                    .stroke(color, style: StrokeStyle(lineWidth: 5, lineCap: .round))
                    .rotationEffect(.degrees(-90))
                Text(value.map { "\(Int($0))" } ?? "–").font(.callout.weight(.semibold)).monospacedDigit()
            }
            .frame(width: 44, height: 44)
            Text("\(title) ≈").font(.caption2).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity)
    }
}

// MARK: - heart

struct HeartSection: View {
    let health: HealthSummary?

    var body: some View {
        let hr = health?.hr
        Tiles {
            Tile(title: "Resting HR", value: hr?.resting.map { "\(Int($0))" }, unit: "bpm",
                 detail: health?.sleep?.lowestHr != nil ? "Lowest while asleep" : "Lowest while still")
            Tile(title: "Today's range", value: hr.map { "\(Int($0.min))–\(Int($0.max))" }, unit: "bpm",
                 detail: hr.map { "Average \(Int($0.avg)) bpm" } ?? "")
        }
        MetricCard(title: "HRV (RMSSD)", unit: "ms", color: .pink,
                   latest: health?.hrvLatest.map { "\(Int($0))" },
                   detail: health?.hrvAvg.map { "Today's average \(Int($0)) ms" } ?? "From logged beats",
                   points: points(health?.hrv))
        MetricCard(title: "Stress ≈", unit: "/100", color: .purple,
                   latest: health?.stress?.last.map { "\(Int($0[1]))" },
                   detail: stressDetail, points: points(health?.stress))
        if let zones = health?.hrZones {
            ZonesBar(shares: zones, maxHR: hr?.maxPredicted)
        }
    }

    private var stressDetail: String {
        guard let avg = health?.stressAvg else { return "Needs still moments with a heart rate" }
        return "Avg \(Int(avg)) · high \(duration(health?.stressHighMin ?? 0))"
    }
}

struct ZonesBar: View {
    let shares: [Double]
    let maxHR: Double?
    private let colors: [Color] = [.gray.opacity(0.4), .blue, .green, .yellow, .orange, .red]
    private let names = ["Rest", "Z1", "Z2", "Z3", "Z4", "Z5"]

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            HStack {
                Text("Heart rate zones today").font(.caption).foregroundStyle(.secondary)
                Spacer()
                if let maxHR { Text("max ≈ \(Int(maxHR)) bpm").font(.system(size: 9)).foregroundStyle(.tertiary) }
            }
            GeometryReader { geo in
                HStack(spacing: 1) {
                    ForEach(shares.indices, id: \.self) { i in
                        if shares[i] > 0 {
                            Rectangle().fill(colors[i]).frame(width: max(2, geo.size.width * shares[i] - 1))
                        }
                    }
                }
            }
            .frame(height: 8)
            .clipShape(RoundedRectangle(cornerRadius: 3))
            HStack(spacing: 6) {
                ForEach(shares.indices, id: \.self) { i in
                    HStack(spacing: 2) {
                        Circle().fill(colors[i]).frame(width: 5, height: 5)
                        Text("\(names[i]) \(Int((shares[i] * 100).rounded()))%")
                    }
                }
            }
            .font(.system(size: 9)).foregroundStyle(.secondary)
        }
        .padding(8)
        .background(RoundedRectangle(cornerRadius: 8).fill(Color.primary.opacity(0.05)))
    }
}

// MARK: - activity

struct ActivitySection: View {
    let health: HealthSummary?

    var body: some View {
        if let a = health?.activity {
            let goals = health?.goals
            Tiles {
                Tile(title: "Steps", value: "\(Int(a.steps))",
                     detail: "Goal \(Int(goals?.steps ?? 8000))", estimated: true,
                     progress: a.steps / (goals?.steps ?? 8000), color: .green)
                Tile(title: "Active calories", value: "\(Int(a.activeKcal))", unit: "kcal",
                     detail: "\(Int(a.totalKcal)) kcal total today", estimated: true)
                Tile(title: "Active time", value: duration(a.activeMin),
                     detail: "Light \(duration(a.lightMin)) · vigorous \(duration(a.vigorousMin))",
                     progress: a.activeMin / (goals?.activeMin ?? 30), color: .orange)
                Tile(title: "Sitting still", value: duration(a.sedentaryMin),
                     detail: "Longest stretch \(duration(a.longestStillMin))")
                Tile(title: "Hours with movement", value: "\(Int(a.moveHours))", unit: "h",
                     detail: "Moved a minute or more")
                Tile(title: "Wear time", value: health?.wearMin.map { duration($0) }, detail: "Ring on today")
            }
            StepsChart(hourly: points(a.stepsHourly))
            MetricCard(title: "Activity level", unit: "MET", color: .green,
                       latest: health?.met?.last.map { String(format: "%.1f", $0[1]) },
                       detail: "Today's average \(String(format: "%.1f", a.metAvg))", points: points(health?.met))
        } else {
            Placeholder(text: "No activity logged today yet. Wear the ring and sync its log.")
        }
    }
}

struct StepsChart: View {
    let hourly: [Point]

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Steps by hour ≈").font(.caption).foregroundStyle(.secondary)
            if hourly.isEmpty {
                Text("No steps yet").font(.caption2).foregroundStyle(.tertiary)
            } else {
                Chart(hourly) {
                    BarMark(x: .value("Hour", $0.at, unit: .hour), y: .value("Steps", $0.value))
                        .foregroundStyle(.green.gradient)
                }
                .chartXAxis {
                    AxisMarks(values: .stride(by: .hour, count: 3)) {
                        AxisValueLabel(format: .dateTime.hour())
                    }
                }
                .chartYAxis(.hidden)
                .frame(height: 50)
            }
        }
        .padding(8)
        .background(RoundedRectangle(cornerRadius: 8).fill(Color.primary.opacity(0.05)))
    }
}

// MARK: - sleep

struct SleepSection: View {
    let sleep: HealthSummary.Sleep?
    let goal: Double

    var body: some View {
        if let s = sleep {
            Tiles {
                Tile(title: "Time asleep", value: duration(s.asleepMin),
                     detail: "\(clock(s.start)) – \(clock(s.end))", estimated: true,
                     progress: s.asleepMin / (goal * 60), color: .indigo)
                Tile(title: "Efficiency", value: s.efficiency.map { "\(Int($0 * 100))" }, unit: "%",
                     detail: "Restless \(duration(s.restlessMin)) · in bed \(duration(s.inBedMin))", estimated: true)
                Tile(title: "Lowest heart rate", value: s.lowestHr.map { "\(Int($0))" }, unit: "bpm",
                     detail: "During the night")
                Tile(title: "Night HRV", value: s.avgHrv.map { "\(Int($0))" }, unit: "ms", detail: "Average while asleep")
            }
            Hypnogram(sleep: s)
        } else {
            Placeholder(text: "Wear the ring to bed and sync in the morning: time asleep, efficiency, stages and "
                        + "your lowest heart rate show up here.")
        }
    }
}

struct Hypnogram: View {
    let sleep: HealthSummary.Sleep
    private let order = ["awake", "rem", "light", "deep"]
    private let colors: [String: Color] = ["awake": .orange, "rem": .cyan, "light": .blue, "deep": .indigo]

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            Text("Sleep stages ≈").font(.caption).foregroundStyle(.secondary)
            Chart(sleep.stages, id: \.at) { block in
                RectangleMark(xStart: .value("Start", Date(timeIntervalSince1970: block.at)),
                              xEnd: .value("End", Date(timeIntervalSince1970: block.at + 300)),
                              y: .value("Stage", block.stage.uppercased()), height: 12)
                    .foregroundStyle(colors[block.stage] ?? .gray)
            }
            .chartYScale(domain: order.map { $0.uppercased() })
            .chartYAxis(.hidden)
            .chartXAxis { AxisMarks(values: .automatic(desiredCount: 4)) { AxisValueLabel(format: .dateTime.hour()) } }
            .frame(height: 70)
            HStack(spacing: 8) {
                ForEach(order, id: \.self) { stage in
                    HStack(spacing: 2) {
                        Circle().fill(colors[stage]!).frame(width: 5, height: 5)
                        Text("\(stage == "rem" ? "REM" : stage.capitalized) \(duration(sleep.stageMin[stage] ?? 0))")
                    }
                }
            }
            .font(.system(size: 9)).foregroundStyle(.secondary)
        }
        .padding(8)
        .background(RoundedRectangle(cornerRadius: 8).fill(Color.primary.opacity(0.05)))
    }
}

// MARK: - body

struct BodySection: View {
    let health: HealthSummary?

    var body: some View {
        Tiles {
            Tile(title: "Temperature change", value: health?.tempDev.map { String(format: "%+.1f", ($0 * 10).rounded() / 10 + 0) }, unit: "°C",
                 detail: "Against today's usual")
            Tile(title: "Breathing rate", value: health?.respRate.map { String(format: "%.0f", $0) }, unit: "/min",
                 detail: "From beat rhythm", estimated: true)
            Tile(title: "VO₂ max", value: health?.vo2max.map { String(format: "%.0f", $0) }, unit: "ml/kg/min",
                 detail: "From max and resting HR", estimated: true)
            Tile(title: "Cardio fitness", value: health?.vo2max.map(fitnessLevel),
                 detail: "For age \(Int(health?.assume?.age ?? 30))", estimated: true)
        }
    }

    /// Rough bands for a 30-year-old (ACSM), shifted by about 0.3 per year of age.
    private func fitnessLevel(_ vo2: Double) -> String {
        let shift = ((health?.assume?.age ?? 30) - 30) * 0.3
        switch vo2 + shift {
        case ..<33: return "Low"
        case ..<39: return "Fair"
        case ..<45: return "Good"
        case ..<51: return "Very good"
        default: return "Excellent"
        }
    }
}
