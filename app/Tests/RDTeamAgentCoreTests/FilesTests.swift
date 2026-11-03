import CoreGraphics
import CoreText
import Foundation
import Testing
@testable import RDTeamAgentCore

private func temporaryFolder() throws -> URL {
    let url = FileManager.default.temporaryDirectory
        .appendingPathComponent("rd-team-agent-tests-\(UUID().uuidString)", isDirectory: true)
    try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
    return url
}

/// A one-page PDF; with `text` nil the page is blank, like a scan with no text layer.
private func makePDF(in folder: URL, name: String, text: String?) throws -> URL {
    let url = folder.appendingPathComponent(name)
    var box = CGRect(x: 0, y: 0, width: 612, height: 792)
    let context = try #require(CGContext(url as CFURL, mediaBox: &box, nil))
    context.beginPDFPage(nil)
    if let text {
        let font = CTFontCreateWithName("Helvetica" as CFString, 14, nil)
        let attributed = NSAttributedString(string: text, attributes: [NSAttributedString.Key(kCTFontAttributeName as String): font])
        context.textPosition = CGPoint(x: 72, y: 700)
        CTLineDraw(CTLineCreateWithAttributedString(attributed), context)
    }
    context.endPDFPage()
    context.closePDF()
    return url
}

@Suite struct AttachmentTests {
    @Test func markdownIsReadAsText() throws {
        let folder = try temporaryFolder()
        let url = folder.appendingPathComponent("需求.md")
        try "# 标题\n内容".write(to: url, atomically: true, encoding: .utf8)
        let attachment = try AttachmentLoader.load(url)
        #expect(attachment.name == "需求.md")
        #expect(attachment.kind == .markdown)
        #expect(attachment.document == TaskDocument(path: "需求.md", content: "# 标题\n内容"))
    }

    @Test func blankUnsupportedAndBinaryFilesAreRejected() throws {
        let folder = try temporaryFolder()
        let blank = folder.appendingPathComponent("blank.md")
        try " \n".write(to: blank, atomically: true, encoding: .utf8)
        #expect(throws: AttachmentError.blank("blank.md")) { try AttachmentLoader.load(blank) }

        let text = folder.appendingPathComponent("notes.txt")
        try "x".write(to: text, atomically: true, encoding: .utf8)
        #expect(throws: AttachmentError.unsupportedType("notes.txt")) { try AttachmentLoader.load(text) }

        let binary = folder.appendingPathComponent("bad.md")
        try Data([0xFF, 0xFE, 0x00, 0xC3]).write(to: binary)
        #expect(throws: AttachmentError.unreadable("bad.md")) { try AttachmentLoader.load(binary) }
    }

    @Test func pdfTextIsExtracted() throws {
        let folder = try temporaryFolder()
        let url = try makePDF(in: folder, name: "spec.pdf", text: "Batch import users from CSV")
        let attachment = try AttachmentLoader.load(url)
        #expect(attachment.kind == .pdf)
        #expect(attachment.pageCount == 1)
        #expect(attachment.content.contains("Batch import users"))
        #expect(attachment.summary.hasPrefix("1 页"))
    }

    @Test func pdfWithoutTextIsRejected() throws {
        let folder = try temporaryFolder()
        let url = try makePDF(in: folder, name: "scan.pdf", text: nil)
        #expect(throws: AttachmentError.noTextInPDF("scan.pdf")) { try AttachmentLoader.load(url) }

        let fake = folder.appendingPathComponent("fake.pdf")
        try "not a pdf".write(to: fake, atomically: true, encoding: .utf8)
        #expect(throws: AttachmentError.unreadable("fake.pdf")) { try AttachmentLoader.load(fake) }
    }

    @Test func mergeEnforcesNamesAndLimits() throws {
        let one = Attachment(name: "a.md", kind: .markdown, content: "x", pageCount: nil)
        let merged = try AttachmentLoader.merge([], adding: [one])
        #expect(merged == [one])
        #expect(throws: AttachmentError.duplicateName("a.md")) { try AttachmentLoader.merge(merged, adding: [one]) }

        let many = (0..<21).map { Attachment(name: "\($0).md", kind: .markdown, content: "x", pageCount: nil) }
        #expect(throws: AttachmentError.tooMany(21)) { try AttachmentLoader.merge([], adding: many) }

        let big = Attachment(name: "big.md", kind: .markdown, content: String(repeating: "x", count: 2_000_001), pageCount: nil)
        #expect(throws: AttachmentError.tooLarge(2_000_001)) { try AttachmentLoader.merge([], adding: [big]) }
    }

