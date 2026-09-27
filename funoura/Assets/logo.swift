import SwiftUI
import AppKit

struct Sparkle: Shape {
    func path(in r: CGRect) -> Path {
        var p = Path()
        let c = CGPoint(x: r.midX, y: r.midY), w = r.width / 2, h = r.height / 2, k: CGFloat = 0.18
        p.move(to: CGPoint(x: c.x, y: c.y - h))
        p.addQuadCurve(to: CGPoint(x: c.x + w, y: c.y), control: CGPoint(x: c.x + w * k, y: c.y - h * k))
        p.addQuadCurve(to: CGPoint(x: c.x, y: c.y + h), control: CGPoint(x: c.x + w * k, y: c.y + h * k))
        p.addQuadCurve(to: CGPoint(x: c.x - w, y: c.y), control: CGPoint(x: c.x - w * k, y: c.y + h * k))
        p.addQuadCurve(to: CGPoint(x: c.x, y: c.y - h), control: CGPoint(x: c.x - w * k, y: c.y - h * k))
        return p
    }
}

struct Cursor: Shape {  // the classic pointer, tip at top-left of the rect
    func path(in r: CGRect) -> Path {
        let pts: [(CGFloat, CGFloat)] = [(0, 0), (0, 0.78), (0.2, 0.6), (0.34, 0.92), (0.47, 0.86), (0.33, 0.55), (0.6, 0.55)]
        var p = Path()
        p.addLines(pts.map { CGPoint(x: r.minX + $0.0 * r.height, y: r.minY + $0.1 * r.height) })
        p.closeSubpath()
        return p
    }
}

struct Icon: View {
    var top: Color
    var bottom: Color
    var glow: Color
    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 185, style: .continuous)
                .fill(LinearGradient(colors: [top, bottom],
                                     startPoint: .top, endPoint: .bottom))
                .overlay(RoundedRectangle(cornerRadius: 185, style: .continuous)
                    .fill(RadialGradient(colors: [glow, .clear],
                                         center: UnitPoint(x: 0.75, y: 0.2), startRadius: 0, endRadius: 520)))
                .frame(width: 824, height: 824)

            // ring: back edge, green sensor glow on the inside, then the front band over it
            ZStack {
                Ellipse().stroke(Color(white: 0.35), lineWidth: 58)
                    .frame(width: 470, height: 250).offset(y: 22)
                Ellipse().fill(RadialGradient(colors: [Color(red: 0.3, green: 1, blue: 0.55).opacity(0.9), .clear],
                                              center: .center, startRadius: 0, endRadius: 70))
                    .frame(width: 200, height: 60).offset(y: -62)
                Ellipse().stroke(AngularGradient(colors: [Color(white: 0.98), Color(white: 0.62), Color(white: 0.92),
                                                          Color(white: 0.55), Color(white: 0.98)], center: .center),
                                 lineWidth: 58)
                    .frame(width: 470, height: 250)
                Ellipse().trim(from: 0.55, to: 0.95).stroke(Color.white.opacity(0.7), style: StrokeStyle(lineWidth: 8, lineCap: .round))
                    .frame(width: 430, height: 215).offset(y: -4)
            }
            .rotationEffect(.degrees(-12))
            .offset(x: -30, y: 110)

            Cursor().fill(.white)
                .overlay(Cursor().stroke(Color(red: 0.13, green: 0.12, blue: 0.30), lineWidth: 18).clipShape(Cursor()))
                .frame(width: 300, height: 300)
                .rotationEffect(.degrees(-8))
                .shadow(color: .black.opacity(0.35), radius: 18, y: 10)
                .offset(x: 120, y: -95)

            Sparkle().fill(Color(red: 1, green: 0.85, blue: 0.35)).frame(width: 110, height: 130).offset(x: -210, y: -190)
            Sparkle().fill(Color.white.opacity(0.9)).frame(width: 50, height: 60).offset(x: -120, y: -260)
        }
        .frame(width: 1024, height: 1024)
    }
}

@main
struct Main {
    @MainActor static func main() {
        let variants: [(String, Icon)] = [
            ("violet", Icon(top: Color(red: 0.13, green: 0.12, blue: 0.30), bottom: Color(red: 0.36, green: 0.22, blue: 0.78),
                            glow: Color(red: 1, green: 0.55, blue: 0.75).opacity(0.45))),
            ("midnight", Icon(top: Color(red: 0.16, green: 0.17, blue: 0.2), bottom: Color(red: 0.03, green: 0.03, blue: 0.05),
                              glow: Color(red: 0.35, green: 0.55, blue: 1).opacity(0.35))),
        ]
        for (name, icon) in variants {
            let r = ImageRenderer(content: icon); r.scale = 1
            let rep = NSBitmapImageRep(cgImage: r.cgImage!)
            try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: CommandLine.arguments[1] + "/\(name).png"))
        }
    }
}
