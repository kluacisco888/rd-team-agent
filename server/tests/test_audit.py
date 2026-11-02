from __future__ import annotations

import unittest

from pydantic import ValidationError

from prd_agent.audit import ValidationReport, build_validation_report
from prd_agent.contract import ParagraphSection, PrdDocument
from prd_agent.contracts import (
    Citation,
    CodeImpact,
    EvidenceBackedText,
    EvidenceStatus,
    ImpactType,
    SourceType,
    ValidationIssue,
)
from prd_agent.workflow import DraftArtifact, RetrievalBundle, RetrievedChunk


class ValidationReportTest(unittest.TestCase):
    def citation(self, chunk_id: str = "chunk-1") -> Citation:
        return Citation(
            chunk_id=chunk_id,
            source_type=SourceType.DOCUMENT,
            source_uri="s3://example/requirements.md",
            locator="Import workflow",
            retrieved_at="2026-09-15T00:00:00Z",
        )

    def evidence(self) -> RetrievalBundle:
        return RetrievalBundle(
            chunks=[
                RetrievedChunk(
                    chunk_id="chunk-1",
                    source_type=SourceType.DOCUMENT,
                    source_uri="s3://example/requirements.md",
                    locator="Import workflow",
                    content="Sensitive source content must not enter the audit report.",
                )
            ],
            source_manifest={"src/imports.py": frozenset({"ImportService"})},
        )

    def draft(self) -> DraftArtifact:
        citation = self.citation()
        return DraftArtifact(
            document=PrdDocument(
                contract_version="v1",
                sections=[
                    ParagraphSection(
                        section_id="summary",
                        paragraphs=["Add batch import."],
                    )
                ],
            ),
            claims=[
                EvidenceBackedText(
                    text="Import exists.",
                    status=EvidenceStatus.CONFIRMED,
                    citations=[citation],
                )
            ],
            code_impacts=[
                CodeImpact(
                    repository="example/service",
                    commit_sha="abc123",
                    file_path="src/imports.py",
                    symbol="ImportService",
                    impact_type=ImpactType.MODIFY,
                    rationale=EvidenceBackedText(
                        text="ImportService owns imports.",
                        status=EvidenceStatus.CONFIRMED,
                        citations=[citation],
                    ),
                )
            ],
        )

    def test_builds_deduplicated_content_minimized_pass_report(self) -> None:
        report = build_validation_report(
            draft=self.draft(),
            evidence=self.evidence(),
            issues=[],
            repair_attempts=1,
        )
        self.assertTrue(report.passed)
        self.assertEqual(report.claim_count, 2)
        self.assertEqual(report.confirmed_claim_count, 2)
        self.assertEqual(report.citation_count, 1)
        self.assertEqual(report.code_impact_count, 1)
        self.assertEqual(report.retrieved_chunk_ids, ("chunk-1",))
        serialized = report.model_dump_json()
        self.assertNotIn("Sensitive source content", serialized)

    def test_failed_report_preserves_machine_readable_issues(self) -> None:
        issue = ValidationIssue(
            rule_id="evidence.chunk_id.not_retrieved",
            field_path="claims[0].citations[0].chunk_id",
            message="Citation was not retrieved.",
        )
        report = build_validation_report(
            draft=self.draft(),
            evidence=self.evidence(),
            issues=[issue],
            repair_attempts=2,
        )
        self.assertFalse(report.passed)
        self.assertEqual(report.issues, (issue,))

    def test_pass_and_failure_states_are_consistent(self) -> None:
        issue = ValidationIssue(
            rule_id="test.failure",
            field_path="document",
            message="Failed.",
        )
        base = {
            "repair_attempts": 0,
            "retrieved_chunk_ids": (),
            "claim_count": 0,
            "confirmed_claim_count": 0,
            "citation_count": 0,
            "code_impact_count": 0,
            "citations": (),
            "code_impacts": (),
        }
        with self.assertRaisesRegex(ValidationError, "passed report cannot"):
            ValidationReport(passed=True, issues=(issue,), **base)
        with self.assertRaisesRegex(ValidationError, "failed report requires"):
            ValidationReport(passed=False, issues=(), **base)

    def test_passed_report_rejects_unretrieved_citation(self) -> None:
        draft = self.draft().model_copy(
            update={
                "claims": [
                    EvidenceBackedText(
                        text="Invented support.",
                        status=EvidenceStatus.CONFIRMED,
                        citations=[self.citation("invented")],
                    )
                ],
                "code_impacts": [],
            }
        )
        with self.assertRaisesRegex(ValueError, "outside retrieval"):
            build_validation_report(
                draft=draft,
                evidence=self.evidence(),
                issues=[],
                repair_attempts=0,
            )

    def test_repair_budget_is_enforced(self) -> None:
        with self.assertRaises(ValidationError):
            ValidationReport(
                passed=False,
                repair_attempts=3,
                retrieved_chunk_ids=(),
                claim_count=0,
                confirmed_claim_count=0,
                citation_count=0,
                code_impact_count=0,
                citations=(),
                code_impacts=(),
                issues=(
                    ValidationIssue(
                        rule_id="failed",
                        field_path="document",
                        message="Failed.",
                    ),
                ),
            )


if __name__ == "__main__":
    unittest.main()