    @Test func namesFollowTheRuntimeRule() {
        #expect(AttachmentLoader.isSupportedName("现有用户管理说明.pdf"))
        #expect(!AttachmentLoader.isSupportedName(""))
        #expect(!AttachmentLoader.isSupportedName(".."))
        #expect(!AttachmentLoader.isSupportedName("a\\b.md"))
        #expect(!AttachmentLoader.isSupportedName(String(repeating: "a", count: 513)))
    }
}

@Suite struct LocalStoreTests {
    @Test func indexRoundTripsPerUser() throws {
        let base = try temporaryFolder()
        let store = LocalIndexStore(userId: "a4b894d8-a011/../x", baseFolder: base)
        #expect(store.fileURL.deletingLastPathComponent().lastPathComponent == "a4b894d8-a011x")
        #expect(store.load() == LocalIndex())

        var index = LocalIndex()
        index.sessions["s1"] = SessionRecord(title: "CSV 导入", createdAt: Date(timeIntervalSince1970: 1_700_000_000),
                                             taskIds: ["task-1"], savedFiles: ["task-1": "/tmp/a.md"])
        try store.save(index)
        #expect(store.load() == index)
    }

    @Test func titleIsTheShortenedFirstLine() {
        #expect(LocalIndex.title(fromMessage: "写一份 PRD\n细节") == "写一份 PRD")
        let long = String(repeating: "长", count: 40)
        #expect(LocalIndex.title(fromMessage: long) == String(repeating: "长", count: 30) + "…")
    }

    @Test func prdFileNamesAreSafeAndNeverOverwrite() throws {
        let id = "task-e52f687514e443f99ee1ec6f2164d01c"
        #expect(PRDFileWriter.fileName(title: "CSV/批量:导入…", taskId: id) == "CSV 批量 导入-e52f6875.md")
        #expect(PRDFileWriter.fileName(title: nil, taskId: id) == "PRD-e52f6875.md")

        let folder = try temporaryFolder().appendingPathComponent("新目录", isDirectory: true)
        let first = try PRDFileWriter.write(markdown: "one", folder: folder, fileName: "a.md")
        let second = try PRDFileWriter.write(markdown: "two", folder: folder, fileName: "a.md")
        #expect(first.lastPathComponent == "a.md")
        #expect(second.lastPathComponent == "a-2.md")
        #expect(try String(contentsOf: first, encoding: .utf8) == "one")
        #expect(try String(contentsOf: second, encoding: .utf8) == "two")
    }
}

@Suite struct MarkdownParserTests {
    @Test func blocks() {
        let markdown = """
        # PRD 标题

        第一段
        第二行

        ## 1. Scope
        - item one
          continued
        - [ ] open question
        1. first
        2) second

        | Field | Rule |
        | --- | :---: |
        | email | must be unique \\| case-insensitive |

        > quoted

        ---
        ```json
        {"a": 1}
        ```
        """
        #expect(MarkdownParser.parse(markdown) == [
            .heading(level: 1, text: "PRD 标题"),
            .paragraph("第一段\n第二行"),
            .heading(level: 2, text: "1. Scope"),
            .listItem(marker: "•", indent: 0, text: "item one continued"),
            .listItem(marker: "☐", indent: 0, text: "open question"),
            .listItem(marker: "1.", indent: 0, text: "first"),
            .listItem(marker: "2.", indent: 0, text: "second"),
            .table(header: ["Field", "Rule"], rows: [["email", "must be unique | case-insensitive"]]),
            .quote("quoted"),
            .rule,
            .code("{\"a\": 1}"),
        ])
    }

    @Test func notHeadingsOrTables() {
        #expect(MarkdownParser.parse("#hashtag") == [.paragraph("#hashtag")])
        #expect(MarkdownParser.parse("a | b\nnext") == [.paragraph("a | b\nnext")])
        #expect(MarkdownParser.parse("  - nested") == [.listItem(marker: "•", indent: 1, text: "nested")])
    }
}
