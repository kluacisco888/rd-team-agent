from __future__ import annotations

import unittest

from prd_agent.contracts import GenerationRequest, SourceType
from prd_agent.knowledge_base import (
    AgentCoreKnowledgeBaseRetriever,
    BedrockKnowledgeBaseRetrieveClient,
    KnowledgeBasePassage,
    KnowledgeBaseRetrievalError,
    task_scope_filter,
)

TASK_ID = "task-1"


class FakeRetrieveClient:
    def __init__(self, results_by_query) -> None:
        self.results_by_query = results_by_query
        self.calls = []

    async def retrieve(self, **kwargs):
        self.calls.append(kwargs)
        return self.results_by_query.get(kwargs["query"], [])


class FakeManifestProvider:
    def __init__(self) -> None:
        self.calls = []

    async def load(self, **kwargs):
        self.calls.append(kwargs)
        return {"src/imports.py": ["ImportService", "ImportService.create"]}


class FakeBedrockRuntime:
    def __init__(self, response) -> None:
        self.response = response
        self.calls = []

    def retrieve(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class KnowledgeBaseRetrieverTest(unittest.IsolatedAsyncioTestCase):
    def request(self) -> GenerationRequest:
        return GenerationRequest(
            project_id="project-1",
            requirement_text="Support batch import.",
            prd_contract_version="v1",
            knowledge_base_snapshot=TASK_ID,
            repository="example/service",
            repository_commit="abc123",
        )

    def passage(
        self,
        *,
        chunk_id: str = "chunk-1",
        score: float | None = 0.8,
        project_id: str = "project-1",
        source_type: SourceType = SourceType.DOCUMENT,
        repository_commit: str | None = None,
        task_id: str | None = TASK_ID,
    ) -> KnowledgeBasePassage:
        return KnowledgeBasePassage(
            chunk_id=chunk_id,
            content="Existing import behavior.",
            source_type=source_type,
            source_uri="upload://docs/import.md",
            locator="Import workflow",
            project_id=project_id,
            retrieval_score=score,
            repository_commit=repository_commit,
            task_id=task_id,
        )

    def code_passage(self, **overrides) -> KnowledgeBasePassage:
        values = {
            "source_type": SourceType.CODE,
            "repository_commit": "abc123",
            "task_id": None,
        }
        values.update(overrides)
        return self.passage(**values)

    def retriever(self, client, **options) -> AgentCoreKnowledgeBaseRetriever:
        return AgentCoreKnowledgeBaseRetriever(
            client=client,
            manifest_provider=FakeManifestProvider(),
            **options,
        )

    async def test_deduplicates_queries_and_chunks_using_highest_score(self) -> None:
        low = self.passage(score=0.4)
        high = self.passage(score=0.9)
        second = self.passage(chunk_id="chunk-2", score=0.7)
        client = FakeRetrieveClient({"imports": [low, second], "batch": [high]})
        manifest = FakeManifestProvider()
        retriever = AgentCoreKnowledgeBaseRetriever(
            client=client,
            manifest_provider=manifest,
            top_k=7,
        )
        result = await retriever.retrieve(
            self.request(), [" imports ", "batch", "imports"]
        )
        self.assertEqual(
            [chunk.chunk_id for chunk in result.chunks], ["chunk-1", "chunk-2"]
        )
        self.assertEqual(result.chunks[0].retrieval_score, 0.9)
        self.assertEqual([call["query"] for call in client.calls], ["imports", "batch"])
        self.assertTrue(all(call["top_k"] == 7 for call in client.calls))
        self.assertEqual(
            result.source_manifest["src/imports.py"],
            frozenset({"ImportService", "ImportService.create"}),
        )

    async def test_every_query_is_filtered_to_this_task_and_pinned_code(self) -> None:
        client = FakeRetrieveClient({"imports": [self.passage()]})
        await self.retriever(client).retrieve(self.request(), ["imports"])
        expected = task_scope_filter(
            project_id="project-1", task_id=TASK_ID, repository_commit="abc123"
        )
        self.assertEqual(client.calls[0]["metadata_filter"], expected)
        self.assertEqual(
            expected,
            {
                "orAll": [
                    {
                        "andAll": [
                            {"equals": {"key": "project_id", "value": "project-1"}},
                            {"equals": {"key": "task_id", "value": TASK_ID}},
                        ]
                    },
                    {
                        "andAll": [
                            {"equals": {"key": "project_id", "value": "project-1"}},
                            {"equals": {"key": "source_type", "value": "code"}},
                            {"equals": {"key": "commit_sha", "value": "abc123"}},
                        ]
                    },
                ]
            },
        )

    async def test_another_tasks_document_fails_closed(self) -> None:
        for task_id in ("task-2", None):
            with self.subTest(task_id=task_id):
                client = FakeRetrieveClient(
                    {"imports": [self.passage(task_id=task_id)]}
                )
                with self.assertRaisesRegex(
                    KnowledgeBaseRetrievalError, "outside this task"
                ):
                    await self.retriever(client).retrieve(self.request(), ["imports"])

    async def test_cross_project_passage_fails_closed(self) -> None:
        client = FakeRetrieveClient({"imports": [self.passage(project_id="project-2")]})
        with self.assertRaisesRegex(KnowledgeBaseRetrievalError, "cross-project"):
            await self.retriever(client).retrieve(self.request(), ["imports"])

    async def test_code_passage_must_match_pinned_commit(self) -> None:
        client = FakeRetrieveClient(
            {"imports": [self.code_passage(repository_commit="wrong-commit")]}
        )
        with self.assertRaisesRegex(KnowledgeBaseRetrievalError, "pinned"):
            await self.retriever(client).retrieve(self.request(), ["imports"])

    async def test_code_passage_cannot_carry_a_task_scope(self) -> None:
        client = FakeRetrieveClient({"imports": [self.code_passage(task_id=TASK_ID)]})
        with self.assertRaisesRegex(KnowledgeBaseRetrievalError, "task scope"):
            await self.retriever(client).retrieve(self.request(), ["imports"])

    async def test_valid_code_and_task_passages_are_returned(self) -> None:
        client = FakeRetrieveClient(
            {"imports": [self.code_passage(chunk_id="code-1"), self.passage()]}
        )
        result = await self.retriever(client).retrieve(self.request(), ["imports"])
        by_id = {chunk.chunk_id: chunk for chunk in result.chunks}
        self.assertEqual(by_id["code-1"].repository_commit, "abc123")
        self.assertEqual(by_id["chunk-1"].source_uri, "upload://docs/import.md")

    async def test_empty_results_and_queries_fail_closed(self) -> None:
        retriever = self.retriever(FakeRetrieveClient({}))
        with self.assertRaisesRegex(KnowledgeBaseRetrievalError, "no passages"):
            await retriever.retrieve(self.request(), ["imports"])
        with self.assertRaisesRegex(KnowledgeBaseRetrievalError, "non-empty query"):
            await retriever.retrieve(self.request(), [" ", ""])

    async def test_queries_over_budget_are_dropped_after_deduplication(self) -> None:
        client = FakeRetrieveClient({"one": [self.passage()]})
        retriever = self.retriever(client, max_queries=2)
        with self.assertLogs("prd_agent.knowledge_base", "WARNING") as logs:
            result = await retriever.retrieve(
                self.request(), ["one", " one ", "two", "three", "four"]
            )
        self.assertEqual([call["query"] for call in client.calls], ["one", "two"])
        self.assertEqual([chunk.chunk_id for chunk in result.chunks], ["chunk-1"])
        self.assertIn("dropping 2 of 4 search queries", logs.output[0])

    async def test_queries_within_budget_log_nothing(self) -> None:
        client = FakeRetrieveClient({"one": [self.passage()]})
        retriever = self.retriever(client, max_queries=2)
        with self.assertNoLogs("prd_agent.knowledge_base", "WARNING"):
            await retriever.retrieve(self.request(), ["one", "two"])
        self.assertEqual(len(client.calls), 2)

    async def test_non_code_passage_cannot_carry_commit(self) -> None:
        client = FakeRetrieveClient(
            {"imports": [self.passage(repository_commit="abc123")]}
        )
        with self.assertRaisesRegex(KnowledgeBaseRetrievalError, "non-code"):
            await self.retriever(client).retrieve(self.request(), ["imports"])


class BedrockRetrieveClientTest(unittest.IsolatedAsyncioTestCase):
    def custom_source_result(self, **metadata_overrides):
        metadata = {
            "chunk_id": "chunk-1",
            "project_id": "project-1",
            "source_type": "document",
            "source_uri": "upload://docs/import.md",
            "locator": "Import > Limits",
            "task_id": TASK_ID,
            "_data_source_type": "CUSTOM",
        }
        metadata.update(metadata_overrides)
        return {
            "content": {"type": "TEXT", "text": "At most 10,000 rows."},
            "location": {
                "type": "CUSTOM",
                "customDocumentLocation": {"id": "task-1-0001"},
            },
            "metadata": metadata,
            "score": 0.61,
        }

    async def test_sends_runtime_filter_and_parses_custom_source_results(self) -> None:
        runtime = FakeBedrockRuntime(
            {"retrievalResults": [self.custom_source_result()]}
        )
        client = BedrockKnowledgeBaseRetrieveClient(
            client=runtime, knowledge_base_id="KB12345678"
        )
        metadata_filter = {"equals": {"key": "task_id", "value": TASK_ID}}
        passages = await client.retrieve(
            query=" row limit ", metadata_filter=metadata_filter, top_k=5
        )
        self.assertEqual(
            runtime.calls,
            [
                {
                    "knowledgeBaseId": "KB12345678",
                    "retrievalQuery": {"text": "row limit"},
                    "retrievalConfiguration": {
                        "managedSearchConfiguration": {
                            "numberOfResults": 5,
                            "filter": metadata_filter,
                        }
                    },
                }
            ],
        )
        self.assertEqual(passages[0].task_id, TASK_ID)
        self.assertEqual(passages[0].source_uri, "upload://docs/import.md")
        self.assertEqual(passages[0].retrieval_score, 0.61)

    async def test_knowledge_base_chunk_id_identifies_each_chunk(self) -> None:
        # A whole-file document carries no chunk_id of its own; the service's
        # _chunk_id wins over a caller-set one when both are present.
        results = [
            self.custom_source_result(chunk_id=None, _chunk_id="kb-chunk-a"),
            self.custom_source_result(chunk_id="file-level", _chunk_id="kb-chunk-b"),
        ]
        client = BedrockKnowledgeBaseRetrieveClient(
            client=FakeBedrockRuntime({"retrievalResults": results}),
            knowledge_base_id="KB12345678",
        )
        passages = await client.retrieve(query="q", metadata_filter={}, top_k=2)
        self.assertEqual(
            [passage.chunk_id for passage in passages], ["kb-chunk-a", "kb-chunk-b"]
        )

    async def test_missing_traceability_or_bad_task_metadata_fails_closed(self) -> None:
        for overrides, message in (
            ({"source_uri": None}, "traceability"),
            ({"task_id": ""}, "task_id metadata"),
            ({"task_id": 7}, "task_id metadata"),
        ):
            with self.subTest(overrides=overrides):
                runtime = FakeBedrockRuntime(
                    {"retrievalResults": [self.custom_source_result(**overrides)]}
                )
                client = BedrockKnowledgeBaseRetrieveClient(
                    client=runtime, knowledge_base_id="KB12345678"
                )
                with self.assertRaisesRegex(KnowledgeBaseRetrievalError, message):
                    await client.retrieve(query="q", metadata_filter={}, top_k=1)

    async def test_malformed_response_fails_closed(self) -> None:
        client = BedrockKnowledgeBaseRetrieveClient(
            client=FakeBedrockRuntime({}), knowledge_base_id="KB12345678"
        )
        with self.assertRaisesRegex(KnowledgeBaseRetrievalError, "retrievalResults"):
            await client.retrieve(query="q", metadata_filter={}, top_k=1)


if __name__ == "__main__":
    unittest.main()
