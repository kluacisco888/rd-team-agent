"""Per-invocation document isolation inside the shared managed knowledge base.

Each Runtime invocation writes the caller's files, one knowledge-base document
per file, into a custom data source tagged with a Runtime-generated ``task_id``.
The managed knowledge base chunks, embeds and indexes them itself. The Runtime
waits until every file is queryable, and deletes them when the invocation ends.
Retrieval filters on the same ``task_id`` (see ``knowledge_base``), so one
caller's documents can never become evidence in another caller's PRD.

Deletion only saves storage cost: a leaked document carries a task_id that no
later invocation will ever query.
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import logging
import time
from collections.abc import (
    AsyncIterator,
    Awaitable,
    Callable,
    Iterator,
    Mapping,
    Sequence,
)
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Protocol

from prd_agent.contracts import InvocationRequest

logger = logging.getLogger(__name__)

# NOT_FOUND can be reported briefly right after an accepted ingest call.
_PENDING_STATUSES = frozenset({"STARTING", "PENDING", "IN_PROGRESS", "NOT_FOUND"})
# Text is queryable at TEXT_INDEXED; INDEXED only adds multimodal elements.
_READY_STATUSES = frozenset({"TEXT_INDEXED", "INDEXED"})
_THROTTLING_CODES = frozenset({"ThrottlingException", "ServiceQuotaExceededException"})
# Exceeding the per-knowledge-base concurrent ingestion limit is reported as a
# ValidationException, so ingestion also retries that code with backoff.
_INGEST_RETRY_CODES = _THROTTLING_CODES | {"ValidationException"}
_API_BATCH_LIMIT = 10


class TaskDocumentRejected(ValueError):
    """The caller's documents cannot be staged; the message is safe to return."""


class TaskDocumentStagingError(RuntimeError):
    """The knowledge base could not make this task's documents queryable."""


class TaskDocumentIndexingFailed(TaskDocumentStagingError):
    """The knowledge base reported a terminal non-ready status for a document."""


class TaskDocumentIndexingTimeout(TaskDocumentStagingError):
    """Some documents were still being indexed when the wait budget ran out."""

    def __init__(self, *, pending: int, total: int, timeout_seconds: float) -> None:
        super().__init__(
            f"{pending} of {total} task documents were not indexed within "
            f"{timeout_seconds:g} seconds"
        )
        self.pending = pending
        self.total = total
        self.timeout_seconds = timeout_seconds


class KnowledgeBaseDocumentsClient(Protocol):
    """The subset of the boto3 ``bedrock-agent`` client used for direct ingestion."""

    def ingest_knowledge_base_documents(self, **kwargs: Any) -> Mapping[str, Any]: ...

    def get_knowledge_base_documents(self, **kwargs: Any) -> Mapping[str, Any]: ...

    def delete_knowledge_base_documents(self, **kwargs: Any) -> Mapping[str, Any]: ...


@dataclass(frozen=True, slots=True)
class StagedTask:
    task_id: str
    document_ids: tuple[str, ...]


