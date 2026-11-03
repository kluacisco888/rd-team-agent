import Foundation

public enum TaskState: String, Sendable, Equatable {
    case running, succeeded, failed, unknown
}

/// A PRD task as the orchestrator stores it (`status.json` in the results bucket).
public struct TaskStatus: Sendable, Equatable, Identifiable {
    public var id: String { taskId }
    public let taskId: String
    public let state: TaskState
    public let startedAt: Date?
    public let finishedAt: Date?
    public let requirement: String
    public let documents: [String]
    public let prdBytes: Int?
    public let validationPassed: Bool?
    public let errorCode: String?
    public let errorMessage: String?
    /// The server kept the requirement and documents, so the task can be retried.
    public let inputSaved: Bool
    public let retriedFrom: String?
    public let retriedAs: String?

    public init(
        taskId: String, state: TaskState, startedAt: Date? = nil, finishedAt: Date? = nil,
        requirement: String = "", documents: [String] = [], prdBytes: Int? = nil,
        validationPassed: Bool? = nil, errorCode: String? = nil, errorMessage: String? = nil,
        inputSaved: Bool = false, retriedFrom: String? = nil, retriedAs: String? = nil
    ) {
        self.taskId = taskId
        self.state = state
        self.startedAt = startedAt
        self.finishedAt = finishedAt
        self.requirement = requirement
        self.documents = documents
        self.prdBytes = prdBytes
        self.validationPassed = validationPassed
        self.errorCode = errorCode
        self.errorMessage = errorMessage
        self.inputSaved = inputSaved
        self.retriedFrom = retriedFrom
        self.retriedAs = retriedAs
    }

    static func parse(_ object: [String: Any]?) -> TaskStatus? {
        guard let object, let taskId = object["task_id"] as? String, !taskId.isEmpty else { return nil }
        let error = object["error"] as? [String: Any]
        return TaskStatus(
            taskId: taskId,
            state: TaskState(rawValue: object["state"] as? String ?? "") ?? .unknown,
            startedAt: ISODate.parse(object["started_at"] as? String),
            finishedAt: ISODate.parse(object["finished_at"] as? String),
            requirement: object["requirement"] as? String ?? "",
            documents: object["documents"] as? [String] ?? [],
            prdBytes: (object["prd_bytes"] as? NSNumber)?.intValue,
            validationPassed: object["validation_passed"] as? Bool,
            errorCode: error?["code"] as? String,
            errorMessage: error?["message"] as? String,
            inputSaved: object["input_saved"] as? Bool ?? false,
            retriedFrom: object["retried_from"] as? String,
            retriedAs: object["retried_as"] as? String
        )
    }

    public enum RetryOption: Sendable, Equatable {
        /// Show the retry button.
        case retry
        /// Created before the server saved inputs: attach again and ask in chat.
        case reattach
        /// Not failed, already retried, or the same input would fail the same way.
        case none
    }

    /// Codes for which the server refuses a retry.
    public static let notRetryableCodes: Set<String> = ["INVALID_DOCUMENTS", "NEEDS_CLARIFICATION"]

    public var retryOption: RetryOption {
        guard state == .failed, retriedAs == nil else { return .none }
        if let errorCode, Self.notRetryableCodes.contains(errorCode) { return .none }
        return inputSaved ? .retry : .reattach
    }

    /// What went wrong, in Chinese, for a failed task.
    public var failureDescription: String? {
        guard state == .failed else { return nil }
        let detail = errorMessage.map { "：\($0)" } ?? ""
        switch errorCode {
        // What to do next is shown under the text: a retry button or a hint.
        case "GENERATION_FAILED": return "生成失败。"
        case "INVALID_DOCUMENTS": return "附件不符合要求\(detail)"
        case "DOCUMENT_INDEXING_TIMEOUT": return "附件建索引超时。"
        case "DOCUMENT_INDEXING_FAILED": return "附件建索引失败\(detail)"
        case "JOB_LOST": return "任务超过 45 分钟没有结果，已视为中断。"
        case "NEEDS_CLARIFICATION": return "需求还缺信息，请在对话里补充后再让我生成。"
        case let code?: return "生成没有完成（\(code)）\(detail)"
        case nil: return "生成失败。"
        }
    }
}

public struct SessionSummary: Sendable, Equatable {
    public let sessionId: String
    public let createdAt: Date?
}

public struct HistoryMessage: Sendable, Equatable {
    public let role: String
    public let text: String
    public let at: Date?
}

