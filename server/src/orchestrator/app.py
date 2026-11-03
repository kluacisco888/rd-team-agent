"""Runtime entrypoint: authenticate, then chat or answer a model-free request.

Payload actions:
- chat: {"action": "chat", "message": str, "documents": [{"path", "content"}]}
- list_sessions / history / list_memories: no model call
- task_status / task_result: {"task_id": str}; no model call
- retry_task: {"task_id": str}; reruns a failed task's saved input, no model call
- delete_session: deletes this session's conversation, summaries and results

The caller is always the verified token's subject; the session is the Runtime
session ID header. Nothing in the payload can name another user.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

import boto3
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from botocore.config import Config
from pydantic import TypeAdapter, ValidationError

from orchestrator.agent import (
    AgentFactory,
    TurnContext,
    bedrock_agent_factory,
    build_prompt,
    build_tools,
    stream_reply,
)
from orchestrator.identity import (
    AuthenticationError,
    Caller,
    CognitoAccessTokenVerifier,
    authorization_header,
)
from orchestrator.jobs import JobRejected, PrdJobRunner
from orchestrator.memory import ConversationMemory
from orchestrator.results import SESSION_ID_PATTERN, ResultStore
from orchestrator.sessions import SessionBusy, SessionManager
from orchestrator.settings import OrchestratorSettings
from prd_agent.application import JsonContractProvider
from prd_agent.contracts import (
    MAX_TASK_DOCUMENT_TOTAL_BYTES,
    MAX_TASK_DOCUMENTS,
    TaskDocument,
)
from prd_agent.knowledge_base import (
    AgentCoreKnowledgeBaseRetriever,
    BedrockKnowledgeBaseRetrieveClient,
)
from prd_agent.manifest import FileSourceManifestProvider
from prd_agent.runtime import PrdGenerationService, StaticVersionProvider
from prd_agent.strands_adapters import (
    BedrockAgentFactory,
    StrandsModelConfig,
    StrandsPrdComposer,
    StrandsRequirementAnalyzer,
)
from prd_agent.task_documents import KnowledgeBaseTaskDocuments
from prd_agent.workflow import PrdWorkflow

logger = logging.getLogger(__name__)

MAX_MESSAGE_CHARS = 20_000
# The PRD analyzer is told this cap and the retriever enforces it.
PRD_MAX_SEARCH_QUERIES = 12
_DOCUMENTS = TypeAdapter(list[TaskDocument])


def _error(code: str, message: str) -> dict[str, Any]:
    return {"event": "error", "error": {"code": code, "message": message}}


def parse_documents(raw: Any) -> list[TaskDocument]:
    documents = _DOCUMENTS.validate_python(raw or [])
    if len(documents) > MAX_TASK_DOCUMENTS:
        raise ValueError(f"at most {MAX_TASK_DOCUMENTS} documents are allowed")
    if len({document.path for document in documents}) != len(documents):
        raise ValueError("document paths must be unique")
    total = sum(len(document.content.encode("utf-8")) for document in documents)
    if total > MAX_TASK_DOCUMENT_TOTAL_BYTES:
        raise ValueError(
            f"documents exceed {MAX_TASK_DOCUMENT_TOTAL_BYTES} bytes in total"
        )
    return documents


class OrchestratorHandler:
    def __init__(
        self,
        *,
        verifier: CognitoAccessTokenVerifier,
        memory: ConversationMemory,
        jobs: PrdJobRunner,
        sessions: SessionManager,
        agent_factory: AgentFactory,
        history_turns: int,
    ) -> None:
        self._verifier = verifier
        self._memory = memory
        self._jobs = jobs
        self._sessions = sessions
        self._agent_factory = agent_factory
        self._history_turns = history_turns

    async def handle(self, payload: Any, context: Any) -> AsyncIterator[dict[str, Any]]:
        header = authorization_header(getattr(context, "request_headers", None))
        try:
            caller = await asyncio.to_thread(self._verifier.verify, header)
        except AuthenticationError as error:
            yield _error("UNAUTHENTICATED", str(error))
            return
        session_id = getattr(context, "session_id", None) or ""
        if not SESSION_ID_PATTERN.fullmatch(session_id):
            yield _error(
                "INVALID_SESSION",
                "a Runtime session ID of 33-100 characters is required",
            )
            return
        if not isinstance(payload, dict):
            yield _error("INVALID_REQUEST", "payload must be a JSON object")
            return

        action = payload.get("action", "chat")
        try:
            if action == "chat":
                async for event in self._chat(caller, session_id, payload):
                    yield event
            elif action == "list_sessions":
                sessions, deleted = await asyncio.to_thread(
                    self._sessions.list, caller=caller
                )
                yield {
                    "event": "sessions",
                    "sessions": sessions,
                    "deleted_session_ids": deleted,
                }
            elif action == "delete_session":
                async for event in self._delete_session(caller, session_id):
                    yield event
            elif action == "retry_task":
                async for event in self._retry(caller, session_id, payload):
                    yield event
            elif action == "history":
                messages = await asyncio.to_thread(
                    self._memory.messages, user_id=caller.user_id, session_id=session_id
                )
                yield {"event": "history", "messages": messages}
            elif action == "list_memories":
                records = await asyncio.to_thread(
                    self._memory.long_term,
                    user_id=caller.user_id,
                    session_id=session_id,
                )
                yield {"event": "memories", "records": records}
            elif action in {"task_status", "task_result"}:
                async for event in self._task(caller, session_id, action, payload):
                    yield event
            else:
                yield _error("INVALID_REQUEST", f"unknown action {action!r}")
        except Exception:
            logger.exception("request failed", extra={"action": action})
            yield _error("INTERNAL_ERROR", "the request failed; see the Runtime logs")

    async def _task(
        self, caller: Caller, session_id: str, action: str, payload: dict
    ) -> AsyncIterator[dict[str, Any]]:
        task_id = payload.get("task_id")
        if not isinstance(task_id, str):
            yield _error("INVALID_REQUEST", "task_id is required")
            return
        try:
            status = await asyncio.to_thread(
                self._jobs.status, caller=caller, session_id=session_id, task_id=task_id
            )
        except ValueError:
            yield _error("INVALID_REQUEST", "task_id is malformed")
            return
        if status is None:
            yield _error("NOT_FOUND", "no such task in this session")
            return
        if action == "task_status":
            yield {"event": "task_status", "task": status}
            return
        if status.get("state") != "succeeded":
            yield _error("NOT_READY", f"task state is {status.get('state')}")
            return
        markdown = await asyncio.to_thread(
            self._jobs.result, caller=caller, session_id=session_id, task_id=task_id
        )
        if markdown is None:
            yield _error("NOT_FOUND", "the PRD object is missing")
            return
        yield {"event": "task_result", "task": status, "prd_markdown": markdown}

    async def _retry(
        self, caller: Caller, session_id: str, payload: dict
    ) -> AsyncIterator[dict[str, Any]]:
        task_id = payload.get("task_id")
        if not isinstance(task_id, str):
            yield _error("INVALID_REQUEST", "task_id is required")
            return
        try:
            status = await asyncio.to_thread(
                self._jobs.retry,
                caller=caller,
                session_id=session_id,
                task_id=task_id,
                loop=asyncio.get_running_loop(),
            )
        except JobRejected as rejected:
            yield _error("NOT_RETRYABLE", str(rejected))
            return
        except ValueError:
            yield _error("INVALID_REQUEST", "task_id is malformed")
            return
        if status is None:
            yield _error("NOT_FOUND", "no such task in this session")
            return
        yield {"event": "task_started", "task": status}

    async def _delete_session(
        self, caller: Caller, session_id: str
    ) -> AsyncIterator[dict[str, Any]]:
        try:
            deleted = await asyncio.to_thread(
                self._sessions.delete, caller=caller, session_id=session_id
            )
        except SessionBusy as busy:
            yield _error(
                "TASK_RUNNING",
                "a PRD task in this session is still running: "
                + ", ".join(busy.task_ids),
            )
            return
        yield {"event": "session_deleted", "session_id": session_id, "deleted": deleted}

    async def _chat(
        self, caller: Caller, session_id: str, payload: dict
    ) -> AsyncIterator[dict[str, Any]]:
        message = payload.get("message")
        if not isinstance(message, str) or not message.strip():
            yield _error("INVALID_REQUEST", "message is required")
            return
        if len(message) > MAX_MESSAGE_CHARS:
            yield _error(
                "INVALID_REQUEST", f"message exceeds {MAX_MESSAGE_CHARS} characters"
            )
            return
        try:
            documents = parse_documents(payload.get("documents"))
        except (ValidationError, ValueError) as error:
            detail = (
                "invalid documents"
                if isinstance(error, ValidationError)
                else str(error)
            )
            yield _error("INVALID_DOCUMENTS", detail)
            return

        history, memories = await asyncio.gather(
            asyncio.to_thread(
                self._memory.history,
                user_id=caller.user_id,
                session_id=session_id,
                max_turns=self._history_turns,
            ),
            asyncio.to_thread(
                self._memory.long_term,
                user_id=caller.user_id,
                query=message,
                kinds=("preference", "fact"),
            ),
        )
        turn = TurnContext(
            caller=caller,
            session_id=session_id,
            documents=documents,
            loop=asyncio.get_running_loop(),
        )
        agent = self._agent_factory(build_tools(self._jobs, turn), history)
        reply: list[str] = []
        yield {"event": "started", "session_id": session_id}
        async for text in stream_reply(
            agent, build_prompt(message, documents, memories)
        ):
            reply.append(text)
            yield {"event": "text", "text": text}
        for task in turn.started_tasks:
            yield {"event": "task_started", "task": task}

        user_turn = message
        if documents:
            user_turn += (
                "\n\n[attachments: " + ", ".join(d.path for d in documents) + "]"
            )
        await asyncio.to_thread(
            self._memory.record,
            user_id=caller.user_id,
            session_id=session_id,
            turns=[("USER", user_turn), ("ASSISTANT", "".join(reply))],
        )
        yield {"event": "done"}


_AWS_CLIENT_CONFIG = Config(
    connect_timeout=10, read_timeout=60, retries={"mode": "standard", "max_attempts": 5}
)


def build_orchestrator_app(settings: OrchestratorSettings) -> BedrockAgentCoreApp:
    """Compose the production graph; AWS clients use the Runtime execution role."""

    manifests = FileSourceManifestProvider(settings.source_manifest_root)
    manifest_path = manifests.path_for(
        project_id=settings.project_id,
        repository=settings.repository_name,
        commit_sha=settings.repository_commit,
    )
    if not manifest_path.is_file():
        raise RuntimeError(
            f"source manifest for the configured commit is missing: {manifest_path}"
        )

    aws = boto3.Session(region_name=settings.aws_region)
    app = BedrockAgentCoreApp(debug=False)
    prd_agents = BedrockAgentFactory(
        StrandsModelConfig(
            model_id=settings.model_id,
            region_name=settings.aws_region,
            max_tokens=settings.prd_max_output_tokens,
            read_timeout_seconds=settings.model_read_timeout_seconds,
        )
    )
    service = PrdGenerationService(
        workflow=PrdWorkflow(
            analyzer=StrandsRequirementAnalyzer(
                prd_agents, max_search_queries=PRD_MAX_SEARCH_QUERIES
            ),
            retriever=AgentCoreKnowledgeBaseRetriever(
                client=BedrockKnowledgeBaseRetrieveClient(
                    client=aws.client(
                        "bedrock-agent-runtime", config=_AWS_CLIENT_CONFIG
                    ),
                    knowledge_base_id=settings.knowledge_base_id,
                ),
                manifest_provider=manifests,
                max_queries=PRD_MAX_SEARCH_QUERIES,
            ),
            composer=StrandsPrdComposer(prd_agents),
            max_repair_attempts=1,
        ),
        contract_provider=JsonContractProvider(settings.prd_contract_path),
        version_provider=StaticVersionProvider(
            prompt_version=settings.prompt_version,
            runtime_version=settings.runtime_version,
            model_id=settings.model_id,
        ),
        document_stager=KnowledgeBaseTaskDocuments(
            client=aws.client("bedrock-agent", config=_AWS_CLIENT_CONFIG),
            knowledge_base_id=settings.knowledge_base_id,
            data_source_id=settings.task_data_source_id,
            project_id=settings.project_id,
        ),
    )
    memory = ConversationMemory(
        client=aws.client("bedrock-agentcore", config=_AWS_CLIENT_CONFIG),
        memory_id=settings.memory_id,
        preference_strategy_id=settings.preference_strategy_id,
        semantic_strategy_id=settings.semantic_strategy_id,
        summary_strategy_id=settings.summary_strategy_id,
    )
    store = ResultStore(
        client=aws.client("s3", config=_AWS_CLIENT_CONFIG),
        bucket=settings.results_bucket,
        expected_owner=settings.aws_account_id,
    )
    jobs = PrdJobRunner(
        service=service,
        store=store,
        memory=memory,
        busy=app,
        project_id=settings.project_id,
        repository=settings.repository_name,
        repository_commit=settings.repository_commit,
        prd_contract_version=settings.prd_contract_version,
    )
    handler = OrchestratorHandler(
        verifier=CognitoAccessTokenVerifier(
            issuer=settings.cognito_issuer,
            allowed_client_ids=settings.allowed_client_ids,
        ),
        memory=memory,
        jobs=jobs,
        sessions=SessionManager(memory=memory, jobs=jobs, store=store),
        agent_factory=bedrock_agent_factory(
            model_id=settings.model_id,
            region=settings.aws_region,
            max_tokens=settings.orchestrator_max_output_tokens,
            read_timeout_seconds=settings.model_read_timeout_seconds,
        ),
        history_turns=settings.history_turns,
    )

    @app.entrypoint
    async def invoke(payload: Any, context: Any) -> AsyncIterator[dict[str, Any]]:
        async for event in handler.handle(payload, context):
            yield event

    return app
