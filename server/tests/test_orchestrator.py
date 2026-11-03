from __future__ import annotations

import asyncio
import json
import time
import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import jwt
from botocore.exceptions import ClientError
from cryptography.hazmat.primitives.asymmetric import rsa

from orchestrator.agent import TurnContext, build_prompt, build_tools
from orchestrator.app import OrchestratorHandler, parse_documents
from orchestrator.identity import (
    AuthenticationError,
    Caller,
    CognitoAccessTokenVerifier,
    authorization_header,
)
from orchestrator.jobs import JobRejected, PrdJobRunner
from orchestrator.memory import ConversationMemory
from orchestrator.results import ResultStore, result_key
from orchestrator.settings import REQUIRED_ENVIRONMENT_KEYS, load_orchestrator_settings
from prd_agent.contracts import GenerationResult, RunStatus, TaskDocument, VersionSet

ISSUER = "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_TestPool"
USER = "1f2e3d4c-0000-4000-8000-000000000001"
OTHER = "1f2e3d4c-0000-4000-8000-000000000002"
SESSION = "s" * 40
TASK = "task-" + "0" * 32
COMMIT = "a" * 40
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class StaticKeys:
    def get_signing_key_from_jwt(self, token: str) -> Any:
        return SimpleNamespace(key=KEY.public_key())


def token(**overrides: Any) -> str:
    now = int(time.time())
    claims = {
        "sub": USER,
        "iss": ISSUER,
        "client_id": "cli-client",
        "token_use": "access",
        "iat": now,
        "exp": now + 600,
        "username": "tester1",
    }
    claims.update(overrides)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, KEY, algorithm="RS256")


def verifier() -> CognitoAccessTokenVerifier:
    return CognitoAccessTokenVerifier(
        issuer=ISSUER,
        allowed_client_ids=["cli-client", "mac-client"],
        key_source=StaticKeys(),
    )


class SettingsTest(unittest.TestCase):
    def environ(self) -> dict[str, str]:
        values = {key: "x" for key in REQUIRED_ENVIRONMENT_KEYS}
        values.update(
            AWS_REGION="us-east-1",
            AWS_ACCOUNT_ID="123456789012",
            BEDROCK_MODEL_ID="us.anthropic.claude-opus-5-5",
            KNOWLEDGE_BASE_ID="ABCDEFGHIJ",
            TASK_DATA_SOURCE_ID="KLMNOPQRST",
            REPOSITORY_COMMIT=COMMIT,
            PRD_CONTRACT_PATH="/opt/contract.json",
            SOURCE_MANIFEST_ROOT="/opt",
            RESULTS_BUCKET="results-bucket",
            COGNITO_ISSUER=ISSUER,
            ALLOWED_CLIENT_IDS=" a , b ,",
        )
        return values

    def test_loads_and_splits_clients(self) -> None:
        settings = load_orchestrator_settings(self.environ())
        self.assertEqual(settings.allowed_client_ids, ("a", "b"))

    def test_missing_key_fails_closed(self) -> None:
        environ = self.environ()
        del environ["MEMORY_ID"]
        with self.assertRaisesRegex(ValueError, "MEMORY_ID"):
            load_orchestrator_settings(environ)


class IdentityTest(unittest.TestCase):
    def test_valid_access_token(self) -> None:
        caller = verifier().verify(f"Bearer {token()}")
        self.assertEqual((caller.user_id, caller.username), (USER, "tester1"))

    def test_rejections(self) -> None:
        cases = {
            "no header": None,
            "not bearer": f"Basic {token()}",
            "id token": f"Bearer {token(token_use='id')}",
            "other client": f"Bearer {token(client_id='evil')}",
            "wrong issuer": f"Bearer {token(iss='https://cognito-idp.us-east-1.amazonaws.com/x')}",
            "expired": f"Bearer {token(exp=int(time.time()) - 3600)}",
            "no client_id": f"Bearer {token(client_id=None)}",
            "bad subject": f"Bearer {token(sub='../other')}",
            "garbage": "Bearer not-a-jwt",
        }
        for name, header in cases.items():
            with self.subTest(name), self.assertRaises(AuthenticationError):
                verifier().verify(header)

    def test_unsigned_token_is_rejected(self) -> None:
        forged = jwt.encode({"sub": USER, "iss": ISSUER}, key=None, algorithm="none")
        with self.assertRaises(AuthenticationError):
            verifier().verify(f"Bearer {forged}")

    def test_header_lookup_ignores_case(self) -> None:
        self.assertEqual(
            authorization_header({"authorization": "Bearer x"}), "Bearer x"
        )
        self.assertIsNone(authorization_header(None))


