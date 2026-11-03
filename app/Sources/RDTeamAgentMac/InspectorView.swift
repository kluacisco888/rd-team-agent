import RDTeamAgentCore
import SwiftUI

/// Right-hand pane: this session's tasks, its attachments and the user's memory.
struct InspectorView: View {
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                TaskSection()
                AttachmentSection()
                MemorySection()
            }
            .padding(16)
        }
    }
}

private struct SectionHeader: View {
    let title: String
    var detail: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title).font(.headline)
            if let detail {
                Text(detail).font(.caption).foregroundStyle(.secondary)
            }
        }
    }
}

private struct TaskSection: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            SectionHeader(title: "本次任务")
            if model.currentTasks.isEmpty {
                Text("还没开始生成。需求说清楚后，进度和结果显示在这里。")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }
            ForEach(model.currentTasks) { task in
                TaskCard(task: task)
            }
        }
    }
}

private struct TaskCard: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.openWindow) private var openWindow
    let task: TaskStatus

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                badge
                Spacer()
                Text(String(task.taskId.prefix(13)) + "…")
                    .font(.caption.monospaced())
                    .foregroundStyle(.secondary)
                    .help(task.taskId)
            }
            switch task.state {
            case .running:
                ProgressView().progressViewStyle(.linear)
                TimelineView(.periodic(from: .now, by: 1)) { context in
                    Text("已用 \(Format.duration(context.date.timeIntervalSince(task.startedAt ?? context.date))) · 通常需要 6-15 分钟")
                        .font(.callout)
                        .monospacedDigit()
                }
                Text("可以关掉 App，下次打开会接着查进度。App 每 20 秒查一次，查进度不经过模型。")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            case .succeeded:
                Text(summary).font(.callout)
                if let url = model.savedFile(for: task.taskId) {
                    Text("已存到本机：\(url.lastPathComponent)")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .textSelection(.enabled)
                }
                HStack {
                    Button("预览全文") { openWindow(id: "prd", value: task.taskId) }
                        .buttonStyle(.borderedProminent)
                    Button("在 Finder 中显示") { model.revealPRD(task.taskId) }
                        .disabled(model.savedFile(for: task.taskId) == nil)
                    Button("另存为…") { model.savePRDAs(task.taskId) }
                }
                .controlSize(.small)
            case .failed, .unknown:
                Text(task.failureDescription ?? "状态未知。")
                    .font(.callout)
                    .foregroundStyle(.red)
            }
            if !task.documents.isEmpty {
                Text("依据的附件：\(task.documents.joined(separator: "、"))")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(12)
        .background(RoundedRectangle(cornerRadius: 10).fill(Color.secondary.opacity(0.08)))
    }

    private var badge: some View {
        let (text, color): (String, Color) = switch task.state {
        case .running: ("生成中", .blue)
        case .succeeded: ("已生成", .green)
        case .failed: ("失败", .red)
        case .unknown: ("未知", .gray)
        }
        return Text(text)
            .font(.caption.bold())
            .padding(.horizontal, 8)
            .padding(.vertical, 2)
            .background(Capsule().fill(color.opacity(0.18)))
            .foregroundStyle(color)
    }

    private var summary: String {
        var parts: [String] = []
        if let start = task.startedAt, let end = task.finishedAt {
            parts.append("用时 \(Format.duration(end.timeIntervalSince(start)))")
        }
        if let bytes = task.prdBytes { parts.append(Format.kilobytes(bytes)) }
        switch task.validationPassed {
        case true?: parts.append("校验通过")
        case false?: parts.append("校验未通过")
        case nil: break
        }
        return parts.joined(separator: " · ")
    }
}

private struct AttachmentSection: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            SectionHeader(
                title: "附件",
                detail: ".md 和 PDF（PDF 在本机提取文字），最多 20 份、共 2 MB。每次发消息都会带上，只对这个会话可见；关掉 App 后要重新附加。"
            )
            ForEach(model.attachments) { attachment in
                HStack(alignment: .top) {
                    Image(systemName: attachment.kind == .pdf ? "doc.richtext" : "doc.text")
                    VStack(alignment: .leading, spacing: 2) {
                        Text(attachment.name).lineLimit(2)
                        Text(attachment.summary).font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button { model.removeAttachment(attachment.name) } label: { Image(systemName: "minus.circle") }
                        .buttonStyle(.borderless)
                        .help("移除")
                        .disabled(model.isSendingHere)
                }
            }
            Button {
                model.chooseAttachments()
            } label: {
                Label("添加附件…", systemImage: "paperclip")
            }
            .disabled(model.isLoadingAttachments)
        }
    }
}

private struct MemorySection: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                SectionHeader(title: "我的长期记忆", detail: "按账号分开存，别人看不到。偏好和事实跨会话通用；摘要只显示这个会话的。")
                Spacer()
                if model.memoriesLoading {
                    ProgressView().controlSize(.small)
                } else {
                    Button { Task { await model.refreshMemories() } } label: { Image(systemName: "arrow.clockwise") }
                        .buttonStyle(.borderless)
                        .help("刷新")
                }
            }
            if let error = model.memoriesError {
                Text(error).font(.caption).foregroundStyle(.red)
            }
            if model.memories.isEmpty, model.memoriesError == nil {
                Text("还没有。对话后由后台提炼，通常要等几分钟。")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }
            ForEach(Array(model.memories.enumerated()), id: \.offset) { _, record in
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Text(record.kindLabel)
                        .font(.caption.bold())
                        .padding(.horizontal, 6)
                        .padding(.vertical, 1)
                        .background(Capsule().fill(Color.secondary.opacity(0.15)))
                    Text(record.displayText).font(.callout).textSelection(.enabled)
                }
            }
        }
    }
}
