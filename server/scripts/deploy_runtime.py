#!/usr/bin/env python3
"""Package the PRD agent and create or update its AgentCore Runtime.

Dry-run is the default: it builds and validates the linux/arm64 direct-code
package and prints the deployment plan without calling any AWS write API.
With --apply it uploads the package to the stack bucket, creates or updates
the Runtime, waits for READY, and merges the Runtime identifiers into the
state file written by provision_test_stack.py.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import time
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from prd_agent.deployment import DeploymentPlan, runtime_environment  # noqa: E402
from prd_agent.kb_bundle import compute_source_snapshot_id  # noqa: E402
from prd_agent.settings import load_settings  # noqa: E402

PYTHON_VERSION = "3.12"
RUNTIME_LANGUAGE = "PYTHON_3_12"
ENTRY_POINT = ["app.py"]
CONTRACT_FILE = "mock-csv-batch-user-import.contract.json"
MAX_ZIPPED_BYTES = 250 * 1024 * 1024
MAX_UNZIPPED_BYTES = 750 * 1024 * 1024
# Fixed timestamp keeps identical inputs producing a byte-identical archive.
_ZIP_EPOCH = (2026, 1, 1, 0, 0, 0)


def _skip(path: Path) -> bool:
    return "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}


def stage_application(project_root: Path, package_dir: Path) -> None:
    """Copy exactly the files the Runtime needs into the package root."""

    shutil.copy2(project_root / "app.py", package_dir / "app.py")
    for source in sorted((project_root / "src").rglob("*.py")):
        if _skip(source) or source.is_symlink():
            continue
        target = package_dir / "src" / source.relative_to(project_root / "src")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    contracts = package_dir / "contracts"
    contracts.mkdir(parents=True, exist_ok=True)
    shutil.copy2(project_root / "contracts" / CONTRACT_FILE, contracts / CONTRACT_FILE)


def install_dependencies(project_root: Path, package_dir: Path) -> None:
    """Install pinned linux/arm64 wheels; AgentCore Runtime is arm64-only."""

    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("uv is required: https://docs.astral.sh/uv/")
    subprocess.run(
        [
            uv,
            "pip",
            "install",
            "--quiet",
            "--python-platform",
            "aarch64-manylinux2014",
            "--python-version",
            PYTHON_VERSION,
            "--only-binary=:all:",
            "--target",
            str(package_dir),
            "-r",
            str(project_root / "requirements.txt"),
        ],
        check=True,
    )


def _package_files(package_dir: Path) -> Iterator[Path]:
    for path in sorted(package_dir.rglob("*")):
        if path.is_file() and not path.is_symlink() and not _skip(path):
            yield path


def write_zip(package_dir: Path, zip_path: Path) -> dict[str, Any]:
    """Write a deterministic archive with Runtime-readable POSIX permissions."""

    unzipped = 0
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in _package_files(package_dir):
            relative = path.relative_to(package_dir).as_posix()
            executable = bool(path.stat().st_mode & stat.S_IXUSR)
            mode = 0o755 if executable else 0o644
            info = zipfile.ZipInfo(relative, date_time=_ZIP_EPOCH)
            info.external_attr = (stat.S_IFREG | mode) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            data = path.read_bytes()
            unzipped += len(data)
            archive.writestr(info, data)
    zipped = zip_path.stat().st_size
    if zipped > MAX_ZIPPED_BYTES or unzipped > MAX_UNZIPPED_BYTES:
        raise RuntimeError(
            f"package too large: {zipped} B zipped / {unzipped} B unzipped "
            "(limits 250 MB / 750 MB)"
        )
    return {
        "zip_path": str(zip_path),
        "zipped_bytes": zipped,
        "unzipped_bytes": unzipped,
        "sha256": hashlib.sha256(zip_path.read_bytes()).hexdigest(),
    }


def build_package(project_root: Path, build_dir: Path) -> dict[str, Any]:
    package_dir = build_dir / "package"
    if build_dir.exists():
        shutil.rmtree(build_dir)
    package_dir.mkdir(parents=True)
    install_dependencies(project_root, package_dir)
    stage_application(project_root, package_dir)
    packaged_snapshot = compute_source_snapshot_id(package_dir / "src")
    local_snapshot = compute_source_snapshot_id(project_root / "src")
    if packaged_snapshot != local_snapshot:
        raise RuntimeError("packaged src/ differs from the local source snapshot")
    result = write_zip(package_dir, build_dir / "deployment_package.zip")
    result["source_snapshot"] = packaged_snapshot
    return result


def _find_runtime(client, name: str) -> dict[str, Any] | None:
    token = None
    while True:
        kwargs: dict[str, Any] = {"maxResults": 100}
        if token:
            kwargs["nextToken"] = token
        page = client.list_agent_runtimes(**kwargs)
        for item in page.get("agentRuntimes", []):
            if item.get("agentRuntimeName") == name:
                return item
        token = page.get("nextToken")
        if not token:
            return None


def _retry_iam_propagation(call):
    from botocore.exceptions import ClientError

    for attempt in range(8):
        try:
            return call()
        except ClientError as error:
            code = error.response.get("Error", {}).get("Code")
            message = error.response.get("Error", {}).get("Message", "").lower()
            if (
                code in {"ValidationException", "AccessDeniedException"}
                and ("role" in message or "assume" in message)
                and attempt < 7
            ):
                print("WAIT Runtime role propagation", file=sys.stderr)
                time.sleep(10)
                continue
            raise
    raise AssertionError("unreachable")


def _wait_ready(client, runtime_id: str, timeout_seconds: int = 900) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while True:
        runtime = client.get_agent_runtime(agentRuntimeId=runtime_id)
        status = runtime["status"]
        print(f"WAIT AgentCore Runtime: {status}", file=sys.stderr)
        if status == "READY":
            return runtime
        if status in {"CREATE_FAILED", "UPDATE_FAILED"}:
            raise RuntimeError(
                f"Runtime entered {status}: {runtime.get('failureReason', 'no reason')}"
            )
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Runtime not READY after {timeout_seconds}s ({status})")
        time.sleep(10)


def deploy(
    *,
    session,
    plan: DeploymentPlan,
    state: dict[str, Any],
    package: dict[str, Any],
    environment: dict[str, str],
) -> dict[str, Any]:
    s3 = session.client("s3")
    control = session.client("bedrock-agentcore-control")
    key = f"{plan.runtime_artifact_prefix}{package['sha256'][:16]}.zip"
    s3.upload_file(
        package["zip_path"],
        plan.bucket_name,
        key,
        ExtraArgs={
            "ExpectedBucketOwner": plan.account_id,
            "ServerSideEncryption": "AES256",
        },
    )
    artifact = {
        "codeConfiguration": {
            "code": {"s3": {"bucket": plan.bucket_name, "prefix": key}},
            "runtime": RUNTIME_LANGUAGE,
            "entryPoint": ENTRY_POINT,
        }
    }
    common = {
        "agentRuntimeArtifact": artifact,
        "roleArn": state["runtime_role_arn"],
        "networkConfiguration": {"networkMode": "PUBLIC"},
        "protocolConfiguration": {"serverProtocol": "HTTP"},
        "environmentVariables": environment,
        "description": f"English PRD agent (Strands + {plan.model_id}), mock E2E",
    }
    existing = _find_runtime(control, plan.runtime_name)
    if existing:
        runtime_id = existing["agentRuntimeId"]
        _retry_iam_propagation(
            lambda: control.update_agent_runtime(agentRuntimeId=runtime_id, **common)
        )
    else:
        created = _retry_iam_propagation(
            lambda: control.create_agent_runtime(
                agentRuntimeName=plan.runtime_name,
                tags=plan.tags,
                **common,
            )
        )
        runtime_id = created["agentRuntimeId"]
    runtime = _wait_ready(control, runtime_id)
    return {
        "runtime_id": runtime_id,
        "runtime_arn": runtime["agentRuntimeArn"],
        "runtime_version": runtime["agentRuntimeVersion"],
        "runtime_status": runtime["status"],
        "runtime_artifact_key": key,
        "runtime_artifact_sha256": package["sha256"],
        "runtime_environment": environment,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="default")
    parser.add_argument("--region", required=True)
    parser.add_argument("--expected-account", required=True)
    parser.add_argument("--state-file", type=Path, help="state from the provisioner")
    parser.add_argument(
        "--environment",
        default="development",
        choices=["development", "test", "production"],
    )
    parser.add_argument("--prompt-version", default="mock-v5")
    parser.add_argument("--runtime-version", help="defaults to the package hash")
    parser.add_argument(
        "--build-dir", type=Path, default=PROJECT_ROOT / "build" / "runtime"
    )
    parser.add_argument("--build-only", action="store_true", help="package, no AWS")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    package = build_package(PROJECT_ROOT, args.build_dir)
    print(json.dumps({"package": package}, indent=2, sort_keys=True))
    if args.build_only:
        return 0
    if args.state_file is None or not args.state_file.exists():
        parser.error("--state-file from a successful provisioner --apply is required")

    state = json.loads(args.state_file.read_text(encoding="utf-8"))
    if (state["account_id"], state["region"]) != (args.expected_account, args.region):
        raise RuntimeError("state file belongs to a different account or Region")
    if package["source_snapshot"] != state["source_snapshot"]:
        raise RuntimeError(
            "src/ changed after the knowledge base was ingested "
            f"({state['source_snapshot']} -> {package['source_snapshot']}); "
            "re-run provision_test_stack.py --apply first"
        )
    if "task_data_source_id" not in state:
        raise RuntimeError(
            "state file has no task_data_source_id; "
            "re-run provision_test_stack.py --apply first"
        )

    plan = DeploymentPlan(
        account_id=state["account_id"],
        region=state["region"],
        prefix=state["prefix"],
        project_id=state["project_id"],
        repository=state["repository"],
        model_id=state["model_id"],
        allow_global_inference=state.get("allow_global_inference", False),
    )
    environment = runtime_environment(
        plan,
        environment=args.environment,
        knowledge_base_id=state["knowledge_base_id"],
        task_data_source_id=state["task_data_source_id"],
        prompt_version=args.prompt_version,
        runtime_version=args.runtime_version or f"mock-{package['sha256'][:12]}",
    )
    # Fail closed locally on any configuration the Runtime would reject at start.
    load_settings(
        {
            **environment,
            "PRD_CONTRACT_PATH": str(PROJECT_ROOT / "contracts" / CONTRACT_FILE),
        }
    )
    summary = {
        "runtime_name": plan.runtime_name,
        "runtime_role_arn": state["runtime_role_arn"],
        "artifact_bucket": plan.bucket_name,
        "artifact_prefix": plan.runtime_artifact_prefix,
        "environment_variables": environment,
    }
    if not args.apply:
        print(json.dumps({"mode": "dry-run", **summary}, indent=2, sort_keys=True))
        return 0

    import boto3

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    account = session.client("sts").get_caller_identity()["Account"]
    if account != args.expected_account:
        raise RuntimeError(f"AWS account mismatch: expected {args.expected_account}")
    result = deploy(
        session=session,
        plan=plan,
        state=state,
        package=package,
        environment=environment,
    )
    state.update(result)
    temporary = args.state_file.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temporary, args.state_file)
    print("===PRD_AGENT_RUNTIME_STATE===")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
