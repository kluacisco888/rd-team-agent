#!/usr/bin/env python3
"""End-to-end smoke test of the deployed orchestrator, run by a person.

It signs in a Cognito test user through the CLI test client (the password is
typed at the prompt and never stored), then in one new session:
  1. chats with an attachment until a PRD task starts (answer the agent's
     questions at the prompt if it asks any);
  2. polls the task until it finishes and saves the PRD under deploy/out/smoke/;
  3. lists sessions, history and long-term memories;
  4. with --other-user, signs in a second user and checks that user cannot see
     the first user's session, history or task.

Usage:
  python scripts/smoke_orchestrator.py --user tester1 --other-user tester2
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote

import boto3
import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATE = PROJECT_ROOT / "deploy" / "out" / "rd-team-agent.json"
DEFAULT_ATTACHMENT = PROJECT_ROOT / "examples" / "mock-csv-batch-user-import-prd.md"
DEFAULT_MESSAGE = (
    "请根据附件，为管理后台写一份「CSV 批量导入用户」的 PRD。"  # noqa: RUF001
    "附件里的需求已经比较完整，如果没有阻塞性的问题，请直接开始生成。"  # noqa: RUF001
)


class SmokeFailure(RuntimeError):
    pass


def sign_in(state: dict, username: str) -> str:
    password = getpass.getpass(f"Password for {username}: ")
    cognito = boto3.client("cognito-idp", region_name=state["region"])
    try:
        response = cognito.initiate_auth(
            ClientId=state["cli_client_id"],
            AuthFlow="USER_PASSWORD_AUTH",
            AuthParameters={"USERNAME": username, "PASSWORD": password},
        )
    except cognito.exceptions.NotAuthorizedException as error:
        raise SmokeFailure(f"sign-in failed for {username}: wrong password?") from error
    if "AuthenticationResult" not in response:
        raise SmokeFailure(
            f"{username} must finish sign-up ({response.get('ChallengeName')}); "
            "set a permanent password with "
            "aws cognito-idp admin-set-user-password --permanent"
        )
    return response["AuthenticationResult"]["AccessToken"]


class RuntimeClient:
    def __init__(self, state: dict, access_token: str, session_id: str) -> None:
        arn = quote(state["runtime_arn"], safe="")
        self.url = (
            f"https://bedrock-agentcore.{state['region']}.amazonaws.com/runtimes/"
            f"{arn}/invocations?qualifier=DEFAULT"
        )
        self.headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id,
        }

    def call(self, payload: dict, *, echo: bool = False) -> list[dict[str, Any]]:
        events = []
        timeout = httpx.Timeout(connect=15, read=900, write=60, pool=15)
        with httpx.stream(
            "POST", self.url, headers=self.headers, json=payload, timeout=timeout
        ) as response:
            if response.status_code != 200:
                body = response.read().decode("utf-8", errors="replace")[:500]
                raise SmokeFailure(f"HTTP {response.status_code}: {body}")
            for line in response.iter_lines():
                if not line.startswith("data:"):
                    continue
                event = json.loads(line[len("data:") :].strip())
                if isinstance(event, str):  # some SDK paths double-encode
                    event = json.loads(event)
                events.append(event)
                if echo and event.get("event") == "text":
                    print(event["text"], end="", flush=True)
        if echo:
            print()
        return events

    def one(self, payload: dict, *, attempts: int = 5) -> dict[str, Any]:
        """A read-only request, retried when the connection drops (e.g. a proxy)."""

        for attempt in range(1, attempts + 1):
            try:
                events = self.call(payload)
                break
            except httpx.TransportError as error:
                if attempt == attempts:
                    raise SmokeFailure(
                        f"{payload.get('action')} failed: {error}"
                    ) from error
                print(f"connection error ({error}); retry {attempt}/{attempts - 1}")
                time.sleep(5 * attempt)
        if not events:
            raise SmokeFailure(f"no events for {payload.get('action')}")
        return events[0]


def run(args: argparse.Namespace) -> list[tuple[str, bool, str]]:
    state = json.loads(args.state_file.read_text(encoding="utf-8"))
    checks: list[tuple[str, bool, str]] = []
    session_id = args.session or f"smoke-{uuid.uuid4().hex}"
    print(f"session {session_id}")
    client = RuntimeClient(state, sign_in(state, args.user), session_id)

    documents = [
        {"path": path.name, "content": path.read_text(encoding="utf-8")}
        for path in args.attachment
    ]
    message = args.message
    task = {"task_id": args.task_id} if args.task_id else None
    for _ in range(0 if task else 4):
        print(f"\n> {message}\n")
        events = client.call(
            {"action": "chat", "message": message, "documents": documents}, echo=True
        )
        errors = [e for e in events if e.get("event") == "error"]
        if errors:
            raise SmokeFailure(f"chat failed: {errors[0]['error']}")
        started = [e["task"] for e in events if e.get("event") == "task_started"]
        if started:
            task = started[0]
            break
        if not sys.stdin.isatty():
            break
        message = input("\nYour reply (empty to stop): ").strip()
        if not message:
            break
    checks.append(
        ("chat starts a PRD task", task is not None, task["task_id"] if task else "")
    )
    if task is None:
        return checks

    task_id = task["task_id"]
    deadline = time.monotonic() + args.timeout_minutes * 60
    status: dict[str, Any] = {}
    while time.monotonic() < deadline:
        reply = client.one({"action": "task_status", "task_id": task_id})
        status = reply.get("task", {})
        print(f"task {task_id}: {status.get('state')}")
        if status.get("state") in {"succeeded", "failed"}:
            break
        time.sleep(20)
    checks.append(
        ("task succeeds", status.get("state") == "succeeded", json.dumps(status)[:300])
    )
    if status.get("state") == "succeeded":
        result = client.one({"action": "task_result", "task_id": task_id})
        markdown = result.get("prd_markdown") or ""
        out = PROJECT_ROOT / "deploy" / "out" / "smoke" / f"{task_id}.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(markdown, encoding="utf-8")
        checks.append(
            ("PRD fetched", bool(markdown), f"{len(markdown)} chars -> {out}")
        )

    sessions = client.one({"action": "list_sessions"}).get("sessions", [])
    ids = [s["session_id"] for s in sessions]
    checks.append(("session listed", session_id in ids, f"{len(ids)} session(s)"))
    history = client.one({"action": "history"}).get("messages", [])
    checks.append(("history recorded", len(history) >= 2, f"{len(history)} message(s)"))
    memories = client.one({"action": "list_memories"}).get("records", [])
    print(
        f"long-term memory records: {len(memories)} (extraction runs in the background)"
    )

    if args.other_user:
        other = RuntimeClient(state, sign_in(state, args.other_user), session_id)
        other_sessions = other.one({"action": "list_sessions"}).get("sessions", [])
        checks.append(
            (
                f"{args.other_user} cannot list the session",
                session_id not in [s["session_id"] for s in other_sessions],
                "",
            )
        )
        other_history = other.one({"action": "history"}).get("messages", [])
        checks.append(
            (
                f"{args.other_user} sees no history",
                other_history == [],
                f"{len(other_history)}",
            )
        )
        other_task = other.one({"action": "task_status", "task_id": task_id})
        code = other_task.get("error", {}).get("code")
        checks.append(
            (f"{args.other_user} cannot read the task", code == "NOT_FOUND", str(code))
        )
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--state-file", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--user", default="tester1")
    parser.add_argument(
        "--other-user", help="second test user for the isolation checks"
    )
    parser.add_argument("--message", default=DEFAULT_MESSAGE)
    parser.add_argument("--attachment", type=Path, action="append")
    parser.add_argument("--timeout-minutes", type=int, default=20)
    parser.add_argument("--session", help="reuse this Runtime session ID")
    parser.add_argument(
        "--task-id",
        help="skip the chat and resume checks on this task (needs --session)",
    )
    args = parser.parse_args()
    if args.task_id and not args.session:
        parser.error("--task-id needs the --session it was started in")
    args.attachment = args.attachment or [DEFAULT_ATTACHMENT]
    try:
        checks = run(args)
    except SmokeFailure as error:
        print(f"\nFAIL {error}")
        return 1
    print()
    for name, passed, detail in checks:
        print(f"{'PASS' if passed else 'FAIL'}  {name}  {detail}")
    return 0 if checks and all(passed for _, passed, _ in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