def _batches(items: Sequence[Any], size: int) -> Iterator[Sequence[Any]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _error_code(error: BaseException) -> str | None:
    response = getattr(error, "response", None)
    if isinstance(response, Mapping):
        code = response.get("Error", {}).get("Code")
        return code if isinstance(code, str) else None
    return None


def _identifier(document_id: str) -> dict[str, Any]:
    return {"dataSourceType": "CUSTOM", "custom": {"id": document_id}}


def _detail_id(detail: Mapping[str, Any]) -> str | None:
    return detail.get("identifier", {}).get("custom", {}).get("id")


def _attribute(key: str, value: str) -> dict[str, Any]:
    return {"key": key, "value": {"type": "STRING", "stringValue": value}}


class KnowledgeBaseTaskDocuments:
    """Stage one invocation's documents for the lifetime of an ``async with`` block."""

    def __init__(
        self,
        *,
        client: KnowledgeBaseDocumentsClient,
        knowledge_base_id: str,
        data_source_id: str,
        project_id: str,
        poll_interval_seconds: float = 5.0,
        index_timeout_seconds: float = 300.0,
        max_attempts: int = 5,
        retry_base_seconds: float = 1.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not (knowledge_base_id and data_source_id and project_id):
            raise ValueError(
                "knowledge_base_id, data_source_id and project_id are required"
            )
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if poll_interval_seconds <= 0 or index_timeout_seconds <= 0:
            raise ValueError("poll interval and index timeout must be positive")
        self._client = client
        self._knowledge_base_id = knowledge_base_id
        self._data_source_id = data_source_id
        self._project_id = project_id
        self._poll_interval = poll_interval_seconds
        self._index_timeout = index_timeout_seconds
        self._max_attempts = max_attempts
        self._retry_base = retry_base_seconds
        self._sleep = sleep
        self._clock = clock

    def build_documents(
        self,
        *,
        task_id: str,
        request: InvocationRequest,
    ) -> list[dict[str, Any]]:
        """Tag each caller file as one knowledge-base document, without calling AWS."""

        if request.project_id != self._project_id:
            raise TaskDocumentRejected("project_id is not served by this Runtime")
        return [
            self._record(task_id, index, document.path, document.content)
            for index, document in enumerate(request.documents)
        ]

    def _record(
        self, task_id: str, index: int, path: str, content: str
    ) -> dict[str, Any]:
        attributes = {
            "project_id": self._project_id,
            "task_id": task_id,
            "source_type": "document",
            "source_path": path,
            "source_uri": f"upload://{path}",
            # The knowledge base chunks the file, so the file is the citation locator.
            "locator": path,
            "document_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        }
        return {
            "content": {
                "dataSourceType": "CUSTOM",
                "custom": {
                    "customDocumentIdentifier": {"id": f"{task_id}-{index:02d}"},
                    "sourceType": "IN_LINE",
                    "inlineContent": {
                        "type": "TEXT",
                        "textContent": {"data": content},
                    },
                },
            },
            "metadata": {
                "type": "IN_LINE_ATTRIBUTE",
                "inlineAttributes": [
                    _attribute(key, value) for key, value in attributes.items()
                ],
            },
        }

    @asynccontextmanager
    async def stage(
        self,
        *,
        task_id: str,
        request: InvocationRequest,
    ) -> AsyncIterator[StagedTask]:
        """Ingest, wait until queryable, yield, then always delete what was written."""

        documents = self.build_documents(task_id=task_id, request=request)
        paths = {
            f"{task_id}-{index:02d}": document.path
            for index, document in enumerate(request.documents)
        }
        written: list[str] = []
        try:
            started = self._clock()
            for batch in _batches(documents, _API_BATCH_LIMIT):
                ids = [
                    item["content"]["custom"]["customDocumentIdentifier"]["id"]
                    for item in batch
                ]
                # Recorded before the call: a failed call may have accepted some.
                written.extend(ids)
                response = await self._call(
                    self._client.ingest_knowledge_base_documents,
                    retry_codes=_INGEST_RETRY_CODES,
                    knowledgeBaseId=self._knowledge_base_id,
                    dataSourceId=self._data_source_id,
                    documents=list(batch),
                )
                for detail in response.get("documentDetails", []):
                    status = detail.get("status")
                    if status not in _PENDING_STATUSES | _READY_STATUSES:
                        self._fail(task_id, paths, detail)
            await self._wait_until_ready(task_id, written, paths, started)
            yield StagedTask(task_id=task_id, document_ids=tuple(written))
        finally:
            await self._delete(task_id, written)

    def _fail(
        self, task_id: str, paths: Mapping[str, str], detail: Mapping[str, Any]
    ) -> None:
        document_id = _detail_id(detail)
        path = paths.get(document_id or "", "unknown document")
        status = detail.get("status")
        logger.warning(
            "task document %s (%s) ended in status %s: %s",
            document_id,
            path,
            status,
            detail.get("statusReason"),
            extra={"task_id": task_id},
        )
        raise TaskDocumentIndexingFailed(
            f"The knowledge base could not index {path!r} (status {status})."
        )

    async def _wait_until_ready(
        self,
        task_id: str,
        document_ids: Sequence[str],
        paths: Mapping[str, str],
        started: float,
    ) -> None:
        deadline = started + self._index_timeout
        pending = list(document_ids)
        last_status: dict[str, str | None] = {}
        while True:
            still_pending: list[str] = []
            for batch in _batches(pending, _API_BATCH_LIMIT):
                response = await self._call(
                    self._client.get_knowledge_base_documents,
                    retry_codes=_THROTTLING_CODES,
                    knowledgeBaseId=self._knowledge_base_id,
                    dataSourceId=self._data_source_id,
                    documentIdentifiers=[_identifier(item) for item in batch],
                )
                details = {
                    _detail_id(detail): detail
                    for detail in response.get("documentDetails", [])
                }
                for document_id in batch:
                    detail = details.get(document_id, {})
                    status = detail.get("status")
                    last_status[document_id] = status
                    if status in _READY_STATUSES:
                        logger.info(
                            "task document %s (%s) %s after %.1f seconds",
                            document_id,
                            paths.get(document_id),
                            status,
                            self._clock() - started,
                            extra={"task_id": task_id},
                        )
                    elif status is None or status in _PENDING_STATUSES:
                        still_pending.append(document_id)
                    else:
                        self._fail(task_id, paths, detail)
            pending = still_pending
            if not pending:
                return
            if self._clock() >= deadline:
                for document_id in pending:
                    logger.warning(
                        "task document %s (%s) still %s after %g seconds",
                        document_id,
                        paths.get(document_id),
                        last_status.get(document_id),
                        self._index_timeout,
                        extra={"task_id": task_id},
                    )
                raise TaskDocumentIndexingTimeout(
                    pending=len(pending),
                    total=len(document_ids),
                    timeout_seconds=self._index_timeout,
                )
            await self._sleep(self._poll_interval)

    async def _delete(self, task_id: str, document_ids: Sequence[str]) -> None:
        failed = 0
        for batch in _batches(document_ids, _API_BATCH_LIMIT):
            try:
                await self._call(
                    self._client.delete_knowledge_base_documents,
                    retry_codes=_THROTTLING_CODES,
                    knowledgeBaseId=self._knowledge_base_id,
                    dataSourceId=self._data_source_id,
                    documentIdentifiers=[_identifier(item) for item in batch],
                )
            except Exception as error:  # cleanup must never mask the task outcome
                failed += len(batch)
                logger.warning(
                    "task document cleanup failed",
                    extra={"task_id": task_id, "error_code": _error_code(error)},
                )
        if failed:
            logger.warning(
                "task documents left for scheduled cleanup",
                extra={"task_id": task_id, "document_count": failed},
            )

    async def _call(
        self,
        operation: Callable[..., Mapping[str, Any]],
        *,
        retry_codes: frozenset[str],
        **kwargs: Any,
    ) -> Mapping[str, Any]:
        for attempt in range(1, self._max_attempts + 1):
            try:
                return await asyncio.to_thread(functools.partial(operation, **kwargs))
            except Exception as error:
                if (
                    _error_code(error) not in retry_codes
                    or attempt == self._max_attempts
                ):
                    raise
                await self._sleep(self._retry_base * 2 ** (attempt - 1))
        raise AssertionError("retry loop must return or raise")
