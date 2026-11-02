"""AWS SigV4 authentication for Strands MCP calls to AgentCore Gateway."""

from __future__ import annotations

from typing import Protocol

import boto3
import httpx
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.credentials import ReadOnlyCredentials
from strands.tools.mcp.mcp_client import MCPClient


class CredentialProvider(Protocol):
    def get_credentials(self): ...


class SigV4HttpxAuth(httpx.Auth):
    """Sign each buffered HTTP request with the Runtime execution role."""

    requires_request_body = True

    def __init__(
        self,
        *,
        region_name: str,
        service_name: str = "bedrock-agentcore",
        credential_provider: CredentialProvider | None = None,
    ) -> None:
        if not region_name or not service_name:
            raise ValueError("region_name and service_name are required")
        self._region_name = region_name
        self._service_name = service_name
        self._credential_provider = credential_provider or boto3.Session(
            region_name=region_name
        )

    def auth_flow(self, request: httpx.Request):
        credentials = self._credential_provider.get_credentials()
        if credentials is None:
            raise RuntimeError("AWS credentials are unavailable for Gateway signing")
        frozen: ReadOnlyCredentials = credentials.get_frozen_credentials()
        aws_request = AWSRequest(
            method=request.method,
            url=str(request.url),
            data=request.content,
            headers=dict(request.headers),
        )
        SigV4Auth(
            frozen,
            self._service_name,
            self._region_name,
        ).add_auth(aws_request)
        request.headers.update(dict(aws_request.headers))
        yield request


class SigV4MCPClientFactory:
    """Create authenticated Strands MCP clients for an AWS_IAM Gateway."""

    def __init__(self, *, url: str, region_name: str) -> None:
        if not url.startswith("https://"):
            raise ValueError("AgentCore Gateway URL must use HTTPS")
        self._url = url
        self._auth = SigV4HttpxAuth(region_name=region_name)

    def __call__(self, headers: dict[str, str]) -> MCPClient:
        return MCPClient(
            url=self._url,
            headers=headers or None,
            auth_provider=self._auth,
            application_name="agentcore-prd-agent",
            continue_on_error=False,
        )
