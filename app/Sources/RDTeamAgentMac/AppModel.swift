import AppKit
import AuthenticationServices
import Foundation
import RDTeamAgentCore
import UniformTypeIdentifiers

struct ChatMessage: Identifiable, Equatable {
    enum Role { case user, assistant, notice }

    let id = UUID()
    let role: Role
    var text: String
    var attachments: [String] = []
    var isError = false
}

struct SessionItem: Identifiable, Equatable {
    let id: String
    let title: String
    let createdAt: Date?
    let groupId: String?
}

/// All UI state. Lives on the main actor; network work runs inside URLSession.
@MainActor
final class AppModel: ObservableObject {
    enum AuthPhase: Equatable { case restoring, signedOut, signingIn, signedIn }

    static let maxMessageCharacters = 20_000
    static let pollInterval: UInt64 = 20_000_000_000
    private static let newSessionKey = ""
    private static let configKey = "appConfig"

    // MARK: Connection and sign-in

    @Published private(set) var config: AppConfig
    @Published private(set) var authPhase: AuthPhase = .restoring
    @Published private(set) var identity: UserIdentity?
    @Published var authMessage: String?
    @Published var banner: String?

    // MARK: Sessions and chat

    @Published private(set) var sessions: [SessionItem] = []
    @Published private(set) var groups: [SessionGroup] = []
    @Published var selectedSessionId: String? {
        didSet { if oldValue != selectedSessionId { selectionChanged() } }
    }
    @Published private(set) var transcripts: [String: [ChatMessage]] = [:]
    @Published private(set) var loadingSessionId: String?
    @Published private(set) var sendingSessionId: String?
    @Published var draft = ""

    // MARK: Attachments, tasks and memory

    @Published private(set) var attachmentsBySession: [String: [Attachment]] = [:]
    @Published private(set) var isLoadingAttachments = false
    @Published var attachmentError: String?
    @Published private(set) var tasks: [String: TaskStatus] = [:]
    @Published private(set) var prdCache: [String: String] = [:]
    @Published private(set) var memories: [MemoryRecord] = []
    @Published private(set) var memoriesLoading = false
    @Published private(set) var memoriesError: String?
    @Published private(set) var deletingSessionId: String?
    @Published private(set) var retryingTaskIds: Set<String> = []

    private var taskSessions: [String: String] = [:]
    private var serverSessions: [SessionSummary] = []
    private var index = LocalIndex()
    private var indexStore: LocalIndexStore?
    private var tokens: TokenSet?
    private let tokenStore = TokenStore()
    private let tokenClient = TokenClient()
    private let runtime = RuntimeClient()
    private var refreshTask: Task<TokenSet, Error>?
    private var pollTask: Task<Void, Never>?
    private var authSession: ASWebAuthenticationSession?
    private let anchor = PresentationAnchor()

    init() {
        config = Self.loadConfig()
        Task { await restoreSignIn() }
    }

    /// Keychain calls can block for as long as macOS shows its access prompt
    /// (an ad-hoc signed build gets a new signature every time it is rebuilt),
    /// so they never run on the main actor and the window shows meanwhile.
    private func restoreSignIn() async {
        let store = tokenStore
        let saved = await Task.detached(priority: .userInitiated) { try? store.load() }.value
        guard authPhase == .restoring else { return }
        guard let saved else {
            authPhase = .signedOut
            return
        }
        tokens = saved
        await resumeSignIn()
        if authPhase == .restoring { authPhase = .signedOut }
    }

    private func saveTokens(_ tokens: TokenSet) async throws {
        let store = tokenStore
        try await Task.detached(priority: .userInitiated) { try store.save(tokens) }.value
    }

    // MARK: Derived state for the views

    var currentKey: String { selectedSessionId ?? Self.newSessionKey }
    var messages: [ChatMessage] { transcripts[currentKey] ?? [] }
    var attachments: [Attachment] { attachmentsBySession[currentKey] ?? [] }
    var isSending: Bool { sendingSessionId != nil }
    var isSendingHere: Bool { sendingSessionId != nil && sendingSessionId == selectedSessionId }

