"""Background PRD results in S3 under results/<user>/<session>/<task>/."""

from __future__ import annotations

import json
import re
from typing import Any

from botocore.exceptions import ClientError

from orchestrator.identity import USER_ID_PATTERN

SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{32,99}$")
TASK_ID_PATTERN = re.compile(r"^task-[0-9a-f]{32}$")


def result_key(user_id: str, session_id: str, task_id: str, name: str) -> str:
    """Every part is validated, so no caller input can reach another user's prefix."""

    if not USER_ID_PATTERN.fullmatch(user_id):
        raise ValueError("invalid user ID")
    if not SESSION_ID_PATTERN.fullmatch(session_id):
        raise ValueError("invalid session ID")
    if not TASK_ID_PATTERN.fullmatch(task_id):
        raise ValueError("invalid task ID")
    if name not in {"status.json", "prd.md"}:
        raise ValueError("invalid result object name")
    return f"results/{user_id}/{session_id}/{task_id}/{name}"


class ResultStore:
    def __init__(self, *, client: Any, bucket: str, expected_owner: str) -> None:
        self._client = client
        self._bucket = bucket
        self._owner = expected_owner

    def _put(self, key: str, body: bytes, content_type: str) -> None:
        self._client.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=body,
            ContentType=content_type,
            ServerSideEncryption="AES256",
            ExpectedBucketOwner=self._owner,
        )

    def _get(self, key: str) -> bytes | None:
        try:
            response = self._client.get_object(
                Bucket=self._bucket, Key=key, ExpectedBucketOwner=self._owner
            )
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") in {"NoSuchKey", "404"}:
                return None
            raise
        return response["Body"].read()

    def put_status(
        self, user_id: str, session_id: str, task_id: str, status: dict
    ) -> None:
        body = json.dumps(status, ensure_ascii=False, sort_keys=True).encode("utf-8")
        key = result_key(user_id, session_id, task_id, "status.json")
        self._put(key, body, "application/json")

    def get_status(self, user_id: str, session_id: str, task_id: str) -> dict | None:
        body = self._get(result_key(user_id, session_id, task_id, "status.json"))
        return None if body is None else json.loads(body)

    def put_prd(
        self, user_id: str, session_id: str, task_id: str, markdown: str
    ) -> None:
        key = result_key(user_id, session_id, task_id, "prd.md")
        self._put(key, markdown.encode("utf-8"), "text/markdown; charset=utf-8")

    def get_prd(self, user_id: str, session_id: str, task_id: str) -> str | None:
        body = self._get(result_key(user_id, session_id, task_id, "prd.md"))
        return None if body is None else body.decode("utf-8")
