from __future__ import annotations

import unittest

from pydantic import ValidationError

from prd_agent.gateway_transport import (
    AgentCoreGatewayConfig,
    MCPGatewayRetrieveClient,
)
from prd_agent.knowledge_base import KnowledgeBaseRetrievalError


class FakeMCPClient:
    def __init__(self, result) -> None:
        self.result = result
        self.calls = []
        self.entered = False

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.entered = False

    def call_tool_sync(self, *args):
        self.calls.append(args)
        return self.result


class RecordingClientFactory:
    def __init__(self, result) -> None:
        self.result = result
        self.headers = []
        self.clients = []

    def __call__(self, headers):
        self.headers.append(headers)
        client = FakeMCPClient(self.result)
        self.clients.append(client)
        return client


def successful_result():
    import json

    payload = {
        "retrievalResults": [
            {
                "content": {"type": "TEXT", "text": "Existing import behavior."},
                "location": {"s3Location": {"uri": "s3://example/doc.md"}},
                "metadata": {
                    "chunk_id": "chunk-1",
                    "project_id": "project-1",
                    "source_type": "document",
                    "heading": "Import workflow",
                    "x-amz-bedrock-kb-source-uri": "s3://example/doc.md",
                },
                "score": 0.91,
            },
            {
                "content": {"type": "TEXT", "text": "class ImportService: ..."},
                "location": {"s3Location": {"uri": "s3://example/src/imports.py"}},
                "metadata": {
                    "chunk_id": "chunk-2",
                    "project_id": "project-1",
                    "source_type": "code",
                    "file_path": "src/imports.py",
                    "line_start": 10,
                    "line_end": 20,
                    "commit_sha": "abc123",
                },
                "score": 0.8,
            },
        ]
    }
    return {
        "status": "success",
        "toolUseId": "tool-1",
        "content": [{"text": json.dumps(payload)}],
        "isError": False,
    }


