from __future__ import annotations

import unittest
from collections.abc import Sequence

from prd_agent.contract import (
    ParagraphSection,
    PrdContract,
    PrdDocument,
    SectionContract,
    SectionKind,
)
from prd_agent.contracts import (
    Citation,
    ClarificationQuestion,
    EvidenceBackedText,
    EvidenceStatus,
    GenerationRequest,
    RequirementIntent,
    RunStatus,
    SourceType,
    VersionSet,
)
from prd_agent.workflow import DraftArtifact, PrdWorkflow, RetrievalBundle, RetrievedChunk


class FakeAnalyzer:
    def __init__(self, questions: list[ClarificationQuestion] | None = None) -> None:
        self.questions = questions or []
        self.calls = 0

    async def analyze(self, request: GenerationRequest) -> RequirementIntent:
        self.calls += 1
        return RequirementIntent(
            original_requirement=request.requirement_text,
            normalized_english_requirement=request.requirement_text,
            problem_statement=request.requirement_text,
            search_queries=["existing batch import behavior"],
            clarification_questions=self.questions,
        )


class FakeRetriever:
    def __init__(self) -> None:
        self.calls = 0

    async def retrieve(
        self, request: GenerationRequest, queries: Sequence[str]
    ) -> RetrievalBundle:
        self.calls += 1
        return RetrievalBundle(
            chunks=[
                RetrievedChunk(
                    chunk_id="chunk-1",
                    source_type=SourceType.DOCUMENT,
                    source_uri="s3://example/requirements.md",
                    locator="Import workflow",
                    content="The service currently supports individual imports.",
                )
            ],
            source_manifest={},
        )


class SequenceComposer:
    def __init__(self, drafts: list[DraftArtifact]) -> None:
        self.drafts = drafts
        self.compose_calls = 0
        self.repair_calls = 0

    async def compose(self, request, intent, contract, evidence) -> DraftArtifact:
        self.compose_calls += 1
        return self.drafts[0]

    async def repair(
        self, request, intent, contract, evidence, previous, issues
    ) -> DraftArtifact:
        self.repair_calls += 1
        return self.drafts[min(self.repair_calls, len(self.drafts) - 1)]