    var canSend: Bool {
        authPhase == .signedIn && sendingSessionId == nil && !isLoadingAttachments
            && !draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    var currentTitle: String {
        guard let id = selectedSessionId else { return "新会话" }
        return sessions.first { $0.id == id }?.title ?? "会话"
    }

    /// Tasks of the selected session, newest first.
    var currentTasks: [TaskStatus] {
        guard let id = selectedSessionId else { return [] }
        return tasks.values
            .filter { taskSessions[$0.taskId] == id }
            .sorted { ($0.startedAt ?? .distantPast) > ($1.startedAt ?? .distantPast) }
    }

    func savedFile(for taskId: String) -> URL? {
        guard let sessionId = taskSessions[taskId],
              let path = index.sessions[sessionId]?.savedFiles[taskId],
              FileManager.default.fileExists(atPath: path)
        else { return nil }
        return URL(fileURLWithPath: path)
    }

    func prdTitle(_ taskId: String?) -> String {
        guard let taskId, let sessionId = taskSessions[taskId] else { return "PRD" }
        return "PRD · " + (sessions.first { $0.id == sessionId }?.title ?? taskId)
    }

    // MARK: Settings

    /// Returns the problems to show; an accepted change signs the user out,
    /// because tokens and sessions belong to the old pool and Runtime.
    func saveConfig(_ new: AppConfig) -> [String] {
        let trimmed = new.trimmed()
        let errors = trimmed.validationErrors()
        guard errors.isEmpty else { return errors }
        guard trimmed != config else { return [] }
        config = trimmed
        if let data = try? JSONEncoder().encode(trimmed) {
            UserDefaults.standard.set(data, forKey: Self.configKey)
        }
        if authPhase != .signedOut { signOut(message: "连接设置已更改，请重新登录。") }
        return []
    }

    private static func loadConfig() -> AppConfig {
        guard let data = UserDefaults.standard.data(forKey: configKey),
              let config = try? JSONDecoder().decode(AppConfig.self, from: data)
        else { return .defaults }
        return config
    }

    // MARK: Sign-in

    func signIn() {
        guard authPhase != .signingIn else { return }
        let pkce = PKCE.generate()
        guard config.validationErrors().isEmpty, let url = CognitoAuth.authorizeURL(config: config, pkce: pkce) else {
            authMessage = AuthError.invalidConfig.errorDescription
            return
        }
        let handler: (URL?, Error?) -> Void = { [weak self] callback, error in
            Task { @MainActor in await self?.finishSignIn(callback: callback, error: error, pkce: pkce) }
        }
        let session: ASWebAuthenticationSession
        if #available(macOS 14.4, *) {
            session = ASWebAuthenticationSession(url: url, callback: .customScheme(AppConfig.callbackScheme), completionHandler: handler)
        } else {
            session = ASWebAuthenticationSession(url: url, callbackURLScheme: AppConfig.callbackScheme, completionHandler: handler)
        }
        session.presentationContextProvider = anchor
        // No shared cookies: every sign-in asks for a user name, so switching
        // between test accounts works and signing out leaves nothing behind.
        session.prefersEphemeralWebBrowserSession = true
        authSession = session
        authPhase = .signingIn
        authMessage = nil
        if !session.start() {
            authSession = nil
            authPhase = .signedOut
            authMessage = "无法打开登录窗口。"
        }
    }

    private func finishSignIn(callback: URL?, error: Error?, pkce: PKCE) async {
        authSession = nil
        do {
            if let error {
                if let sessionError = error as? ASWebAuthenticationSessionError, sessionError.code == .canceledLogin {
                    throw AuthError.cancelled
                }
                throw error
            }
            guard let callback else { throw AuthError.missingCode }
            let code = try CognitoAuth.authorizationCode(from: callback, expectedState: pkce.state)
            let fresh = try await tokenClient.exchange(code: code, pkce: pkce, config: config)
            try await saveTokens(fresh)
            activate(fresh)
        } catch AuthError.cancelled {
            authPhase = .signedOut
        } catch {
            authPhase = .signedOut
            authMessage = error.localizedDescription
        }
    }