class GatewayTransportTest(unittest.IsolatedAsyncioTestCase):
    def config(self) -> AgentCoreGatewayConfig:
        return AgentCoreGatewayConfig(
            url="https://gateway.example.com/mcp",
            retrieve_tool_name="project-kb___Retrieve",
            timeout_seconds=45,
            send_managed_search_configuration=True,
        )

    async def test_calls_retrieve_with_server_controlled_context_and_filter(self) -> None:
        factory = RecordingClientFactory(successful_result())
        client = MCPGatewayRetrieveClient(
            config=self.config(),
            access_token_provider=lambda: "secret-token",
            client_factory=factory,
        )
        passages = await client.retrieve(
            query=" existing import ",
            project_id="project-1",
            user_id="user-1",
            top_k=7,
        )
        self.assertEqual([passage.chunk_id for passage in passages], ["chunk-1", "chunk-2"])
        self.assertEqual(passages[0].locator, "Import workflow")
        self.assertEqual(passages[1].locator, "src/imports.py:10-20")
        self.assertEqual(passages[1].repository_commit, "abc123")
        self.assertEqual(factory.headers, [{"Authorization": "Bearer secret-token"}])

        call = factory.clients[0].calls[0]
        self.assertEqual(call[1], "project-kb___Retrieve")
        arguments = call[2]
        self.assertEqual(arguments["retrievalQuery"], {"text": "existing import"})
        managed = arguments["retrievalConfiguration"]["managedSearchConfiguration"]
        self.assertEqual(managed["numberOfResults"], 7)
        self.assertEqual(managed["overrideSearchType"], "HYBRID")
        self.assertEqual(
            managed["filter"],
            {"equals": {"key": "project_id", "value": "project-1"}},
        )
        self.assertEqual(arguments["userContext"], {"userId": "user-1"})
        self.assertEqual(call[3].total_seconds(), 45)

    async def test_tool_error_and_missing_retrieve_payload_fail_closed(self) -> None:
        for result, message in (
            ({"status": "error", "content": []}, "Retrieve tool failed"),
            ({"status": "success", "content": []}, "no text payload"),
            (
                {"status": "success", "content": [{"text": "{}"}]},
                "no retrievalResults",
            ),
        ):
            with self.subTest(result=result):
                client = MCPGatewayRetrieveClient(
                    config=self.config(),
                    access_token_provider=lambda: "token",
                    client_factory=RecordingClientFactory(result),
                )
                with self.assertRaisesRegex(KnowledgeBaseRetrievalError, message):
                    await client.retrieve(
                        query="imports",
                        project_id="project-1",
                        user_id="user-1",
                        top_k=5,
                    )

    async def test_missing_traceability_metadata_fails_closed(self) -> None:
        import json

        result = successful_result()
        payload = json.loads(result["content"][0]["text"])
        del payload["retrievalResults"][0]["metadata"]["chunk_id"]
        result["content"][0]["text"] = json.dumps(payload)
        client = MCPGatewayRetrieveClient(
            config=self.config(),
            access_token_provider=lambda: "token",
            client_factory=RecordingClientFactory(result),
        )
        with self.assertRaisesRegex(KnowledgeBaseRetrievalError, "traceability"):
            await client.retrieve(
                query="imports",
                project_id="project-1",
                user_id="user-1",
                top_k=5,
            )

    async def test_missing_access_token_fails_before_opening_client(self) -> None:
        factory = RecordingClientFactory(successful_result())
        client = MCPGatewayRetrieveClient(
            config=self.config(),
            access_token_provider=lambda: "",
            client_factory=factory,
        )
        with self.assertRaisesRegex(KnowledgeBaseRetrievalError, "token is unavailable"):
            await client.retrieve(
                query="imports",
                project_id="project-1",
                user_id="user-1",
                top_k=5,
            )
        self.assertEqual(factory.clients, [])

    def test_gateway_requires_https_and_known_search_type(self) -> None:
        with self.assertRaisesRegex(ValidationError, "must use HTTPS"):
            AgentCoreGatewayConfig(
                url="http://gateway.example.com/mcp",
                retrieve_tool_name="Retrieve",
            )
        with self.assertRaisesRegex(ValidationError, "HYBRID or SEMANTIC"):
            AgentCoreGatewayConfig(
                url="https://gateway.example.com/mcp",
                retrieve_tool_name="Retrieve",
                search_type="INVALID",
            )
    async def test_authenticated_client_factory_does_not_require_bearer_token(self) -> None:
        factory = RecordingClientFactory(successful_result())
        client = MCPGatewayRetrieveClient(
            config=self.config(),
            client_factory=factory,
        )
        passages = await client.retrieve(
            query="imports",
            project_id="project-1",
            user_id="user-1",
            top_k=5,
        )
        self.assertEqual(passages[0].chunk_id, "chunk-1")
        self.assertEqual(factory.headers, [{}])

    def test_gateway_requires_an_authentication_path(self) -> None:
        with self.assertRaisesRegex(ValueError, "authenticated client_factory"):
            MCPGatewayRetrieveClient(config=self.config())
    async def test_default_request_leaves_search_configuration_server_side(self) -> None:
        factory = RecordingClientFactory(successful_result())
        client = MCPGatewayRetrieveClient(
            config=AgentCoreGatewayConfig(
                url="https://gateway.example.com/mcp",
                retrieve_tool_name="project-kb___Retrieve",
            ),
            client_factory=factory,
        )
        await client.retrieve(
            query="imports",
            project_id="project-1",
            user_id="user-1",
            top_k=99,
        )
        arguments = factory.clients[0].calls[0][2]
        self.assertEqual(arguments["retrievalQuery"], {"text": "imports"})
        self.assertEqual(arguments["userContext"], {"userId": "user-1"})
        self.assertNotIn("retrievalConfiguration", arguments)


if __name__ == "__main__":
    unittest.main()
