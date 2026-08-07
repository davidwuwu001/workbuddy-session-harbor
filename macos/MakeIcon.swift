import AppKit

let output = URL(fileURLWithPath: CommandLine.arguments[1])
let size: CGFloat = 1024
let image = NSImage(size: NSSize(width: size, height: size))
image.lockFocus()

let canvas = NSRect(x: 0, y: 0, width: size, height: size)
NSColor(calibratedRed: 0.06, green: 0.16, blue: 0.30, alpha: 1).setFill()
NSBezierPath(roundedRect: canvas.insetBy(dx: 32, dy: 32), xRadius: 210, yRadius: 210).fill()

let left = NSRect(x: 184, y: 312, width: 656, height: 400)
NSColor(calibratedRed: 0.12, green: 0.54, blue: 0.91, alpha: 1).setFill()
NSBezierPath(roundedRect: left, xRadius: 92, yRadius: 92).fill()

let arrows = "⇄"
let font = NSFont.systemFont(ofSize: 390, weight: .bold)
let style = NSMutableParagraphStyle()
style.alignment = .center
arrows.draw(in: NSRect(x: 120, y: 294, width: 784, height: 430), withAttributes: [
    .font: font, .foregroundColor: NSColor.white, .paragraphStyle: style,
])

let label = "WB"
label.draw(in: NSRect(x: 0, y: 124, width: size, height: 90), withAttributes: [
    .font: NSFont.systemFont(ofSize: 78, weight: .semibold),
    .foregroundColor: NSColor(calibratedWhite: 1, alpha: 0.86), .paragraphStyle: style,
])
image.unlockFocus()

guard let data = image.tiffRepresentation,
      let bitmap = NSBitmapImageRep(data: data),
      let png = bitmap.representation(using: .png, properties: [:]) else {
    fatalError("无法生成图标")
}
try png.write(to: output)
