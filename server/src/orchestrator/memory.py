"""AgentCore Memory access, always scoped to one caller (actorId = JWT sub)."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any

from botocore.exceptions import ClientError

MAX_EVENT_TEXT = 90_000  # one event payload must stay under the 100 KB message limit


def _clip(text: str) -> str:
    return (
        text if len(text) <= MAX_EVENT_TEXT else text[:MAX_EVENT_TEXT] + "\n[truncated]"
    )


def _iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def _pages(
    call: Callable[..., dict[str, Any]], key: str, **kwargs: Any
) -> Iterator[dict]:
    token = None
    while True:
        page = call(**kwargs, **({"nextToken": token} if token else {}))
        yield from page.get(key, [])
        token = page.get("nextToken")
        if not token:
            return


class ConversationMemory:
    def __init__(
        self,
        *,
        client: Any,
        memory_id: str,
        preference_strategy_id: str,
        semantic_strategy_id: str,
        summary_strategy_id: str,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._client = client
        self._memory_id = memory_id
        self._strategies = {
            "preference": preference_strategy_id,
            "fact": semantic_strategy_id,
            "summary": summary_strategy_id,
        }
        self._clock = clock

    @staticmethod
    def namespace(strategy_id: str, user_id: str) -> str:
        """Matches the namespaces /strategy/{memoryStrategyId}/actor/{actorId}/."""

        return f"/strategy/{strategy_id}/actor/{user_id}/"

    def record(
        self, *, user_id: str, session_id: str, turns: list[tuple[str, str]]
    ) -> None:
        """Append (role, text) turns; role is USER or ASSISTANT."""

        payload = [
            {"conversational": {"content": {"text": _clip(text)}, "role": role}}
            for role, text in turns
            if text.strip()
        ]
        if not payload:
            return
        self._client.create_event(
            memoryId=self._memory_id,
            actorId=user_id,
            sessionId=session_id,
            eventTimestamp=self._clock(),
            payload=payload,
        )

    def messages(self, *, user_id: str, session_id: str) -> list[dict[str, str]]:
        """This caller's messages in this session, oldest first."""

        events = list(
            _pages(
                self._client.list_events,
                "events",
                memoryId=self._memory_id,
                actorId=user_id,
                sessionId=session_id,
                includePayloads=True,
                maxResults=100,
            )
        )
        events.sort(key=lambda event: event.get("eventTimestamp") or datetime.min)
        result = []
        for event in events:
            for item in event.get("payload", []):
                turn = item.get("conversational")
                if not turn:
                    continue
                text = (turn.get("content") or {}).get("text") or ""
                if text:
                    result.append(
                        {
                            "role": turn.get("role", "").lower(),
                            "text": text,
                            "at": _iso(event.get("eventTimestamp")),
                        }
                    )
        return result

    def history(self, *, user_id: str, session_id: str, max_turns: int) -> list[dict]:
        """Strands/Bedrock messages: start with user, roles alternate, last N turns."""

        merged: list[dict[str, Any]] = []
        for message in self.messages(user_id=user_id, session_id=session_id):
            role = "assistant" if message["role"] == "assistant" else "user"
            if merged and merged[-1]["role"] == role:
                merged[-1]["content"][0]["text"] += "\n\n" + message["text"]
            else:
                merged.append({"role": role, "content": [{"text": message["text"]}]})
        merged = merged[-2 * max_turns :]
        while merged and merged[0]["role"] != "user":
            merged.pop(0)
        # The agent appends the new user turn, so history must end with assistant.
        if merged and merged[-1]["role"] == "user":
            merged.pop()
        return merged

    def sessions(self, *, user_id: str) -> list[dict[str, str | None]]:
        summaries = _pages(
            self._client.list_sessions,
            "sessionSummaries",
            memoryId=self._memory_id,
            actorId=user_id,
            maxResults=100,
        )
        try:
            items = [
                {
                    "session_id": item["sessionId"],
                    "created_at": _iso(item.get("createdAt")),
                }
                for item in summaries
                if item.get("actorId", user_id) == user_id
            ]
        except ClientError as error:
            # ListSessions answers ResourceNotFoundException for an actor with no
            # events yet, i.e. a user who has never chatted: no sessions.
            if error.response.get("Error", {}).get("Code") != (
                "ResourceNotFoundException"
            ):
                raise
            return []
        return sorted(items, key=lambda item: item["created_at"] or "", reverse=True)

    def long_term(
        self,
        *,
        user_id: str,
        query: str | None = None,
        kinds: tuple[str, ...] = ("preference", "fact", "summary"),
        limit: int = 20,
        session_id: str | None = None,
    ) -> list[dict[str, str | None]]:
        """Records under this caller's namespaces only; query=None lists them.

        With session_id, summaries come only from that session's namespace
        (/strategy/{id}/actor/{actorId}/session/{sessionId}/); preferences and
        facts belong to the user and are listed whatever the session.
        """

        records = []
        for kind in kinds:
            namespace = self.namespace(self._strategies[kind], user_id)
            if kind == "summary" and session_id is not None:
                namespace += f"session/{session_id}/"
            if query:
                page = self._client.retrieve_memory_records(
                    memoryId=self._memory_id,
                    namespace=namespace,
                    searchCriteria={"searchQuery": query[:1000], "topK": 5},
                    maxResults=5,
                )
            else:
                page = self._client.list_memory_records(
                    memoryId=self._memory_id, namespace=namespace, maxResults=limit
                )
            for item in page.get("memoryRecordSummaries", []):
                if not all(
                    ns.startswith(namespace)
                    for ns in item.get("namespaces", [namespace])
                ):
                    continue  # never surface a record from another caller's namespace
                text = (item.get("content") or {}).get("text")
                if text:
                    records.append(
                        {
                            "kind": kind,
                            "text": text,
                            "created_at": _iso(item.get("createdAt")),
                        }
                    )
        return records
