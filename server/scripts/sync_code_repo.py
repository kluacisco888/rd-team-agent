#!/usr/bin/env python3
"""Sync one repository commit's code into the knowledge base. Dry-run is the default.

The code comes from the commit itself (``git archive``), so files that are not
committed, such as local edits, ``node_modules`` or ``.venv``, never reach the
knowledge base. Each chunk records the repository, the full commit SHA, the file
and its line range; the PRD tool retrieves code with the same commit SHA.

Dry-run (default) makes only read calls: it resolves the commit, builds the
chunks, checks the data source and lists the objects that already exist.

With --apply it then:
1. writes the commit's source manifest under --manifest-root;
2. uploads the new chunks to the S3 data source's bucket under content/code/,
   refusing to overwrite any object whose content differs;
3. starts one ingestion job on the data source and waits for it;
4. runs a read-only Retrieve with the PRD tool's filter and checks that every
   passage belongs to this commit.

Example:
  python scripts/sync_code_repo.py --repo ../sample-app --commit main \
    --project-id rd-team-agent-demo --repository-name rd-team-agent-sample-app \
    --bucket <bucket> --knowledge-base-id <kb-id> --data-source-id <s3-data-source-id>
"""

from __future__ import annotations

import argparse
import io
import json
import re
import subprocess
import sys
import tarfile
import tempfile
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import boto3
from botocore.exceptions import ClientError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from prd_agent.kb_bundle import BundleObject, build_code_bundle  # noqa: E402
from prd_agent.knowledge_base import task_scope_filter  # noqa: E402
from prd_agent.manifest import FileSourceManifestProvider  # noqa: E402

CODE_PREFIX = "content/code/"
DEFAULT_MANIFEST_ROOT = PROJECT_ROOT / "deploy" / "out"
DEFAULT_CHECK_QUERY = "application entry point, request handling and data validation"
_FULL_SHA = re.compile(r"[0-9a-f]{40}")


class SyncError(RuntimeError):
    """A check failed; the script stops before writing anything further."""


def resolve_commit(repo: Path, revision: str) -> str:
    """Return the full SHA of ``revision`` in ``repo``."""

    if not revision or revision.startswith("-"):
        raise SyncError("--commit must name a revision, not an option")
    result = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "rev-parse",
            "--verify",
            "--quiet",
            f"{revision}^{{commit}}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    sha = result.stdout.strip()
    if result.returncode != 0 or not _FULL_SHA.fullmatch(sha):
        raise SyncError(f"cannot resolve commit {revision!r} in {repo}")
    return sha


