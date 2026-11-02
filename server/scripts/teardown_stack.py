#!/usr/bin/env python3
"""Tear down ONE isolated mock stack created by this repository's scripts.

Dry-run is the default and only lists what exists. Deletion requires both
--apply and --confirm-prefix equal to --prefix. Every resource is matched by
its exact deterministic name AND must carry the Project=AgentCorePrdAgent tag
(IAM roles must also sit under the dedicated path); anything else is refused.
Deletion is irreversible: the bucket is versioned and every object version is
removed.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from prd_agent.deployment import DeploymentPlan  # noqa: E402

ROLE_PATH = "/agentcore-prd-agent-test/"
POLICY_NAME = "PrdAgentMockE2EPolicy"
PROJECT_TAG = "AgentCorePrdAgent"


def _code(error: ClientError) -> str:
    return error.response.get("Error", {}).get("Code", "")


def _named(items: list[dict[str, Any]], key: str, name: str) -> dict[str, Any] | None:
    matches = [item for item in items if item.get(key) == name]
    if len(matches) > 1:
        raise RuntimeError(f"multiple resources named {name!r}; refusing to guess")
    return matches[0] if matches else None


def _require_tag(tags: dict[str, str], what: str) -> None:
    if tags.get("Project") != PROJECT_TAG:
        raise RuntimeError(f"refusing to delete untagged {what}")


def _wait_gone(
    description: str, probe, *, gone_codes: set[str], timeout: int = 600
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            probe()
        except ClientError as error:
            if _code(error) in gone_codes:
                return
            raise
        print(f"WAIT delete {description}", file=sys.stderr)
        time.sleep(5)
    raise TimeoutError(f"{description} still exists after {timeout}s")


def discover(session, plan: DeploymentPlan) -> dict[str, Any]:
    control = session.client("bedrock-agentcore-control")
    agent = session.client("bedrock-agent")
    s3 = session.client("s3")
    iam = session.client("iam")
    found: dict[str, Any] = {}

    runtimes = control.list_agent_runtimes(maxResults=100).get("agentRuntimes", [])
    if runtime := _named(runtimes, "agentRuntimeName", plan.runtime_name):
        found["runtime"] = runtime

    gateways = control.list_gateways(maxResults=100).get("items", [])
    if gateway := _named(gateways, "name", plan.gateway_name):
        targets = control.list_gateway_targets(
            gatewayIdentifier=gateway["gatewayId"], maxResults=100
        ).get("items", [])
        found["gateway"] = {**gateway, "targets": targets}

    kbs = agent.list_knowledge_bases(maxResults=100).get("knowledgeBaseSummaries", [])
    if kb := _named(kbs, "name", plan.knowledge_base_name):
        sources = agent.list_data_sources(
            knowledgeBaseId=kb["knowledgeBaseId"], maxResults=100
        ).get("dataSourceSummaries", [])
        found["knowledge_base"] = {**kb, "data_sources": sources}

    try:
        s3.head_bucket(Bucket=plan.bucket_name, ExpectedBucketOwner=plan.account_id)
        found["bucket"] = plan.bucket_name
    except ClientError as error:
        if _code(error) not in {"404", "NoSuchBucket", "NotFound"}:
            raise

    roles = []
    for name in (plan.runtime_role_name, plan.gateway_role_name, plan.kb_role_name):
        try:
            roles.append(iam.get_role(RoleName=name)["Role"]["RoleName"])
        except ClientError as error:
            if _code(error) != "NoSuchEntity":
                raise
    if roles:
        found["roles"] = roles
    return found


def delete(session, plan: DeploymentPlan, found: dict[str, Any]) -> None:
    control = session.client("bedrock-agentcore-control")
    agent = session.client("bedrock-agent")
    s3 = session.client("s3")
    iam = session.client("iam")

    if runtime := found.get("runtime"):
        arn = runtime["agentRuntimeArn"]
        _require_tag(
            control.list_tags_for_resource(resourceArn=arn).get("tags", {}), arn
        )
        control.delete_agent_runtime(agentRuntimeId=runtime["agentRuntimeId"])
        _wait_gone(
            "runtime",
            lambda: control.get_agent_runtime(agentRuntimeId=runtime["agentRuntimeId"]),
            gone_codes={"ResourceNotFoundException"},
        )

    if gateway := found.get("gateway"):
        gateway_id = gateway["gatewayId"]
        detail = control.get_gateway(gatewayIdentifier=gateway_id)
        _require_tag(
            control.list_tags_for_resource(resourceArn=detail["gatewayArn"]).get(
                "tags", {}
            ),
            detail["gatewayArn"],
        )
        foreign = [
            t["name"]
            for t in gateway["targets"]
            if t["name"] != plan.gateway_target_name
        ]
        if foreign:
            raise RuntimeError(
                f"gateway has targets this stack did not create: {foreign}"
            )
        for target in gateway["targets"]:
            control.delete_gateway_target(
                gatewayIdentifier=gateway_id, targetId=target["targetId"]
            )
            _wait_gone(
                "gateway target",
                lambda target=target: control.get_gateway_target(
                    gatewayIdentifier=gateway_id, targetId=target["targetId"]
                ),
                gone_codes={"ResourceNotFoundException"},
            )
        control.delete_gateway(gatewayIdentifier=gateway_id)
        _wait_gone(
            "gateway",
            lambda: control.get_gateway(gatewayIdentifier=gateway_id),
            gone_codes={"ResourceNotFoundException"},
        )

    if kb := found.get("knowledge_base"):
        kb_id = kb["knowledgeBaseId"]
        kb_arn = agent.get_knowledge_base(knowledgeBaseId=kb_id)["knowledgeBase"][
            "knowledgeBaseArn"
        ]
        _require_tag(
            agent.list_tags_for_resource(resourceArn=kb_arn).get("tags", {}), kb_arn
        )
        owned_sources = {plan.data_source_name, plan.task_data_source_name}
        for source in kb["data_sources"]:
            if source["name"] not in owned_sources:
                raise RuntimeError(
                    f"knowledge base has a foreign data source: {source['name']}"
                )
            agent.delete_data_source(
                knowledgeBaseId=kb_id, dataSourceId=source["dataSourceId"]
            )
            _wait_gone(
                "data source",
                lambda source=source: agent.get_data_source(
                    knowledgeBaseId=kb_id, dataSourceId=source["dataSourceId"]
                ),
                gone_codes={"ResourceNotFoundException"},
            )
        agent.delete_knowledge_base(knowledgeBaseId=kb_id)
        _wait_gone(
            "knowledge base",
            lambda: agent.get_knowledge_base(knowledgeBaseId=kb_id),
            gone_codes={"ResourceNotFoundException"},
        )

    if bucket := found.get("bucket"):
        tags = {
            item["Key"]: item["Value"]
            for item in s3.get_bucket_tagging(Bucket=bucket)["TagSet"]
        }
        _require_tag(tags, f"bucket {bucket}")
        for page in s3.get_paginator("list_object_versions").paginate(Bucket=bucket):
            doomed = [
                {"Key": item["Key"], "VersionId": item["VersionId"]}
                for item in page.get("Versions", []) + page.get("DeleteMarkers", [])
            ]
            for start in range(0, len(doomed), 1000):
                s3.delete_objects(
                    Bucket=bucket,
                    Delete={"Objects": doomed[start : start + 1000], "Quiet": True},
                    ExpectedBucketOwner=plan.account_id,
                )
        s3.delete_bucket(Bucket=bucket, ExpectedBucketOwner=plan.account_id)

    for name in found.get("roles", []):
        role = iam.get_role(RoleName=name)["Role"]
        tags = {
            item["Key"]: item["Value"]
            for item in iam.list_role_tags(RoleName=name).get("Tags", [])
        }
        if role.get("Path") != ROLE_PATH:
            raise RuntimeError(f"refusing to delete role outside {ROLE_PATH}: {name}")
        _require_tag(tags, f"role {name}")
        policies = iam.list_role_policies(RoleName=name).get("PolicyNames", [])
        attached = iam.list_attached_role_policies(RoleName=name).get(
            "AttachedPolicies", []
        )
        if set(policies) - {POLICY_NAME} or attached:
            raise RuntimeError(f"role {name} carries policies this stack did not add")
        for policy in policies:
            iam.delete_role_policy(RoleName=name, PolicyName=policy)
        iam.delete_role(RoleName=name)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="default")
    parser.add_argument("--region", required=True)
    parser.add_argument("--expected-account", required=True)
    parser.add_argument("--prefix", default="prd-agent-mock-e2e")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-prefix")
    args = parser.parse_args()

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    account = session.client("sts").get_caller_identity()["Account"]
    if account != args.expected_account:
        raise RuntimeError(f"AWS account mismatch: expected {args.expected_account}")
    plan = DeploymentPlan(account_id=account, region=args.region, prefix=args.prefix)
    found = discover(session, plan)
    print(
        json.dumps(
            {"mode": "apply" if args.apply else "dry-run", "found": found},
            indent=2,
            sort_keys=True,
            default=str,
        )
    )
    if not args.apply:
        return 0
    if args.confirm_prefix != args.prefix:
        parser.error("--apply requires --confirm-prefix equal to --prefix")
    delete(session, plan, found)
    print(json.dumps({"deleted": sorted(found)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
