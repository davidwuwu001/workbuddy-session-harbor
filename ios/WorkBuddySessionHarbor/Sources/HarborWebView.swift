import SwiftUI
import WebKit

struct HarborWebView: UIViewRepresentable {
    let url: URL
    let accessToken: String

    func makeUIView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        let token = String(data: try! JSONSerialization.data(withJSONObject: [accessToken]), encoding: .utf8)!
        configuration.userContentController.addUserScript(WKUserScript(
            source: """
            const originalFetch = window.fetch;
            window.fetch = (input, init = {}) => {
              const headers = new Headers(init.headers || (input instanceof Request ? input.headers : {}));
              headers.set('X-WorkBuddy-Access-Token', (token)[0]);
              return originalFetch(input, {...init, headers});
            };
            """,
            injectionTime: .atDocumentStart,
            forMainFrameOnly: false
        ))
        return WKWebView(frame: .zero, configuration: configuration)
    }

    func updateUIView(_ webView: WKWebView, context: Context) {
        guard webView.url != url else { return }
        var request = URLRequest(url: url)
        request.setValue(accessToken, forHTTPHeaderField: "X-WorkBuddy-Access-Token")
        webView.load(request)
    }
}
