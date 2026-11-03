// Renders the app icon (indigo "RD" wordmark) into Resources/AppIcon.icns.
// Usage: swift scripts/make_icon.swift   (needs only the Command Line Tools)
//
// Sizes up to 64 px use a simplified mark (bigger "RD", no rule or "AGENT"),
// because the full layout turns to mush at 16-32 px.
import AppKit

let canvas: CGFloat = 1024
/// Shadows are in device pixels and ignore the CTM, so they are scaled by hand.
var deviceScale: CGFloat = 1

func rgb(_ hex: UInt32, _ alpha: CGFloat = 1) -> CGColor {
    CGColor(red: CGFloat((hex >> 16) & 0xFF) / 255, green: CGFloat((hex >> 8) & 0xFF) / 255,
            blue: CGFloat(hex & 0xFF) / 255, alpha: alpha)
}

func roundedFont(_ size: CGFloat, _ weight: NSFont.Weight) -> NSFont {
    let font = NSFont.systemFont(ofSize: size, weight: weight)
    guard let descriptor = font.fontDescriptor.withDesign(.rounded) else { return font }
    return NSFont(descriptor: descriptor, size: size) ?? font
}

/// Draws `text` horizontally centred with its baseline at `baseline`.
func drawText(_ text: String, font: NSFont, color: NSColor, baseline: CGFloat, kern: CGFloat) {
    let string = NSAttributedString(string: text, attributes: [.font: font, .foregroundColor: color, .kern: kern])
    // draw(at:) places the line's bottom (descender) at the point.
    string.draw(at: NSPoint(x: canvas / 2 - string.size().width / 2, y: baseline + font.descender))
}

func drawBackground(_ context: CGContext) {
    let squircle = CGPath(roundedRect: CGRect(x: 100, y: 100, width: 824, height: 824),
                          cornerWidth: 186, cornerHeight: 186, transform: nil)
    context.saveGState()
    context.setShadow(offset: CGSize(width: 0, height: -12 * deviceScale), blur: 28 * deviceScale,
                      color: rgb(0x000000, 0.35))
    context.addPath(squircle)
    context.setFillColor(rgb(0x312E81))
    context.fillPath()
    context.restoreGState()

    context.saveGState()
    context.addPath(squircle)
    context.clip()
    let space = CGColorSpaceCreateDeviceRGB()
    let fill = CGGradient(colorsSpace: space, colors: [rgb(0x818CF8), rgb(0x312E81)] as CFArray, locations: [0, 1])!
    context.drawLinearGradient(fill, start: CGPoint(x: 512, y: 924), end: CGPoint(x: 512, y: 100), options: [])
    let glow = CGGradient(colorsSpace: space, colors: [rgb(0xFFFFFF, 0.18), rgb(0xFFFFFF, 0)] as CFArray,
                          locations: [0, 1])!
    context.drawRadialGradient(glow, startCenter: CGPoint(x: 330, y: 860), startRadius: 0,
                               endCenter: CGPoint(x: 330, y: 860), endRadius: 620, options: [])
    context.restoreGState()
}

func drawIcon(_ context: CGContext, simplified: Bool) {
    drawBackground(context)
    if simplified {
        let font = roundedFont(390, .heavy)
        drawText("RD", font: font, color: .white, baseline: 512 - font.capHeight / 2, kern: -8)
        return
    }
    drawText("RD", font: roundedFont(330, .heavy), color: .white, baseline: 380 - roundedFont(330, .heavy).descender, kern: -6)
    context.setFillColor(rgb(0xFFFFFF, 0.85))
    context.addPath(CGPath(roundedRect: CGRect(x: 262, y: 330, width: 500, height: 22),
                           cornerWidth: 11, cornerHeight: 11, transform: nil))
    context.fillPath()
    let small = roundedFont(84, .semibold)
    drawText("AGENT", font: small, color: NSColor.white.withAlphaComponent(0.8),
             baseline: 210 - small.descender, kern: 22)
}

func png(pixels: Int) -> Data {
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: pixels, pixelsHigh: pixels,
                               bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                               colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    NSGraphicsContext.saveGraphicsState()
    let graphics = NSGraphicsContext(bitmapImageRep: rep)!
    NSGraphicsContext.current = graphics
    deviceScale = CGFloat(pixels) / canvas
    graphics.cgContext.scaleBy(x: deviceScale, y: deviceScale)
    drawIcon(graphics.cgContext, simplified: pixels <= 64)
    NSGraphicsContext.restoreGraphicsState()
    return rep.representation(using: .png, properties: [:])!
}

let root = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
let resources = root.appendingPathComponent("Resources", isDirectory: true)
let iconset = FileManager.default.temporaryDirectory
    .appendingPathComponent("AppIcon-\(UUID().uuidString).iconset", isDirectory: true)
try FileManager.default.createDirectory(at: iconset, withIntermediateDirectories: true)
try FileManager.default.createDirectory(at: resources, withIntermediateDirectories: true)
defer { try? FileManager.default.removeItem(at: iconset) }

for points in [16, 32, 128, 256, 512] {
    try png(pixels: points).write(to: iconset.appendingPathComponent("icon_\(points)x\(points).png"))
    try png(pixels: points * 2).write(to: iconset.appendingPathComponent("icon_\(points)x\(points)@2x.png"))
}

let output = resources.appendingPathComponent("AppIcon.icns")
let iconutil = Process()
iconutil.executableURL = URL(fileURLWithPath: "/usr/bin/iconutil")
iconutil.arguments = ["--convert", "icns", "--output", output.path, iconset.path]
try iconutil.run()
iconutil.waitUntilExit()
guard iconutil.terminationStatus == 0 else {
    FileHandle.standardError.write(Data("iconutil failed\n".utf8))
    exit(1)
}
print(output.path)