    /// Tokens from the keychain at launch: refresh if close to expiry. When
    /// offline the saved tokens are kept and requests retry later.
    private func resumeSignIn() async {
        do {
            _ = try await accessToken()
        } catch AuthError.sessionExpired {
            return
        } catch {}
        guard let tokens else { return }
        activate(tokens)
    }

    private func activate(_ tokens: TokenSet) {
        guard let user = CognitoAuth.identity(fromAccessToken: tokens.accessToken) else {
            signOut(message: "令牌里没有用户 ID，请重新登录。")
            return
        }
        self.tokens = tokens
        identity = user
        let store = LocalIndexStore(userId: user.userId)
        indexStore = store
        index = store.load()
        if index.utilitySessionId == nil {
            index.utilitySessionId = RuntimeClient.newSessionId()
            saveIndex()
        }
        for (sessionId, record) in index.sessions {
            for taskId in record.taskIds { taskSessions[taskId] = sessionId }
        }
        authMessage = nil
        authPhase = .signedIn
        rebuildSessions(server: [])
        startPolling()
        Task {
            await refreshSessions()
            await refreshMemories()
            await resumeTasks()
        }
    }

    func signOut(message: String? = nil) {
        if let refreshToken = tokens?.refreshToken {
            let client = tokenClient
            let config = config
            Task { await client.revoke(refreshToken: refreshToken, config: config) }
        }
        let store = tokenStore
        Task.detached { try? store.delete() }
        authPhase = .signedOut
        pollTask?.cancel()
        pollTask = nil
        tokens = nil
        identity = nil
        indexStore = nil
        index = LocalIndex()
        serverSessions = []
        sessions = []
        groups = []
        selectedSessionId = nil
        transcripts = [:]
        attachmentsBySession = [:]
        tasks = [:]
        taskSessions = [:]
        prdCache = [:]
        memories = []
        memoriesError = nil
        draft = ""
        banner = nil
        authMessage = message
    }

    /// A valid access token, refreshed when it expires within five minutes.
    /// Concurrent callers share one refresh.
    private func accessToken(forceRefresh: Bool = false) async throws -> String {
        guard let current = tokens else { throw AuthError.sessionExpired }
        if !forceRefresh, !current.expiresSoon() { return current.accessToken }
        if let refreshTask { return try await refreshTask.value.accessToken }
        let client = tokenClient
        let config = config
        let task = Task { try await client.refresh(current, config: config) }
        refreshTask = task
        defer { refreshTask = nil }
        do {
            let fresh = try await task.value
            tokens = fresh
            try? await saveTokens(fresh)
            return fresh.accessToken
        } catch AuthError.sessionExpired {
            signOut(message: AuthError.sessionExpired.errorDescription)
            throw AuthError.sessionExpired
        }
    }

    /// A read-only request; a 401 gets one refresh and one retry.
    private func call(_ body: Data, sessionId: String) async throws -> ServerEvent {
        do {
            return try await runtime.request(body: body, config: config, sessionId: sessionId, accessToken: try await accessToken())
        } catch RuntimeClientError.unauthorized {
            do {
                return try await runtime.request(
                    body: body, config: config, sessionId: sessionId, accessToken: try await accessToken(forceRefresh: true)
                )
            } catch RuntimeClientError.unauthorized {
                signOut(message: RuntimeClientError.unauthorized.errorDescription)
                throw RuntimeClientError.unauthorized
            }
        }
    }

    // MARK: Sessions

    func refreshSessions() async {
        guard authPhase == .signedIn, let utility = index.utilitySessionId else { return }
        do {
            switch try await call(try RuntimePayload.action("list_sessions"), sessionId: utility) {
            case .sessions(let list, let deleted):
                // Deleted on another device (or a delete whose reply was lost).
                for id in deleted where index.sessions[id] != nil && sendingSessionId != id {
                    forgetSession(id)
                }
                rebuildSessions(server: list)
            case .error(let error): banner = "会话列表没取到：\(error.localizedDescription)"
            default: break
            }
        } catch {
            if authPhase == .signedIn { banner = "会话列表没取到：\(error.localizedDescription)" }
        }
    }