class FakeMemoryClient:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self.records: dict[str, list[dict]] = {}
        self.sessions: list[dict] = []

    def create_event(self, **kwargs: Any) -> dict:
        self.events.append(kwargs)
        return {}

    def list_events(self, **kwargs: Any) -> dict:
        return {
            "events": [
                e
                for e in self.events
                if e["actorId"] == kwargs["actorId"]
                and e["sessionId"] == kwargs["sessionId"]
            ]
        }

    def list_sessions(self, **kwargs: Any) -> dict:
        return {"sessionSummaries": self.sessions}

    def list_memory_records(self, **kwargs: Any) -> dict:
        return {"memoryRecordSummaries": self.records.get(kwargs["namespace"], [])}

    def retrieve_memory_records(self, **kwargs: Any) -> dict:
        return {"memoryRecordSummaries": self.records.get(kwargs["namespace"], [])}


def memory(client: FakeMemoryClient) -> ConversationMemory:
    ticks = iter(
        datetime(2026, 11, 2, tzinfo=UTC) + timedelta(seconds=i) for i in range(100)
    )
    return ConversationMemory(
        client=client,
        memory_id="mem",
        preference_strategy_id="pref",
        semantic_strategy_id="fact",
        summary_strategy_id="sum",
        clock=lambda: next(ticks),
    )


class MemoryTest(unittest.TestCase):
    def test_history_alternates_and_ends_with_assistant(self) -> None:
        client = FakeMemoryClient()
        store = memory(client)
        store.record(user_id=USER, session_id=SESSION, turns=[("ASSISTANT", "stray")])
        store.record(
            user_id=USER,
            session_id=SESSION,
            turns=[("USER", "hi"), ("ASSISTANT", "hello")],
        )
        store.record(
            user_id=USER, session_id=SESSION, turns=[("ASSISTANT", "PRD ready")]
        )
        store.record(user_id=USER, session_id=SESSION, turns=[("USER", "dangling")])
        history = store.history(user_id=USER, session_id=SESSION, max_turns=10)
        self.assertEqual([m["role"] for m in history], ["user", "assistant"])
        self.assertEqual(history[1]["content"][0]["text"], "hello\n\nPRD ready")

    def test_other_users_events_are_not_read(self) -> None:
        client = FakeMemoryClient()
        store = memory(client)
        store.record(user_id=OTHER, session_id=SESSION, turns=[("USER", "secret")])
        self.assertEqual(store.messages(user_id=USER, session_id=SESSION), [])

    def test_long_term_uses_own_namespace_and_drops_foreign_records(self) -> None:
        client = FakeMemoryClient()
        own = ConversationMemory.namespace("pref", USER)
        client.records[own] = [
            {"content": {"text": "likes Given/When/Then"}, "namespaces": [own]},
            {
                "content": {"text": "leak"},
                "namespaces": [ConversationMemory.namespace("pref", OTHER)],
            },
        ]
        records = memory(client).long_term(user_id=USER, kinds=("preference",))
        self.assertEqual([r["text"] for r in records], ["likes Given/When/Then"])

    def test_summaries_are_limited_to_the_given_session(self) -> None:
        client = FakeMemoryClient()
        actor = ConversationMemory.namespace("sum", USER)
        this_session = f"{actor}session/{SESSION}/"
        client.records[this_session] = [
            {"content": {"text": "this session"}, "namespaces": [this_session]},
            {
                "content": {"text": "other session"},
                "namespaces": [f"{actor}session/{'x' * 40}/"],
            },
        ]
        client.records[actor] = [
            {"content": {"text": "any session"}, "namespaces": [actor]}
        ]
        scoped = memory(client).long_term(
            user_id=USER, kinds=("summary",), session_id=SESSION
        )
        self.assertEqual([r["text"] for r in scoped], ["this session"])
        unscoped = memory(client).long_term(user_id=USER, kinds=("summary",))
        self.assertEqual([r["text"] for r in unscoped], ["any session"])

    def test_sessions_filters_other_actors(self) -> None:
        client = FakeMemoryClient()
        client.sessions = [
            {
                "sessionId": "a" * 40,
                "actorId": USER,
                "createdAt": datetime(2026, 1, 1, tzinfo=UTC),
            },
            {"sessionId": "b" * 40, "actorId": OTHER},
        ]
        self.assertEqual(
            [s["session_id"] for s in memory(client).sessions(user_id=USER)], ["a" * 40]
        )

    def test_sessions_of_user_without_events_is_empty(self) -> None:
        client = FakeMemoryClient()

        def unknown_actor(**kwargs: Any) -> dict:
            raise ClientError(
                {"Error": {"Code": "ResourceNotFoundException"}}, "ListSessions"
            )

        client.list_sessions = unknown_actor
        self.assertEqual(memory(client).sessions(user_id=USER), [])

    def test_sessions_other_errors_propagate(self) -> None:
        client = FakeMemoryClient()

        def throttled(**kwargs: Any) -> dict:
            raise ClientError(
                {"Error": {"Code": "ThrottlingException"}}, "ListSessions"
            )

        client.list_sessions = throttled
        with self.assertRaises(ClientError):
            memory(client).sessions(user_id=USER)