class PrdWorkflowTest(unittest.IsolatedAsyncioTestCase):
    def request(self, *, mode: str = "interactive") -> GenerationRequest:
        return GenerationRequest(
            project_id="project-1",
            requirement_text="Support batch import.",
            prd_contract_version="v1",
            knowledge_base_snapshot="kb-1",
            repository="example/service",
            repository_commit="abc123",
            mode=mode,
        )

    def contract(self) -> PrdContract:
        return PrdContract(
            version="v1",
            document_title="Product Requirements Document",
            sections=[
                SectionContract(
                    section_id="summary",
                    title="1. Summary",
                    kind=SectionKind.PARAGRAPH,
                )
            ],
        )

    def versions(self, **updates: str) -> VersionSet:
        values = {
            "knowledge_base_snapshot": "kb-1",
            "repository_commit": "abc123",
            "prd_contract_version": "v1",
            "prompt_version": "prompt-1",
            "runtime_version": "runtime-1",
            "model_id": "us.anthropic.claude-opus-4-6-v1",
        }
        values.update(updates)
        return VersionSet(**values)

    def draft(self, *, chunk_id: str = "chunk-1") -> DraftArtifact:
        return DraftArtifact(
            document=PrdDocument(
                contract_version="v1",
                sections=[
                    ParagraphSection(
                        section_id="summary",
                        paragraphs=["Add a batch import workflow."],
                    )
                ],
            ),
            claims=[
                EvidenceBackedText(
                    text="Individual import already exists.",
                    status=EvidenceStatus.CONFIRMED,
                    citations=[
                        Citation(
                            chunk_id=chunk_id,
                            source_type=SourceType.DOCUMENT,
                            source_uri="s3://example/requirements.md",
                            locator="Import workflow",
                            retrieved_at="2026-09-15T00:00:00Z",
                        )
                    ],
                )
            ],
        )

    async def test_interactive_mode_stops_for_clarification(self) -> None:
        question = ClarificationQuestion(
            question_id="Q1",
            question="What is the maximum batch size?",
            rationale="The limit changes processing design.",
            blocked_fields=["functional_requirements.batch_size"],
        )
        analyzer = FakeAnalyzer([question])
        retriever = FakeRetriever()
        composer = SequenceComposer([self.draft()])
        result = await PrdWorkflow(
            analyzer=analyzer, retriever=retriever, composer=composer
        ).run(
            run_id="run-1",
            request=self.request(),
            contract=self.contract(),
            versions=self.versions(),
        )
        self.assertEqual(result.status, RunStatus.NEEDS_CLARIFICATION)
        self.assertEqual(retriever.calls, 0)
        self.assertEqual(composer.compose_calls, 0)

    async def test_valid_draft_completes_with_deterministic_markdown(self) -> None:
        result = await PrdWorkflow(
            analyzer=FakeAnalyzer(),
            retriever=FakeRetriever(),
            composer=SequenceComposer([self.draft()]),
        ).run(
            run_id="run-1",
            request=self.request(),
            contract=self.contract(),
            versions=self.versions(),
        )
        self.assertEqual(result.status, RunStatus.COMPLETED)
        self.assertEqual(
            result.prd_markdown,
            "# Product Requirements Document\n\n## 1. Summary\n\n"
            "Add a batch import workflow.\n",
        )
        self.assertEqual(result.versions, self.versions())

    async def test_invalid_citation_is_repaired_once(self) -> None:
        composer = SequenceComposer(
            [self.draft(chunk_id="invented"), self.draft(chunk_id="chunk-1")]
        )
        result = await PrdWorkflow(
            analyzer=FakeAnalyzer(),
            retriever=FakeRetriever(),
            composer=composer,
        ).run(
            run_id="run-1",
            request=self.request(),
            contract=self.contract(),
            versions=self.versions(),
        )
        self.assertEqual(result.status, RunStatus.COMPLETED)
        self.assertEqual(composer.repair_calls, 1)

    async def test_persistent_invalid_draft_fails_after_two_repairs(self) -> None:
        composer = SequenceComposer([self.draft(chunk_id="invented")])
        result = await PrdWorkflow(
            analyzer=FakeAnalyzer(),
            retriever=FakeRetriever(),
            composer=composer,
        ).run(
            run_id="run-1",
            request=self.request(),
            contract=self.contract(),
            versions=self.versions(),
        )
        self.assertEqual(result.status, RunStatus.FAILED_VALIDATION)
        self.assertEqual(composer.repair_calls, 2)
        self.assertEqual(
            result.validation_issues[0].rule_id,
            "evidence.chunk_id.not_retrieved",
        )

    async def test_version_mismatch_fails_before_model_or_retrieval(self) -> None:
        analyzer = FakeAnalyzer()
        retriever = FakeRetriever()
        composer = SequenceComposer([self.draft()])
        result = await PrdWorkflow(
            analyzer=analyzer, retriever=retriever, composer=composer
        ).run(
            run_id="run-1",
            request=self.request(),
            contract=self.contract(),
            versions=self.versions(repository_commit="wrong"),
        )
        self.assertEqual(result.status, RunStatus.FAILED_VALIDATION)
        self.assertEqual(result.validation_issues[0].rule_id, "version.repository_commit.mismatch")
        self.assertEqual(analyzer.calls, 0)
        self.assertEqual(retriever.calls, 0)

    async def test_one_shot_mode_continues_despite_questions(self) -> None:
        question = ClarificationQuestion(
            question_id="Q1",
            question="What is the maximum batch size?",
            rationale="The limit changes processing design.",
            blocked_fields=["functional_requirements.batch_size"],
        )
        retriever = FakeRetriever()
        result = await PrdWorkflow(
            analyzer=FakeAnalyzer([question]),
            retriever=retriever,
            composer=SequenceComposer([self.draft()]),
        ).run(
            run_id="run-1",
            request=self.request(mode="one_shot"),
            contract=self.contract(),
            versions=self.versions(),
        )
        self.assertEqual(result.status, RunStatus.COMPLETED)
        self.assertEqual(retriever.calls, 1)


if __name__ == "__main__":
    unittest.main()