    /// Server sessions plus the ones this Mac started; titles come from this Mac.
    private func rebuildSessions(server: [SessionSummary]?) {
        if let server { serverSessions = server }
        var items: [String: SessionItem] = [:]
        for summary in serverSessions {
            let record = index.sessions[summary.sessionId]
            let created = summary.createdAt ?? record?.createdAt
            items[summary.sessionId] = SessionItem(
                id: summary.sessionId, title: record?.title ?? Format.untitled(created), createdAt: created,
                groupId: record?.groupId
            )
        }
        for (id, record) in index.sessions where items[id] == nil {
            items[id] = SessionItem(
                id: id, title: record.title ?? Format.untitled(record.createdAt), createdAt: record.createdAt,
                groupId: record.groupId
            )
        }
        sessions = items.values.sorted { ($0.createdAt ?? .distantPast) > ($1.createdAt ?? .distantPast) }
        groups = index.groups
    }

    // MARK: Titles and groups (kept on this Mac only)

    /// Each returns the message to show, or nil once the change is saved.
    func renameSession(_ id: String, to title: String) -> String? {
        let created = recordCreatedAt(id)
        return changeIndex { try $0.renameSession(id, to: title, createdAt: created) }
    }

    /// With a session ID, the new group also receives that session.
    func createGroup(named name: String, moving sessionId: String? = nil) -> String? {
        let created = sessionId.map { recordCreatedAt($0) }
        return changeIndex { index in
            let group = try index.createGroup(named: name)
            if let sessionId, let created {
                try index.moveSession(sessionId, toGroup: group.id, createdAt: created)
            }
        }
    }

    func renameGroup(_ id: String, to name: String) -> String? {
        changeIndex { try $0.renameGroup(id, to: name) }
    }

    func deleteGroup(_ id: String) {
        _ = changeIndex { $0.deleteGroup(id) }
    }

    func moveSession(_ id: String, toGroup groupId: String?) -> String? {
        let created = recordCreatedAt(id)
        return changeIndex { try $0.moveSession(id, toGroup: groupId, createdAt: created) }
    }

    /// Applies a change to a copy, so a rejected name leaves the index as it was.
    private func changeIndex(_ change: (inout LocalIndex) throws -> Void) -> String? {
        var updated = index
        do {
            try change(&updated)
        } catch {
            return error.localizedDescription
        }
        index = updated
        saveIndex()
        rebuildSessions(server: nil)
        return nil
    }

    /// For a session this Mac has no record of yet, the server's creation time.
    private func recordCreatedAt(_ sessionId: String) -> Date {
        index.sessions[sessionId]?.createdAt
            ?? serverSessions.first { $0.sessionId == sessionId }?.createdAt
            ?? Date()
    }

    // MARK: Deleting a session

    /// The server deletes the conversation, its summaries and its PRD results,
    /// and refuses while a task runs. Deleting is idempotent, so the read
    /// path's retries are safe here too.
    func deleteSession(_ id: String) async {
        guard deletingSessionId == nil else { return }
        guard sendingSessionId != id else {
            banner = "这个会话正在回复，等回复结束后再删。"
            return
        }
        deletingSessionId = id
        banner = nil // an earlier attempt's error no longer applies
        defer { deletingSessionId = nil }
        do {
            switch try await call(try RuntimePayload.action("delete_session"), sessionId: id) {
            case .sessionDeleted:
                forgetSession(id)
            case .error(let error):
                banner = "会话没删掉：\(error.localizedDescription)"
            default:
                banner = "会话没删掉：服务端的回复不对。"
            }
        } catch {
            if authPhase == .signedIn { banner = "会话没删掉：\(error.localizedDescription)" }
        }
    }

    private func forgetSession(_ id: String) {
        if selectedSessionId == id { newSession() }
        for (taskId, sessionId) in taskSessions where sessionId == id {
            taskSessions.removeValue(forKey: taskId)
            tasks.removeValue(forKey: taskId)
            prdCache.removeValue(forKey: taskId)
        }
        transcripts.removeValue(forKey: id)
        attachmentsBySession.removeValue(forKey: id)
        serverSessions.removeAll { $0.sessionId == id }
        index.removeSession(id)
        saveIndex()
        rebuildSessions(server: nil)
    }

