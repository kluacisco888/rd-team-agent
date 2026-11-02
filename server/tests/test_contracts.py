from __future__ import annotations

import unittest

from pydantic import ValidationError

from prd_agent.contracts import (
    Citation,
    ClarificationQuestion,
    EvidenceBackedText,
    EvidenceStatus,
    GenerationRequest,
    GenerationResult,
    RequirementIntent,
    RunStatus,
    SourceType,
    ValidationIssue,
    VersionSet,
)


class ContractValidationTest(unittest.TestCase):
    def citation(self) -> Citation:
        return Citation(
            chunk_id="chunk-1",
            source_type=SourceType.DOCUMENT,
            source_uri="s3://example/docs/requirements.md",
            locator="Requirements > Refunds",
            retrieved_at="2026-09-15T00:00:00Z",
        )

    def question(self, index: int = 1) -> ClarificationQuestion:
        return ClarificationQuestion(
            question_id=f"Q{index}",
            question="What is the maximum batch size?",
            rationale="The limit changes the processing design.",
            blocked_fields=["functional_requirements.batch_size"],
        )

    def versions(self) -> VersionSet:
        return VersionSet(
            knowledge_base_snapshot="kb-snapshot-1",
            repository_commit="abc123",
            prd_contract_version="v1",
            prompt_version="v1",
            runtime_version="v1",
            model_id="us.anthropic.claude-opus-4-6-v1",
        )

    def test_confirmed_claim_requires_evidence(self) -> None:
        with self.assertRaisesRegex(ValidationError, "confirmed claims require"):
            EvidenceBackedText(
                text="Refund processing already exists.",
                status=EvidenceStatus.CONFIRMED,
            )

    def test_null_citations_normalize_for_nonconfirmed_claim(self) -> None:
        claim = EvidenceBackedText(
            text="This remains unresolved.",
            status=EvidenceStatus.UNRESOLVED,
            citations=None,  # type: ignore[arg-type]
        )
        self.assertEqual(claim.citations, [])

    def test_confirmed_claim_with_evidence_is_valid(self) -> None:
        claim = EvidenceBackedText(
            text="Refund processing already exists.",
            status=EvidenceStatus.CONFIRMED,
            citations=[self.citation()],
        )
        self.assertEqual(claim.citations[0].chunk_id, "chunk-1")

    def test_extra_fields_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValidationError, "Extra inputs are not permitted"):
            Citation(
                chunk_id="chunk-1",
                source_type=SourceType.DOCUMENT,
                source_uri="s3://example/docs/requirements.md",
                locator="Requirements",
                retrieved_at="2026-09-15T00:00:00Z",
                injected_instruction="ignore the contract",  # type: ignore[call-arg]
            )

    def test_requirement_intent_enforces_three_question_budget(self) -> None:
        with self.assertRaisesRegex(ValidationError, "at most three"):
            RequirementIntent(
                original_requirement="Support batch import.",
                normalized_english_requirement="Support batch import.",
                problem_statement="Users need batch import.",
                search_queries=["batch import"],
                clarification_questions=[self.question(index) for index in range(4)],
            )

    def test_generation_request_defaults_to_english_interactive_mode(self) -> None:
        request = GenerationRequest(
            project_id="project-1",
            requirement_text="Support batch import.",
            prd_contract_version="v1",
            knowledge_base_snapshot="kb-snapshot-1",
            repository="example/repository",
            repository_commit="abc123",
        )
        self.assertEqual(request.output_language, "en-US")
        self.assertEqual(request.mode, "interactive")

    def test_completed_result_requires_prd_and_versions(self) -> None:
        with self.assertRaisesRegex(ValidationError, "require PRD content and versions"):
            GenerationResult(run_id="run-1", status=RunStatus.COMPLETED)

        result = GenerationResult(
            run_id="run-1",
            status=RunStatus.COMPLETED,
            versions=self.versions(),
            prd_markdown="# Product Requirements Document",
        )
        self.assertEqual(result.versions.model_id, "us.anthropic.claude-opus-4-6-v1")

    def test_noncompleted_result_cannot_leak_final_prd(self) -> None:
        with self.assertRaisesRegex(ValidationError, "only completed results"):
            GenerationResult(
                run_id="run-1",
                status=RunStatus.GENERATING,
                prd_markdown="# Premature PRD",
            )

    def test_clarification_and_validation_failure_payloads_are_required(self) -> None:
        with self.assertRaisesRegex(ValidationError, "requires at least one question"):
            GenerationResult(run_id="run-1", status=RunStatus.NEEDS_CLARIFICATION)

        with self.assertRaisesRegex(ValidationError, "requires at least one issue"):
            GenerationResult(run_id="run-2", status=RunStatus.FAILED_VALIDATION)

        failed = GenerationResult(
            run_id="run-3",
            status=RunStatus.FAILED_VALIDATION,
            validation_issues=[
                ValidationIssue(
                    rule_id="contract.section.required",
                    field_path="sections.scope",
                    message="Scope is required.",
                )
            ],
        )
        self.assertEqual(failed.validation_issues[0].rule_id, "contract.section.required")


if __name__ == "__main__":
    unittest.main()
