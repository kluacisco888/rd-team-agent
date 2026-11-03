import Foundation

/// Where the app signs in and which Runtime it calls. Holds no secrets.
public struct AppConfig: Codable, Sendable, Equatable {
    public var runtimeArn: String
    public var qualifier: String
    /// Cognito managed login domain, e.g. https://example.auth.us-east-1.amazoncognito.com
    public var loginDomain: String
    public var clientId: String

    public static let callbackScheme = "rdteamagent"
    public static let redirectURI = "rdteamagent://callback"
    public static let scopes = "openid email profile"

    public init(runtimeArn: String, qualifier: String, loginDomain: String, clientId: String) {
        self.runtimeArn = runtimeArn
        self.qualifier = qualifier
        self.loginDomain = loginDomain
        self.clientId = clientId
    }

    /// The us-east-1 demo deployment this app was built against.
    public static let defaults = AppConfig(
        runtimeArn: "arn:aws:bedrock-agentcore:us-east-1:954976326201:runtime/rd_team_agent-QTcxE482tI",
        qualifier: "DEFAULT",
        loginDomain: "https://rd-team-agent-954976326201.auth.us-east-1.amazoncognito.com",
        clientId: "5d06ul864pk4qems0lia46up94"
    )

    public func trimmed() -> AppConfig {
        AppConfig(
            runtimeArn: runtimeArn.trimmingCharacters(in: .whitespacesAndNewlines),
            qualifier: qualifier.trimmingCharacters(in: .whitespacesAndNewlines),
            loginDomain: loginDomain.trimmingCharacters(in: .whitespacesAndNewlines)
                .trimmingCharacters(in: CharacterSet(charactersIn: "/")),
            clientId: clientId.trimmingCharacters(in: .whitespacesAndNewlines)
        )
    }

    /// The Runtime's region, read from its ARN.
    public var region: String? {
        let parts = runtimeArn.split(separator: ":", omittingEmptySubsequences: false)
        guard parts.count == 6, parts[2] == "bedrock-agentcore", !parts[3].isEmpty else { return nil }
        return String(parts[3])
    }

    /// Problems a person must fix before the app can work, in Chinese.
    public func validationErrors() -> [String] {
        var errors: [String] = []
        if runtimeArn.range(
            of: #"^arn:aws[a-z-]*:bedrock-agentcore:[a-z0-9-]+:\d{12}:runtime/[A-Za-z0-9_-]+$"#,
            options: .regularExpression
        ) == nil {
            errors.append("Runtime ARN 格式不对，应形如 arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/名称-ID。")
        }
        if qualifier.range(of: #"^[A-Za-z0-9_-]{1,64}$"#, options: .regularExpression) == nil {
            errors.append("Qualifier 只能包含字母、数字、下划线和短横线，通常填 DEFAULT。")
        }
        if loginBaseURL == nil {
            errors.append("登录域名应是 https:// 开头、不带路径的地址。")
        }
        if clientId.range(of: #"^[A-Za-z0-9]{1,128}$"#, options: .regularExpression) == nil {
            errors.append("App client ID 只能包含字母和数字。")
        }
        return errors
    }

    public var loginBaseURL: URL? {
        guard let components = URLComponents(string: loginDomain),
              components.scheme == "https",
              let host = components.host, !host.isEmpty,
              components.path.isEmpty || components.path == "/",
              components.query == nil
        else { return nil }
        return URL(string: "https://\(host)")
    }

    public var invocationsURL: URL? {
        guard let region else { return nil }
        let arn = runtimeArn.addingPercentEncoding(withAllowedCharacters: FormEncoding.unreserved) ?? ""
        let qualifier = qualifier.addingPercentEncoding(withAllowedCharacters: FormEncoding.unreserved) ?? ""
        return URL(string: "https://bedrock-agentcore.\(region).amazonaws.com/runtimes/\(arn)/invocations?qualifier=\(qualifier)")
    }
}

enum FormEncoding {
    /// RFC 3986 unreserved characters; everything else is percent-encoded.
    static let unreserved = CharacterSet(
        charactersIn: "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
    )

    static func encode(_ pairs: [(String, String)]) -> String {
        pairs.map { key, value in
            let k = key.addingPercentEncoding(withAllowedCharacters: unreserved) ?? key
            let v = value.addingPercentEncoding(withAllowedCharacters: unreserved) ?? value
            return "\(k)=\(v)"
        }.joined(separator: "&")
    }
}