    func newSession() {
        selectedSessionId = nil
        transcripts[Self.newSessionKey] = nil
        attachmentsBySession[Self.newSessionKey] = nil
        attachmentError = nil
    }

    private func selectionChanged() {
        attachmentError = nil
        guard authPhase == .signedIn else { return }
        Task { await refreshMemories() }
        guard let id = selectedSessionId, transcripts[id] == nil else { return }
        Task { await loadSession(id) }
    }

    func reloadCurrentSession() {
        guard let id = selectedSessionId, sendingSessionId != id else { return }
        Task {
            await loadSession(id, force: true)
            await refreshMemories()
        }
    }

    func loadSession(_ id: String, force: Bool = false) async {
        guard force || transcripts[id] == nil else { return }
        loadingSessionId = id
        defer { if loadingSessionId == id { loadingSessionId = nil } }
        do {
            let event = try await call(try RuntimePayload.action("history"), sessionId: id)
            guard case .history(let history) = event else {
                if case .error(let error) = event { banner = "会话记录没取到：\(error.localizedDescription)" }
                return
            }
            guard sendingSessionId != id else { return } // a reply is streaming in; keep it
            transcripts[id] = Self.messages(from: history)
            var taskIds = index.sessions[id]?.taskIds ?? []
            for message in history {
                for taskId in Transcript.taskIds(in: message.text) where !taskIds.contains(taskId) {
                    taskIds.append(taskId)
                }
            }
            for taskId in taskIds where force || tasks[taskId] == nil {
                await refreshTask(taskId, sessionId: id)
            }
        } catch {
            if authPhase == .signedIn { banner = "会话记录没取到：\(error.localizedDescription)" }
        }
    }

    static func messages(from history: [HistoryMessage]) -> [ChatMessage] {
        history.map { message in
            if message.role == "user" {
                let split = Transcript.splitUserTurn(message.text)
                return ChatMessage(role: .user, text: split.message, attachments: split.attachments)
            }
            if let notice = Transcript.notice(fromAssistantText: message.text) {
                return ChatMessage(role: .notice, text: notice)
            }
            return ChatMessage(role: .assistant, text: message.text)
        }
    }

    // MARK: Chat

    func send() {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard canSend else { return }
        guard text.count <= Self.maxMessageCharacters else {
            banner = "一条消息最多 \(Self.maxMessageCharacters.formatted()) 个字符。"
            return
        }
        let sessionId: String
        if let selected = selectedSessionId {
            sessionId = selected
        } else {
            sessionId = RuntimeClient.newSessionId()
            index.sessions[sessionId] = SessionRecord(title: LocalIndex.title(fromMessage: text))
            saveIndex()
            attachmentsBySession[sessionId] = attachmentsBySession.removeValue(forKey: Self.newSessionKey)
            transcripts[sessionId] = transcripts.removeValue(forKey: Self.newSessionKey) ?? []
            rebuildSessions(server: nil)
            selectedSessionId = sessionId
        }
        // The PRD tool only sees this request's documents, so every turn carries them.
        let documents = (attachmentsBySession[sessionId] ?? []).map(\.document)
        let body: Data
        do {
            body = try RuntimePayload.chat(message: text, documents: documents)
        } catch {
            banner = "消息没能打包：\(error.localizedDescription)"
            return
        }
        let reply = ChatMessage(role: .assistant, text: "")
        transcripts[sessionId, default: []].append(ChatMessage(role: .user, text: text, attachments: documents.map(\.path)))
        transcripts[sessionId, default: []].append(reply)
        draft = ""
        banner = nil
        sendingSessionId = sessionId
        Task { await stream(body: body, sessionId: sessionId, replyId: reply.id) }
    }