class ResultKeyTest(unittest.TestCase):
    def test_valid_key(self) -> None:
        self.assertEqual(
            result_key(USER, SESSION, TASK, "prd.md"),
            f"results/{USER}/{SESSION}/{TASK}/prd.md",
        )

    def test_traversal_and_bad_parts_rejected(self) -> None:
        for args in (
            ("../x", SESSION, TASK, "prd.md"),
            (USER, "short", TASK, "prd.md"),
            (USER, SESSION, "task-../../x", "prd.md"),
            (USER, SESSION, TASK, "other.txt"),
        ):
            with self.subTest(args=args), self.assertRaises(ValueError):
                result_key(*args)


class FakeS3:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_object(self, **kwargs: Any) -> dict:
        assert kwargs["ServerSideEncryption"] == "AES256"
        self.objects[kwargs["Key"]] = kwargs["Body"]
        return {}

    def get_object(self, **kwargs: Any) -> dict:
        if kwargs["Key"] not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": SimpleNamespace(read=lambda: self.objects[kwargs["Key"]])}


class FakeBusy:
    def __init__(self) -> None:
        self.active: set[int] = set()
        self.next = 0

    def add_async_task(self, name: str, metadata: dict | None = None) -> int:
        self.next += 1
        self.active.add(self.next)
        return self.next

    def complete_async_task(self, task_id: int) -> bool:
        self.active.discard(task_id)
        return True


VERSIONS = VersionSet(
    knowledge_base_snapshot="snap",
    repository_commit=COMMIT,
    prd_contract_version="v1",
    prompt_version="p",
    runtime_version="r",
    model_id="us.anthropic.claude-opus-5-5",
)


class FakeService:
    def __init__(self, outcome: Any) -> None:
        self.outcome = outcome
        self.requests: list = []

    async def generate(self, *, run_id: str, request: Any) -> GenerationResult:
        self.requests.append(request)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


DOC = [TaskDocument(path="spec.md", content="# Spec\nImport users from CSV.")]
CALLER = Caller(user_id=USER, username="tester1", client_id="cli-client")


def runner(
    service: FakeService,
    s3: FakeS3,
    client: FakeMemoryClient,
    busy: FakeBusy,
    clock=None,
):
    return PrdJobRunner(
        service=service,
        store=ResultStore(client=s3, bucket="b", expected_owner="123456789012"),
        memory=memory(client),
        busy=busy,
        project_id="demo",
        repository="sample",
        repository_commit=COMMIT,
        prd_contract_version="v1",
        clock=clock or (lambda: datetime.now(UTC)),
        task_id_factory=lambda: TASK,
    )


