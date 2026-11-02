#!/usr/bin/env python3
"""Invoke the deployed PRD Runtime once and save verifiable E2E evidence.

Builds the request from deploy/cloud-e2e-request.json, pins it to the source
snapshot recorded in the state file, attaches every Markdown file under
--docs-dir as this task's documents (the Runtime indexes them for this
invocation only and deletes them afterwards), streams the SSE response, and
writes response.sse, prd.md, validation-report.json and result-metadata.json to
a run directory.

Exit codes: 0 = completed and validation passed; 2 = needs clarification;
1 = any other outcome (error event, failed validation, missing result).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def parse_sse(lines: Iterable[bytes]) -> Iterator[dict[str, Any]]:
    """Yield JSON objects from `data:` lines; ignore keep-alives and comments."""

    for raw in lines:
        line = raw.decode("utf-8").strip()
        if not line.startswith("data:"):
            continue
        body = line[len("data:") :].strip()
        if body:
            yield json.loads(body)


def load_documents(docs_dir: Path) -> list[dict[str, str]]:
    """Read *.md files under docs_dir as {path, content}, paths relative to it."""

    if not docs_dir.is_dir():
        raise RuntimeError(f"--docs-dir is not a directory: {docs_dir}")
    files = sorted(
        path
        for path in docs_dir.rglob("*.md")
        if path.is_file()
        and not path.is_symlink()
        and not any(part.startswith(".") for part in path.relative_to(docs_dir).parts)
    )
    if not files:
        raise RuntimeError(f"no Markdown documents found under {docs_dir}")
    return [
        {
            "path": path.relative_to(docs_dir).as_posix(),
            "content": path.read_text(encoding="utf-8"),
        }
        for path in files
    ]


def build_request(
    template: dict[str, Any],
    state: dict[str, Any],
    run_id: str,
    documents: list[dict[str, str]],
) -> dict[str, Any]:
    request = dict(template)
    # The Runtime assigns the knowledge-base scope itself and rejects this field.
    request.pop("knowledge_base_snapshot", None)
    request.update(
        {
            "run_id": run_id,
            "project_id": state["project_id"],
            "repository": state["repository"],
            "repository_commit": state["source_snapshot"],
            "documents": documents,
        }
    )
    return request


def summarize(events: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for event in events:
        counts[event.get("event", "unknown")] = (
            counts.get(event.get("event", "unknown"), 0) + 1
        )
    final = events[-1] if events else {}
    result = final.get("result") or {}
    report = result.get("validation_report") or {}
    return {
        "event_count": len(events),
        "event_counts": counts,
        "final_event": final.get("event"),
        "status": result.get("status"),
        "versions": result.get("versions"),
        "validation_passed": report.get("passed"),
        "repair_attempts": report.get("repair_attempts"),
        "citation_count": report.get("citation_count"),
        "error": final.get("error"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="default")
    parser.add_argument("--region", required=True)
    parser.add_argument("--expected-account", required=True)
    parser.add_argument("--state-file", type=Path, required=True)
    parser.add_argument(
        "--request-template",
        type=Path,
        default=PROJECT_ROOT / "deploy" / "cloud-e2e-request.json",
    )
    parser.add_argument("--requirement-text", help="override the mock requirement")
    parser.add_argument(
        "--docs-dir",
        type=Path,
        default=PROJECT_ROOT / "examples",
        help="Markdown documents sent with this task (default: examples/)",
    )
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--read-timeout", type=int, default=900)
    args = parser.parse_args()

    import boto3
    from botocore.config import Config

    state = json.loads(args.state_file.read_text(encoding="utf-8"))
    if (state["account_id"], state["region"]) != (args.expected_account, args.region):
        raise RuntimeError("state file belongs to a different account or Region")
    if "runtime_arn" not in state:
        raise RuntimeError(
            "state file has no runtime_arn; run deploy_runtime.py --apply"
        )

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    if session.client("sts").get_caller_identity()["Account"] != args.expected_account:
        raise RuntimeError(f"AWS account mismatch: expected {args.expected_account}")

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"e2e-{stamp}"
    template = json.loads(args.request_template.read_text(encoding="utf-8"))
    request = build_request(template, state, run_id, load_documents(args.docs_dir))
    if args.requirement_text:
        request["requirement_text"] = args.requirement_text
    session_id = f"{state['project_id']}-{stamp}-{uuid4().hex}"  # >= 33 chars

    run_dir = args.out_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "request.json").write_text(
        json.dumps(request, indent=2) + "\n", encoding="utf-8"
    )

    client = session.client(
        "bedrock-agentcore",
        config=Config(read_timeout=args.read_timeout, retries={"max_attempts": 1}),
    )
    started = time.monotonic()
    response = client.invoke_agent_runtime(
        agentRuntimeArn=state["runtime_arn"],
        qualifier="DEFAULT",
        runtimeSessionId=session_id,
        contentType="application/json",
        accept="text/event-stream",
        payload=json.dumps(request).encode("utf-8"),
    )
    events: list[dict[str, Any]] = []
    with (run_dir / "response.sse").open("wb") as sse:
        for raw in response["response"].iter_lines():
            sse.write(raw + b"\n")
            for event in parse_sse([raw]):
                events.append(event)
                print(
                    f"EVENT {event.get('event')} t+{int(time.monotonic() - started)}s",
                    file=sys.stderr,
                )

    try:
        session.client("bedrock-agentcore").stop_runtime_session(
            agentRuntimeArn=state["runtime_arn"],
            runtimeSessionId=session_id,
            qualifier="DEFAULT",
        )
    except Exception as error:  # best effort: the idle timeout reclaims it anyway
        print(f"WARN stop_runtime_session: {type(error).__name__}", file=sys.stderr)

    summary = summarize(events)
    summary.update(
        {
            "run_id": run_id,
            "runtime_session_id": session_id,
            "http_status": response.get("statusCode"),
            "content_type": response.get("contentType"),
            "elapsed_seconds": int(time.monotonic() - started),
        }
    )
    result = (events[-1].get("result") if events else None) or {}
    if result.get("prd_markdown"):
        (run_dir / "prd.md").write_text(result["prd_markdown"], encoding="utf-8")
    if result.get("validation_report"):
        (run_dir / "validation-report.json").write_text(
            json.dumps(result["validation_report"], indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    (run_dir / "result-metadata.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"run_dir": str(run_dir), **summary}, indent=2, sort_keys=True))

    if summary["status"] == "completed" and summary["validation_passed"] is True:
        return 0
    if summary["status"] == "needs_clarification":
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
