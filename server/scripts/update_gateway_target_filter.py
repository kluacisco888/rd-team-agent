#!/usr/bin/env python3
"""Update only the dedicated mock Gateway target's source-version filter."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from prd_agent.deployment import DeploymentPlan, gateway_target_configuration  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="default")
    parser.add_argument("--region", required=True)
    parser.add_argument("--expected-account", required=True)
    parser.add_argument("--gateway-id", required=True)
    parser.add_argument("--target-id", required=True)
    parser.add_argument("--knowledge-base-id", required=True)
    parser.add_argument("--source-snapshot", required=True)
    args = parser.parse_args()

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    account = session.client("sts").get_caller_identity()["Account"]
    if account != args.expected_account:
        raise RuntimeError(
            f"AWS account mismatch: expected {args.expected_account}, received {account}"
        )
    plan = DeploymentPlan(
        account_id=account,
        region=args.region,
        prefix="prd-agent-mock-e2e",
    )
    client = session.client("bedrock-agentcore-control")
    client.update_gateway_target(
        gatewayIdentifier=args.gateway_id,
        targetId=args.target_id,
        name=plan.gateway_target_name,
        description="Managed Knowledge Base connector for mock PRD E2E",
        targetConfiguration=gateway_target_configuration(
            plan,
            args.knowledge_base_id,
            args.source_snapshot,
        ),
        credentialProviderConfigurations=[
            {"credentialProviderType": "GATEWAY_IAM_ROLE"}
        ],
    )
    deadline = time.monotonic() + 300
    while True:
        target = client.get_gateway_target(
            gatewayIdentifier=args.gateway_id,
            targetId=args.target_id,
        )
        status = target["status"]
        if status == "READY":
            print(
                json.dumps(
                    {
                        "gateway_id": args.gateway_id,
                        "target_id": args.target_id,
                        "status": status,
                        "source_snapshot": args.source_snapshot,
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0
        if status == "FAILED":
            raise RuntimeError(f"Gateway target update failed: {target}")
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Gateway target update timed out at {status}")
        time.sleep(5)


if __name__ == "__main__":
    raise SystemExit(main())
