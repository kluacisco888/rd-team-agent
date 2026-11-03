import Foundation
import PDFKit

/// One document sent with a chat turn: a name and its text.
public struct TaskDocument: Codable, Sendable, Equatable {
    public let path: String
    public let content: String

    public init(path: String, content: String) {
        self.path = path
        self.content = content
    }
}

public enum AttachmentKind: String, Sendable, Equatable {
    case markdown, pdf
}

/// A local file read into text. A PDF is sent as the text PDFKit extracts.
public struct Attachment: Sendable, Equatable, Identifiable {
    public var id: String { name }
    public let name: String
    public let kind: AttachmentKind
    public let content: String
    public let pageCount: Int?

    public var characterCount: Int { content.count }
    public var byteCount: Int { content.utf8.count }
    public var document: TaskDocument { TaskDocument(path: name, content: content) }

    public var summary: String {
        switch kind {
        case .markdown: "\(characterCount.formatted()) 字"
        case .pdf: "\(pageCount ?? 0) 页 · 提取 \(characterCount.formatted()) 字"
        }
    }
}

public enum AttachmentError: LocalizedError, Equatable {
    case unsupportedType(String)
    case unreadable(String)
    case blank(String)
    case lockedPDF(String)
    case noTextInPDF(String)
    case unsupportedName(String)
    case duplicateName(String)
    case tooMany(Int)
    case tooLarge(Int)

    public var errorDescription: String? {
        switch self {
        case .unsupportedType(let name): "「\(name)」不是 .md 或 PDF 文件。"
        case .unreadable(let name): "「\(name)」读不了：Markdown 要是 UTF-8 文本，PDF 要能正常打开。"
        case .blank(let name): "「\(name)」是空文件。"
        case .lockedPDF(let name): "「\(name)」有密码保护，先去掉密码再附加。"
        case .noTextInPDF(let name): "「\(name)」里提不出文字（可能是扫描件），请换成能选中文字的 PDF 或 .md。"
        case .unsupportedName(let name): "「\(name)」的文件名含不支持的字符或过长。"
        case .duplicateName(let name): "已经附加了同名文件「\(name)」。"
        case .tooMany(let count): "一共 \(count) 份附件，最多 \(AttachmentLoader.maxDocuments) 份。"
        case .tooLarge(let bytes):
            "附件文字合计 \(bytes / 1024) KB，超过 \(AttachmentLoader.maxTotalBytes / 1_000_000) MB 的上限。"
        }
    }
}

/// Reads attachments and applies the Runtime's limits (20 documents, 2 MB of
/// UTF-8 text in total, counted after PDF extraction) before any network call.
public enum AttachmentLoader {
    public static let maxDocuments = 20
    public static let maxTotalBytes = 2_000_000
    public static let markdownExtensions: Set<String> = ["md", "markdown"]
    static let maxNameLength = 512

    public static func load(_ url: URL) throws -> Attachment {
        let name = url.lastPathComponent
        guard isSupportedName(name) else { throw AttachmentError.unsupportedName(name) }
        let ext = url.pathExtension.lowercased()
        if markdownExtensions.contains(ext) {
            guard let text = try? String(contentsOf: url, encoding: .utf8) else { throw AttachmentError.unreadable(name) }
            guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { throw AttachmentError.blank(name) }
            return Attachment(name: name, kind: .markdown, content: text, pageCount: nil)
        }
        if ext == "pdf" {
            let (text, pages) = try pdfText(url, name: name)
            return Attachment(name: name, kind: .pdf, content: text, pageCount: pages)
        }
        throw AttachmentError.unsupportedType(name)
    }

    /// Adds files to what is already attached, all or nothing.
    public static func merge(_ existing: [Attachment], adding new: [Attachment]) throws -> [Attachment] {
        var result = existing
        for attachment in new {
            guard !result.contains(where: { $0.name == attachment.name }) else {
                throw AttachmentError.duplicateName(attachment.name)
            }
            result.append(attachment)
        }
        guard result.count <= maxDocuments else { throw AttachmentError.tooMany(result.count) }
        let bytes = result.reduce(0) { $0 + $1.byteCount }
        guard bytes <= maxTotalBytes else { throw AttachmentError.tooLarge(bytes) }
        return result
    }

    static func pdfText(_ url: URL, name: String) throws -> (String, Int) {
        guard let document = PDFDocument(url: url) else { throw AttachmentError.unreadable(name) }
        guard !document.isLocked else { throw AttachmentError.lockedPDF(name) }
        var pages: [String] = []
        for index in 0..<document.pageCount {
            if let text = document.page(at: index)?.string?.trimmingCharacters(in: .whitespacesAndNewlines), !text.isEmpty {
                pages.append(text)
            }
        }
        let text = pages.joined(separator: "\n\n")
        guard !text.isEmpty else { throw AttachmentError.noTextInPDF(name) }
        return (text, document.pageCount)
    }

    /// The Runtime wants a normalized relative POSIX path; a bare file name is one.
    static func isSupportedName(_ name: String) -> Bool {
        !name.isEmpty && name.count <= maxNameLength && name != "." && name != ".."
            && !name.contains("/") && !name.contains("\\")
    }
}
