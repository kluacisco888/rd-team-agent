from __future__ import annotations

import unittest

from pydantic import ValidationError

from prd_agent.contracts import (
    MAX_TASK_DOCUMENT_TOTAL_BYTES,
    MAX_TASK_DOCUMENTS,
    InvocationRequest,
)
from prd_agent.task_documents import (
    KnowledgeBaseTaskDocuments,
    TaskDocumentIndexingFailed,
    TaskDocumentIndexingTimeout,
    TaskDocumentRejected,
)

TASK_ID = "task-0123456789abcdef"
MARKDOWN = (
    "# Import\n\nAdmins upload CSV files.\n\n"
    "## Limits\n\nAt most 10,000 rows per file.\n\n"
    "## Errors\n\nA downloadable error report lists each failed row.\n"
)


class FakeClientError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.response = {"Error": {"Code": code, "Message": "fake"}}


class FakeDocumentsClient:
    """Mimics the boto3 bedrock-agent direct-ingestion operations."""

    def __init__(
        self,
        *,
        polls_until_indexed: int = 1,
        final_status: str = "INDEXED",
        final_statuses: dict[str, str] | None = None,
        ingest_errors: list[str] | None = None,
        delete_error: str | None = None,
    ) -> None:
        self.polls_until_indexed = polls_until_indexed
        self.final_status = final_status
        self.final_statuses = dict(final_statuses or {})
        self.ingest_errors = list(ingest_errors or [])
        self.delete_error = delete_error
        self.ingest_calls: list[dict] = []
        self.get_calls: list[dict] = []
        self.delete_calls: list[dict] = []
        self.polls: dict[str, int] = {}

    def ingest_knowledge_base_documents(self, **kwargs):
        self.ingest_calls.append(kwargs)
        if self.ingest_errors:
            raise FakeClientError(self.ingest_errors.pop(0))
        return {
            "documentDetails": [
                {
                    "identifier": {
                        "dataSourceType": "CUSTOM",
                        "custom": {
                            "id": item["content"]["custom"]["customDocumentIdentifier"][
                                "id"
                            ]
                        },
                    },
                    "status": "STARTING",
                }
                for item in kwargs["documents"]
            ]
        }

    def get_knowledge_base_documents(self, **kwargs):
        self.get_calls.append(kwargs)
        details = []
        for identifier in kwargs["documentIdentifiers"]:
            document_id = identifier["custom"]["id"]
            self.polls[document_id] = self.polls.get(document_id, 0) + 1
            status = (
                self.final_statuses.get(document_id, self.final_status)
                if self.polls[document_id] >= self.polls_until_indexed
                else "IN_PROGRESS"
            )
            details.append(
                {"identifier": identifier, "status": status, "statusReason": "fake"}
            )
        return {"documentDetails": details}

    def delete_knowledge_base_documents(self, **kwargs):
        self.delete_calls.append(kwargs)
        if self.delete_error:
            raise FakeClientError(self.delete_error)
        return {"documentDetails": []}

    def ingested_ids(self) -> list[str]:
        return [
            item["content"]["custom"]["customDocumentIdentifier"]["id"]
            for call in self.ingest_calls
            for item in call["documents"]
        ]

    def deleted_ids(self) -> list[str]:
        return [
            identifier["custom"]["id"]
            for call in self.delete_calls
            for identifier in call["documentIdentifiers"]
        ]


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def invocation(**updates) -> InvocationRequest:
    values = {
        "project_id": "mock-prd-e2e",
        "requirement_text": "Preview CSV import errors.",
        "prd_contract_version": "v1",
        "repository": "agentcore-prd-agent",
        "repository_commit": "source-abc",
        "documents": [{"path": "docs/import.md", "content": MARKDOWN}],
    }
    values.update(updates)
    return InvocationRequest.model_validate(values)


def attributes(document: dict) -> dict:
    result = {}
    for item in document["metadata"]["inlineAttributes"]:
        value = item["value"]
        result[item["key"]] = value.get("stringValue", value.get("numberValue"))
    return result


