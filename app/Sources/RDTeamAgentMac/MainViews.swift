import AppKit
import RDTeamAgentCore
import SwiftUI

struct RootView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        switch model.authPhase {
        case .signedIn: MainView()
        case .signedOut, .signingIn: LoginView()
        case .restoring: RestoringView()
        }
    }
}

struct RestoringView: View {
    var body: some View {
        VStack(spacing: 12) {
            ProgressView()
            Text("正在恢复登录…")
            Text("如果系统弹窗询问能否使用钥匙串里的登录令牌，选「允许」即可；选「拒绝」会回到登录页。")
                .font(.footnote)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 420)
        }
        .padding(40)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

struct LoginView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        VStack(spacing: 16) {
            Image(nsImage: NSApp.applicationIconImage)
                .resizable()
                .frame(width: 96, height: 96)
            Text("研发团队 Agent").font(.largeTitle.bold())
            Text("用公司账号登录后开始对话").foregroundStyle(.secondary)
            Button {
                model.signIn()
            } label: {
                Text(model.authPhase == .signingIn ? "正在登录…" : "登录").frame(minWidth: 160)
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
            .keyboardShortcut(.defaultAction)
            .disabled(model.authPhase == .signingIn)
            if let message = model.authMessage {
                Text(message)
                    .foregroundStyle(.red)
                    .multilineTextAlignment(.center)
                    .frame(maxWidth: 420)
            }
            SettingsLink { Text("连接设置…") }
                .buttonStyle(.link)
            Text("测试账号由管理员创建，不开放注册。")
                .font(.footnote)
                .foregroundStyle(.secondary)
        }
        .padding(40)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

/// View-only state. SwiftUI's `@State` is a macro in the macOS 27 SDK and its
/// plugin ships only with Xcode, so views keep local state in an object instead
/// and the app still builds with just the Command Line Tools.
@MainActor
final class ViewState: ObservableObject {
    @Published var showInspector = true
    @Published var isDropTarget = false
}

struct MainView: View {
    @EnvironmentObject private var model: AppModel
    @StateObject private var state = ViewState()

    var body: some View {
        NavigationSplitView {
            SessionSidebar()
                .navigationSplitViewColumnWidth(min: 200, ideal: 240, max: 320)
        } detail: {
            ChatView()
                .navigationTitle(model.currentTitle)
                .inspector(isPresented: $state.showInspector) {
                    InspectorView()
                        .inspectorColumnWidth(min: 280, ideal: 330, max: 440)
                }
                .toolbar {
                    ToolbarItemGroup {
                        Button {
                            model.reloadCurrentSession()
                        } label: {
                            Label("重新载入", systemImage: "arrow.clockwise")
                        }
                        .help("从服务端重新载入这个会话的记录和任务")
                        .disabled(model.selectedSessionId == nil || model.isSendingHere)
                        Button {
                            state.showInspector.toggle()
                        } label: {
                            Label("检查器", systemImage: "sidebar.right")
                        }
                        .help("显示或隐藏右侧的任务、附件和长期记忆")
                    }
                }
        }
    }
}

struct SessionSidebar: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        List(selection: $model.selectedSessionId) {
            Section("会话") {
                if model.sessions.isEmpty {
                    Text("还没有会话").foregroundStyle(.secondary)
                }
                ForEach(model.sessions) { session in
                    VStack(alignment: .leading, spacing: 2) {
                        Text(session.title).lineLimit(1)
                        Text(Format.dateTime(session.createdAt))
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    .padding(.vertical, 2)
                    .tag(session.id as String?)
                }
            }
        }
        .safeAreaInset(edge: .top) {
            Button {
                model.newSession()
            } label: {
                Label("新会话", systemImage: "square.and.pencil").frame(maxWidth: .infinity)
            }
            .controlSize(.large)
            .padding(.horizontal, 10)
            .padding(.top, 6)
            .disabled(model.isSending)
        }
        .safeAreaInset(edge: .bottom) {
            HStack {
                Label(model.identity?.username ?? "", systemImage: "person.crop.circle")
                    .lineLimit(1)
                Spacer()
                Menu {
                    Button("刷新会话列表") { Task { await model.refreshSessions() } }
                    SettingsLink { Text("连接设置…") }
                    Divider()
                    Button("退出登录") { model.signOut() }
                } label: {
                    Image(systemName: "ellipsis.circle")
                }
                .menuStyle(.borderlessButton)
                .fixedSize()
            }
            .padding(10)
        }
    }
}

struct ChatView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        VStack(spacing: 0) {
            if let banner = model.banner {
                HStack(alignment: .top) {
                    Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.orange)
                    Text(banner).frame(maxWidth: .infinity, alignment: .leading).textSelection(.enabled)
                    Button { model.banner = nil } label: { Image(systemName: "xmark") }
                        .buttonStyle(.borderless)
                }
                .padding(10)
                .background(Color.orange.opacity(0.12))
            }
            transcript
            Divider()
            ComposerView()
        }
    }

    private var transcript: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 14) {
                    if model.loadingSessionId != nil, model.loadingSessionId == model.selectedSessionId {
                        ProgressView("正在载入会话记录…").frame(maxWidth: .infinity)
                    } else if model.messages.isEmpty {
                        EmptyChatHint()
                    }
                    ForEach(model.messages) { message in
                        MessageRow(message: message, streaming: model.isSendingHere && message.id == model.messages.last?.id)
                    }
                    Color.clear.frame(height: 1).id("bottom")
                }
                .padding(20)
            }
            .onChange(of: model.messages.last?.text) {
                withAnimation { proxy.scrollTo("bottom", anchor: .bottom) }
            }
            .onChange(of: model.selectedSessionId) {
                proxy.scrollTo("bottom", anchor: .bottom)
            }
        }
    }
}

