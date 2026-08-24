import AppKit
import Foundation
import WebKit

final class AppDelegate: NSObject, NSApplicationDelegate, WKUIDelegate {
    private let serviceURL = URL(string: "http://127.0.0.1:7531/")!
    private var serviceProcess: Process?
    private var window: NSWindow!
    private var webView: WKWebView!

    func applicationDidFinishLaunching(_ notification: Notification) {
        webView = WKWebView(frame: .zero)
        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1040, height: 800),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        window.title = "AI 账号坞"
        window.contentView = webView
        webView.uiDelegate = self
        webView.autoresizingMask = [.width, .height]
        window.contentMinSize = NSSize(width: 360, height: 260)
        window.minSize = NSSize(width: 360, height: 320)
        window.center()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        connect(attempt: 0)
    }

    func applicationWillTerminate(_ notification: Notification) {
        serviceProcess?.terminate()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }

    private func connect(attempt: Int) {
        var request = URLRequest(url: serviceURL)
        request.timeoutInterval = 1
        URLSession.shared.dataTask(with: request) { [weak self] _, response, _ in
            DispatchQueue.main.async {
                guard let self else { return }
                if (response as? HTTPURLResponse)?.statusCode == 200 {
                    self.webView.load(URLRequest(url: self.serviceURL))
                    return
                }
                if attempt == 0 || self.serviceProcess?.isRunning == false {
                    self.startService()
                }
                if attempt < 30 {
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.35) {
                        self.connect(attempt: attempt + 1)
                    }
                } else {
                    self.showError("无法启动本地同步服务。请确认本机 Python 及 cryptography 可用。")
                }
            }
        }.resume()
    }

    private func startService() {
        if serviceProcess?.isRunning == true { return }
        serviceProcess = nil
        guard let script = Bundle.main.url(forResource: "workbuddy-sync-app", withExtension: "py") else {
            showError("应用资源不完整：未找到同步服务脚本。")
            return
        }
        let pythonPaths = ["/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3"]
        guard let python = pythonPaths.first(where: { FileManager.default.isExecutableFile(atPath: $0) }) else {
            showError("未找到 Python 3。")
            return
        }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: python)
        process.arguments = ["-B", script.path, "--port", "7531", "--no-browser"]
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        do {
            try process.run()
            serviceProcess = process
        } catch {
            showError("无法启动同步服务：\(error.localizedDescription)")
        }
    }

    private func showError(_ message: String) {
        webView.loadHTMLString("""
        <html><body style="font-family:-apple-system; background:#1a1a1a; color:#eee; padding:48px">
        <h2>AI 账号坞</h2><p>\(message)</p>
        </body></html>
        """, baseURL: nil)
    }

    func webView(
        _ webView: WKWebView,
        runJavaScriptConfirmPanelWithMessage message: String,
        initiatedByFrame frame: WKFrameInfo,
        completionHandler: @escaping (Bool) -> Void
    ) {
        let alert = NSAlert()
        alert.messageText = "确认操作"
        alert.informativeText = message
        alert.addButton(withTitle: "继续")
        alert.addButton(withTitle: "取消")
        alert.beginSheetModal(for: window) { response in
            completionHandler(response == .alertFirstButtonReturn)
        }
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
