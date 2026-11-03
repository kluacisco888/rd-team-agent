"""A caller's sessions across Memory and the results bucket: list and delete."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from orchestrator.identity import Caller
from orchestrator.jobs import PrdJobRunner
from orchestrator.memory import ConversationMemory
from orchestrator.results import ResultStore


class SessionBusy(Exception):
    """A PRD task in the session is still running, so it cannot be deleted."""

    def __init__(self, task_ids: list[str]) -> None:
        super().__init__(", ".join(task_ids))
        self.task_ids = task_ids


class SessionManager:
    def __init__(
        self,
        *,
        memory: ConversationMemory,
        jobs: PrdJobRunner,
        store: ResultStore,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._memory = memory
        self._jobs = jobs
        self._store = store
        self._clock = clock

    def list(self, *, caller: Caller) -> tuple[list[dict[str, Any]], list[str]]:
        """The caller's sessions, and the IDs of the ones they deleted.

        The deleted IDs let an app drop its own record of a session that was
        deleted on another device.
        """

        deleted = self._store.deleted_sessions(caller.user_id)
        sessions = [
            session
            for session in self._memory.sessions(user_id=caller.user_id)
            if session["session_id"] not in deleted
        ]
        return sessions, sorted(deleted)

    def delete(self, *, caller: Caller, session_id: str) -> dict[str, int]:
        """Delete the session's conversation, summaries and PRD results.

        The tombstone goes first, so the session is hidden even if a later step
        fails; deleting again finishes the job. Preferences and facts are the
        user's, not the session's, and are kept.
        """

        running = self._jobs.running_tasks(caller=caller, session_id=session_id)
        if running:
            raise SessionBusy(running)
        user_id = caller.user_id
        self._store.put_tombstone(user_id, session_id, self._clock().isoformat())
        return {
            "events": self._memory.delete_events(
                user_id=user_id, session_id=session_id
            ),
            "summaries": self._memory.delete_session_summaries(
                user_id=user_id, session_id=session_id
            ),
            "result_objects": self._store.delete_session(user_id, session_id),
        }
