import AppKit

let output = URL(fileURLWithPath: CommandLine.arguments[1])
let size: CGFloat = 1024
let image = NSImage(size: NSSize(width: size, height: size))
image.lockFocus()

let canvas = NSRect(x: 48, y: 48, width: 928, height: 928)
let background = NSBezierPath(roundedRect: canvas, xRadius: 220, yRadius: 220)
NSGradient(
    starting: NSColor(calibratedRed: 0.07, green: 0.12, blue: 0.29, alpha: 1),
    ending: NSColor(calibratedRed: 0.11, green: 0.39, blue: 0.66, alpha: 1)
)?.draw(in: background, angle: -45)

let glow = NSBezierPath(ovalIn: NSRect(x: 520, y: 526, width: 330, height: 330))
NSColor(calibratedRed: 0.18, green: 0.86, blue: 0.78, alpha: 0.16).setFill()
glow.fill()

let backCard = NSBezierPath(roundedRect: NSRect(x: 364, y: 278, width: 386, height: 484), xRadius: 72, yRadius: 72)
NSColor(calibratedRed: 0.49, green: 0.72, blue: 0.96, alpha: 0.42).setFill()
backCard.fill()

let card = NSBezierPath(roundedRect: NSRect(x: 232, y: 252, width: 430, height: 522), xRadius: 82, yRadius: 82)
NSColor(calibratedRed: 0.96, green: 0.98, blue: 1.0, alpha: 1).setFill()
card.fill()

let title = NSBezierPath(roundedRect: NSRect(x: 302, y: 654, width: 190, height: 34), xRadius: 17, yRadius: 17)
NSColor(calibratedRed: 0.12, green: 0.26, blue: 0.48, alpha: 1).setFill()
title.fill()

for (index, width) in [266, 212, 244].enumerated() {
    let line = NSBezierPath(roundedRect: NSRect(x: 302, y: 572 - CGFloat(index) * 74, width: CGFloat(width), height: 26), xRadius: 13, yRadius: 13)
    NSColor(calibratedRed: 0.66, green: 0.75, blue: 0.86, alpha: 1).setFill()
    line.fill()
}

let syncCircle = NSBezierPath(ovalIn: NSRect(x: 548, y: 470, width: 276, height: 276))
NSColor(calibratedRed: 0.12, green: 0.82, blue: 0.73, alpha: 1).setFill()
syncCircle.fill()

func drawArrow(from: NSPoint, control1: NSPoint, control2: NSPoint, to: NSPoint, tip: NSPoint, wing1: NSPoint, wing2: NSPoint) {
    let path = NSBezierPath()
    path.move(to: from)
    path.curve(to: to, controlPoint1: control1, controlPoint2: control2)
    path.lineWidth = 24
    path.lineCapStyle = .round
    NSColor.white.setStroke()
    path.stroke()

    let head = NSBezierPath()
    head.move(to: tip)
    head.line(to: wing1)
    head.line(to: wing2)
    head.close()
    NSColor.white.setFill()
    head.fill()
}

drawArrow(
    from: NSPoint(x: 598, y: 638), control1: NSPoint(x: 650, y: 692), control2: NSPoint(x: 752, y: 687), to: NSPoint(x: 774, y: 614),
    tip: NSPoint(x: 790, y: 585), wing1: NSPoint(x: 738, y: 604), wing2: NSPoint(x: 786, y: 635)
)
drawArrow(
    from: NSPoint(x: 774, y: 578), control1: NSPoint(x: 722, y: 524), control2: NSPoint(x: 620, y: 529), to: NSPoint(x: 598, y: 602),
    tip: NSPoint(x: 582, y: 631), wing1: NSPoint(x: 634, y: 612), wing2: NSPoint(x: 586, y: 581)
)

image.unlockFocus()

guard let data = image.tiffRepresentation,
      let bitmap = NSBitmapImageRep(data: data),
      let png = bitmap.representation(using: .png, properties: [:]) else {
    fatalError("无法生成图标")
}
try png.write(to: output)
