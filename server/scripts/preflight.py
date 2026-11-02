#!/usr/bin/env python3
"""Read-only deployment preflight. Creates no AWS resources.

Checks, in order, and stops at the first hard failure:
1. Local toolchain (uv on PATH, Python 3.12 interpreter).
2. AWS identity matches --expected-account.
3. The Region can host a managed knowledge base (the binding constraint).
4. The selected Claude inference profile is ACTIVE in the Region.
5. Bedrock Agent and AgentCore control-plane APIs answer for this caller.
6. Optional (--probe-model): one tiny Converse call proves model access.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from prd_agent.deployment import (  # noqa: E402
    DEFAULT_MODEL_FAMILY,
    MODEL_FAMILIES,
    RegionSupportError,
    assert_region_supported,
    default_model_id,
    foundation_model_id,
)


def _error_code(error: Exception) -> str:
    if isinstance(error, ClientError):
        return error.response.get("Error", {}).get("Code", "ClientError")
    return type(error).__name__


def run(args: argparse.Namespace) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    report: dict[str, Any] = {"region": args.region, "checks": checks}

    def record(name: str, ok: bool, detail: str) -> bool:
        checks.append({"check": name, "ok": ok, "detail": detail})
        return ok

    record(
        "toolchain.uv",
        shutil.which("uv") is not None,
        "uv is required to build the linux/arm64 Runtime package",
    )
    record(
        "toolchain.python",
        sys.version_info[:2] == (3, 12),
        f"running Python {sys.version.split()[0]}; the project requires 3.12",
    )

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    try:
        account = session.client("sts").get_caller_identity()["Account"]
    except (ClientError, BotoCoreError) as error:
        record("aws.identity", False, f"STS failed: {_error_code(error)}")
        return report
    if not record(
        "aws.identity",
        account == args.expected_account,
        f"caller account {account}, expected {args.expected_account}",
    ):
        return report

    try:
        if not args.allow_unlisted_region:
            assert_region_supported(args.region)
        record("region.managed_knowledge_base", True, "managed KB available")
        model_id = args.model_id or default_model_id(
            args.region,
            allow_global_inference=args.allow_global_inference,
            family=args.model_family,
        )
    except RegionSupportError as error:
        record("region.support", False, str(error))
        return report
    report["model_id"] = model_id
    report["allow_global_inference"] = args.allow_global_inference

    if model_id != foundation_model_id(model_id):
        try:
            profile = session.client("bedrock").get_inference_profile(
                inferenceProfileIdentifier=model_id
            )
            record(
                "model.inference_profile",
                profile.get("status") == "ACTIVE",
                f"{model_id} status={profile.get('status')}",
            )
        except (ClientError, BotoCoreError) as error:
            record(
                "model.inference_profile",
                False,
                f"{model_id} unavailable: {_error_code(error)}",
            )

    for name, call in (
        (
            "api.bedrock_agent",
            lambda: session.client("bedrock-agent").list_knowledge_bases(maxResults=1),
        ),
        (
            "api.agentcore_gateway",
            lambda: session.client("bedrock-agentcore-control").list_gateways(
                maxResults=1
            ),
        ),
        (
            "api.agentcore_runtime",
            lambda: session.client("bedrock-agentcore-control").list_agent_runtimes(
                maxResults=1
            ),
        ),
    ):
        try:
            call()
            record(name, True, "reachable")
        except (ClientError, BotoCoreError) as error:
            record(name, False, _error_code(error))

    if args.probe_model:
        try:
            session.client("bedrock-runtime").converse(
                modelId=model_id,
                messages=[{"role": "user", "content": [{"text": "Reply OK."}]}],
                inferenceConfig={"maxTokens": 32},
            )
            record("model.invoke", True, f"tiny Converse call to {model_id} succeeded")
        except (ClientError, BotoCoreError) as error:
            record(
                "model.invoke",
                False,
                f"{model_id}: {_error_code(error)} (check Bedrock model access / "
                "Anthropic first-time-use form)",
            )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="default")
    parser.add_argument("--region", required=True)
    parser.add_argument("--expected-account", required=True)
    parser.add_argument(
        "--model-family", choices=sorted(MODEL_FAMILIES), default=DEFAULT_MODEL_FAMILY
    )
    parser.add_argument("--model-id")
    parser.add_argument("--allow-global-inference", action="store_true")
    parser.add_argument("--allow-unlisted-region", action="store_true")
    parser.add_argument("--probe-model", action="store_true")
    args = parser.parse_args()

    report = run(args)
    report["passed"] = all(item["ok"] for item in report["checks"])
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
