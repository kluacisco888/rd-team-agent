from __future__ import annotations

import unittest
from unittest.mock import patch

import httpx
from botocore.credentials import Credentials

from prd_agent.aws_auth import SigV4HttpxAuth, SigV4MCPClientFactory


class FakeCredentialProvider:
    def __init__(self, credentials=None) -> None:
        self._credentials = credentials

    def get_credentials(self):
        return self._credentials


class SigV4AuthTest(unittest.TestCase):
    def test_signs_agentcore_gateway_request(self) -> None:
        auth = SigV4HttpxAuth(
            region_name="us-east-1",
            credential_provider=FakeCredentialProvider(
                Credentials("TESTACCESSKEY", "test-secret", "test-session")
            ),
        )
        request = httpx.Request(
            "POST",
            "https://gateway.example.com/mcp",
            json={"jsonrpc": "2.0", "method": "tools/list", "id": 1},
        )
        signed = next(auth.auth_flow(request))
        self.assertTrue(signed.headers["Authorization"].startswith("AWS4-HMAC-SHA256"))
        self.assertIn("Credential=TESTACCESSKEY/", signed.headers["Authorization"])
        self.assertIn("/us-east-1/bedrock-agentcore/aws4_request", signed.headers["Authorization"])
        self.assertEqual(signed.headers["X-Amz-Security-Token"], "test-session")
        self.assertIn("X-Amz-Date", signed.headers)

    def test_missing_credentials_fails_closed(self) -> None:
        auth = SigV4HttpxAuth(
            region_name="us-east-1",
            credential_provider=FakeCredentialProvider(),
        )
        request = httpx.Request("GET", "https://gateway.example.com/mcp")
        with self.assertRaisesRegex(RuntimeError, "credentials are unavailable"):
            next(auth.auth_flow(request))

    def test_factory_configures_strands_mcp_client_with_sigv4(self) -> None:
        with patch("prd_agent.aws_auth.MCPClient") as client_class:
            factory = SigV4MCPClientFactory(
                url="https://gateway.example.com/mcp",
                region_name="us-east-1",
            )
            factory({})
            kwargs = client_class.call_args.kwargs
            self.assertEqual(kwargs["url"], "https://gateway.example.com/mcp")
            self.assertIsInstance(kwargs["auth_provider"], SigV4HttpxAuth)
            self.assertIsNone(kwargs["headers"])
            self.assertFalse(kwargs["continue_on_error"])

    def test_factory_rejects_non_https_gateway(self) -> None:
        with self.assertRaisesRegex(ValueError, "must use HTTPS"):
            SigV4MCPClientFactory(
                url="http://gateway.example.com/mcp",
                region_name="us-east-1",
            )


if __name__ == "__main__":
    unittest.main()
