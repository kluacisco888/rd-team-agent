"""Dependency composition for the deployable AgentCore Runtime application."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from pathlib import Path

import boto3
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from botocore.config import Config

from prd_agent.code_ingestion import build_repository_snapshot
from prd_agent.contract import PrdContract
from prd_agent.kb_bundle import compute_source_snapshot_id
from prd_agent.knowledge_base import (
    AgentCoreKnowledgeBaseRetriever,
    BedrockKnowledgeBaseRetrieveClient,
)
from prd_agent.manifest import ManifestIntegrityError
from prd_agent.runtime import (
    PrdGenerationService,
    StaticVersionProvider,
    create_runtime_app,
)
from prd_agent.settings import AppSettings
from prd_agent.strands_adapters import (
    BedrockAgentFactory,
    StrandsModelConfig,
    StrandsPrdComposer,
    StrandsRequirementAnalyzer,
)
from prd_agent.task_documents import KnowledgeBaseTaskDocuments
from prd_agent.workflow import PrdWorkflow

# Knowledge-base calls are short; the SDK's standard mode retries throttling.
_KNOWLEDGE_BASE_CLIENT_CONFIG = Config(
    connect_timeout=10,
    read_timeout=60,
    retries={"mode": "standard", "max_attempts": 5},
)


class JsonContractProvider:
    """Load a reviewed immutable Contract file and enforce its requested version."""

    def __init__(self, path: Path) -> None:
        self._path = path

    async def load(self, version: str) -> PrdContract:
        try:
            payload = await asyncio.to_thread(self._path.read_text, encoding="utf-8")
        except FileNotFoundError as error:
            raise LookupError("configured PRD contract file does not exist") from error
        try:
            contract = PrdContract.model_validate_json(payload)
        except ValueError as error:
            raise LookupError("configured PRD contract file is invalid") from error
        if contract.version != version:
            raise LookupError(f"PRD contract version {version!r} is unavailable")
        return contract


class PackagedSourceManifestProvider:
    """Rebuild and verify the source manifest from the deployed Runtime artifact."""

    def __init__(
        self,
        *,
        source_root: Path,
        project_id: str,
        repository: str,
    ) -> None:
        self.project_id = project_id
        self.repository = repository
        self.commit_sha = compute_source_snapshot_id(source_root)
        snapshot = build_repository_snapshot(
            source_root,
            project_id=project_id,
            repository=repository,
            commit_sha=self.commit_sha,
            max_chars=5000,
        )
        self._manifest = {
            path: tuple(sorted(symbols))
            for path, symbols in snapshot.manifest.items()
        }

    async def load(
        self,
        *,
        project_id: str,
        repository: str,
        commit_sha: str,
    ) -> Mapping[str, Sequence[str]]:
        expected = (self.project_id, self.repository, self.commit_sha)
        actual = (project_id, repository, commit_sha)
        if actual != expected:
            raise ManifestIntegrityError(
                "request source identity does not match the deployed Runtime artifact"
            )
        return self._manifest


def build_runtime_app(
    settings: AppSettings,
    *,
    source_root: Path | None = None,
) -> BedrockAgentCoreApp:
    """Build the production dependency graph without storing credentials.

    AWS clients resolve the Runtime execution role through the default
    credential chain.
    """

    packaged_sources = PackagedSourceManifestProvider(
        source_root=source_root or Path(__file__).resolve().parents[1],
        project_id=settings.project_id,
        repository=settings.repository_name,
    )
    agent_factory = BedrockAgentFactory(
        StrandsModelConfig(
            model_id=settings.model_id,
            region_name=settings.aws_region,
            max_tokens=settings.model_max_output_tokens,
            read_timeout_seconds=settings.model_read_timeout_seconds,
        )
    )
    aws_session = boto3.Session(region_name=settings.aws_region)
    retriever = AgentCoreKnowledgeBaseRetriever(
        client=BedrockKnowledgeBaseRetrieveClient(
            client=aws_session.client(
                "bedrock-agent-runtime", config=_KNOWLEDGE_BASE_CLIENT_CONFIG
            ),
            knowledge_base_id=settings.knowledge_base_id,
        ),
        manifest_provider=packaged_sources,
        top_k=settings.retrieval_top_k,
        max_queries=settings.retrieval_max_queries,
    )
    task_documents = KnowledgeBaseTaskDocuments(
        client=aws_session.client(
            "bedrock-agent", config=_KNOWLEDGE_BASE_CLIENT_CONFIG
        ),
        knowledge_base_id=settings.knowledge_base_id,
        data_source_id=settings.task_data_source_id,
        project_id=settings.project_id,
    )
    workflow = PrdWorkflow(
        analyzer=StrandsRequirementAnalyzer(
            agent_factory, max_search_queries=settings.retrieval_max_queries
        ),
        retriever=retriever,
        composer=StrandsPrdComposer(agent_factory),
        max_repair_attempts=1,
    )
    service = PrdGenerationService(
        workflow=workflow,
        contract_provider=JsonContractProvider(settings.prd_contract_path),
        version_provider=StaticVersionProvider(
            prompt_version=settings.prompt_version,
            runtime_version=settings.runtime_version,
            model_id=settings.model_id,
        ),
        document_stager=task_documents,
    )
    return create_runtime_app(service)
