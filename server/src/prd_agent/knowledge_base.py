"""Strict, task-scoped AgentCore Knowledge Base retrieval boundary.

The Runtime calls the managed knowledge base ``Retrieve`` API directly with a
metadata filter it builds itself: this invocation's task documents, plus the
project's code pinned to one source snapshot. Every returned passage is checked
against the same scope again, so a filter that was ignored or mis-applied fails
closed instead of leaking another task's documents into a prompt.
"""

from __future__ import annotations

import asyncio
import functools
import logging
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from pydantic import ConfigDict, Field

from prd_agent.contracts import GenerationRequest, SourceType, StrictModel
from prd_agent.workflow import RetrievalBundle, RetrievedChunk

logger = logging.getLogger(__name__)


class KnowledgeBaseRetrievalError(RuntimeError):
    """Raised when retrieval results cannot safely support PRD generation."""


class KnowledgeBasePassage(StrictModel):
    """Normalized passage returned by the managed knowledge base."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    chunk_id: str = Field(min_length=1)
    content: str = Field(min_length=1)
    source_type: SourceType
    source_uri: str = Field(min_length=1)
    locator: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    retrieval_score: float | None = None
    repository_commit: str | None = None
    task_id: str | None = None


# The Gateway transport (kept for smoke tooling) returns the same passage shape.
GatewayPassage = KnowledgeBasePassage


class RetrieveClient(Protocol):
    async def retrieve(
        self,
        *,
        query: str,
        metadata_filter: Mapping[str, Any],
        top_k: int,
    ) -> Sequence[KnowledgeBasePassage]: ...


class SourceManifestProvider(Protocol):
    async def load(
        self,
        *,
        project_id: str,
        repository: str,
        commit_sha: str,
    ) -> Mapping[str, Sequence[str]]: ...


def _equals(key: str, value: str) -> dict[str, Any]:
    return {"equals": {"key": key, "value": value}}


def task_scope_filter(
    *,
    project_id: str,
    task_id: str,
    repository_commit: str,
) -> dict[str, Any]:
    """This task's documents, or the project's code at the pinned snapshot."""

    if not (project_id and task_id and repository_commit):
        raise ValueError("project_id, task_id and repository_commit are required")
    return {
        "orAll": [
            {
                "andAll": [
                    _equals("project_id", project_id),
                    _equals("task_id", task_id),
                ]
            },
            {
                "andAll": [
                    _equals("project_id", project_id),
                    _equals("source_type", SourceType.CODE.value),
                    _equals("commit_sha", repository_commit),
                ]
            },
        ]
    }


def _optional_string(metadata: Mapping[str, Any], key: str) -> str | None:
    value = metadata.get(key)
    if value is not None and not (isinstance(value, str) and value):
        raise KnowledgeBaseRetrievalError(f"{key} metadata must be a non-empty string")
    return value


def _location_uri(location: Any) -> str | None:
    if not isinstance(location, Mapping):
        return None
    for key, field in (
        ("s3Location", "uri"),
        ("webLocation", "url"),
        ("confluenceLocation", "url"),
        ("sharePointLocation", "url"),
    ):
        value = location.get(key)
        if isinstance(value, Mapping) and isinstance(value.get(field), str):
            return value[field]
    return None


def _metadata_locator(metadata: Mapping[str, Any]) -> str | None:
    heading = metadata.get("heading")
    if isinstance(heading, str) and heading:
        return heading
    file_path = metadata.get("file_path")
    if not isinstance(file_path, str) or not file_path:
        return None
    line_start = metadata.get("line_start")
    line_end = metadata.get("line_end")
    if isinstance(line_start, int) and isinstance(line_end, int):
        return f"{file_path}:{line_start}-{line_end}"
    return file_path


def parse_passage(item: Any) -> KnowledgeBasePassage:
    """Normalize one ``retrievalResults`` entry, failing closed on missing metadata."""

    if not isinstance(item, Mapping):
        raise KnowledgeBaseRetrievalError("Knowledge Base passage must be an object")
    metadata = item.get("metadata")
    content_block = item.get("content")
    if not isinstance(metadata, Mapping) or not isinstance(content_block, Mapping):
        raise KnowledgeBaseRetrievalError(
            "Knowledge Base passage requires content and metadata objects"
        )

    text = content_block.get("text")
    # The knowledge base's own chunk ID is unique per indexed chunk; the
    # caller-set chunk_id is only a fallback, because the service may split one
    # ingested document into several chunks that all inherit its metadata.
    chunk_id = metadata.get("_chunk_id") or metadata.get("chunk_id")
    project_id = metadata.get("project_id")
    source_type_value = metadata.get("source_type")
    source_uri = metadata.get("source_uri") or _location_uri(item.get("location"))
    locator = metadata.get("locator") or _metadata_locator(metadata)
    if not all(
        isinstance(value, str) and value
        for value in (
            text,
            chunk_id,
            project_id,
            source_type_value,
            source_uri,
            locator,
        )
    ):
        raise KnowledgeBaseRetrievalError(
            "Knowledge Base passage is missing required traceability metadata"
        )

    try:
        source_type = SourceType(source_type_value)
    except ValueError as error:
        raise KnowledgeBaseRetrievalError(
            "Knowledge Base passage has an invalid source_type"
        ) from error

    score = item.get("score")
    if score is not None and not isinstance(score, (int, float)):
        raise KnowledgeBaseRetrievalError(
            "Knowledge Base passage score must be numeric"
        )

    return KnowledgeBasePassage(
        chunk_id=chunk_id,
        content=text,
        source_type=source_type,
        source_uri=source_uri,
        locator=locator,
        project_id=project_id,
        retrieval_score=float(score) if score is not None else None,
        repository_commit=_optional_string(metadata, "commit_sha"),
        task_id=_optional_string(metadata, "task_id"),
    )


class BedrockKnowledgeBaseRetrieveClient:
    """Call the managed knowledge base ``Retrieve`` API with a Runtime-built filter."""

    def __init__(self, *, client: Any, knowledge_base_id: str) -> None:
        if not knowledge_base_id:
            raise ValueError("knowledge_base_id is required")
        self._client = client
        self._knowledge_base_id = knowledge_base_id

    async def retrieve(
        self,
        *,
        query: str,
        metadata_filter: Mapping[str, Any],
        top_k: int,
    ) -> list[KnowledgeBasePassage]:
        if not query.strip():
            raise ValueError("query is required")
        if top_k < 1:
            raise ValueError("top_k must be positive")
        response = await asyncio.to_thread(
            functools.partial(
                self._client.retrieve,
                knowledgeBaseId=self._knowledge_base_id,
                retrievalQuery={"text": query.strip()},
                retrievalConfiguration={
                    "managedSearchConfiguration": {
                        "numberOfResults": top_k,
                        "filter": dict(metadata_filter),
                    }
                },
            )
        )
        raw_results = response.get("retrievalResults")
        if not isinstance(raw_results, list):
            raise KnowledgeBaseRetrievalError(
                "Knowledge Base Retrieve response has no retrievalResults list"
            )
        return [parse_passage(item) for item in raw_results]


class AgentCoreKnowledgeBaseRetriever:
    """The workflow's only retrieval implementation.

    The task scope comes from ``request.knowledge_base_snapshot``, which the
    Runtime assigns per invocation and the caller cannot set. Results crossing a
    project, task, or repository-version boundary fail closed before reaching a
    Strands prompt. At most ``max_queries`` distinct queries are sent: the
    analyzer is asked for that many, most important first, and any beyond the
    budget are dropped rather than failing the whole generation.
    """

    def __init__(
        self,
        *,
        client: RetrieveClient,
        manifest_provider: SourceManifestProvider,
        top_k: int = 10,
        max_queries: int = 12,
        allow_empty: bool = False,
    ) -> None:
        if top_k < 1:
            raise ValueError("top_k must be positive")
        if max_queries < 1:
            raise ValueError("max_queries must be positive")
        self._client = client
        self._manifest_provider = manifest_provider
        self._top_k = top_k
        self._max_queries = max_queries
        self._allow_empty = allow_empty

    async def retrieve(
        self,
        request: GenerationRequest,
        queries: Sequence[str],
    ) -> RetrievalBundle:
        normalized_queries = list(
            dict.fromkeys(query.strip() for query in queries if query.strip())
        )
        if not normalized_queries:
            raise KnowledgeBaseRetrievalError(
                "at least one non-empty query is required"
            )
        if len(normalized_queries) > self._max_queries:
            logger.warning(
                "dropping %d of %d search queries over the budget of %d",
                len(normalized_queries) - self._max_queries,
                len(normalized_queries),
                self._max_queries,
            )
            normalized_queries = normalized_queries[: self._max_queries]

        metadata_filter = task_scope_filter(
            project_id=request.project_id,
            task_id=request.knowledge_base_snapshot,
            repository_commit=request.repository_commit,
        )
        passage_groups, manifest = await asyncio.gather(
            asyncio.gather(
                *(
                    self._client.retrieve(
                        query=query,
                        metadata_filter=metadata_filter,
                        top_k=self._top_k,
                    )
                    for query in normalized_queries
                )
            ),
            self._manifest_provider.load(
                project_id=request.project_id,
                repository=request.repository,
                commit_sha=request.repository_commit,
            ),
        )

        by_chunk_id: dict[str, KnowledgeBasePassage] = {}
        for passages in passage_groups:
            for passage in passages:
                self._validate_passage(request, passage)
                existing = by_chunk_id.get(passage.chunk_id)
                if existing is None or self._score(passage) > self._score(existing):
                    by_chunk_id[passage.chunk_id] = passage

        if not by_chunk_id and not self._allow_empty:
            raise KnowledgeBaseRetrievalError(
                "AgentCore Knowledge Base returned no passages for this request"
            )

        ordered = sorted(
            by_chunk_id.values(),
            key=lambda passage: (
                -self._score(passage),
                passage.source_uri,
                passage.locator,
                passage.chunk_id,
            ),
        )
        return RetrievalBundle(
            chunks=[
                RetrievedChunk(
                    chunk_id=passage.chunk_id,
                    source_type=passage.source_type,
                    source_uri=passage.source_uri,
                    locator=passage.locator,
                    content=passage.content,
                    retrieval_score=passage.retrieval_score,
                    repository_commit=passage.repository_commit,
                )
                for passage in ordered
            ],
            source_manifest={
                path: frozenset(symbols) for path, symbols in manifest.items()
            },
        )

    @staticmethod
    def _score(passage: KnowledgeBasePassage) -> float:
        return passage.retrieval_score if passage.retrieval_score is not None else -1.0

    @staticmethod
    def _validate_passage(
        request: GenerationRequest,
        passage: KnowledgeBasePassage,
    ) -> None:
        if passage.project_id != request.project_id:
            raise KnowledgeBaseRetrievalError(
                "AgentCore Knowledge Base returned a cross-project passage"
            )
        if passage.source_type is SourceType.CODE:
            if passage.repository_commit != request.repository_commit:
                raise KnowledgeBaseRetrievalError(
                    "code passage does not match the pinned repository commit"
                )
            if passage.task_id is not None:
                raise KnowledgeBaseRetrievalError(
                    "code passage unexpectedly carries a task scope"
                )
            return
        if passage.repository_commit is not None:
            raise KnowledgeBaseRetrievalError(
                "non-code passage unexpectedly contains a repository commit"
            )
        if passage.task_id != request.knowledge_base_snapshot:
            raise KnowledgeBaseRetrievalError(
                "AgentCore Knowledge Base returned a passage outside this task"
            )