def export_commit(repo: Path, sha: str, destination: Path) -> None:
    """Extract exactly the files of commit ``sha`` into ``destination``."""

    result = subprocess.run(
        ["git", "-C", str(repo), "archive", "--format=tar", sha],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise SyncError(f"git archive failed: {message}")
    with tarfile.open(fileobj=io.BytesIO(result.stdout)) as archive:
        archive.extractall(destination, filter="data")


def plan_uploads(
    s3: Any,
    bucket: str,
    objects: Sequence[BundleObject],
) -> tuple[list[BundleObject], list[BundleObject]]:
    """Split objects into (to upload, already present with identical content).

    Raises SyncError when an object exists with different content, so a run never
    replaces chunks that another commit or project already indexed.
    """

    for item in objects:
        if not item.key.startswith(CODE_PREFIX):
            raise SyncError(f"object key outside {CODE_PREFIX}: {item.key}")
    existing: set[str] = set()
    for page in s3.get_paginator("list_objects_v2").paginate(
        Bucket=bucket, Prefix=CODE_PREFIX
    ):
        existing.update(entry["Key"] for entry in page.get("Contents", []))

    to_upload: list[BundleObject] = []
    identical: list[BundleObject] = []
    conflicts: list[str] = []
    for item in objects:
        if item.key not in existing:
            to_upload.append(item)
            continue
        body = s3.get_object(Bucket=bucket, Key=item.key)["Body"].read()
        if body == item.body:
            identical.append(item)
        else:
            conflicts.append(item.key)
    if conflicts:
        raise SyncError(
            f"{len(conflicts)} object(s) already exist with different content; "
            f"refusing to overwrite, first: {conflicts[0]}"
        )
    return to_upload, identical


def upload(
    s3: Any,
    bucket: str,
    objects: Iterable[BundleObject],
    *,
    tags: Mapping[str, str],
) -> int:
    """Create each object; S3 rejects the write if the key appeared meanwhile."""

    tagging = urlencode(dict(tags))
    count = 0
    for item in objects:
        try:
            s3.put_object(
                Bucket=bucket,
                Key=item.key,
                Body=item.body,
                ContentType=item.content_type,
                ServerSideEncryption="AES256",
                Tagging=tagging,
                IfNoneMatch="*",
            )
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") == "PreconditionFailed":
                raise SyncError(
                    f"{item.key} was created by someone else after the check; "
                    "not overwriting"
                ) from error
            raise
        count += 1
    return count


def run_ingestion(
    agent: Any,
    *,
    knowledge_base_id: str,
    data_source_id: str,
    description: str,
    timeout_seconds: int = 900,
    poll_seconds: int = 10,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Start one ingestion job and wait until it completes without failed documents."""

    job = agent.start_ingestion_job(
        knowledgeBaseId=knowledge_base_id,
        dataSourceId=data_source_id,
        description=description,
    )["ingestionJob"]
    job_id = job["ingestionJobId"]
    deadline = clock() + timeout_seconds
    while True:
        job = agent.get_ingestion_job(
            knowledgeBaseId=knowledge_base_id,
            dataSourceId=data_source_id,
            ingestionJobId=job_id,
        )["ingestionJob"]
        status = job["status"]
        print(f"WAIT ingestion {job_id}: {status}", file=sys.stderr)
        if status == "COMPLETE":
            failed = job.get("statistics", {}).get("numberOfDocumentsFailed", 0)
            if failed:
                raise SyncError(
                    f"ingestion {job_id} finished with {failed} failed document(s): "
                    f"{job.get('failureReasons')}"
                )
            return job
        if status in {"FAILED", "STOPPED"}:
            raise SyncError(
                f"ingestion {job_id} ended as {status}: {job.get('failureReasons')}"
            )
        if clock() >= deadline:
            raise SyncError(
                f"timed out waiting for ingestion {job_id}; last status {status}"
            )
        sleep(poll_seconds)


def check_retrieval(
    runtime: Any,
    *,
    knowledge_base_id: str,
    project_id: str,
    commit_sha: str,
    manifest_files: Iterable[str],
    query: str,
    attempts: int = 8,
    wait_seconds: int = 15,
    sleep: Callable[[float], None] = time.sleep,
) -> list[dict[str, Any]]:
    """Retrieve with the PRD tool's filter and require passages from this commit only.

    The task branch of the filter uses a task ID no document carries, so only the
    code branch (project + source_type=code + commit) can match.
    """

    files = set(manifest_files)
    metadata_filter = task_scope_filter(
        project_id=project_id,
        task_id=f"sync-check-{commit_sha[:12]}",
        repository_commit=commit_sha,
    )
    for attempt in range(1, attempts + 1):
        response = runtime.retrieve(
            knowledgeBaseId=knowledge_base_id,
            retrievalQuery={"text": query},
            retrievalConfiguration={
                "managedSearchConfiguration": {
                    "numberOfResults": 5,
                    "filter": metadata_filter,
                }
            },
        )
        results = response.get("retrievalResults", [])
        if results:
            hits = []
            for item in results:
                metadata = item.get("metadata", {})
                if (
                    metadata.get("project_id") != project_id
                    or metadata.get("source_type") != "code"
                    or metadata.get("commit_sha") != commit_sha
                    or metadata.get("file_path") not in files
                ):
                    raise SyncError(
                        f"Retrieve returned a passage outside this commit: {metadata}"
                    )
                hits.append(
                    {
                        "file_path": metadata["file_path"],
                        "locator": metadata.get("locator"),
                        "score": item.get("score"),
                    }
                )
            return hits
        if attempt < attempts:
            print(
                f"WAIT retrieve: no passages yet (attempt {attempt}/{attempts})",
                file=sys.stderr,
            )
            sleep(wait_seconds)
    raise SyncError("Retrieve returned no code passages for this commit")


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sync one repository commit's source code into the knowledge base "
        "(dry-run unless --apply)."
    )
    parser.add_argument(
        "--repo", required=True, type=Path, help="local clone of the code repository"
    )
    parser.add_argument(
        "--commit", required=True, help="commit to sync: SHA, branch or tag"
    )
    parser.add_argument(
        "--project-id", required=True, help="project_id stored on every chunk"
    )
    parser.add_argument(
        "--repository-name",
        required=True,
        help="repository name stored on every chunk, e.g. rd-team-agent-sample-app",
    )
    parser.add_argument(
        "--bucket", required=True, help="bucket read by the S3 data source"
    )
    parser.add_argument("--knowledge-base-id", required=True)
    parser.add_argument(
        "--data-source-id",
        required=True,
        help="the S3 data source that reads the bucket's content/ prefix",
    )
    parser.add_argument(
        "--manifest-root",
        type=Path,
        default=DEFAULT_MANIFEST_ROOT,
        help="where --apply writes source-manifests/<hash>.json (default: deploy/out)",
    )
    parser.add_argument("--check-query", default=DEFAULT_CHECK_QUERY)
    parser.add_argument("--profile")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument(
        "--apply", action="store_true", help="write to S3 and start ingestion"
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    repo = args.repo.resolve()
    sha = resolve_commit(repo, args.commit)
    with tempfile.TemporaryDirectory(prefix="sync-code-") as directory:
        tree = Path(directory) / "tree"
        tree.mkdir()
        export_commit(repo, sha, tree)
        bundle = build_code_bundle(
            tree,
            project_id=args.project_id,
            repository=args.repository_name,
            commit_sha=sha,
        )

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    s3 = session.client("s3")
    agent = session.client("bedrock-agent")
    data_source = agent.get_data_source(
        knowledgeBaseId=args.knowledge_base_id,
        dataSourceId=args.data_source_id,
    )["dataSource"]
    if data_source.get("status") != "AVAILABLE":
        raise SyncError(
            f"data source {args.data_source_id} is {data_source.get('status')}"
        )
    to_upload, identical = plan_uploads(s3, args.bucket, bundle.objects)
    manifests = FileSourceManifestProvider(args.manifest_root)
    manifest_path = manifests.path_for(
        project_id=bundle.project_id,
        repository=bundle.repository,
        commit_sha=bundle.commit_sha,
    )

    plan = {
        "repository": bundle.repository,
        "commit_sha": bundle.commit_sha,
        "project_id": bundle.project_id,
        "manifest_files": len(bundle.manifest.files),
        "chunks": len(bundle.objects) // 2,
        "objects": len(bundle.objects),
        "objects_to_upload": len(to_upload),
        "objects_already_identical": len(identical),
        "upload_bytes": sum(len(item.body) for item in to_upload),
        "bucket": args.bucket,
        "prefix": CODE_PREFIX,
        "knowledge_base_id": args.knowledge_base_id,
        "data_source": f"{data_source.get('name')} ({args.data_source_id})",
        "manifest_path": str(manifest_path),
    }
    mode = "apply" if args.apply else "dry-run"
    print(json.dumps({"mode": mode, "plan": plan}, ensure_ascii=False, indent=2))
    if not args.apply:
        print(
            "dry-run: nothing written; rerun with --apply to upload and ingest",
            file=sys.stderr,
        )
        return 0

    manifests.write(bundle.manifest)
    uploaded = upload(
        s3,
        args.bucket,
        to_upload,
        tags={"Project": bundle.project_id, "Repository": bundle.repository},
    )
    job = run_ingestion(
        agent,
        knowledge_base_id=args.knowledge_base_id,
        data_source_id=args.data_source_id,
        description=f"Sync {bundle.repository}@{bundle.commit_sha[:12]}",
    )
    hits = check_retrieval(
        session.client("bedrock-agent-runtime"),
        knowledge_base_id=args.knowledge_base_id,
        project_id=bundle.project_id,
        commit_sha=bundle.commit_sha,
        manifest_files=bundle.manifest.files,
        query=args.check_query,
    )
    result = {
        "uploaded": uploaded,
        "manifest_path": str(manifest_path),
        "ingestion_job_id": job["ingestionJobId"],
        "ingestion_statistics": job.get("statistics", {}),
        "retrieve_check": hits,
    }
    print(json.dumps({"result": result}, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SyncError as error:
        print(f"ERROR {error}", file=sys.stderr)
        sys.exit(1)