class InvocationRequestTest(unittest.TestCase):
    def test_caller_cannot_choose_the_knowledge_base_scope(self) -> None:
        with self.assertRaises(ValidationError):
            invocation(knowledge_base_snapshot="task-of-someone-else")

    def test_generation_request_takes_runtime_scope_and_drops_documents(self) -> None:
        request = invocation().to_generation_request(TASK_ID)
        self.assertEqual(request.knowledge_base_snapshot, TASK_ID)
        self.assertNotIn("documents", request.model_dump())
        self.assertEqual(request.repository_commit, "source-abc")

    def test_document_paths_must_be_relative_and_unique(self) -> None:
        for path in ("/etc/passwd", "../secret.md", "a/../b.md", "./a.md", "a\\b.md"):
            with self.subTest(path=path), self.assertRaises(ValidationError):
                invocation(documents=[{"path": path, "content": "# A\n\nB"}])
        with self.assertRaisesRegex(ValidationError, "unique"):
            invocation(
                documents=[
                    {"path": "a.md", "content": "# A\n\nB"},
                    {"path": "a.md", "content": "# C\n\nD"},
                ]
            )

    def test_document_count_and_size_are_bounded(self) -> None:
        with self.assertRaises(ValidationError):
            invocation(documents=[])
        with self.assertRaises(ValidationError):
            invocation(documents=[{"path": "a.md", "content": "  \n "}])
        with self.assertRaises(ValidationError):
            invocation(
                documents=[
                    {"path": f"d{index}.md", "content": "# A\n\nB"}
                    for index in range(MAX_TASK_DOCUMENTS + 1)
                ]
            )
        with self.assertRaisesRegex(ValidationError, "UTF-8 bytes"):
            invocation(
                documents=[
                    {
                        "path": "big.md",
                        "content": "x" * (MAX_TASK_DOCUMENT_TOTAL_BYTES + 1),
                    }
                ]
            )


