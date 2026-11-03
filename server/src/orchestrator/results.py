"""Background PRD results in S3 under results/<user>/<session>/<task>/.

A deleted session leaves a tombstone at results/<user>/_deleted/<session>.
Memory keeps listing a session after all its events are deleted, so the
tombstone is what hides it. "_deleted" cannot be a session ID, so the two
prefixes never collide.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Any

from botocore.exceptions import ClientError

from orchestrator.identity import USER_ID_PATTERN

SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{32,99}$")
TASK_ID_PATTERN = re.compile(r"^task-[0-9a-f]{32}$")
OBJECT_NAMES = frozenset({"status.json", "prd.md", "request.json"})
TOMBSTONES = "_deleted"
DELETE_BATCH = 1000  # the DeleteObjects limit


def _user_prefix(user_id: str) -> str:
    if not USER_ID_PATTERN.fullmatch(user_id):
        raise ValueError("invalid user ID")
    return f"results/{user_id}/"


def session_prefix(user_id: str, session_id: str) -> str:
    """Every part is validated, so no caller input can reach another user's prefix."""

    if not SESSION_ID_PATTERN.fullmatch(session_id):
        raise ValueError("invalid session ID")
    return f"{_user_prefix(user_id)}{session_id}/"


def result_key(user_id: str, session_id: str, task_id: str, name: str) -> str:
    if not TASK_ID_PATTERN.fullmatch(task_id):
        raise ValueError("invalid task ID")
    if name not in OBJECT_NAMES:
        raise ValueError("invalid result object name")
    return f"{session_prefix(user_id, session_id)}{task_id}/{name}"


def tombstone_key(user_id: str, session_id: str) -> str:
    if not SESSION_ID_PATTERN.fullmatch(session_id):
        raise ValueError("invalid session ID")
    return f"{_user_prefix(user_id)}{TOMBSTONES}/{session_id}"


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

    def _keys(self, prefix: str) -> Iterator[str]:
        token = None
        while True:
            page = self._client.list_objects_v2(
                Bucket=self._bucket,
                Prefix=prefix,
                ExpectedBucketOwner=self._owner,
                **({"ContinuationToken": token} if token else {}),
            )
            for item in page.get("Contents", []):
                yield item["Key"]
            token = page.get("NextContinuationToken")
            if not page.get("IsTruncated") or not token:
                return

    def _put_json(self, key: str, value: dict) -> None:
        body = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self._put(key, body, "application/json")

    def _get_json(self, key: str) -> dict | None:
        body = self._get(key)
        return None if body is None else json.loads(body)

    def put_status(
        self, user_id: str, session_id: str, task_id: str, status: dict
    ) -> None:
        self._put_json(result_key(user_id, session_id, task_id, "status.json"), status)

    def get_status(self, user_id: str, session_id: str, task_id: str) -> dict | None:
        return self._get_json(result_key(user_id, session_id, task_id, "status.json"))

    def put_request(
        self, user_id: str, session_id: str, task_id: str, request: dict
    ) -> None:
        """The task's full input, so a failed task can be retried as it was."""

        self._put_json(
            result_key(user_id, session_id, task_id, "request.json"), request
        )

    def get_request(self, user_id: str, session_id: str, task_id: str) -> dict | None:
        return self._get_json(result_key(user_id, session_id, task_id, "request.json"))

    def put_prd(
        self, user_id: str, session_id: str, task_id: str, markdown: str
    ) -> None:
        key = result_key(user_id, session_id, task_id, "prd.md")
        self._put(key, markdown.encode("utf-8"), "text/markdown; charset=utf-8")

    def get_prd(self, user_id: str, session_id: str, task_id: str) -> str | None:
        body = self._get(result_key(user_id, session_id, task_id, "prd.md"))
        return None if body is None else body.decode("utf-8")

    def task_ids(self, user_id: str, session_id: str) -> list[str]:
        """Tasks in this session that have a status object."""

        prefix = session_prefix(user_id, session_id)
        tasks = []
        for key in self._keys(prefix):
            task_id, _, name = key[len(prefix) :].partition("/")
            if name == "status.json" and TASK_ID_PATTERN.fullmatch(task_id):
                tasks.append(task_id)
        return tasks

    def delete_session(self, user_id: str, session_id: str) -> int:
        """Delete every object under the session prefix; returns how many."""

        keys = list(self._keys(session_prefix(user_id, session_id)))
        for start in range(0, len(keys), DELETE_BATCH):
            batch = keys[start : start + DELETE_BATCH]
            response = self._client.delete_objects(
                Bucket=self._bucket,
                Delete={"Objects": [{"Key": key} for key in batch], "Quiet": True},
                ExpectedBucketOwner=self._owner,
            )
            errors = response.get("Errors") or []
            if errors:
                codes = sorted({error.get("Code", "?") for error in errors})
                raise RuntimeError(
                    f"{len(errors)} result object(s) were not deleted: {codes}"
                )
        return len(keys)

    def put_tombstone(self, user_id: str, session_id: str, deleted_at: str) -> None:
        self._put_json(tombstone_key(user_id, session_id), {"deleted_at": deleted_at})

    def deleted_sessions(self, user_id: str) -> set[str]:
        prefix = f"{_user_prefix(user_id)}{TOMBSTONES}/"
        return {
            key[len(prefix) :]
            for key in self._keys(prefix)
            if SESSION_ID_PATTERN.fullmatch(key[len(prefix) :])
        }
