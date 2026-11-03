import Foundation
import Security

public enum KeychainError: LocalizedError, Equatable {
    case status(OSStatus)

    public var errorDescription: String? {
        switch self {
        case .status(let status):
            let detail = SecCopyErrorMessageString(status, nil) as String? ?? "OSStatus \(status)"
            return "钥匙串操作失败：\(detail)"
        }
    }
}

/// One generic-password item in the login keychain.
public struct KeychainStore: Sendable {
    public let service: String
    public let account: String
    public let label: String

    public init(service: String, account: String, label: String) {
        self.service = service
        self.account = account
        self.label = label
    }

    private var baseQuery: [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
    }

    public func save(_ data: Data) throws {
        let update: [String: Any] = [kSecValueData as String: data]
        let status = SecItemUpdate(baseQuery as CFDictionary, update as CFDictionary)
        if status == errSecItemNotFound {
            var add = baseQuery
            add[kSecValueData as String] = data
            add[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlocked
            add[kSecAttrLabel as String] = label
            let addStatus = SecItemAdd(add as CFDictionary, nil)
            guard addStatus == errSecSuccess else { throw KeychainError.status(addStatus) }
        } else if status != errSecSuccess {
            throw KeychainError.status(status)
        }
    }

    public func load() throws -> Data? {
        var query = baseQuery
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var item: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &item)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess, let data = item as? Data else { throw KeychainError.status(status) }
        return data
    }

    public func delete() throws {
        let status = SecItemDelete(baseQuery as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else { throw KeychainError.status(status) }
    }
}

/// The signed-in user's Cognito tokens, kept only in the keychain.
public struct TokenStore: Sendable {
    private let keychain: KeychainStore

    public init(keychain: KeychainStore = KeychainStore(
        service: "com.dengwj.rd-team-agent", account: "cognito-tokens", label: "研发团队 Agent 登录令牌"
    )) {
        self.keychain = keychain
    }

    public func save(_ tokens: TokenSet) throws {
        try keychain.save(JSONEncoder().encode(tokens))
    }

    /// A malformed item is treated as no sign-in rather than an error.
    public func load() throws -> TokenSet? {
        guard let data = try keychain.load() else { return nil }
        return try? JSONDecoder().decode(TokenSet.self, from: data)
    }

    public func delete() throws {
        try keychain.delete()
    }
}