class TaskDocumentsTest(unittest.IsolatedAsyncioTestCase):
    def stager(self, client: FakeDocumentsClient, fake_time: FakeTime, **options):
        return KnowledgeBaseTaskDocuments(
            client=client,
            knowledge_base_id="KB12345678",
            data_source_id="DS12345678",
            project_id="mock-prd-e2e",
            sleep=fake_time.sleep,
            clock=fake_time.clock,
            **options,
        )

    async def test_each_file_is_one_tagged_document_indexed_then_deleted(self) -> None:
        client = FakeDocumentsClient(polls_until_indexed=3)
        fake_time = FakeTime()
        request = invocation(
            documents=[
                {"path": "docs/import.md", "content": MARKDOWN},
                {"path": "notes.txt", "content": "Plain notes."},
            ]
        )
        async with self.stager(client, fake_time, poll_interval_seconds=2).stage(
            task_id=TASK_ID, request=request
        ) as staged:
            self.assertEqual(client.delete_calls, [])
            self.assertEqual(staged.task_id, TASK_ID)
            self.assertEqual(list(staged.document_ids), client.ingested_ids())

        call = client.ingest_calls[0]
        self.assertEqual(
            (call["knowledgeBaseId"], call["dataSourceId"]),
            ("KB12345678", "DS12345678"),
        )
        documents = call["documents"]
        # The managed knowledge base chunks each file itself.
        self.assertEqual(client.ingested_ids(), [f"{TASK_ID}-00", f"{TASK_ID}-01"])
        self.assertEqual(
            [
                document["content"]["custom"]["inlineContent"]["textContent"]["data"]
                for document in documents
            ],
            [MARKDOWN, "Plain notes."],
        )
        paths = ("docs/import.md", "notes.txt")
        for document, path in zip(documents, paths, strict=True):
            self.assertEqual(document["content"]["dataSourceType"], "CUSTOM")
            tags = attributes(document)
            self.assertEqual(tags["task_id"], TASK_ID)
            self.assertEqual(tags["project_id"], "mock-prd-e2e")
            self.assertEqual(tags["source_type"], "document")
            self.assertEqual(tags["source_path"], path)
            self.assertEqual(tags["source_uri"], f"upload://{path}")
            self.assertEqual(tags["locator"], path)
            self.assertEqual(len(tags["document_hash"]), 64)
            self.assertNotIn("chunk_id", tags)
        self.assertEqual(fake_time.sleeps, [2, 2])  # polled three times
        self.assertEqual(sorted(client.deleted_ids()), sorted(client.ingested_ids()))

    async def test_documents_are_deleted_when_generation_fails(self) -> None:
        client = FakeDocumentsClient()
        with self.assertRaisesRegex(RuntimeError, "model failed"):
            async with self.stager(client, FakeTime()).stage(
                task_id=TASK_ID, request=invocation()
            ):
                raise RuntimeError("model failed")
        self.assertEqual(sorted(client.deleted_ids()), sorted(client.ingested_ids()))

    async def test_failed_indexing_fails_closed_and_cleans_up(self) -> None:
        client = FakeDocumentsClient(final_status="FAILED")
        with self.assertRaisesRegex(
            TaskDocumentIndexingFailed, r"'docs/import\.md' \(status FAILED\)"
        ):
            async with self.stager(client, FakeTime()).stage(
                task_id=TASK_ID, request=invocation()
            ):
                self.fail("must not run generation on unindexed documents")
        self.assertEqual(sorted(client.deleted_ids()), sorted(client.ingested_ids()))

    async def test_index_timeout_fails_closed_and_cleans_up(self) -> None:
        client = FakeDocumentsClient(polls_until_indexed=10_000)
        fake_time = FakeTime()
        stager = self.stager(
            client, fake_time, poll_interval_seconds=5, index_timeout_seconds=12
        )
        with (
            self.assertLogs("prd_agent.task_documents", level="WARNING") as logs,
            self.assertRaisesRegex(
                TaskDocumentIndexingTimeout, "1 of 1 task documents.*within 12 seconds"
            ),
        ):
            async with stager.stage(task_id=TASK_ID, request=invocation()):
                self.fail("must not run generation on unindexed documents")
        self.assertEqual(fake_time.sleeps, [5, 5, 5])
        self.assertTrue(
            any(
                "docs/import.md" in line and "IN_PROGRESS" in line
                for line in logs.output
            )
        )
        self.assertEqual(sorted(client.deleted_ids()), sorted(client.ingested_ids()))

    async def test_concurrency_limit_is_retried_with_backoff(self) -> None:
        client = FakeDocumentsClient(
            ingest_errors=["ValidationException", "ThrottlingException"]
        )
        fake_time = FakeTime()
        async with self.stager(client, fake_time, retry_base_seconds=1).stage(
            task_id=TASK_ID, request=invocation()
        ):
            pass
        self.assertEqual(len(client.ingest_calls), 3)
        self.assertEqual(fake_time.sleeps[:2], [1, 2])

    async def test_non_retryable_ingest_error_still_deletes_attempted_ids(self) -> None:
        client = FakeDocumentsClient(ingest_errors=["AccessDeniedException"])
        with self.assertRaises(FakeClientError):
            async with self.stager(client, FakeTime()).stage(
                task_id=TASK_ID, request=invocation()
            ):
                self.fail("must not run generation when ingestion fails")
        self.assertEqual(len(client.ingest_calls), 1)
        self.assertEqual(sorted(client.deleted_ids()), sorted(client.ingested_ids()))

    async def test_text_indexed_is_ready_and_other_statuses_fail_fast(self) -> None:
        client = FakeDocumentsClient(final_status="TEXT_INDEXED")
        async with self.stager(client, FakeTime()).stage(
            task_id=TASK_ID, request=invocation()
        ) as staged:
            self.assertEqual(staged.document_ids, (f"{TASK_ID}-00",))
        for status in ("PARTIALLY_INDEXED", "IGNORED", "METADATA_UPDATE_FAILED"):
            with self.subTest(status=status):
                client = FakeDocumentsClient(
                    polls_until_indexed=1,
                    final_status="INDEXED",
                    final_statuses={f"{TASK_ID}-01": status},
                )
                fake_time = FakeTime()
                request = invocation(
                    documents=[
                        {"path": "a.md", "content": "# A\n\nB"},
                        {"path": "b.md", "content": "# C\n\nD"},
                    ]
                )
                with self.assertRaisesRegex(TaskDocumentIndexingFailed, "'b.md'"):
                    async with self.stager(client, fake_time).stage(
                        task_id=TASK_ID, request=request
                    ):
                        self.fail("must not run generation on a failed document")
                self.assertEqual(fake_time.sleeps, [])
                self.assertEqual(
                    sorted(client.deleted_ids()), sorted(client.ingested_ids())
                )

    async def test_requests_are_batched_to_the_api_limit_of_ten(self) -> None:
        client = FakeDocumentsClient()
        request = invocation(
            documents=[
                {"path": f"d{index}.md", "content": f"# D{index}\n\nBody {index}."}
                for index in range(MAX_TASK_DOCUMENTS)
            ]
        )
        async with self.stager(client, FakeTime()).stage(
            task_id=TASK_ID, request=request
        ):
            pass
        self.assertEqual(
            [len(call["documents"]) for call in client.ingest_calls], [10, 10]
        )
        self.assertTrue(
            all(len(call["documentIdentifiers"]) <= 10 for call in client.get_calls)
        )
        self.assertEqual(
            [len(call["documentIdentifiers"]) for call in client.delete_calls],
            [10, 10],
        )
        self.assertEqual(len(set(client.ingested_ids())), MAX_TASK_DOCUMENTS)

    async def test_foreign_project_is_rejected_before_any_write(self) -> None:
        client = FakeDocumentsClient()
        with self.assertRaisesRegex(TaskDocumentRejected, "not served"):
            async with self.stager(client, FakeTime()).stage(
                task_id=TASK_ID, request=invocation(project_id="other-project")
            ):
                pass
        self.assertEqual((client.ingest_calls, client.delete_calls), ([], []))

    async def test_cleanup_failure_is_logged_without_masking_success(self) -> None:
        client = FakeDocumentsClient(delete_error="AccessDeniedException")
        with self.assertLogs("prd_agent.task_documents", level="WARNING") as logs:
            async with self.stager(client, FakeTime()).stage(
                task_id=TASK_ID, request=invocation()
            ) as staged:
                result = staged.task_id
        self.assertEqual(result, TASK_ID)
        self.assertTrue(any("scheduled cleanup" in line for line in logs.output))
        self.assertTrue(all(MARKDOWN not in line for line in logs.output))


if __name__ == "__main__":
    unittest.main()
