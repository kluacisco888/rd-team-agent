import CryptoKit
import Foundation
import Security

/// One sign-in attempt's PKCE verifier, its S256 challenge and the CSRF state.
public struct PKCE: Sendable, Equatable {
    public let verifier: String
    public let challenge: String
    public let state: String

    public init(verifier: String, state: String) {
        self.verifier = verifier
        self.state = state
        self.challenge = Self.challenge(for: verifier)
    }

    public static func generate() -> PKCE {
        // 32 random bytes give a 43-character verifier, the RFC 7636 minimum.
        PKCE(verifier: randomURLSafe(byteCount: 32), state: randomURLSafe(byteCount: 16))
    }

    public static func challenge(for verifier: String) -> String {
        base64URL(Data(SHA256.hash(data: Data(verifier.utf8))))
    }

    static func randomURLSafe(byteCount: Int) -> String {
        var bytes = [UInt8](repeating: 0, count: byteCount)
        let status = SecRandomCopyBytes(kSecRandomDefault, byteCount, &bytes)
        precondition(status == errSecSuccess, "SecRandomCopyBytes failed: \(status)")
        return base64URL(Data(bytes))
    }

    public static func base64URL(_ data: Data) -> String {
        data.base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }
}

public struct TokenSet: Codable, Sendable, Equatable {
    public var accessToken: String
    public var idToken: String?
    public var refreshToken: String?
    public var expiresAt: Date

    public init(accessToken: String, idToken: String?, refreshToken: String?, expiresAt: Date) {
        self.accessToken = accessToken
        self.idToken = idToken
        self.refreshToken = refreshToken
        self.expiresAt = expiresAt
    }

    /// True when the access token expires within `margin` seconds.
    public func expiresSoon(now: Date = Date(), margin: TimeInterval = 300) -> Bool {
        expiresAt.timeIntervalSince(now) < margin
    }
}

/// Who is signed in, read from the access token for display and local files.
/// The Runtime verifies the token itself; nothing here is trusted for access.
public struct UserIdentity: Sendable, Equatable {
    public let userId: String
    public let username: String
}

public enum AuthError: LocalizedError, Equatable {
    case invalidConfig
    case cancelled
    case stateMismatch
    case loginPage(String)
    case missingCode
    case tokenRequest(status: Int, message: String)
    case invalidTokenResponse
    case sessionExpired

    public var errorDescription: String? {
        switch self {
        case .invalidConfig: "连接设置不完整，请先在设置里改好。"
        case .cancelled: "登录已取消。"
        case .stateMismatch: "登录回调和本次登录对不上，已拒绝。请重新登录。"
        case .loginPage(let message): "登录页返回错误：\(message)"
        case .missingCode: "登录回调里没有授权码。"
        case .tokenRequest(let status, let message): "换取令牌失败（HTTP \(status)）：\(message)"
        case .invalidTokenResponse: "登录服务返回的令牌格式不对。"
        case .sessionExpired: "登录已过期，请重新登录。"
        }
    }
}

/// Builds Cognito managed-login URLs and token requests (authorization code + PKCE).
public enum CognitoAuth {
    public static func authorizeURL(config: AppConfig, pkce: PKCE) -> URL? {
        guard let base = config.loginBaseURL else { return nil }
        let query = FormEncoding.encode([
            ("response_type", "code"),
            ("client_id", config.clientId),
            ("redirect_uri", AppConfig.redirectURI),
            ("scope", AppConfig.scopes),
            ("state", pkce.state),
            ("code_challenge", pkce.challenge),
            ("code_challenge_method", "S256"),
        ])
        return URL(string: "\(base.absoluteString)/oauth2/authorize?\(query)")
    }

    /// Reads the authorization code from the callback, refusing a foreign state.
    public static func authorizationCode(from callback: URL, expectedState: String) throws -> String {
        let items = URLComponents(url: callback, resolvingAgainstBaseURL: false)?.queryItems ?? []
        func value(_ name: String) -> String? { items.first { $0.name == name }?.value }
        guard value("state") == expectedState else { throw AuthError.stateMismatch }
        if let error = value("error") {
            throw AuthError.loginPage([error, value("error_description")].compactMap { $0 }.joined(separator: "："))
        }
        guard let code = value("code"), !code.isEmpty else { throw AuthError.missingCode }
        return code
    }

    public static func codeExchangeRequest(config: AppConfig, code: String, pkce: PKCE) -> URLRequest? {
        formRequest(config: config, path: "/oauth2/token", pairs: [
            ("grant_type", "authorization_code"),
            ("client_id", config.clientId),
            ("code", code),
            ("redirect_uri", AppConfig.redirectURI),
            ("code_verifier", pkce.verifier),
        ])
    }

