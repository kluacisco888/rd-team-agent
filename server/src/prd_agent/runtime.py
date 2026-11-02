"""Amazon Bedrock AgentCore Runtime entrypoint for the PRD workflow."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol
from uuid import uuid4

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from pydantic import ValidationError

from prd_agent.contract import PrdContract
from prd_agent.contracts import (
    GenerationRequest,
    GenerationResult,
    InvocationRequest,
    VersionSet,
)
from prd_agent.task_documents import (
    TaskDocumentIndexingFailed,
    TaskDocumentIndexingTimeout,
    TaskDocumentRejected,
)
from prd_agent.workflow import PrdWorkflow

logger = logging.getLogger(__name__)


class ContractProvider(Protocol):
    async def load(self, version: str) -> PrdContract: ...


class VersionProvider(Protocol):
    async def resolve(self, request: GenerationRequest) -> VersionSet: ...


class TaskDocumentStager(Protocol):
    def stage(
        self,
        *,
        task_id: str,
        request: InvocationRequest,
    ) -> AbstractAsyncContextManager[Any]: ...


def new_task_id() -> str:
    """An unguessable per-invocation scope; never derived from caller input."""

    return f"task-{uuid4().hex}"


class PrdGenerationService:
    """Resolve immutable inputs, stage task documents, then run the workflow."""

    def __init__(
        self,
        *,
        workflow: PrdWorkflow,
        contract_provider: ContractProvider,
        version_provider: VersionProvider,
        document_stager: TaskDocumentStager,
        task_id_factory: Callable[[], str] = new_task_id,
    ) -> None:
        self._workflow = workflow
        self._contract_provider = contract_provider
        self._version_provider = version_provider
        self._document_stager = document_stager
        self._task_id_factory = task_id_factory

    async def generate(
        self,
        *,
        run_id: str,
        request: InvocationRequest,
    ) -> GenerationResult:
        # Resolve the Contract first so a bad version fails before any ingestion.
        contract = await self._contract_provider.load(request.prd_contract_version)
        task_id = self._task_id_factory()
        async with self._document_stager.stage(task_id=task_id, request=request):
            generation_request = request.to_generation_request(task_id)
            versions = await self._version_provider.resolve(generation_request)
            return await self._workflow.run(
                run_id=run_id,
                request=generation_request,
                contract=contract,
                versions=versions,
            )


class StaticContractProvider:
    """MVP provider for one preloaded, versioned PRD contract."""

    def __init__(self, contract: PrdContract) -> None:
        self._contract = contract

    async def load(self, version: str) -> PrdContract:
        if version != self._contract.version:
            raise LookupError(f"PRD contract version {version!r} is unavailable")
        return self._contract


class StaticVersionProvider:
    """MVP provider whose infrastructure versions are fixed at process startup."""

    def __init__(
        self,
        *,
        prompt_version: str,
        runtime_version: str,
        model_id: str,
    ) -> None:
        self._prompt_version = prompt_version
        self._runtime_version = runtime_version
        self._model_id = model_id

    async def resolve(self, request: GenerationRequest) -> VersionSet:
        return VersionSet(
            knowledge_base_snapshot=request.knowledge_base_snapshot,
            repository_commit=request.repository_commit,
            prd_contract_version=request.prd_contract_version,
            prompt_version=self._prompt_version,
            runtime_version=self._runtime_version,
            model_id=self._model_id,
        )


async def stream_runtime_invocation(
    service: PrdGenerationService,
    payload: Any,
    *,
    heartbeat_seconds: float = 20.0,
) -> AsyncIterator[dict[str, Any]]:
    """Validate untrusted Runtime input and stream stable event envelopes."""

    if heartbeat_seconds <= 0:
        raise ValueError("heartbeat_seconds must be positive")

    if not isinstance(payload, dict):
        yield {
            "event": "error",
            "error": {
                "code": "INVALID_REQUEST",
                "message": "Invocation payload must be a JSON object.",
            },
        }
        return

    raw_payload = dict(payload)
    supplied_run_id = raw_payload.pop("run_id", None)
    run_id = supplied_run_id if isinstance(supplied_run_id, str) and supplied_run_id else uuid4().hex

    try:
        request = InvocationRequest.model_validate(raw_payload)
    except ValidationError as error:
        yield {
            "event": "error",
            "run_id": run_id,
            "error": {
                "code": "INVALID_REQUEST",
                "message": "Invocation payload failed validation.",
                "details": [
                    {
                        "location": ".".join(str(part) for part in item["loc"]),
                        "type": item["type"],
                        "message": item["msg"],
                    }
                    for item in error.errors(include_url=False, include_input=False)
                ],
            },
        }
        return

    yield {"event": "started", "run_id": run_id}
    try:
        generation = asyncio.create_task(
            service.generate(run_id=run_id, request=request)
        )
        while not generation.done():
            done, _ = await asyncio.wait(
                {generation},
                timeout=heartbeat_seconds,
            )
            if not done:
                yield {
                    "event": "progress",
                    "run_id": run_id,
                    "stage": "generating",
                }
        result = await generation
    except TaskDocumentRejected as error:
        yield {
            "event": "error",
            "run_id": run_id,
            "error": {
                "code": "INVALID_DOCUMENTS",
                "message": str(error),
            },
        }
        return
    except TaskDocumentIndexingTimeout as error:
        logger.warning("task document indexing timed out", extra={"run_id": run_id})
        yield {
            "event": "error",
            "run_id": run_id,
            "error": {
                "code": "DOCUMENT_INDEXING_TIMEOUT",
                "message": (
                    f"The knowledge base did not finish indexing {error.pending} of "
                    f"{error.total} documents within {error.timeout_seconds:g} "
                    "seconds. Retry the task."
                ),
            },
        }
        return
    except TaskDocumentIndexingFailed as error:
        yield {
            "event": "error",
            "run_id": run_id,
            "error": {
                "code": "DOCUMENT_INDEXING_FAILED",
                "message": str(error),
            },
        }
        return
    except LookupError as error:
        yield {
            "event": "error",
            "run_id": run_id,
            "error": {
                "code": "CONFIGURATION_NOT_FOUND",
                "message": str(error),
            },
        }
        return
    except Exception:
        logger.exception("PRD generation failed", extra={"run_id": run_id})
        # Detailed exceptions belong in OTEL logs, never in the customer response.
        yield {
            "event": "error",
            "run_id": run_id,
            "error": {
                "code": "GENERATION_FAILED",
                "message": "PRD generation failed. Use the run ID to inspect observability traces.",
            },
        }
        return

    yield {
        "event": "result",
        "run_id": run_id,
        "result": result.model_dump(mode="json"),
    }


def create_runtime_app(service: PrdGenerationService) -> BedrockAgentCoreApp:
    """Create the AgentCore application without ambient global dependencies."""

    app = BedrockAgentCoreApp(debug=False)

    @app.entrypoint
    async def invoke(payload: Any) -> AsyncIterator[dict[str, Any]]:
        async for event in stream_runtime_invocation(service, payload):
            yield event

    return app
