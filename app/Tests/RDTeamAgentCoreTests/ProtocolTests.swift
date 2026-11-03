import Foundation
import Testing
@testable import RDTeamAgentCore

@Suite struct SSEParserTests {
    @Test func ignoresNonDataLines() throws {
        #expect(try SSEParser.event(fromLine: "") == nil)
        #expect(try SSEParser.event(fromLine: ": heartbeat") == nil)
        #expect(try SSEParser.event(fromLine: "event: message") == nil)
        #expect(try SSEParser.event(fromLine: #"data: {"event":"something_new"}"#) == nil)
    }

    @Test func chatEvents() throws {
        #expect(try SSEParser.event(fromLine: #"data: {"event":"started","session_id":"s"}"#) == .started)
        #expect(try SSEParser.event(fromLine: #"data: {"event":"text","text":"你好"}"#) == .text("你好"))
        #expect(try SSEParser.event(fromLine: #"data: {"event":"done"}"#) == .done)
        let started = try SSEParser.event(fromLine:
            #"data: {"event":"task_started","task":{"task_id":"task-1","state":"running","documents":["a.md"],"started_at":"2026-11-02T12:01:19.739059+00:00"}}"#)
        guard case .taskStarted(let task)? = started else { Issue.record("not task_started"); return }
        #expect(task.taskId == "task-1")
        #expect(task.state == .running)
        #expect(task.documents == ["a.md"])
        #expect(task.startedAt == ISODate.parse("2026-11-02T12:01:19.739059+00:00"))
    }

    @Test func doubleEncodedEventIsUnwrapped() throws {
        #expect(try SSEParser.event(fromLine: #"data: "{\"event\":\"done\"}""#) == .done)
    }

    @Test func errorEvent() throws {
        let event = try SSEParser.event(fromLine: #"data: {"event":"error","error":{"code":"NOT_FOUND","message":"no such task"}}"#)
        #expect(event == .error(ServerError(code: "NOT_FOUND", message: "no such task")))
    }

    @Test func listEvents() throws {
        let sessions = try SSEParser.event(fromLine:
            #"data: {"event":"sessions","sessions":[{"session_id":"s1","created_at":"2026-11-02T12:00:00+00:00"},{"session_id":"s2","created_at":null}]}"#)
        #expect(sessions == .sessions([
            SessionSummary(sessionId: "s1", createdAt: ISODate.parse("2026-11-02T12:00:00+00:00")),
            SessionSummary(sessionId: "s2", createdAt: nil),
        ]))
        let history = try SSEParser.event(fromLine:
            #"data: {"event":"history","messages":[{"role":"user","text":"hi","at":"2026-11-02T12:00:00+00:00"}]}"#)
        #expect(history == .history([HistoryMessage(role: "user", text: "hi", at: ISODate.parse("2026-11-02T12:00:00Z"))]))
        let memories = try SSEParser.event(fromLine:
            #"data: {"event":"memories","records":[{"kind":"preference","text":"Given/When/Then","created_at":null}]}"#)
        #expect(memories == .memories([MemoryRecord(kind: "preference", text: "Given/When/Then", createdAt: nil)]))
    }

    @Test func taskStatusAndResult() throws {
        let status = try SSEParser.event(fromLine:
            #"data: {"event":"task_status","task":{"task_id":"task-2","state":"failed","error":{"code":"INVALID_DOCUMENTS","message":"too big"}}}"#)
        guard case .taskStatus(let task)? = status else { Issue.record("not task_status"); return }
        #expect(task.state == .failed)
        #expect(task.errorCode == "INVALID_DOCUMENTS")
        #expect(task.failureDescription == "附件不符合要求：too big")

        let result = try SSEParser.event(fromLine:
            ##"data: {"event":"task_result","task":{"task_id":"task-3","state":"succeeded","prd_bytes":12,"validation_passed":true},"prd_markdown":"# PRD"}"##)
        guard case .taskResult(let done, let markdown)? = result else { Issue.record("not task_result"); return }
        #expect(done.prdBytes == 12)
        #expect(done.validationPassed == true)
        #expect(markdown == "# PRD")
    }

    @Test func malformedEventsThrow() {
        #expect(throws: RuntimeClientError.malformedEvent) {
            try SSEParser.event(fromLine: #"data: {"event":"task_status","task":{}}"#)
        }
        #expect(throws: RuntimeClientError.malformedEvent) {
            try SSEParser.event(fromLine: "data: [1,2]")
        }
    }
}

@Suite struct ISODateTests {
    @Test func parsesMicrosecondsAndOffsets() throws {
        let date = try #require(ISODate.parse("2026-11-02T12:41:18.863055+00:00"))
        let whole = try #require(ISODate.parse("2026-11-02T12:41:18Z"))
        #expect(abs(date.timeIntervalSince(whole) - 0.863055) < 0.0001)
        let shifted = try #require(ISODate.parse("2026-11-02T20:41:18+08:00"))
        #expect(shifted == whole)
        #expect(ISODate.parse("2026-11-02T12:41:18") == whole)
        #expect(ISODate.parse("yesterday") == nil)
        #expect(ISODate.parse(nil) == nil)
    }
}

@Suite struct PayloadTests {
    @Test func chatPayloadCarriesDocuments() throws {
        let data = try RuntimePayload.chat(message: "写 PRD", documents: [TaskDocument(path: "a.md", content: "x")])
        let json = try #require(try JSONSerialization.jsonObject(with: data) as? [String: Any])
        #expect(json["action"] as? String == "chat")
        #expect(json["message"] as? String == "写 PRD")
        let documents = try #require(json["documents"] as? [[String: String]])
        #expect(documents == [["path": "a.md", "content": "x"]])
    }

    @Test func actionPayload() throws {
        let data = try RuntimePayload.action("task_status", taskId: "task-1")
        #expect(String(decoding: data, as: UTF8.self) == #"{"action":"task_status","task_id":"task-1"}"#)
        #expect(String(decoding: try RuntimePayload.action("list_sessions"), as: UTF8.self) == #"{"action":"list_sessions"}"#)
    }

    @Test func requestCarriesBearerAndSession() throws {
        let request = try RuntimeClient.makeRequest(body: Data("{}".utf8), config: .defaults, sessionId: "rdta-1", accessToken: "tok")
        #expect(request.value(forHTTPHeaderField: "Authorization") == "Bearer tok")
        #expect(request.value(forHTTPHeaderField: "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id") == "rdta-1")
        #expect(request.value(forHTTPHeaderField: "Accept") == "text/event-stream")
        #expect(RuntimeClient.newSessionId().count == 41)
    }

    @Test func transientErrors() {
        #expect(RuntimeClient.isTransient(URLError(.networkConnectionLost)))
        #expect(RuntimeClient.isTransient(RuntimeClientError.http(status: 503, message: "")))
        #expect(!RuntimeClient.isTransient(RuntimeClientError.http(status: 400, message: "")))
        #expect(!RuntimeClient.isTransient(RuntimeClientError.unauthorized))
    }
}

@Suite struct TranscriptTests {
    @Test func userTurnAttachmentsAreSplitOff() {
        let split = Transcript.splitUserTurn("写个 PRD\n\n[attachments: a.md, 说明.pdf]")
        #expect(split.message == "写个 PRD")
        #expect(split.attachments == ["a.md", "说明.pdf"])
        #expect(Transcript.splitUserTurn("plain [x]").attachments.isEmpty)
    }

    @Test func jobNotesBecomeNotices() {
        let id = "task-" + String(repeating: "a", count: 32)
        #expect(Transcript.notice(fromAssistantText: "PRD generated (task \(id)); validation passed; 10 bytes stored")
            == "PRD 已生成（\(id)）")
        #expect(Transcript.notice(fromAssistantText: "PRD generation for task \(id) failed: X.")
            == "PRD 生成没有完成（\(id)）")
        #expect(Transcript.notice(fromAssistantText: "任务 ID 是 \(id)") == nil)
    }

    @Test func taskIdsAreFoundOnce() {
        let a = "task-" + String(repeating: "a", count: 32)
        let b = "task-" + String(repeating: "b", count: 32)
        #expect(Transcript.taskIds(in: "`\(a)` 和 \(b)，再说一次 \(a)") == [a, b])
    }
}
