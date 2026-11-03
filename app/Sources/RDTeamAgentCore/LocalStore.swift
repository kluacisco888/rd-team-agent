import Foundation

/// What this Mac remembers about one session. The server keeps the messages;
/// it has no titles, and the app needs the task IDs to resume polling.
public struct SessionRecord: Codable, Sendable, Equatable {
    public var title: String?
    public var createdAt: Date
    public var taskIds: [String]
    /// Task ID to the path of the PRD saved on this Mac.
    public var savedFiles: [String: String]

    public init(title: String? = nil, createdAt: Date = Date(), taskIds: [String] = [], savedFiles: [String: String] = [:]) {
        self.title = title
        self.createdAt = createdAt
        self.taskIds = taskIds
        self.savedFiles = savedFiles
    }
}

public struct LocalIndex: Codable, Sendable, Equatable {
    public var sessions: [String: SessionRecord]
    /// Runtime session ID for requests that belong to no conversation (session
    /// list, long-term memory), kept stable so they reuse one Runtime session.
    public var utilitySessionId: String?

    public init(sessions: [String: SessionRecord] = [:], utilitySessionId: String? = nil) {
        self.sessions = sessions
        self.utilitySessionId = utilitySessionId
    }

    /// A short title for the session list and PRD file names: a quoted feature
    /// name such as 「CSV 批量导入用户」 if the first message has one, otherwise
    /// its first clause, at most 20 characters.
    public static func title(fromMessage message: String) -> String {
        let text = message.trimmingCharacters(in: .whitespacesAndNewlines)
        if let quoted = text.firstMatch(of: #/[「“"《]([^」”"》\n]{2,30})[」”"》]/#) {
            let name = String(quoted.1).trimmingCharacters(in: .whitespaces)
            if !name.isEmpty { return name }
        }
        let clause = text.split(whereSeparator: { "，,。！？!?；;\n".contains($0) }).first.map(String.init) ?? text
        let trimmed = clause.trimmingCharacters(in: .whitespaces)
        return trimmed.count > 20 ? String(trimmed.prefix(20)) + "…" : trimmed
    }
}

/// One JSON file per signed-in user under Application Support.
public struct LocalIndexStore: Sendable {
    public let fileURL: URL

    public init(userId: String, baseFolder: URL = LocalIndexStore.defaultBaseFolder()) {
        let safe = userId.unicodeScalars.filter {
            CharacterSet.alphanumerics.contains($0) || $0 == "-" || $0 == "_"
        }
        let folderName = safe.isEmpty ? "unknown-user" : String(String.UnicodeScalarView(safe))
        fileURL = baseFolder.appendingPathComponent(folderName, isDirectory: true)
            .appendingPathComponent("sessions.json")
    }

    public static func defaultBaseFolder() -> URL {
        let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? URL(fileURLWithPath: NSHomeDirectory()).appendingPathComponent("Library/Application Support")
        return support.appendingPathComponent("RDTeamAgent", isDirectory: true)
    }

    /// A missing or unreadable file is an empty index.
    public func load() -> LocalIndex {
        guard let data = try? Data(contentsOf: fileURL) else { return LocalIndex() }
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return (try? decoder.decode(LocalIndex.self, from: data)) ?? LocalIndex()
    }

    public func save(_ index: LocalIndex) throws {
        try FileManager.default.createDirectory(at: fileURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        try encoder.encode(index).write(to: fileURL, options: .atomic)
    }
}

/// Writes PRDs as .md files without ever overwriting one.
public enum PRDFileWriter {
    public static func defaultFolder() -> URL {
        let documents = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first
            ?? URL(fileURLWithPath: NSHomeDirectory()).appendingPathComponent("Documents")
        return documents.appendingPathComponent("研发团队 Agent", isDirectory: true)
    }

    /// "<session title>-<first 8 hex of the task ID>.md"
    public static func fileName(title: String?, taskId: String) -> String {
        let forbidden = CharacterSet(charactersIn: "/\\:*?\"<>|").union(.controlCharacters).union(.newlines)
        let cleaned = (title ?? "").unicodeScalars.map { forbidden.contains($0) ? " " : String($0) }.joined()
            .replacingOccurrences(of: "…", with: "")
            .trimmingCharacters(in: .whitespaces)
        let base = cleaned.isEmpty ? "PRD" : String(cleaned.prefix(40)).trimmingCharacters(in: .whitespaces)
        let short = taskId.hasPrefix("task-") ? String(taskId.dropFirst(5).prefix(8)) : String(taskId.prefix(8))
        return "\(base)-\(short).md"
    }

    /// Creates the folder if needed; adds "-2", "-3"... rather than replace a file.
    public static func write(markdown: String, folder: URL, fileName: String) throws -> URL {
        let fileManager = FileManager.default
        try fileManager.createDirectory(at: folder, withIntermediateDirectories: true)
        let stem = (fileName as NSString).deletingPathExtension
        let ext = (fileName as NSString).pathExtension
        var candidate = folder.appendingPathComponent(fileName)
        var counter = 2
        while fileManager.fileExists(atPath: candidate.path) {
            candidate = folder.appendingPathComponent("\(stem)-\(counter).\(ext)")
            counter += 1
        }
        try Data(markdown.utf8).write(to: candidate, options: .withoutOverwriting)
        return candidate
    }
}
