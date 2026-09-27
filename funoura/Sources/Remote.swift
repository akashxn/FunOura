import SwiftUI

/// The ring as a remote: gestures press keys (next slide, play/pause, volume) instead of moving the
/// cursor. It's the same ring mouse as Hands Free, started with --remote, so only one runs at a time.
struct RemoteTab: View {
    @EnvironmentObject var store: Store

    private let gestures: [String: [(icon: String, move: String, does: String)]] = [
        "slides": [("hand.tap", "Tap thumb to finger", "Next slide"),
                   ("hand.tap", "Tap twice", "Previous slide"),
                   ("arrow.up.arrow.down", "Flick hand up or down", "Volume up or down"),
                   ("hand.raised", "Palm up for a second", "Pause or resume the remote")],
        "media": [("hand.tap", "Tap thumb to finger", "Play or pause"),
                  ("hand.tap", "Tap twice", "Next track"),
                  ("arrow.up.arrow.down", "Flick hand up or down", "Volume up or down"),
                  ("hand.raised", "Palm up for a second", "Pause or resume the remote")],
    ]

    var body: some View {
        let s = store.screen
        let on = s.remote != nil && !s.stopping
        let otherJob = store.busy != nil && store.busy != "screenoura"
        VStack(alignment: .leading, spacing: 10) {
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 8) {
                    Image(systemName: "av.remote.fill").font(.title2).foregroundStyle(.tint)
                    VStack(alignment: .leading, spacing: 1) {
                        Text("A remote on your finger").font(.callout.weight(.semibold))
                        Text("Run a presentation or your music with taps and flicks. Your hand can be anywhere: "
                             + "no need to point at the screen.")
                            .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                    }
                }
                Picker("", selection: Binding(get: { store.remoteProfile }, set: { store.chooseRemote($0) })) {
                    Text("Slides").tag("slides")
                    Text("Media").tag("media")
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                Grid(alignment: .leading, horizontalSpacing: 6, verticalSpacing: 3) {
                    ForEach(gestures[store.remoteProfile] ?? [], id: \.move) { g in
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

            SwitchRow(title: "Remote", detail: status,
                      isOn: Binding(get: { on }, set: { $0 ? store.startRemote() : store.stopScreen() }))
                .disabled(s.stopping || otherJob || !s.calibrated || store.settingUp)
            SwitchRow(title: "Listening", detail: "Same as holding your palm up for a second",
                      isOn: Binding(get: { s.remote != nil && s.steering }, set: { store.steer($0) }))
                .disabled(s.remote == nil || !s.connected || s.stopping)

            if !s.calibrated {
                Text("Needs the one-time setup first: click Calibrate in the Hands Free tab.")
                    .font(.caption).foregroundStyle(.orange)
            } else if s.running && s.remote == nil {
                Text("Hands Free is using the ring. Switching the remote on turns cursor control off.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Text(store.remoteProfile == "slides"
                 ? "Works with Keynote, PowerPoint, Google Slides and PDF viewers: put the slideshow in front."
                 : "Works with Music, Spotify, Podcasts and video playing in a browser.")
                .font(.caption2).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
        }
        .toggleStyle(.checkbox)
    }

    private var status: String {
        let s = store.screen
        if let busy = store.busy, busy != "screenoura" { return "Waiting: the ring is busy with \(busy)" }
        guard s.remote != nil else { return "Off" }
        if s.stopping { return "Stopping…" }
        if !s.connected { return s.lastActivity ?? "Connecting to the ring…" }
        guard s.steering else { return "Paused · palm up to resume" }
        let last = s.lastActivity ?? ""
        let pressed = ["Next", "Previous", "Play", "Volume"].contains { last.hasPrefix($0) }
        return pressed ? "On · \(last)" : "On · listening"
    }
}
