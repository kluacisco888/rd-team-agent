"""Deterministic orchestration around Strands and AgentCore adapters."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, Self

from pydantic import Field, model_validator

from prd_agent.contract import ContractViolation, PrdContract, PrdDocument, render_markdown
from prd_agent.contracts import (
    CodeImpact,
    EvidenceBackedText,
    GenerationRequest,
    GenerationResult,
    RequirementIntent,
    RunStatus,
    SourceType,
    StrictModel,
    ValidationIssue,
    VersionSet,
)
from prd_agent.validation import Issue, validate_generation_payload


class RetrievedChunk(StrictModel):
    chunk_id: str = Field(min_length=1)
    source_type: SourceType
    source_uri: str = Field(min_length=1)
    locator: str = Field(min_length=1)
    content: str = Field(min_length=1)
    retrieval_score: float | None = None
    repository_commit: str | None = None


class RetrievalBundle(StrictModel):
    chunks: list[RetrievedChunk]
    source_manifest: dict[str, frozenset[str]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_unique_chunk_ids(self) -> Self:
        ids = [chunk.chunk_id for chunk in self.chunks]
        if len(ids) != len(set(ids)):
            raise ValueError("retrieved chunk IDs must be unique")
        return self


class DraftArtifact(StrictModel):
    document: PrdDocument
    claims: list[EvidenceBackedText] = Field(default_factory=list)
    code_impacts: list[CodeImpact] = Field(default_factory=list)


class RequirementAnalyzer(Protocol):
    async def analyze(self, request: GenerationRequest) -> RequirementIntent: ...


class KnowledgeRetriever(Protocol):
    async def retrieve(
        self,
        request: GenerationRequest,
        queries: Sequence[str],
    ) -> RetrievalBundle: ...


class PrdComposer(Protocol):
    async def compose(
        self,
        request: GenerationRequest,
        intent: RequirementIntent,
        contract: PrdContract,
        evidence: RetrievalBundle,
    ) -> DraftArtifact: ...

    async def repair(
        self,
        request: GenerationRequest,
        intent: RequirementIntent,
        contract: PrdContract,
        evidence: RetrievalBundle,
        previous: DraftArtifact,
        issues: Sequence[ValidationIssue],
    ) -> DraftArtifact: ...


def _as_validation_issue(issue: Issue) -> ValidationIssue:
    return ValidationIssue(
        rule_id=issue.rule_id,
        field_path=issue.field_path,
        message=issue.message,
    )


def _preflight_issues(
    request: GenerationRequest,
    contract: PrdContract,
    versions: VersionSet,
) -> list[ValidationIssue]:
    comparisons = (
        (
            "version.prd_contract.mismatch",
            "prd_contract_version",
            request.prd_contract_version,
            contract.version,
        ),
        (
            "version.prd_contract.audit_mismatch",
            "versions.prd_contract_version",
            request.prd_contract_version,
            versions.prd_contract_version,
        ),
        (
            "version.knowledge_base.mismatch",
            "versions.knowledge_base_snapshot",
            request.knowledge_base_snapshot,
            versions.knowledge_base_snapshot,
        ),
        (
            "version.repository_commit.mismatch",
            "versions.repository_commit",
            request.repository_commit,
            versions.repository_commit,
        ),
    )
    return [
        ValidationIssue(
            rule_id=rule_id,
            field_path=field_path,
            message=f"expected {expected!r}, received {actual!r}",
        )
        for rule_id, field_path, expected, actual in comparisons
        if expected != actual
    ]


def _validate_draft(
    draft: DraftArtifact,
    contract: PrdContract,
    evidence: RetrievalBundle,
) -> tuple[str | None, list[ValidationIssue]]:
    claim_payloads = [claim.model_dump(mode="json") for claim in draft.claims]
    claim_payloads.extend(
        impact.rationale.model_dump(mode="json") for impact in draft.code_impacts
    )
    impact_payloads = [
        {"file_path": impact.file_path, "symbol": impact.symbol}
        for impact in draft.code_impacts
    ]
    issues = [
        _as_validation_issue(issue)
        for issue in validate_generation_payload(
            claims=claim_payloads,
            retrieved_chunk_ids=[chunk.chunk_id for chunk in evidence.chunks],
            clarification_questions=[],
            code_impacts=impact_payloads,
            source_manifest=evidence.source_manifest,
        )
    ]

    markdown: str | None = None
    try:
        markdown = render_markdown(contract, draft.document)
    except ContractViolation as error:
        issues.append(
            ValidationIssue(
                rule_id="contract.render.failed",
                field_path="document",
                message=str(error),
            )
        )
    return markdown, issues


class PrdWorkflow:
    """Execute the PRD pipeline with deterministic gates and bounded repair."""

    def __init__(
        self,
        *,
        analyzer: RequirementAnalyzer,
        retriever: KnowledgeRetriever,
        composer: PrdComposer,
        max_repair_attempts: int = 2,
    ) -> None:
        if max_repair_attempts < 0:
            raise ValueError("max_repair_attempts cannot be negative")
        self._analyzer = analyzer
        self._retriever = retriever
        self._composer = composer
        self._max_repair_attempts = max_repair_attempts

    async def run(
        self,
        *,
        run_id: str,
        request: GenerationRequest,
        contract: PrdContract,
        versions: VersionSet,
    ) -> GenerationResult:
        preflight = _preflight_issues(request, contract, versions)
        if preflight:
            return GenerationResult(
                run_id=run_id,
                status=RunStatus.FAILED_VALIDATION,
                validation_issues=preflight,
            )

        intent = await self._analyzer.analyze(request)
        if request.mode == "interactive" and intent.clarification_questions:
            return GenerationResult(
                run_id=run_id,
                status=RunStatus.NEEDS_CLARIFICATION,
                clarification_questions=intent.clarification_questions,
            )

        evidence = await self._retriever.retrieve(request, intent.search_queries)
        draft = await self._composer.compose(request, intent, contract, evidence)

        for repair_attempt in range(self._max_repair_attempts + 1):
            markdown, issues = _validate_draft(draft, contract, evidence)
            if not issues:
                from prd_agent.audit import build_validation_report

                assert markdown is not None
                report = build_validation_report(
                    draft=draft,
                    evidence=evidence,
                    issues=[],
                    repair_attempts=repair_attempt,
                )
                return GenerationResult(
                    run_id=run_id,
                    status=RunStatus.COMPLETED,
                    versions=versions,
                    prd_markdown=markdown,
                    validation_report=report,
                )
            if repair_attempt == self._max_repair_attempts:
                from prd_agent.audit import build_validation_report

                report = build_validation_report(
                    draft=draft,
                    evidence=evidence,
                    issues=issues,
                    repair_attempts=repair_attempt,
                )
                return GenerationResult(
                    run_id=run_id,
                    status=RunStatus.FAILED_VALIDATION,
                    validation_issues=issues,
                    validation_report=report,
                )
            draft = await self._composer.repair(
                request,
                intent,
                contract,
                evidence,
                draft,
                issues,
            )

        raise AssertionError("bounded repair loop must return a result")
