"""Run PRD generation as an AgentCore async task and keep its state in S3.

The tool call returns as soon as the job is scheduled. While the job runs, the
app reports HealthyBusy so the session is not reclaimed; the S3 status object
lets any later request (or a new session) read the outcome.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from concurrent.futures import Future
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from pydantic import ValidationError

from orchestrator.identity import Caller
from orchestrator.memory import ConversationMemory
from orchestrator.results import ResultStore
from prd_agent.contracts import InvocationRequest, RunStatus, TaskDocument
from prd_agent.runtime import PrdGenerationService, new_task_id
from prd_agent.task_documents import (
    TaskDocumentIndexingFailed,
    TaskDocumentIndexingTimeout,
    TaskDocumentRejected,
)

logger = logging.getLogger(__name__)

# A job whose session died never writes a final status; report it as lost.
STALE_AFTER = timedelta(minutes=45)


class BusyTracker(Protocol):
    def add_async_task(self, name: str, metadata: dict | None = None) -> int: ...

    def complete_async_task(self, task_id: int) -> bool: ...


class JobRejected(ValueError):
    """The request cannot start a job; the message is safe to show the model."""


def _now() -> datetime:
    return datetime.now(UTC)


class PrdJobRunner:
    def __init__(
        self,
        *,
        service: PrdGenerationService,
        store: ResultStore,
        memory: ConversationMemory,
        busy: BusyTracker,
        project_id: str,
        repository: str,
        repository_commit: str,
        prd_contract_version: str,
        clock: Callable[[], datetime] = _now,
        task_id_factory: Callable[[], str] = new_task_id,
    ) -> None:
        self._service = service
        self._store = store
        self._memory = memory
        self._busy = busy
        self._project_id = project_id
        self._repository = repository
        self._commit = repository_commit
        self._contract_version = prd_contract_version
        self._clock = clock
        self._task_id_factory = task_id_factory
        self._futures: set[Future] = set()

    def start(
        self,
        *,
        caller: Caller,
        session_id: str,
        requirement_text: str,
        documents: list[TaskDocument],
        loop: asyncio.AbstractEventLoop,
    ) -> dict[str, Any]:
        if not documents:
            raise JobRejected(
                "No attachment in this request. Ask the user to attach at least one "
                ".md or PDF document before generating."
            )
        try:
            request = InvocationRequest(
                project_id=self._project_id,
                requirement_text=requirement_text,
                prd_contract_version=self._contract_version,
                repository=self._repository,
                repository_commit=self._commit,
                documents=documents,
                mode="one_shot",
            )
        except ValidationError as error:
            raise JobRejected(
                f"invalid generation request: {error.error_count()} problem(s)"
            ) from None

        task_id = self._task_id_factory()
        status = {
            "task_id": task_id,
            "state": "running",
            "started_at": self._clock().isoformat(),
            "requirement": requirement_text[:500],
            "documents": [document.path for document in documents],
        }
        self._store.put_status(caller.user_id, session_id, task_id, status)
        busy_id = self._busy.add_async_task("generate_prd", {"task_id": task_id})
        future = asyncio.run_coroutine_threadsafe(
            self._run(caller, session_id, request, status, busy_id), loop
        )
        self._futures.add(future)
        future.add_done_callback(self._futures.discard)
        return status

    async def _run(
        self,
        caller: Caller,
        session_id: str,
        request: InvocationRequest,
        status: dict[str, Any],
        busy_id: int,
    ) -> None:
        task_id = status["task_id"]
        final = dict(status)
        note = None
        try:
            result = await self._service.generate(run_id=task_id, request=request)
            report = result.validation_report
            final["run_status"] = result.status.value
            final["validation_passed"] = bool(report and report.passed)
            if result.status is RunStatus.COMPLETED and result.prd_markdown:
                await asyncio.to_thread(
                    self._store.put_prd,
                    caller.user_id,
                    session_id,
                    task_id,
                    result.prd_markdown,
                )
                final["state"] = "succeeded"
                final["prd_bytes"] = len(result.prd_markdown.encode("utf-8"))
                final["versions"] = (
                    result.versions.model_dump(mode="json") if result.versions else None
                )
                note = (
                    f"PRD generated (task {task_id}); validation "
                    f"{'passed' if final['validation_passed'] else 'not run'}; "
                    f"{final['prd_bytes']} bytes stored for the app to fetch."
                )
            else:
                final["state"] = "failed"
                final["error"] = {
                    "code": result.status.value.upper(),
                    "issues": [issue.rule_id for issue in result.validation_issues][
                        :20
                    ],
                    "questions": [q.question for q in result.clarification_questions],
                }
                note = (
                    f"PRD generation for task {task_id} ended as {result.status.value}."
                )
        except TaskDocumentRejected as error:
            final.update(
                state="failed",
                error={"code": "INVALID_DOCUMENTS", "message": str(error)},
            )
        except TaskDocumentIndexingTimeout:
            final.update(state="failed", error={"code": "DOCUMENT_INDEXING_TIMEOUT"})
        except TaskDocumentIndexingFailed as error:
            final.update(
                state="failed",
                error={"code": "DOCUMENT_INDEXING_FAILED", "message": str(error)},
            )
        except Exception:
            logger.exception("PRD job failed", extra={"task_id": task_id})
            final.update(state="failed", error={"code": "GENERATION_FAILED"})
        finally:
            final["finished_at"] = self._clock().isoformat()
            try:
                await asyncio.to_thread(
                    self._store.put_status, caller.user_id, session_id, task_id, final
                )
                if note or final["state"] == "failed":
                    code = final.get("error", {}).get("code")
                    text = note or f"PRD generation for task {task_id} failed: {code}."
                    await asyncio.to_thread(
                        self._memory.record,
                        user_id=caller.user_id,
                        session_id=session_id,
                        turns=[("ASSISTANT", text)],
                    )
            except Exception:
                logger.exception(
                    "could not record PRD job outcome", extra={"task_id": task_id}
                )
            finally:
                self._busy.complete_async_task(busy_id)

    def status(self, *, caller: Caller, session_id: str, task_id: str) -> dict | None:
        status = self._store.get_status(caller.user_id, session_id, task_id)
        if status and status.get("state") == "running":
            started = datetime.fromisoformat(status["started_at"])
            if self._clock() - started > STALE_AFTER:
                status = {**status, "state": "failed", "error": {"code": "JOB_LOST"}}
        return status

    def result(self, *, caller: Caller, session_id: str, task_id: str) -> str | None:
        return self._store.get_prd(caller.user_id, session_id, task_id)
