import Foundation

public enum RuntimeClientError: LocalizedError, Equatable {
    case invalidConfig
    case unauthorized
    case http(status: Int, message: String)
    case invalidResponse
    case malformedEvent
    case noEvents

    public var errorDescription: String? {
        switch self {
        case .invalidConfig: "连接设置不完整，请先在设置里改好。"
        case .unauthorized: "登录已失效，请重新登录。"
        case .http(let status, let message): "Runtime 返回 HTTP \(status)：\(message)"
        case .invalidResponse: "收到的不是 HTTP 响应。"
        case .malformedEvent: "Runtime 返回了无法解析的事件。"
        case .noEvents: "Runtime 没有返回任何内容。"
        }
    }
}

/// Calls the orchestrator Runtime with the user's Cognito access token.
public final class RuntimeClient: @unchecked Sendable {
    private let session: URLSession

    public init(session: URLSession = RuntimeClient.makeSession()) {
        self.session = session
    }

    /// A chat turn streams for as long as the model talks, so only an idle
    /// connection times out; the PRD itself runs in the background.
    public static func makeSession() -> URLSession {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 300
        configuration.timeoutIntervalForResource = 1800
        configuration.requestCachePolicy = .reloadIgnoringLocalCacheData
        configuration.urlCache = nil
        return URLSession(configuration: configuration)
    }

    /// 41 characters; the Runtime needs 33-100.
    public static func newSessionId() -> String {
        "rdta-\(UUID().uuidString.lowercased())"
    }

    public func stream(body: Data, config: AppConfig, sessionId: String, accessToken: String) -> AsyncThrowingStream<ServerEvent, Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    let request = try Self.makeRequest(body: body, config: config, sessionId: sessionId, accessToken: accessToken)
                    let (bytes, response) = try await self.session.bytes(for: request)
                    try await Self.requireSuccess(response, bytes: bytes)
                    for try await line in bytes.lines {
                        guard let event = try SSEParser.event(fromLine: line) else { continue }
                        if case .error(let error) = event, error.code == "UNAUTHENTICATED" {
                            throw RuntimeClientError.unauthorized
                        }
                        continuation.yield(event)
                    }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    /// A read-only request answered by a single event. Dropped connections and
    /// gateway errors are retried, since repeating a read changes nothing.
    public func request(body: Data, config: AppConfig, sessionId: String, accessToken: String, attempts: Int = 3) async throws -> ServerEvent {
        var attempt = 1
        while true {
            do {
                for try await event in stream(body: body, config: config, sessionId: sessionId, accessToken: accessToken) {
                    return event
                }
                throw RuntimeClientError.noEvents
            } catch let error where attempt < attempts && Self.isTransient(error) {
                try await Task.sleep(nanoseconds: UInt64(attempt) * 2_000_000_000)
                attempt += 1
            }
        }
    }

    static func makeRequest(body: Data, config: AppConfig, sessionId: String, accessToken: String) throws -> URLRequest {
        guard let url = config.invocationsURL else { throw RuntimeClientError.invalidConfig }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("text/event-stream", forHTTPHeaderField: "Accept")
        request.setValue("Bearer \(accessToken)", forHTTPHeaderField: "Authorization")
        request.setValue(sessionId, forHTTPHeaderField: "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id")
        request.httpBody = body
        return request
    }

    static func isTransient(_ error: Error) -> Bool {
        if let error = error as? URLError {
            return [.networkConnectionLost, .timedOut, .cannotConnectToHost, .cannotFindHost,
                    .secureConnectionFailed, .notConnectedToInternet, .dnsLookupFailed].contains(error.code)
        }
        if case RuntimeClientError.http(let status, _)? = error as? RuntimeClientError {
            return [429, 500, 502, 503, 504].contains(status)
        }
        return false
    }

    private static func requireSuccess(_ response: URLResponse, bytes: URLSession.AsyncBytes) async throws {
        guard let http = response as? HTTPURLResponse else { throw RuntimeClientError.invalidResponse }
        guard http.statusCode == 200 else {
            if http.statusCode == 401 { throw RuntimeClientError.unauthorized }
            var data = Data()
            for try await byte in bytes {
                data.append(byte)
                if data.count >= 16_384 { break }
            }
            throw RuntimeClientError.http(status: http.statusCode, message: errorMessage(data, response: http))
        }
    }

    /// Pulls a readable message out of an AWS JSON error body.
    static func errorMessage(_ data: Data, response: HTTPURLResponse) -> String {
        let type = (response.value(forHTTPHeaderField: "x-amzn-ErrorType") ?? "")
            .split(separator: ":").first.map(String.init) ?? ""
        if let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let message = (json["message"] ?? json["Message"]) as? String {
            return type.isEmpty ? message : "\(type)：\(message)"
        }
        let snippet = String(decoding: data.prefix(300), as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)
        return snippet.isEmpty ? HTTPURLResponse.localizedString(forStatusCode: response.statusCode) : snippet
    }
}
