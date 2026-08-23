import AppKit

// AI 账号坞图标：三平台汇聚 —— 绿/橙/紫三个平台节点汇聚到中心账号枢纽。
// 节点配色与 App 内三平台标识色一致：WorkBuddy 绿、Trae 橙、千问紫。

let output = URL(fileURLWithPath: CommandLine.arguments[1])
let size: CGFloat = 1024
let image = NSImage(size: NSSize(width: size, height: size))
image.lockFocus()

// ---- 背景：深蓝→靛蓝渐变圆角矩形 ----
let canvas = NSRect(x: 48, y: 48, width: 928, height: 928)
let background = NSBezierPath(roundedRect: canvas, xRadius: 224, yRadius: 224)
NSGradient(
    starting: NSColor(calibratedRed: 0.08, green: 0.10, blue: 0.30, alpha: 1),
    ending: NSColor(calibratedRed: 0.24, green: 0.30, blue: 0.68, alpha: 1)
)?.draw(in: background, angle: -45)

// 背景装饰微光点
for (x, y, r, a) in [(836.0, 312.0, 7.0, 0.14), (198.0, 352.0, 5.0, 0.12), (512.0, 902.0, 6.0, 0.10)] {
    let dot = NSBezierPath(ovalIn: NSRect(x: x - r, y: y - r, width: r * 2, height: r * 2))
    NSColor(white: 1, alpha: a).setFill()
    dot.fill()
}

let hubCenter = NSPoint(x: 512, y: 512)
let hubRadius: CGFloat = 145

// 中心枢纽后的柔光
for (r, a) in [(300.0, 0.05), (235.0, 0.06)] {
    let glow = NSBezierPath(ovalIn: NSRect(x: hubCenter.x - r, y: hubCenter.y - r, width: r * 2, height: r * 2))
    NSColor(white: 1, alpha: a).setFill()
    glow.fill()
}

// ---- 三个平台节点：绿(上) / 橙(左下) / 紫(右下) ----
let nodeRadius: CGFloat = 92
let nodes: [(NSPoint, NSColor)] = [
    (NSPoint(x: 512, y: 200), NSColor(calibratedRed: 0.30, green: 0.69, blue: 0.31, alpha: 1)),   // WorkBuddy 绿
    (NSPoint(x: 272, y: 736), NSColor(calibratedRed: 1.00, green: 0.60, blue: 0.00, alpha: 1)),   // Trae 橙
    (NSPoint(x: 752, y: 736), NSColor(calibratedRed: 0.66, green: 0.56, blue: 0.95, alpha: 1)),   // 千问 紫
]

// 汇聚连线（先画线，节点与枢纽压在线上方）
for (center, color) in nodes {
    let line = NSBezierPath()
    line.move(to: center)
    line.line(to: hubCenter)
    line.lineWidth = 22
    line.lineCapStyle = .round
    color.withAlphaComponent(0.55).setStroke()
    line.stroke()
}

// 连线上的流动光点（靠节点一侧 45% 处）
for (center, _) in nodes {
    let t: CGFloat = 0.45
    let p = NSPoint(x: center.x + (hubCenter.x - center.x) * t,
                    y: center.y + (hubCenter.y - center.y) * t)
    let dot = NSBezierPath(ovalIn: NSRect(x: p.x - 9, y: p.y - 9, width: 18, height: 18))
    NSColor(white: 1, alpha: 0.92).setFill()
    dot.fill()
}

// ---- 中心枢纽：白色圆 + 账号人形 ----
NSGraphicsContext.saveGraphicsState()
let hubShadow = NSShadow()
hubShadow.shadowOffset = NSSize(width: 0, height: -10)
hubShadow.shadowBlurRadius = 32
hubShadow.shadowColor = NSColor(white: 0, alpha: 0.38)
hubShadow.set()
let hub = NSBezierPath(ovalIn: NSRect(x: hubCenter.x - hubRadius, y: hubCenter.y - hubRadius,
                                      width: hubRadius * 2, height: hubRadius * 2))
NSColor(calibratedRed: 0.97, green: 0.98, blue: 1.0, alpha: 1).setFill()
hub.fill()
NSGraphicsContext.restoreGraphicsState()

// 枢纽外圈微光环
let ring = NSBezierPath(ovalIn: NSRect(x: hubCenter.x - 170, y: hubCenter.y - 170, width: 340, height: 340))
ring.lineWidth = 7
NSColor(white: 1, alpha: 0.16).setStroke()
ring.stroke()

// 账号人形（裁剪到枢纽圆内）
NSGraphicsContext.saveGraphicsState()
hub.addClip()
NSColor(calibratedRed: 0.10, green: 0.16, blue: 0.34, alpha: 1).setFill()
// AppKit y 轴向上：头部在上方（y 更大），肩部在下方
let head = NSBezierPath(ovalIn: NSRect(x: 512 - 50, y: 552 - 50, width: 100, height: 100))
head.fill()
let shoulders = NSBezierPath(roundedRect: NSRect(x: 414, y: 396, width: 196, height: 130), xRadius: 66, yRadius: 66)
shoulders.fill()
NSGraphicsContext.restoreGraphicsState()

// ---- 节点圆（压住连线端点）----
for (center, color) in nodes {
    NSGraphicsContext.saveGraphicsState()
    let nodeShadow = NSShadow()
    nodeShadow.shadowOffset = NSSize(width: 0, height: -8)
    nodeShadow.shadowBlurRadius = 22
    nodeShadow.shadowColor = NSColor(white: 0, alpha: 0.30)
    nodeShadow.set()
    let node = NSBezierPath(ovalIn: NSRect(x: center.x - nodeRadius, y: center.y - nodeRadius,
                                           width: nodeRadius * 2, height: nodeRadius * 2))
    color.setFill()
    node.fill()
    NSGraphicsContext.restoreGraphicsState()

    // 节点内侧高光弧
    let highlight = NSBezierPath(ovalIn: NSRect(x: center.x - nodeRadius + 10, y: center.y - nodeRadius + 10,
                                                width: (nodeRadius - 10) * 2, height: (nodeRadius - 10) * 2))
    highlight.lineWidth = 5
    NSColor(white: 1, alpha: 0.22).setStroke()
    highlight.stroke()
}

image.unlockFocus()

guard let data = image.tiffRepresentation,
      let bitmap = NSBitmapImageRep(data: data),
      let png = bitmap.representation(using: .png, properties: [:]) else {
    fatalError("无法生成图标")
}
try png.write(to: output)
