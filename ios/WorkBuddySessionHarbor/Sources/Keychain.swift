import Foundation
import Security

enum Keychain {
    private static let service = "cn.workbuddy.sessionharbor.ios"
    private static let account = "lan-access-token"

    static var value: String? {
        get {
            let query: [CFString: Any] = [
                kSecClass: kSecClassGenericPassword,
                kSecAttrService: service,
                kSecAttrAccount: account,
                kSecReturnData: true,
            ]
            var result: CFTypeRef?
            guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess,
                  let data = result as? Data else { return nil }
            return String(data: data, encoding: .utf8)
        }
        set {
            let query: [CFString: Any] = [
                kSecClass: kSecClassGenericPassword,
                kSecAttrService: service,
                kSecAttrAccount: account,
            ]
            guard let newValue else {
                SecItemDelete(query as CFDictionary)
                return
            }
            let attributes = [kSecValueData: Data(newValue.utf8)] as CFDictionary
            if SecItemUpdate(query as CFDictionary, attributes) == errSecItemNotFound {
                var add = query
                add[kSecValueData] = Data(newValue.utf8)
                SecItemAdd(add as CFDictionary, nil)
            }
        }
    }
}