    /// A chat turn is not idempotent (it records memory and may start a task),
    /// so it is retried only when the Runtime rejected the token up front.
    private func stream(body: Data, sessionId: String, replyId: UUID) async {
        var forceRefresh = false
        while true {
            var received = false
            do {
                let token = try await accessToken(forceRefresh: forceRefresh)
                for try await event in runtime.stream(body: body, config: config, sessionId: sessionId, accessToken: token) {
                    received = true
                    handle(event, sessionId: sessionId, replyId: replyId)
                }
            } catch RuntimeClientError.unauthorized where !received && !forceRefresh {
                forceRefresh = true
                continue
            } catch {
                updateReply(sessionId, replyId) {
                    $0.text += ($0.text.isEmpty ? "" : "\n\n") + "发送失败：\(error.localizedDescription)"
                    $0.isError = true
                }
            }
            break
        }
        if let position = transcripts[sessionId]?.firstIndex(where: { $0.id == replyId }),
           transcripts[sessionId]?[position].text.isEmpty == true {
            transcripts[sessionId]?.remove(at: position)
        }
        sendingSessionId = nil
        await refreshSessions()
    }

    private func handle(_ event: ServerEvent, sessionId: String, replyId: UUID) {
        switch event {
        case .text(let text):
            updateReply(sessionId, replyId) { $0.text += text }
        case .taskStarted(let status):
            tasks[status.taskId] = status
            taskSessions[status.taskId] = sessionId
            remember(status.taskId, in: sessionId)
            transcripts[sessionId, default: []].append(
                ChatMessage(role: .notice, text: "PRD 开始在后台生成（\(status.taskId)），进度见右侧「本次任务」")
            )
        case .error(let error):
            updateReply(sessionId, replyId) {
                $0.text += ($0.text.isEmpty ? "" : "\n\n") + (error.errorDescription ?? error.code)
                $0.isError = true
            }
        default:
            break
        }
    }

    private func updateReply(_ sessionId: String, _ replyId: UUID, _ change: (inout ChatMessage) -> Void) {
        guard let position = transcripts[sessionId]?.firstIndex(where: { $0.id == replyId }) else { return }
        change(&transcripts[sessionId]![position])
    }

    // MARK: Attachments

    func chooseAttachments() {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = true
        panel.canChooseDirectories = false
        panel.allowedContentTypes = [.pdf] + AttachmentLoader.markdownExtensions.compactMap { UTType(filenameExtension: $0) }
        panel.prompt = "附加"
        panel.message = "选择 .md 或 PDF 文件（PDF 在本机提取文字后发送）"
        guard panel.runModal() == .OK else { return }
        addAttachments(panel.urls)
    }

    /// PDF extraction can take seconds, so files are read off the main actor.
    func addAttachments(_ urls: [URL]) {
        let files = urls.filter(\.isFileURL)
        guard !files.isEmpty, !isLoadingAttachments else { return }
        let key = currentKey
        attachmentError = nil
        isLoadingAttachments = true
        Task {
            defer { isLoadingAttachments = false }
            do {
                let loaded = try await Task.detached(priority: .userInitiated) {
                    try files.map { try AttachmentLoader.load($0) }
                }.value
                attachmentsBySession[key] = try AttachmentLoader.merge(attachmentsBySession[key] ?? [], adding: loaded)
            } catch {
                attachmentError = error.localizedDescription
            }
        }
    }

    func removeAttachment(_ name: String) {
        attachmentsBySession[currentKey]?.removeAll { $0.name == name }
    }

    // MARK: Tasks

