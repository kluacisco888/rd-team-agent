"""Concrete Strands MCP transport for AgentCore Knowledge Base Gateway."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
from datetime import timedelta
from typing import Any, Protocol
from uuid import uuid4

from pydantic import ConfigDict, Field, field_validator
from strands.tools.mcp.mcp_client import MCPClient

from prd_agent.contracts import StrictModel
from prd_agent.knowledge_base import (
    GatewayPassage,
    KnowledgeBaseRetrievalError,
    parse_passage,
)


class MCPClientLike(Protocol):
    def __enter__(self) -> MCPClientLike: ...

    def __exit__(self, exc_type, exc_value, traceback) -> None: ...

    def call_tool_sync(
        self,
        tool_use_id: str,
        name: str,
        arguments: dict[str, Any] | None = None,
        read_timeout_seconds: timedelta | None = None,
    ) -> Mapping[str, Any]: ...


class AgentCoreGatewayConfig(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    url: str = Field(min_length=1)
    retrieve_tool_name: str = Field(min_length=1)
    timeout_seconds: int = Field(default=60, ge=1, le=900)
    search_type: str = "HYBRID"
    send_managed_search_configuration: bool = False

    @field_validator("url")
    @classmethod
    def require_https(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError("AgentCore Gateway URL must use HTTPS")
        return value

    @field_validator("search_type")
    @classmethod
    def validate_search_type(cls, value: str) -> str:
        if value not in {"HYBRID", "SEMANTIC"}:
            raise ValueError("search_type must be HYBRID or SEMANTIC")
        return value


ClientFactory = Callable[[dict[str, str]], MCPClientLike]
TokenProvider = Callable[[], str]


class MCPGatewayRetrieveClient:
    """Invoke the AgentCore managed-KB Retrieve tool through Strands MCPClient."""

    def __init__(
        self,
        *,
        config: AgentCoreGatewayConfig,
        access_token_provider: TokenProvider | None = None,
        client_factory: ClientFactory | None = None,
    ) -> None:
        if access_token_provider is None and client_factory is None:
            raise ValueError(
                "access_token_provider or an authenticated client_factory is required"
            )
        self._config = config
        self._access_token_provider = access_token_provider
        self._client_factory = client_factory or self._default_client_factory

    def _default_client_factory(self, headers: dict[str, str]) -> MCPClient:
        return MCPClient(
            url=self._config.url,
            headers=headers,
            application_name="agentcore-prd-agent",
            continue_on_error=False,
        )

    async def retrieve(
        self,
        *,
        query: str,
        project_id: str,
        user_id: str,
        top_k: int,
    ) -> Sequence[GatewayPassage]:
        if not query.strip() or not project_id or not user_id:
            raise ValueError("query, project_id, and user_id are required")
        if top_k < 1:
            raise ValueError("top_k must be positive")

        headers: dict[str, str] = {}
        if self._access_token_provider is not None:
            token = self._access_token_provider()
            if not token:
                raise KnowledgeBaseRetrievalError(
                    "AgentCore Gateway access token is unavailable"
                )
            headers["Authorization"] = f"Bearer {token}"

        arguments: dict[str, Any] = {
            "retrievalQuery": {"text": query.strip()},
            "userContext": {"userId": user_id},
        }
        if self._config.send_managed_search_configuration:
            arguments["retrievalConfiguration"] = {
                "managedSearchConfiguration": {
                    "numberOfResults": top_k,
                    "overrideSearchType": self._config.search_type,
                    "filter": {
                        "equals": {
                            "key": "project_id",
                            "value": project_id,
                        }
                    },
                }
            }
        result = await asyncio.to_thread(
            self._call_tool,
            headers,
            arguments,
        )
        return self._parse_result(result)

    def _call_tool(
        self,
        headers: dict[str, str],
        arguments: dict[str, Any],
    ) -> Mapping[str, Any]:
        with self._client_factory(headers) as client:
            return client.call_tool_sync(
                uuid4().hex,
                self._config.retrieve_tool_name,
                arguments,
                timedelta(seconds=self._config.timeout_seconds),
            )

    @staticmethod
    def _parse_result(result: Mapping[str, Any]) -> list[GatewayPassage]:
        if result.get("status") != "success" or result.get("isError") is True:
            raise KnowledgeBaseRetrievalError("AgentCore Gateway Retrieve tool failed")
        payload = result.get("structuredContent")
        if payload is None:
            content = result.get("content")
            if not isinstance(content, list):
                raise KnowledgeBaseRetrievalError(
                    "AgentCore Gateway Retrieve returned no content list"
                )
            text_payload = next(
                (
                    block.get("text")
                    for block in content
                    if isinstance(block, Mapping)
                    and isinstance(block.get("text"), str)
                ),
                None,
            )
            if text_payload is None:
                raise KnowledgeBaseRetrievalError(
                    "AgentCore Gateway Retrieve returned no text payload"
                )
            try:
                import json

                payload = json.loads(text_payload)
            except (TypeError, json.JSONDecodeError) as error:
                raise KnowledgeBaseRetrievalError(
                    "AgentCore Gateway Retrieve returned invalid JSON text"
                ) from error
        if not isinstance(payload, Mapping):
            raise KnowledgeBaseRetrievalError(
                "AgentCore Gateway Retrieve payload must be an object"
            )
        raw_results = payload.get("retrievalResults")
        if not isinstance(raw_results, list):
            raise KnowledgeBaseRetrievalError(
                "AgentCore Gateway Retrieve response has no retrievalResults list"
            )
        return [MCPGatewayRetrieveClient._parse_passage(item) for item in raw_results]

    @staticmethod
    def _parse_passage(item: Any) -> GatewayPassage:
        return parse_passage(item)
