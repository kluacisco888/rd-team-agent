"""Deterministic, content-minimized audit reports for PRD generation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from prd_agent.contracts import (
    Citation,
    EvidenceStatus,
    ValidationIssue,
    ValidationReport,
)

if TYPE_CHECKING:
    from prd_agent.workflow import DraftArtifact, RetrievalBundle


def build_validation_report(
    *,
    draft: DraftArtifact,
    evidence: RetrievalBundle,
    issues: list[ValidationIssue],
    repair_attempts: int,
) -> ValidationReport:
    """Build a deterministic report without copying Knowledge Base chunk content."""

    claims = [*draft.claims, *(impact.rationale for impact in draft.code_impacts)]
    citations_by_key: dict[tuple[str, str, str], Citation] = {}
    for claim in claims:
        for citation in claim.citations:
            key = (citation.chunk_id, citation.source_uri, citation.locator)
            citations_by_key[key] = citation

    citations = tuple(
        citations_by_key[key]
        for key in sorted(citations_by_key)
    )
    retrieved_chunk_ids = tuple(sorted(chunk.chunk_id for chunk in evidence.chunks))

    if not issues:
        unknown = sorted(
            citation.chunk_id
            for citation in citations
            if citation.chunk_id not in set(retrieved_chunk_ids)
        )
        if unknown:
            raise ValueError(
                f"passed report cannot contain citations outside retrieval: {unknown}"
            )

    return ValidationReport(
        passed=not issues,
        repair_attempts=repair_attempts,
        retrieved_chunk_ids=retrieved_chunk_ids,
        claim_count=len(claims),
        confirmed_claim_count=sum(
            claim.status is EvidenceStatus.CONFIRMED for claim in claims
        ),
        citation_count=len(citations),
        code_impact_count=len(draft.code_impacts),
        citations=citations,
        code_impacts=tuple(draft.code_impacts),
        issues=tuple(issues),
    )