    private func startPolling() {
        pollTask?.cancel()
        pollTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: Self.pollInterval)
                guard !Task.isCancelled else { return }
                await self?.pollRunningTasks()
            }
        }
    }

    /// Status checks read S3 through the Runtime and never call the model.
    private func pollRunningTasks() async {
        let running = tasks.values.filter { $0.state == .running }.map(\.taskId)
        for taskId in running {
            guard let sessionId = taskSessions[taskId], sessionId != sendingSessionId else { continue }
            await refreshTask(taskId, sessionId: sessionId)
        }
    }

    /// Tasks this Mac started whose PRD it has not saved yet.
    private func resumeTasks() async {
        for (sessionId, record) in index.sessions {
            for taskId in record.taskIds where record.savedFiles[taskId] == nil {
                await refreshTask(taskId, sessionId: sessionId)
            }
        }
    }

    private func refreshTask(_ taskId: String, sessionId: String) async {
        do {
            let event = try await call(try RuntimePayload.action("task_status", taskId: taskId), sessionId: sessionId)
            switch event {
            case .taskStatus(let status):
                let wasRunning = tasks[taskId]?.state == .running
                tasks[taskId] = status
                taskSessions[taskId] = sessionId
                remember(taskId, in: sessionId)
                if wasRunning, status.state != .running { taskFinished(status, sessionId: sessionId) }
                if status.state == .succeeded { await ensureResult(taskId) }
            case .error(let error) where error.code == "NOT_FOUND" || error.code == "INVALID_REQUEST":
                // A task ID mentioned in this session that belongs elsewhere.
                if tasks[taskId] == nil { taskSessions.removeValue(forKey: taskId) }
            default:
                break
            }
        } catch {
            // The next poll tries again.
        }
    }

    /// Reruns a failed task from the requirement and documents the server
    /// saved; no message is sent and the model is not called.
    func retryTask(_ taskId: String) async {
        guard let sessionId = taskSessions[taskId], !retryingTaskIds.contains(taskId) else { return }
        retryingTaskIds.insert(taskId)
        banner = nil
        defer { retryingTaskIds.remove(taskId) }
        do {
            let event = try await call(try RuntimePayload.action("retry_task", taskId: taskId), sessionId: sessionId)
            switch event {
            case .taskStarted(let status):
                tasks[status.taskId] = status
                taskSessions[status.taskId] = sessionId
                remember(status.taskId, in: sessionId)
                if transcripts[sessionId] != nil {
                    transcripts[sessionId, default: []].append(ChatMessage(
                        role: .notice,
                        text: "已用原来的需求和附件重试（新任务 \(status.taskId)），进度见右侧「本次任务」"
                    ))
                }
                await refreshTask(taskId, sessionId: sessionId) // now carries retried_as
            case .error(let error):
                await refreshTask(taskId, sessionId: sessionId)
                // A resent request after a dropped connection finds the first
                // one already accepted: show the new task instead of an error.
                if let new = tasks[taskId]?.retriedAs {
                    if tasks[new] == nil { await refreshTask(new, sessionId: sessionId) }
                } else {
                    banner = "没能重试：\(error.localizedDescription)"
                }
            default:
                banner = "没能重试：服务端的回复不对。"
            }
        } catch {
            if authPhase == .signedIn { banner = "没能重试：\(error.localizedDescription)" }
        }
    }

    private func taskFinished(_ status: TaskStatus, sessionId: String) {
        let text = status.state == .succeeded
            ? "PRD 已生成（\(status.taskId)），见右侧「本次任务」"
            : "PRD 生成没有完成（\(status.taskId)）：\(status.failureDescription ?? "")"
        if transcripts[sessionId] != nil {
            transcripts[sessionId, default: []].append(ChatMessage(role: .notice, text: text))
        }
        NSApp.requestUserAttention(.informationalRequest)
        Task { await refreshMemories() }
    }

    /// Loads a finished PRD from this Mac's copy or the Runtime; the first
    /// fetch also saves it under ~/Documents/研发团队 Agent/.
    func ensureResult(_ taskId: String) async {
        guard prdCache[taskId] == nil, let sessionId = taskSessions[taskId] else { return }
        if let url = savedFile(for: taskId), let text = try? String(contentsOf: url, encoding: .utf8) {
            prdCache[taskId] = text
            return
        }
        do {
            switch try await call(try RuntimePayload.action("task_result", taskId: taskId), sessionId: sessionId) {
            case .taskResult(let status, let markdown):
                tasks[taskId] = status
                prdCache[taskId] = markdown
                autosave(taskId, sessionId: sessionId, markdown: markdown)
            case .error(let error):
                banner = "PRD 没取到：\(error.localizedDescription)"
            default:
                break
            }
        } catch {
            if authPhase == .signedIn { banner = "PRD 没取到：\(error.localizedDescription)" }
        }
    }

    private func autosave(_ taskId: String, sessionId: String, markdown: String) {
        guard index.sessions[sessionId]?.savedFiles[taskId] == nil else { return }
        do {
            let name = PRDFileWriter.fileName(title: index.sessions[sessionId]?.title, taskId: taskId)
            let url = try PRDFileWriter.write(markdown: markdown, folder: PRDFileWriter.defaultFolder(), fileName: name)
            index.sessions[sessionId, default: SessionRecord()].savedFiles[taskId] = url.path
            saveIndex()
        } catch {
            banner = "PRD 没能存到本机：\(error.localizedDescription)"
        }
    }

    func revealPRD(_ taskId: String) {
        guard let url = savedFile(for: taskId) else { return }
        NSWorkspace.shared.activateFileViewerSelecting([url])
    }

    func savePRDAs(_ taskId: String) {
        Task {
            await ensureResult(taskId)
            guard let markdown = prdCache[taskId] else { return }
            let panel = NSSavePanel()
            let sessionId = taskSessions[taskId] ?? ""
            panel.nameFieldStringValue = PRDFileWriter.fileName(title: index.sessions[sessionId]?.title, taskId: taskId)
            panel.allowedContentTypes = [UTType(filenameExtension: "md") ?? .plainText]
            panel.canCreateDirectories = true
            guard panel.runModal() == .OK, let url = panel.url else { return }
            do {
                try Data(markdown.utf8).write(to: url, options: .atomic)
            } catch {
                banner = "另存失败：\(error.localizedDescription)"
            }
        }
    }

    private func remember(_ taskId: String, in sessionId: String) {
        var record = index.sessions[sessionId] ?? SessionRecord(createdAt: recordCreatedAt(sessionId))
        guard !record.taskIds.contains(taskId) else { return }
        record.taskIds.append(taskId)
        index.sessions[sessionId] = record
        saveIndex()
    }

    private func saveIndex() {
        do {
            try indexStore?.save(index)
        } catch {
            banner = "本机记录没能保存：\(error.localizedDescription)"
        }
    }

    // MARK: Long-term memory

    /// Preferences and facts belong to the user; the Runtime adds summaries
    /// only for the session the request names, so the selected session is used
    /// (a new conversation asks through the utility session, which has none).
    func refreshMemories() async {
        guard authPhase == .signedIn, let utility = index.utilitySessionId else { return }
        let key = currentKey
        let sessionId = selectedSessionId ?? utility
        memoriesLoading = true
        defer { memoriesLoading = false }
        do {
            let event = try await call(try RuntimePayload.action("list_memories"), sessionId: sessionId)
            guard key == currentKey else { return } // the user moved on to another session
            switch event {
            case .memories(let records):
                memories = records
                memoriesError = nil
            case .error(let error):
                memoriesError = error.localizedDescription
            default:
                break
            }
        } catch {
            if authPhase == .signedIn, key == currentKey { memoriesError = error.localizedDescription }
        }
    }
}

/// Gives the sign-in sheet a window to attach to.
final class PresentationAnchor: NSObject, ASWebAuthenticationPresentationContextProviding {
    func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        NSApp.keyWindow ?? NSApp.windows.first { $0.isVisible } ?? ASPresentationAnchor()
    }
}

enum Format {
    static func dateTime(_ date: Date?) -> String {
        guard let date else { return "" }
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "zh_CN")
        formatter.dateFormat = Calendar.current.isDateInToday(date) ? "今天 HH:mm" : "M月d日 HH:mm"
        return formatter.string(from: date)
    }

    static func untitled(_ date: Date?) -> String {
        date.map { "\(dateTime($0)) 的会话" } ?? "未命名会话"
    }

    static func duration(_ seconds: TimeInterval) -> String {
        let total = max(0, Int(seconds.rounded()))
        return total >= 60 ? "\(total / 60) 分 \(total % 60) 秒" : "\(total) 秒"
    }

    static func kilobytes(_ bytes: Int) -> String {
        String(format: "%.1f KB", Double(bytes) / 1024)
    }
}