    public static func refreshRequest(config: AppConfig, refreshToken: String) -> URLRequest? {
        formRequest(config: config, path: "/oauth2/token", pairs: [
            ("grant_type", "refresh_token"),
            ("client_id", config.clientId),
            ("refresh_token", refreshToken),
        ])
    }

    public static func revokeRequest(config: AppConfig, refreshToken: String) -> URLRequest? {
        formRequest(config: config, path: "/oauth2/revoke", pairs: [
            ("token", refreshToken),
            ("client_id", config.clientId),
        ])
    }

    static func formRequest(config: AppConfig, path: String, pairs: [(String, String)]) -> URLRequest? {
        guard let base = config.loginBaseURL, let url = URL(string: base.absoluteString + path) else { return nil }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
        request.httpBody = Data(FormEncoding.encode(pairs).utf8)
        request.timeoutInterval = 30
        return request
    }

    /// A refresh response carries no refresh token, so the previous one is kept.
    public static func parseTokenResponse(_ data: Data, now: Date = Date(), previousRefreshToken: String? = nil) throws -> TokenSet {
        guard let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let access = json["access_token"] as? String, !access.isEmpty,
              let expiresIn = (json["expires_in"] as? NSNumber)?.doubleValue
        else { throw AuthError.invalidTokenResponse }
        return TokenSet(
            accessToken: access,
            idToken: json["id_token"] as? String,
            refreshToken: (json["refresh_token"] as? String) ?? previousRefreshToken,
            expiresAt: now.addingTimeInterval(expiresIn)
        )
    }

    /// Decodes (without verifying) the access token's `sub` and `username`.
    public static func identity(fromAccessToken token: String) -> UserIdentity? {
        let parts = token.split(separator: ".")
        guard parts.count == 3, let payload = base64URLDecode(String(parts[1])),
              let json = try? JSONSerialization.jsonObject(with: payload) as? [String: Any],
              let sub = json["sub"] as? String, !sub.isEmpty
        else { return nil }
        let username = (json["username"] as? String) ?? (json["cognito:username"] as? String) ?? sub
        return UserIdentity(userId: sub, username: username)
    }

    static func base64URLDecode(_ text: String) -> Data? {
        var base64 = text.replacingOccurrences(of: "-", with: "+").replacingOccurrences(of: "_", with: "/")
        let remainder = base64.count % 4
        if remainder > 0 { base64 += String(repeating: "=", count: 4 - remainder) }
        return Data(base64Encoded: base64)
    }

    static func errorText(_ data: Data) -> String {
        if let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let error = json["error"] as? String {
            return [error, json["error_description"] as? String].compactMap { $0 }.joined(separator: "：")
        }
        let text = String(decoding: data.prefix(300), as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)
        return text.isEmpty ? "无内容" : text
    }
}

/// Sends the token requests. Cognito answers `invalid_grant` when a refresh
/// token has expired or was revoked; that becomes `AuthError.sessionExpired`.
public final class TokenClient: @unchecked Sendable {
    private let session: URLSession

    public init(session: URLSession = URLSession(configuration: .ephemeral)) {
        self.session = session
    }

    public func exchange(code: String, pkce: PKCE, config: AppConfig) async throws -> TokenSet {
        guard let request = CognitoAuth.codeExchangeRequest(config: config, code: code, pkce: pkce)
        else { throw AuthError.invalidConfig }
        let (data, status) = try await send(request)
        guard status == 200 else { throw AuthError.tokenRequest(status: status, message: CognitoAuth.errorText(data)) }
        return try CognitoAuth.parseTokenResponse(data)
    }

    public func refresh(_ tokens: TokenSet, config: AppConfig) async throws -> TokenSet {
        guard let refreshToken = tokens.refreshToken else { throw AuthError.sessionExpired }
        guard let request = CognitoAuth.refreshRequest(config: config, refreshToken: refreshToken)
        else { throw AuthError.invalidConfig }
        let (data, status) = try await send(request)
        if status == 400, CognitoAuth.errorText(data).hasPrefix("invalid_grant") { throw AuthError.sessionExpired }
        guard status == 200 else { throw AuthError.tokenRequest(status: status, message: CognitoAuth.errorText(data)) }
        return try CognitoAuth.parseTokenResponse(data, previousRefreshToken: refreshToken)
    }

    /// Best effort: signing out locally does not depend on it.
    public func revoke(refreshToken: String, config: AppConfig) async {
        guard let request = CognitoAuth.revokeRequest(config: config, refreshToken: refreshToken) else { return }
        _ = try? await send(request)
    }

    private func send(_ request: URLRequest) async throws -> (Data, Int) {
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else { throw AuthError.invalidTokenResponse }
        return (data, http.statusCode)
    }
}
