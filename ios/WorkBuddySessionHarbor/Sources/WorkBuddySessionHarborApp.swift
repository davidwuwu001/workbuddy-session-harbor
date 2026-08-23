import SwiftUI

@main
struct WorkBuddySessionHarborApp: App {
    var body: some Scene {
        WindowGroup {
            HarborView()
        }
    }
}

struct HarborView: View {
    @AppStorage("serverURL") private var savedServerURL = ""
    @State private var showingSettings = false
    @State private var reloadID = UUID()

    private var serverURL: String {
        savedServerURL.isEmpty ? defaultValue("WBDefaultServerURL") : savedServerURL
    }

    var body: some View {
        NavigationStack {
            if let url = URL(string: serverURL), !serverURL.contains("$(") {
                HarborWebView(url: url, accessToken: Keychain.value ?? defaultValue("WBDefaultAccessToken"))
                    .id(reloadID)
                    .navigationTitle("AI 账号坞")
                    .toolbar {
                        Button("连接设置", systemImage: "gear") { showingSettings = true }
                    }
            } else {
                VStack(spacing: 12) {
                    Image(systemName: "wifi.exclamationmark")
                        .font(.system(size: 42))
                    Text("尚未连接")
                        .font(.headline)
                    Text("请设置 Mac 的局域网地址和配对口令。")
                        .foregroundStyle(.secondary)
                }
                    .toolbar {
                        Button("连接设置", systemImage: "gear") { showingSettings = true }
                    }
            }
        }
        .sheet(isPresented: $showingSettings) {
            ConnectionSettings(serverURL: serverURL) {
                reloadID = UUID()
            }
        }
    }

    private func defaultValue(_ key: String) -> String {
        let value = Bundle.main.object(forInfoDictionaryKey: key) as? String ?? ""
        return value.contains("$(") ? "" : value
    }
}

private struct ConnectionSettings: View {
    @Environment(\.dismiss) private var dismiss
    @AppStorage("serverURL") private var savedServerURL = ""
    @State private var serverURL: String
    @State private var accessToken = Keychain.value ?? ""
    let onSave: () -> Void

    init(serverURL: String, onSave: @escaping () -> Void) {
        _serverURL = State(initialValue: serverURL)
        self.onSave = onSave
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("Mac 服务") {
                    TextField("http://你的Mac.local:7532", text: $serverURL)
                        .textInputAutocapitalization(.never)
                        .keyboardType(.URL)
                    SecureField("配对口令", text: $accessToken)
                        .textInputAutocapitalization(.never)
                }
                Section {
                    Text("服务和手机必须在同一局域网。口令仅保存到此设备钥匙串。")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            }
            .navigationTitle("连接设置")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("取消") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("保存") {
                        savedServerURL = serverURL.trimmingCharacters(in: .whitespacesAndNewlines)
                        Keychain.value = accessToken.trimmingCharacters(in: .whitespacesAndNewlines)
                        onSave()
                        dismiss()
                    }
                }
            }
        }
    }
}