private struct EmptyChatHint: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("说说你要做的功能").font(.title3.bold())
            Text("附上需求材料（.md 或 PDF），我会先把需求问清楚，再在后台生成 PRD。生成依据是这些附件和已接入的示例代码仓。")
                .foregroundStyle(.secondary)
            Text("例如：请根据附件，为管理后台写一份「CSV 批量导入用户」的 PRD。")
                .foregroundStyle(.secondary)
                .italic()
        }
        .padding(.top, 40)
        .frame(maxWidth: 560, alignment: .leading)
    }
}

private struct MessageRow: View {
    let message: ChatMessage
    let streaming: Bool

    var body: some View {
        switch message.role {
        case .user:
            HStack {
                Spacer(minLength: 80)
                VStack(alignment: .trailing, spacing: 4) {
                    Text(message.text)
                        .textSelection(.enabled)
                        .padding(10)
                        .background(RoundedRectangle(cornerRadius: 12).fill(Color.accentColor.opacity(0.15)))
                    if !message.attachments.isEmpty {
                        Label(message.attachments.joined(separator: "、"), systemImage: "paperclip")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
            }
        case .assistant:
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 4) {
                    if message.text.isEmpty, streaming {
                        ProgressView().controlSize(.small)
                    } else {
                        MarkdownView(markdown: message.text)
                            .textSelection(.enabled)
                            .foregroundStyle(message.isError ? Color.red : Color.primary)
                    }
                }
                Spacer(minLength: 80)
            }
        case .notice:
            Label(message.text, systemImage: "info.circle")
                .font(.callout)
                .foregroundStyle(.secondary)
                .textSelection(.enabled)
                .frame(maxWidth: .infinity)
        }
    }
}

struct ComposerView: View {
    @EnvironmentObject private var model: AppModel
    @StateObject private var state = ViewState()

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            if !model.attachments.isEmpty {
                ScrollView(.horizontal) {
                    HStack {
                        ForEach(model.attachments) { attachment in
                            HStack(spacing: 4) {
                                Image(systemName: attachment.kind == .pdf ? "doc.richtext" : "doc.text")
                                Text(attachment.name).lineLimit(1)
                                Button { model.removeAttachment(attachment.name) } label: {
                                    Image(systemName: "xmark.circle.fill")
                                }
                                .buttonStyle(.borderless)
                                .disabled(model.isSendingHere)
                            }
                            .font(.caption)
                            .padding(.horizontal, 8)
                            .padding(.vertical, 4)
                            .background(Capsule().fill(Color.secondary.opacity(0.12)))
                        }
                    }
                }
            }
            if let error = model.attachmentError {
                Text(error).font(.caption).foregroundStyle(.red)
            }
            HStack(alignment: .bottom, spacing: 8) {
                Button {
                    model.chooseAttachments()
                } label: {
                    if model.isLoadingAttachments {
                        ProgressView().controlSize(.small)
                    } else {
                        Image(systemName: "paperclip")
                    }
                }
                .buttonStyle(.borderless)
                .font(.title3)
                .help("附加 .md 或 PDF")
                .disabled(model.isLoadingAttachments)
                ZStack(alignment: .topLeading) {
                    TextEditor(text: $model.draft)
                        .font(.body)
                        .scrollContentBackground(.hidden)
                        .frame(minHeight: 44, maxHeight: 140)
                    if model.draft.isEmpty {
                        Text("回复，或拖入 .md / PDF 文件…（⌘↩ 发送）")
                            .foregroundStyle(.tertiary)
                            .padding(.leading, 5)
                            .allowsHitTesting(false)
                    }
                }
                .padding(8)
                .background(RoundedRectangle(cornerRadius: 10).fill(Color(nsColor: .textBackgroundColor)))
                .overlay(
                    RoundedRectangle(cornerRadius: 10)
                        .stroke(state.isDropTarget ? Color.accentColor : Color.secondary.opacity(0.3), lineWidth: state.isDropTarget ? 2 : 1)
                )
                Button("发送") { model.send() }
                    .keyboardShortcut(.return, modifiers: .command)
                    .buttonStyle(.borderedProminent)
                    .disabled(!model.canSend)
            }
        }
        .padding(12)
        .dropDestination(for: URL.self) { urls, _ in
            model.addAttachments(urls)
            return true
        } isTargeted: { state.isDropTarget = $0 }
    }
}