class JobsTest(unittest.IsolatedAsyncioTestCase):
    async def _start_and_wait(self, jobs: PrdJobRunner, busy: FakeBusy) -> dict:
        status = jobs.start(
            caller=CALLER,
            session_id=SESSION,
            requirement_text="Build CSV import",
            documents=DOC,
            loop=asyncio.get_running_loop(),
        )
        self.assertEqual(status["state"], "running")
        self.assertEqual(len(busy.active), 1)
        for _ in range(100):
            if not busy.active:
                break
            await asyncio.sleep(0.01)
        self.assertEqual(busy.active, set())
        return status

    async def test_success_stores_prd_status_and_memory_note(self) -> None:
        s3, client, busy = FakeS3(), FakeMemoryClient(), FakeBusy()
        result = GenerationResult(
            run_id=TASK,
            status=RunStatus.COMPLETED,
            versions=VERSIONS,
            prd_markdown="# PRD",
        )
        service = FakeService(result)
        jobs = runner(service, s3, client, busy)
        await self._start_and_wait(jobs, busy)
        self.assertEqual(service.requests[0].mode, "one_shot")
        self.assertEqual(service.requests[0].repository_commit, COMMIT)
        final = jobs.status(caller=CALLER, session_id=SESSION, task_id=TASK)
        self.assertEqual(final["state"], "succeeded")
        self.assertEqual(
            jobs.result(caller=CALLER, session_id=SESSION, task_id=TASK), "# PRD"
        )
        self.assertIn(
            "PRD generated",
            client.events[-1]["payload"][0]["conversational"]["content"]["text"],
        )
        other = Caller(user_id=OTHER, username=None, client_id="cli-client")
        self.assertIsNone(jobs.status(caller=other, session_id=SESSION, task_id=TASK))

    async def test_exception_marks_failed_without_details(self) -> None:
        s3, client, busy = FakeS3(), FakeMemoryClient(), FakeBusy()
        jobs = runner(FakeService(RuntimeError("secret detail")), s3, client, busy)
        await self._start_and_wait(jobs, busy)
        final = jobs.status(caller=CALLER, session_id=SESSION, task_id=TASK)
        self.assertEqual(final["state"], "failed")
        self.assertEqual(final["error"], {"code": "GENERATION_FAILED"})
        self.assertNotIn("secret", json.dumps(final))

    async def test_start_requires_attachment(self) -> None:
        jobs = runner(FakeService(None), FakeS3(), FakeMemoryClient(), FakeBusy())
        with self.assertRaises(JobRejected):
            jobs.start(
                caller=CALLER,
                session_id=SESSION,
                requirement_text="x",
                documents=[],
                loop=asyncio.get_running_loop(),
            )

    def test_running_status_goes_stale(self) -> None:
        s3 = FakeS3()
        started = datetime(2026, 11, 2, tzinfo=UTC)
        jobs = runner(
            FakeService(None),
            s3,
            FakeMemoryClient(),
            FakeBusy(),
            clock=lambda: started + timedelta(hours=2),
        )
        ResultStore(client=s3, bucket="b", expected_owner="1").put_status(
            USER,
            SESSION,
            TASK,
            {"task_id": TASK, "state": "running", "started_at": started.isoformat()},
        )
        status = jobs.status(caller=CALLER, session_id=SESSION, task_id=TASK)
        self.assertEqual(status["error"]["code"], "JOB_LOST")


class FakeAgent:
    def __init__(self, tools: list, chunks: list[str], call_tool: str | None) -> None:
        self.tools = {t.tool_name: t for t in tools}
        self.chunks = chunks
        self.call_tool = call_tool
        self.prompts: list[str] = []

    async def stream_async(self, prompt: str):
        self.prompts.append(prompt)
        if self.call_tool:
            self.tools[self.call_tool](requirement_text="Build CSV import")
        for chunk in self.chunks:
            yield {"data": chunk}
        yield {"result": object()}


class HandlerTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.s3, self.client, self.busy = FakeS3(), FakeMemoryClient(), FakeBusy()
        result = GenerationResult(
            run_id=TASK,
            status=RunStatus.COMPLETED,
            versions=VERSIONS,
            prd_markdown="# PRD",
        )
        self.jobs = runner(FakeService(result), self.s3, self.client, self.busy)
        self.agents: list[FakeAgent] = []

    def handler(self, *, call_tool: str | None = None) -> OrchestratorHandler:
        def factory(tools: list, history: list) -> FakeAgent:
            agent = FakeAgent(tools, ["Started ", "task."], call_tool)
            self.agents.append(agent)
            return agent

        return OrchestratorHandler(
            verifier=verifier(),
            memory=memory(self.client),
            jobs=self.jobs,
            agent_factory=factory,
            history_turns=10,
        )

    async def run_handler(
        self, handler, payload, *, header=None, session=SESSION
    ) -> list[dict]:
        context = SimpleNamespace(
            session_id=session,
            request_headers={"Authorization": header or f"Bearer {token()}"},
        )
        return [event async for event in handler.handle(payload, context)]

    async def test_unauthenticated_request_gets_no_data(self) -> None:
        events = await self.run_handler(
            self.handler(), {"action": "list_sessions"}, header="Bearer bad"
        )
        self.assertEqual(
            events,
            [
                {
                    "event": "error",
                    "error": {
                        "code": "UNAUTHENTICATED",
                        "message": "invalid or expired token",
                    },
                }
            ],
        )

    async def test_short_session_id_rejected(self) -> None:
        events = await self.run_handler(
            self.handler(), {"action": "history"}, session="abc"
        )
        self.assertEqual(events[0]["error"]["code"], "INVALID_SESSION")

    async def test_chat_starts_job_and_records_turn(self) -> None:
        events = await self.run_handler(
            self.handler(call_tool="generate_prd"),
            {
                "action": "chat",
                "message": "生成 PRD",
                "documents": [d.model_dump() for d in DOC],
            },
        )
        kinds = [e["event"] for e in events]
        self.assertEqual(kinds, ["started", "text", "text", "task_started", "done"])
        self.assertEqual(events[3]["task"]["task_id"], TASK)
        recorded = self.client.events[0]["payload"]
        self.assertIn(
            "[attachments: spec.md]", recorded[0]["conversational"]["content"]["text"]
        )
        self.assertEqual(
            recorded[1]["conversational"]["content"]["text"], "Started task."
        )
        self.assertIn("<attachments>", self.agents[0].prompts[0])
        for _ in range(100):
            if not self.busy.active:
                break
            await asyncio.sleep(0.01)
        status = await self.run_handler(
            self.handler(), {"action": "task_result", "task_id": TASK}
        )
        self.assertEqual(status[0]["prd_markdown"], "# PRD")

    async def test_task_of_another_user_is_not_found(self) -> None:
        ResultStore(client=self.s3, bucket="b", expected_owner="1").put_status(
            OTHER,
            SESSION,
            TASK,
            {
                "task_id": TASK,
                "state": "succeeded",
                "started_at": datetime.now(UTC).isoformat(),
            },
        )
        events = await self.run_handler(
            self.handler(), {"action": "task_status", "task_id": TASK}
        )
        self.assertEqual(events[0]["error"]["code"], "NOT_FOUND")

    async def test_bad_documents_and_actions(self) -> None:
        handler = self.handler()
        bad_doc = await self.run_handler(
            handler,
            {"message": "x", "documents": [{"path": "../x.md", "content": "y"}]},
        )
        self.assertEqual(bad_doc[0]["error"]["code"], "INVALID_DOCUMENTS")
        unknown = await self.run_handler(handler, {"action": "drop_tables"})
        self.assertEqual(unknown[0]["error"]["code"], "INVALID_REQUEST")


class PromptAndToolsTest(unittest.IsolatedAsyncioTestCase):
    def test_prompt_marks_attachments_and_memory_as_data(self) -> None:
        prompt = build_prompt(
            "hi", DOC, [{"kind": "preference", "text": "short replies"}]
        )
        self.assertIn("<attachments>", prompt)
        self.assertIn("[preference] short replies", prompt)
        self.assertTrue(prompt.endswith("</user_message>"))

    async def test_generate_tool_reports_missing_attachment(self) -> None:
        jobs = runner(FakeService(None), FakeS3(), FakeMemoryClient(), FakeBusy())
        turn = TurnContext(
            caller=CALLER,
            session_id=SESSION,
            documents=[],
            loop=asyncio.get_running_loop(),
        )
        generate, _ = build_tools(jobs, turn)
        reply = json.loads(generate(requirement_text="x"))
        self.assertIn("attach", reply["error"])
        self.assertEqual(turn.started_tasks, [])

    def test_parse_documents_limits(self) -> None:
        with self.assertRaises(ValueError):
            parse_documents(
                [{"path": "a.md", "content": "x"}, {"path": "a.md", "content": "y"}]
            )
        with self.assertRaises(ValueError):
            parse_documents([{"path": f"{i}.md", "content": "x"} for i in range(21)])


if __name__ == "__main__":
    unittest.main()