public struct MemoryRecord: Sendable, Equatable {
    public let kind: String
    public let text: String
    public let createdAt: Date?

    public var kindLabel: String {
        switch kind {
        case "preference": "偏好"
        case "fact": "事实"
        case "summary": "摘要"
        default: kind
        }
    }

    /// The record as a person should read it; `text` keeps the raw form.
    public var displayText: String { MemoryText.clean(text) }
}

/// AgentCore's built-in strategies store records in machine formats: user
/// preferences as JSON, summaries as `<topic name="…">` blocks of Markdown.
public enum MemoryText {
    public static func clean(_ raw: String, limit: Int = 280) -> String {
        var text = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        if text.hasPrefix("{"),
           let object = try? JSONSerialization.jsonObject(with: Data(text.utf8)) as? [String: Any],
           let preference = object["preference"] as? String {
            text = preference
        }
        text = text.replacing(#/<topic name="([^"]*)">/#) { "\($0.1)：" }
            .replacingOccurrences(of: "</topic>", with: "")
        let lines = text.components(separatedBy: .newlines).compactMap { line -> String? in
            var line = line.trimmingCharacters(in: .whitespaces)
            line = line.replacing(#/^#{1,6}\s*/#, with: "")
            line = line.replacing(#/^[-*+]\s+/#, with: "")
            line = line.replacingOccurrences(of: "**", with: "").replacingOccurrences(of: "`", with: "")
            return line.isEmpty ? nil : line
        }
        text = lines.joined(separator: " ")
        text = text.replacing(#/\s{2,}/#, with: " ").replacingOccurrences(of: "： ", with: "：")
        return text.count > limit ? String(text.prefix(limit)) + "…" : text
    }
}

public struct ServerError: Sendable, Equatable, LocalizedError {
    public let code: String
    public let message: String

    public var errorDescription: String? {
        switch code {
        case "UNAUTHENTICATED": "登录已失效，请重新登录。"
        case "INVALID_DOCUMENTS": "附件不符合要求：\(message)"
        case "INTERNAL_ERROR": "服务端出错了，请稍后再试。"
        case "NOT_FOUND": "没有找到：\(message)"
        case "TASK_RUNNING": "这个会话里还有 PRD 正在生成，等它结束后再删。"
        case "NOT_RETRYABLE": "这个任务现在不能重试，请重新载入会话后再看。"
        default: "\(code)：\(message)"
        }
    }
}

/// One event of the orchestrator's `text/event-stream` reply.
public enum ServerEvent: Sendable, Equatable {
    case started
    case text(String)
    case taskStarted(TaskStatus)
    case done
    case error(ServerError)
    /// `deletedIds`: sessions this user deleted, possibly on another device.
    case sessions([SessionSummary], deletedIds: [String] = [])
    case history([HistoryMessage])
    case memories([MemoryRecord])
    case taskStatus(TaskStatus)
    case taskResult(TaskStatus, markdown: String)
    case sessionDeleted(String)
}

public enum SSEParser {
    /// Parses one stream line; nil for blank lines, comments, other fields and
    /// event types this app does not know.
    public static func event(fromLine line: String) throws -> ServerEvent? {
        let trimmed = line.trimmingCharacters(in: .whitespacesAndNewlines)
        guard trimmed.hasPrefix("data:") else { return nil }
        let body = trimmed.dropFirst("data:".count).trimmingCharacters(in: .whitespaces)
        guard !body.isEmpty else { return nil }
        var value = try JSONSerialization.jsonObject(with: Data(body.utf8), options: [.fragmentsAllowed])
        // Some SDK paths encode the event a second time as a JSON string.
        if let text = value as? String {
            value = try JSONSerialization.jsonObject(with: Data(text.utf8))
        }
        guard let object = value as? [String: Any] else { throw RuntimeClientError.malformedEvent }
        return try event(from: object)
    }

    static func event(from object: [String: Any]) throws -> ServerEvent? {
        switch object["event"] as? String {
        case "started":
            return .started
        case "text":
            return .text(object["text"] as? String ?? "")
        case "task_started":
            guard let task = TaskStatus.parse(object["task"] as? [String: Any]) else { throw RuntimeClientError.malformedEvent }
            return .taskStarted(task)
        case "done":
            return .done
        case "error":
            let error = object["error"] as? [String: Any] ?? [:]
            return .error(ServerError(code: error["code"] as? String ?? "UNKNOWN", message: error["message"] as? String ?? ""))
        case "sessions":
            let items = object["sessions"] as? [[String: Any]] ?? []
            return .sessions(items.compactMap { item in
                guard let id = item["session_id"] as? String else { return nil }
                return SessionSummary(sessionId: id, createdAt: ISODate.parse(item["created_at"] as? String))
            }, deletedIds: object["deleted_session_ids"] as? [String] ?? [])
        case "history":
            let items = object["messages"] as? [[String: Any]] ?? []
            return .history(items.compactMap { item in
                guard let text = item["text"] as? String else { return nil }
                return HistoryMessage(role: item["role"] as? String ?? "", text: text, at: ISODate.parse(item["at"] as? String))
            })
        case "memories":
            let items = object["records"] as? [[String: Any]] ?? []
            return .memories(items.compactMap { item in
                guard let text = item["text"] as? String else { return nil }
                return MemoryRecord(kind: item["kind"] as? String ?? "", text: text, createdAt: ISODate.parse(item["created_at"] as? String))
            })
        case "task_status":
            guard let task = TaskStatus.parse(object["task"] as? [String: Any]) else { throw RuntimeClientError.malformedEvent }
            return .taskStatus(task)
        case "task_result":
            guard let task = TaskStatus.parse(object["task"] as? [String: Any]),
                  let markdown = object["prd_markdown"] as? String
            else { throw RuntimeClientError.malformedEvent }
            return .taskResult(task, markdown: markdown)
        case "session_deleted":
            guard let id = object["session_id"] as? String else { throw RuntimeClientError.malformedEvent }
            return .sessionDeleted(id)
        default:
            return nil
        }
    }
}

/// Parses Python `isoformat()` timestamps, which may carry microseconds.
public enum ISODate {
    public static func parse(_ text: String?) -> Date? {
        guard let text,
              let match = text.firstMatch(of: #/^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(\.\d+)?(Z|[+-]\d{2}:\d{2})?$/#)
        else { return nil }
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        guard let whole = formatter.date(from: String(match.1) + String(match.3 ?? "Z")) else { return nil }
        let fraction = match.2.flatMap { Double("0" + $0) } ?? 0
        return whole.addingTimeInterval(fraction)
    }
}

/// Request bodies the orchestrator accepts.
public enum RuntimePayload {
    public static func chat(message: String, documents: [TaskDocument]) throws -> Data {
        try encode([
            "action": "chat",
            "message": message,
            "documents": documents.map { ["path": $0.path, "content": $0.content] },
        ])
    }

    public static func action(_ action: String, taskId: String? = nil) throws -> Data {
        var body: [String: Any] = ["action": action]
        if let taskId { body["task_id"] = taskId }
        return try encode(body)
    }

    static func encode(_ object: [String: Any]) throws -> Data {
        try JSONSerialization.data(withJSONObject: object, options: [.sortedKeys, .withoutEscapingSlashes])
    }
}

/// Helpers for showing stored history the way it was sent and for finding tasks.
public enum Transcript {
    /// The orchestrator records a user turn as the message plus
    /// "\n\n[attachments: a.md, b.pdf]"; this splits the two apart again.
    public static func splitUserTurn(_ text: String) -> (message: String, attachments: [String]) {
        guard let range = text.range(of: "\n\n[attachments: ", options: .backwards), text.hasSuffix("]") else {
            return (text, [])
        }
        let list = text[range.upperBound..<text.index(before: text.endIndex)]
        let names = list.components(separatedBy: ", ").filter { !$0.isEmpty }
        return (String(text[..<range.lowerBound]), names)
    }

    /// The job runner's own one-line notes, shown in Chinese as status lines.
    public static func notice(fromAssistantText text: String) -> String? {
        guard let taskId = taskIds(in: text).first else { return nil }
        if text.hasPrefix("PRD generated (task ") {
            return "PRD 已生成（\(taskId)）"
        }
        if text.hasPrefix("PRD generation for task ") {
            return "PRD 生成没有完成（\(taskId)）"
        }
        let ids = taskIds(in: text)
        if text.hasPrefix("PRD task "), text.contains(" was retried as task "), ids.count == 2 {
            return "已用原来的需求和附件重试（\(ids[0]) → \(ids[1])）"
        }
        return nil
    }

    public static func taskIds(in text: String) -> [String] {
        var seen: [String] = []
        for match in text.matches(of: #/task-[0-9a-f]{32}/#) where !seen.contains(String(match.0)) {
            seen.append(String(match.0))
        }
        return seen
    }
}
