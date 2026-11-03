import RDTeamAgentCore
import SwiftUI

/// Renders Markdown blocks natively: headings, paragraphs, lists, tables,
/// code, quotes and rules. Inline syntax goes through AttributedString.
struct MarkdownView: View {
    let blocks: [MarkdownBlock]

    init(markdown: String) {
        blocks = MarkdownParser.parse(markdown)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            ForEach(Array(blocks.enumerated()), id: \.offset) { _, block in
                BlockView(block: block)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

private struct BlockView: View {
    let block: MarkdownBlock

    var body: some View {
        switch block {
        case .heading(let level, let text):
            InlineText(text)
                .font(Self.headingFont(level))
                .padding(.top, level <= 2 ? 10 : 4)
        case .paragraph(let text):
            InlineText(text)
        case .listItem(let marker, let indent, let text):
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Text(marker).foregroundStyle(.secondary).frame(minWidth: 18, alignment: .trailing)
                InlineText(text)
            }
            .padding(.leading, CGFloat(indent) * 18)
        case .table(let header, let rows):
            TableBlock(header: header, rows: rows)
        case .code(let code):
            ScrollView(.horizontal) {
                Text(code)
                    .font(.system(.callout, design: .monospaced))
                    .padding(10)
            }
            .background(RoundedRectangle(cornerRadius: 6).fill(Color.secondary.opacity(0.1)))
        case .quote(let text):
            HStack(alignment: .top, spacing: 8) {
                Rectangle().fill(Color.secondary.opacity(0.5)).frame(width: 3)
                InlineText(text).foregroundStyle(.secondary)
            }
        case .rule:
            Divider()
        }
    }

    static func headingFont(_ level: Int) -> Font {
        switch level {
        case 1: .title.bold()
        case 2: .title2.bold()
        case 3: .title3.bold()
        default: .headline
        }
    }
}

private struct TableBlock: View {
    let header: [String]
    let rows: [[String]]

    private var columns: Int { max(header.count, rows.map(\.count).max() ?? 0) }

    var body: some View {
        ScrollView(.horizontal) {
            Grid(alignment: .topLeading, horizontalSpacing: 0, verticalSpacing: 0) {
                GridRow {
                    ForEach(0..<columns, id: \.self) { column in
                        cell(column < header.count ? header[column] : "", bold: true)
                    }
                }
                .background(Color.secondary.opacity(0.12))
                ForEach(Array(rows.enumerated()), id: \.offset) { _, row in
                    GridRow {
                        ForEach(0..<columns, id: \.self) { column in
                            cell(column < row.count ? row[column] : "", bold: false)
                        }
                    }
                }
            }
            .overlay(Rectangle().stroke(Color.secondary.opacity(0.35), lineWidth: 0.5))
        }
    }

    private func cell(_ text: String, bold: Bool) -> some View {
        InlineText(text)
            .fontWeight(bold ? .semibold : .regular)
            .fixedSize(horizontal: false, vertical: true)
            .frame(minWidth: 60, maxWidth: 360, alignment: .topLeading)
            .padding(.horizontal, 8)
            .padding(.vertical, 5)
            .overlay(Rectangle().stroke(Color.secondary.opacity(0.25), lineWidth: 0.5))
    }
}

/// Bold, italics, code spans and links inside one block.
struct InlineText: View {
    let text: String

    init(_ text: String) {
        self.text = text
    }

    var body: some View {
        Text(Self.attributed(text))
    }

    static func attributed(_ text: String) -> AttributedString {
        let options = AttributedString.MarkdownParsingOptions(interpretedSyntax: .inlineOnlyPreservingWhitespace)
        return (try? AttributedString(markdown: text, options: options)) ?? AttributedString(text)
    }
}
