"""Caller identity from the Cognito access token the Runtime already validated.

The Runtime's JWT authorizer rejects bad tokens before the code runs. The code
verifies the token again (signature, issuer, expiry, client, token type), so a
misconfigured or missing authorizer cannot make it trust a forged token. The
user ID comes only from the verified ``sub`` claim.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import jwt

USER_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,127}$")


class AuthenticationError(Exception):
    """The request carries no usable identity. The message is safe to return."""


@dataclass(frozen=True, slots=True)
class Caller:
    user_id: str
    username: str | None
    client_id: str


class SigningKeySource(Protocol):
    def get_signing_key_from_jwt(self, token: str) -> Any: ...


def authorization_header(headers: Mapping[str, str] | None) -> str | None:
    for name, value in (headers or {}).items():
        if name.lower() == "authorization":
            return value
    return None


class CognitoAccessTokenVerifier:
    def __init__(
        self,
        *,
        issuer: str,
        allowed_client_ids: Iterable[str],
        key_source: SigningKeySource | None = None,
        leeway_seconds: int = 30,
    ) -> None:
        self._issuer = issuer
        self._allowed = frozenset(allowed_client_ids)
        if not self._allowed:
            raise ValueError("at least one allowed client ID is required")
        self._keys = key_source or jwt.PyJWKClient(
            f"{issuer}/.well-known/jwks.json", cache_keys=True, lifespan=3600
        )
        self._leeway = leeway_seconds

    def verify(self, header: str | None) -> Caller:
        """Blocking (fetches JWKS on a cache miss); call it from a worker thread."""

        if not header or not header.startswith("Bearer "):
            raise AuthenticationError("missing bearer token")
        token = header[len("Bearer ") :].strip()
        try:
            key = self._keys.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                getattr(key, "key", key),
                algorithms=["RS256"],
                issuer=self._issuer,
                leeway=self._leeway,
                options={
                    "require": ["exp", "iat", "iss", "sub", "token_use", "client_id"],
                    # Cognito access tokens carry client_id, not aud.
                    "verify_aud": False,
                },
            )
        except jwt.PyJWTError as error:
            raise AuthenticationError("invalid or expired token") from error
        if claims.get("token_use") != "access":
            raise AuthenticationError("an access token is required")
        client_id = claims.get("client_id")
        if client_id not in self._allowed:
            raise AuthenticationError("token was issued to an unknown client")
        user_id = claims.get("sub")
        if not isinstance(user_id, str) or not USER_ID_PATTERN.fullmatch(user_id):
            raise AuthenticationError("token subject is invalid")
        username = claims.get("username")
        return Caller(
            user_id=user_id,
            username=username if isinstance(username, str) else None,
            client_id=client_id,
        )
