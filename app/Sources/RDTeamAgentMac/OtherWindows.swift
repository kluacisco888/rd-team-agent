import RDTeamAgentCore
import SwiftUI

/// The whole PRD of one task, in its own window.
struct PRDWindow: View {
    @EnvironmentObject private var model: AppModel
    let taskId: String?

    var body: some View {
        Group {
            if let taskId, let markdown = model.prdCache[taskId] {
                ScrollView {
                    MarkdownView(markdown: markdown)
                        .textSelection(.enabled)
                        .frame(maxWidth: 900, alignment: .leading)
                        .padding(28)
                        .frame(maxWidth: .infinity)
                }
            } else if taskId != nil, model.authPhase == .signedIn {
                ProgressView("正在取回 PRD…").frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                Text("登录后才能查看 PRD。").foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .frame(minWidth: 640, minHeight: 520)
        .navigationTitle(model.prdTitle(taskId))
        .toolbar {
            if let taskId {
                ToolbarItemGroup {
                    Button {
                        model.revealPRD(taskId)
                    } label: {
                        Label("在 Finder 中显示", systemImage: "folder")
                    }
                    .disabled(model.savedFile(for: taskId) == nil)
                    Button {
                        model.savePRDAs(taskId)
                    } label: {
                        Label("另存为…", systemImage: "square.and.arrow.down")
                    }
                }
            }
        }
        .task(id: taskId) {
            if let taskId { await model.ensureResult(taskId) }
        }
    }
}

/// The settings form's edits; an object for the reason given on `ViewState`.
@MainActor
final class SettingsDraft: ObservableObject {
    @Published var config = AppConfig.defaults
    @Published var errors: [String] = []
    @Published var saved = false
}

/// Connection settings: which Runtime to call and where to sign in. No secrets.
struct SettingsView: View {
    @EnvironmentObject private var model: AppModel
    @StateObject private var form = SettingsDraft()

    private var draft: Binding<AppConfig> { $form.config }

    var body: some View {
        Form {
            Section("Runtime") {
                TextField("Runtime ARN", text: draft.runtimeArn)
                TextField("Qualifier", text: draft.qualifier)
            }
            Section("登录（Cognito 托管登录）") {
                TextField("登录域名", text: draft.loginDomain)
                TextField("App client ID", text: draft.clientId)
                LabeledContent("回调地址", value: AppConfig.redirectURI)
            }
            if !form.errors.isEmpty {
                Section {
                    ForEach(form.errors, id: \.self) { Text($0).foregroundStyle(.red) }
                }
            }
            Section {
                HStack {
                    Button("恢复默认") { form.config = .defaults }
                    Spacer()
                    if form.saved { Text("已保存").foregroundStyle(.secondary) }
                    Button("还原") {
                        form.config = model.config
                        form.errors = []
                    }
                    .disabled(form.config == model.config)
                    Button("保存") {
                        form.errors = model.saveConfig(form.config)
                        form.saved = form.errors.isEmpty
                        if form.saved { form.config = model.config }
                    }
                    .keyboardShortcut(.defaultAction)
                    .disabled(form.config == model.config)
                }
                if model.authPhase == .signedIn {
                    Text("保存更改后需要重新登录。").font(.caption).foregroundStyle(.secondary)
                }
            }
        }
        .formStyle(.grouped)
        .frame(width: 600)
        .onAppear { form.config = model.config }
        .onChange(of: form.config) { form.saved = false }
    }
}
