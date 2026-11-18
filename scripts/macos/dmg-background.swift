// The disk image window's background: `swift dmg-background.swift OUT.png OUT@2x.png`.
//
// Drawn at build time (AppKit, no image tools or committed binaries), at 1x and 2x for Retina
// screens; dmgbuild joins the two into one TIFF. The layout matches dmg-settings.py: a
// 640 x 400 point window, BibleLive's icon centred at (170, 180) and Applications' at
// (470, 180), measured from the top left. Light and plain, as most Mac disk images are, so
// Finder's icon labels stay readable.
import AppKit

let width: CGFloat = 640
let height: CGFloat = 400
let iconY: CGFloat = 180  // from the top, as in dmg-settings.py

func color(_ hex: Int) -> NSColor {
    NSColor(
        srgbRed: CGFloat((hex >> 16) & 0xFF) / 255, green: CGFloat((hex >> 8) & 0xFF) / 255,
        blue: CGFloat(hex & 0xFF) / 255, alpha: 1)
}

func centred(_ text: String, font: NSFont, color: NSColor, top: CGFloat) {
    let attrs: [NSAttributedString.Key: Any] = [.font: font, .foregroundColor: color]
    let size = (text as NSString).size(withAttributes: attrs)
    let origin = NSPoint(x: (width - size.width) / 2, y: height - top - size.height)
    (text as NSString).draw(at: origin, withAttributes: attrs)
}

func render(scale: CGFloat, to path: String) {
    guard
        let rep = NSBitmapImageRep(
            bitmapDataPlanes: nil, pixelsWide: Int(width * scale), pixelsHigh: Int(height * scale),
            bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
            colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)
    else { fatalError("cannot create the bitmap") }
    rep.size = NSSize(width: width, height: height)  // draw in points; the rep holds the pixels
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)

    color(0xF6F4EF).setFill()
    NSRect(x: 0, y: 0, width: width, height: height).fill()

    // An arrow from BibleLive to Applications, between the two 128 pt icons.
    let y = height - iconY
    let accent = color(0x8A7F6E)
    let shaft = NSBezierPath()
    shaft.move(to: NSPoint(x: 262, y: y))
    shaft.line(to: NSPoint(x: 368, y: y))
    shaft.lineWidth = 5
    shaft.lineCapStyle = .round
    accent.setStroke()
    shaft.stroke()
    let head = NSBezierPath()
    head.move(to: NSPoint(x: 384, y: y))
    head.line(to: NSPoint(x: 362, y: y + 14))
    head.line(to: NSPoint(x: 362, y: y - 14))
    head.close()
    accent.setFill()
    head.fill()

    centred(
        "Drag BibleLive onto Applications", font: .systemFont(ofSize: 17, weight: .semibold),
        color: color(0x2E2A24), top: 292)
    centred(
        "Then open it from Applications, not from this window.",
        font: .systemFont(ofSize: 13), color: color(0x6B6459), top: 320)

    NSGraphicsContext.restoreGraphicsState()
    guard let png = rep.representation(using: .png, properties: [:]) else {
        fatalError("cannot encode \(path)")
    }
    do { try png.write(to: URL(fileURLWithPath: path)) } catch { fatalError("\(path): \(error)") }
}

let args = CommandLine.arguments
guard args.count == 3 else {
    FileHandle.standardError.write("usage: dmg-background.swift OUT.png OUT@2x.png\n".data(using: .utf8)!)
    exit(2)
}
render(scale: 1, to: args[1])
render(scale: 2, to: args[2])
