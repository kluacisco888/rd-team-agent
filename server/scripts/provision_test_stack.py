#!/usr/bin/env python3
"""Provision the isolated mock AgentCore PRD stack. Dry-run is the default."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable

import boto3
from botocore.exceptions import ClientError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from prd_agent.deployment import (  # noqa: E402
    DEFAULT_MODEL_FAMILY,
    MODEL_FAMILIES,
    DeploymentPlan,
    assert_region_supported,
    default_model_id,
    gateway_permissions,
    gateway_target_configuration,
    gateway_trust,
    knowledge_base_permissions,
    knowledge_base_trust,
    managed_custom_data_source_configuration,
    managed_knowledge_base_configuration,
    managed_s3_data_source_configuration,
    runtime_permissions,
    runtime_trust,
)
from prd_agent.kb_bundle import build_mock_bundle  # noqa: E402

ROLE_PATH = "/agentcore-prd-agent-test/"
POLICY_NAME = "PrdAgentMockE2EPolicy"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _wait_for(
    description: str,
    reader: Callable[[], tuple[str, Any]],
    *,
    ready: set[str],
    failed: set[str],
    timeout_seconds: int = 600,
) -> Any:
    deadline = time.monotonic() + timeout_seconds
    while True:
        status, resource = reader()
        print(f"WAIT {description}: {status}", file=sys.stderr)
        if status in ready:
            return resource
        if status in failed:
            raise RuntimeError(f"{description} entered failure state {status}: {resource}")
        if time.monotonic() >= deadline:
            raise TimeoutError(f"timed out waiting for {description}; last status={status}")
        time.sleep(5)


def _is_not_found(error: ClientError, *codes: str) -> bool:
    return error.response.get("Error", {}).get("Code") in set(codes)


def _role_tags(iam, role_name: str) -> dict[str, str]:
    response = iam.list_role_tags(RoleName=role_name)
    return {item["Key"]: item["Value"] for item in response.get("Tags", [])}


def _ensure_role(
    iam,
    *,
    plan: DeploymentPlan,
    role_name: str,
    trust: dict[str, Any],
    permissions: dict[str, Any],
) -> str:
    try:
        role = iam.get_role(RoleName=role_name)["Role"]
        if role.get("Path") != ROLE_PATH:
            raise RuntimeError(f"refusing to reuse unmarked IAM role {role_name}")
        tags = _role_tags(iam, role_name)
        if tags.get("Project") != plan.tags["Project"]:
            raise RuntimeError(f"refusing to reuse IAM role without project tag: {role_name}")
        iam.update_assume_role_policy(
            RoleName=role_name,
            PolicyDocument=_json(trust),
        )
    except ClientError as error:
        if not _is_not_found(error, "NoSuchEntity"):
            raise
        role = iam.create_role(
            Path=ROLE_PATH,
            RoleName=role_name,
            AssumeRolePolicyDocument=_json(trust),
            Description="Isolated AgentCore PRD mock E2E service role",
            MaxSessionDuration=3600,
            Tags=[{"Key": key, "Value": value} for key, value in plan.tags.items()],
        )["Role"]
    iam.put_role_policy(
        RoleName=role_name,
        PolicyName=POLICY_NAME,
        PolicyDocument=_json(permissions),
    )
    return role["Arn"]


def _ensure_bucket(s3, plan: DeploymentPlan) -> None:
    exists = True
    try:
        s3.head_bucket(Bucket=plan.bucket_name)
    except ClientError as error:
        if not _is_not_found(error, "404", "NoSuchBucket", "NotFound"):
            raise
        exists = False

    if not exists:
        create: dict[str, Any] = {"Bucket": plan.bucket_name}
        if plan.region != "us-east-1":
            create["CreateBucketConfiguration"] = {
                "LocationConstraint": plan.region
            }
        s3.create_bucket(**create)
    else:
        try:
            tags = {
                item["Key"]: item["Value"]
                for item in s3.get_bucket_tagging(Bucket=plan.bucket_name)["TagSet"]
            }
        except ClientError as error:
            if not _is_not_found(error, "NoSuchTagSet"):
                raise
            tags = {}
        if tags.get("Project") != plan.tags["Project"]:
            raise RuntimeError(
                f"refusing to reuse unmarked bucket {plan.bucket_name}"
            )

    s3.put_public_access_block(
        Bucket=plan.bucket_name,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )
    s3.put_bucket_encryption(
        Bucket=plan.bucket_name,
        ServerSideEncryptionConfiguration={
            "Rules": [
                {
                    "ApplyServerSideEncryptionByDefault": {
                        "SSEAlgorithm": "AES256"
                    },
                    "BucketKeyEnabled": True,
                }
            ]
        },
    )
    s3.put_bucket_versioning(
        Bucket=plan.bucket_name,
        VersioningConfiguration={"Status": "Enabled"},
    )
    s3.put_bucket_tagging(
        Bucket=plan.bucket_name,
        Tagging={
            "TagSet": [
                {"Key": key, "Value": value} for key, value in plan.tags.items()
            ]
        },
    )


def _upload_bundle(s3, plan: DeploymentPlan):
    bundle = build_mock_bundle(PROJECT_ROOT, project_id=plan.project_id)
    for item in bundle.objects:
        s3.put_object(
            Bucket=plan.bucket_name,
            Key=item.key,
            Body=item.body,
            ContentType=item.content_type,
            ServerSideEncryption="AES256",
            Tagging="Project=AgentCorePrdAgent&Environment=test-in-production-account",
        )
    return bundle


def _find_named(items: list[dict[str, Any]], name_key: str, name: str):
    matches = [item for item in items if item.get(name_key) == name]
    if len(matches) > 1:
        raise RuntimeError(f"multiple resources found with name {name!r}")
    return matches[0] if matches else None


def _ensure_knowledge_base(client, plan: DeploymentPlan, role_arn: str) -> dict[str, Any]:
    summaries = client.list_knowledge_bases(maxResults=100).get(
        "knowledgeBaseSummaries", []
    )
    existing = _find_named(summaries, "name", plan.knowledge_base_name)
    if existing:
        knowledge_base_id = existing["knowledgeBaseId"]
    else:
        knowledge_base_id = client.create_knowledge_base(
            name=plan.knowledge_base_name,
            description="Isolated mock PRD source and code knowledge base",
            roleArn=role_arn,
            knowledgeBaseConfiguration=managed_knowledge_base_configuration(),
            tags=plan.tags,
        )["knowledgeBase"]["knowledgeBaseId"]

    return _wait_for(
        "managed knowledge base",
        lambda: (
            (resource := client.get_knowledge_base(
                knowledgeBaseId=knowledge_base_id
            )["knowledgeBase"])["status"],
            resource,
        ),
        ready={"ACTIVE"},
        failed={"FAILED", "DELETE_UNSUCCESSFUL"},
    )


def _ensure_data_source(client, plan: DeploymentPlan, knowledge_base_id: str):
    summaries = client.list_data_sources(
        knowledgeBaseId=knowledge_base_id,
        maxResults=100,
    ).get("dataSourceSummaries", [])
    existing = _find_named(summaries, "name", plan.data_source_name)
    if existing:
        data_source_id = existing["dataSourceId"]
        client.update_data_source(
            knowledgeBaseId=knowledge_base_id,
            dataSourceId=data_source_id,
            name=plan.data_source_name,
            description="Pre-split mock PRD and source-code evidence",
            dataSourceConfiguration=managed_s3_data_source_configuration(plan),
            dataDeletionPolicy="DELETE",
        )
    else:
        data_source_id = client.create_data_source(
            knowledgeBaseId=knowledge_base_id,
            name=plan.data_source_name,
            description="Pre-split mock PRD and source-code evidence",
            dataSourceConfiguration=managed_s3_data_source_configuration(plan),
            dataDeletionPolicy="DELETE",
        )["dataSource"]["dataSourceId"]

    return _wait_for(
        "managed S3 data source",
        lambda: (
            (resource := client.get_data_source(
                knowledgeBaseId=knowledge_base_id,
                dataSourceId=data_source_id,
            )["dataSource"])["status"],
            resource,
        ),
        ready={"AVAILABLE"},
        failed={"FAILED", "DELETE_UNSUCCESSFUL"},
    )


def _ensure_task_data_source(client, plan: DeploymentPlan, knowledge_base_id: str):
    """Custom data source the Runtime writes per-invocation documents into.

    An existing one is reused unchanged: a custom connector has no settings
    this stack would need to update.
    """

    summaries = client.list_data_sources(
        knowledgeBaseId=knowledge_base_id,
        maxResults=100,
    ).get("dataSourceSummaries", [])
    existing = _find_named(summaries, "name", plan.task_data_source_name)
    if existing:
        data_source_id = existing["dataSourceId"]
    else:
        data_source_id = client.create_data_source(
            knowledgeBaseId=knowledge_base_id,
            name=plan.task_data_source_name,
            description=(
                "Per-invocation task documents; ingested and deleted by the Runtime"
            ),
            dataSourceConfiguration=managed_custom_data_source_configuration(),
            dataDeletionPolicy="DELETE",
        )["dataSource"]["dataSourceId"]

    return _wait_for(
        "custom task data source",
        lambda: (
            (resource := client.get_data_source(
                knowledgeBaseId=knowledge_base_id,
                dataSourceId=data_source_id,
            )["dataSource"])["status"],
            resource,
        ),
        ready={"AVAILABLE"},
        failed={"FAILED", "DELETE_UNSUCCESSFUL"},
    )


def _ingest(client, knowledge_base_id: str, data_source_id: str):
    job = client.start_ingestion_job(
        knowledgeBaseId=knowledge_base_id,
        dataSourceId=data_source_id,
        description="Mock PRD Agent E2E ingestion",
    )["ingestionJob"]
    ingestion_job_id = job["ingestionJobId"]
    return _wait_for(
        "knowledge base ingestion",
        lambda: (
            (resource := client.get_ingestion_job(
                knowledgeBaseId=knowledge_base_id,
                dataSourceId=data_source_id,
                ingestionJobId=ingestion_job_id,
            )["ingestionJob"])["status"],
            resource,
        ),
        ready={"COMPLETE"},
        failed={"FAILED", "STOPPED"},
        timeout_seconds=900,
    )


def _direct_retrieve_smoke(runtime_client, plan: DeploymentPlan, knowledge_base_id: str):
    response = runtime_client.retrieve(
        knowledgeBaseId=knowledge_base_id,
        retrievalQuery={
            "text": "What is the maximum CSV row count and is XLSX supported?"
        },
        retrievalConfiguration={
            "managedSearchConfiguration": {
                "numberOfResults": 5,
                "filter": {
                    "equals": {"key": "project_id", "value": plan.project_id}
                },
            }
        },
    )
    results = response.get("retrievalResults", [])
    if not results:
        raise RuntimeError("managed knowledge base smoke query returned no results")
    return results


def _ensure_gateway(client, plan: DeploymentPlan, role_arn: str) -> dict[str, Any]:
    items = client.list_gateways(maxResults=100).get("items", [])
    existing = _find_named(items, "name", plan.gateway_name)
    if existing:
        gateway_id = existing["gatewayId"]
    else:
        created = client.create_gateway(
            name=plan.gateway_name,
            description="AWS_IAM gateway for isolated mock PRD evidence retrieval",
            roleArn=role_arn,
            protocolType="MCP",
            protocolConfiguration={
                "mcp": {
                    "instructions": "Retrieve read-only evidence for English PRDs.",
                    "streamingConfiguration": {"enableResponseStreaming": True},
                }
            },
            authorizerType="AWS_IAM",
            tags=plan.tags,
        )
        gateway_id = created["gatewayId"]

    return _wait_for(
        "AgentCore gateway",
        lambda: (
            (resource := client.get_gateway(gatewayIdentifier=gateway_id))["status"],
            resource,
        ),
        ready={"READY"},
        failed={"FAILED"},
    )


def _ensure_gateway_target(
    client,
    plan: DeploymentPlan,
    gateway_id: str,
    knowledge_base_id: str,
    source_snapshot: str,
):
    items = client.list_gateway_targets(
        gatewayIdentifier=gateway_id,
        maxResults=100,
    ).get("items", [])
    existing = _find_named(items, "name", plan.gateway_target_name)
    target_configuration = gateway_target_configuration(
        plan,
        knowledge_base_id,
        source_snapshot,
    )
    credentials = [{"credentialProviderType": "GATEWAY_IAM_ROLE"}]
    if existing:
        target_id = existing["targetId"]
        client.update_gateway_target(
            gatewayIdentifier=gateway_id,
            targetId=target_id,
            name=plan.gateway_target_name,
            description="Managed Knowledge Base connector for mock PRD E2E",
            targetConfiguration=target_configuration,
            credentialProviderConfigurations=credentials,
        )
    else:
        created = client.create_gateway_target(
            gatewayIdentifier=gateway_id,
            name=plan.gateway_target_name,
            description="Managed Knowledge Base connector for mock PRD E2E",
            targetConfiguration=target_configuration,
            credentialProviderConfigurations=credentials,
        )
        target_id = created["targetId"]

    return _wait_for(
        "managed KB gateway target",
        lambda: (
            (resource := client.get_gateway_target(
                gatewayIdentifier=gateway_id,
                targetId=target_id,
            ))["status"],
            resource,
        ),
        ready={"READY"},
        failed={"FAILED"},
    )


def apply(plan: DeploymentPlan, session: boto3.Session) -> dict[str, Any]:
    iam = session.client("iam")
    s3 = session.client("s3")
    bedrock_agent = session.client("bedrock-agent")
    bedrock_runtime = session.client("bedrock-agent-runtime")
    agentcore_control = session.client("bedrock-agentcore-control")

    _ensure_bucket(s3, plan)
    bundle = _upload_bundle(s3, plan)

    kb_role_arn = _ensure_role(
        iam,
        plan=plan,
        role_name=plan.kb_role_name,
        trust=knowledge_base_trust(plan),
        permissions=knowledge_base_permissions(plan),
    )
    time.sleep(10)
    knowledge_base = _ensure_knowledge_base(bedrock_agent, plan, kb_role_arn)
    knowledge_base_id = knowledge_base["knowledgeBaseId"]
    data_source = _ensure_data_source(bedrock_agent, plan, knowledge_base_id)
    ingestion = _ingest(
        bedrock_agent,
        knowledge_base_id,
        data_source["dataSourceId"],
    )
    retrieval_results = _direct_retrieve_smoke(
        bedrock_runtime,
        plan,
        knowledge_base_id,
    )
    task_data_source = _ensure_task_data_source(bedrock_agent, plan, knowledge_base_id)

    gateway_role_arn = _ensure_role(
        iam,
        plan=plan,
        role_name=plan.gateway_role_name,
        trust=gateway_trust(plan),
        permissions=gateway_permissions(plan, knowledge_base_id),
    )
    time.sleep(10)
    gateway = _ensure_gateway(agentcore_control, plan, gateway_role_arn)
    gateway_id = gateway["gatewayId"]
    target = _ensure_gateway_target(
        agentcore_control,
        plan,
        gateway_id,
        knowledge_base_id,
        bundle.commit_sha,
    )
    runtime_role_arn = _ensure_role(
        iam,
        plan=plan,
        role_name=plan.runtime_role_name,
        trust=runtime_trust(plan),
        permissions=runtime_permissions(plan, knowledge_base_id),
    )

    return {
        **plan.summary(),
        "bucket_name": plan.bucket_name,
        "knowledge_base_id": knowledge_base_id,
        "knowledge_base_status": knowledge_base["status"],
        "data_source_id": data_source["dataSourceId"],
        "data_source_status": data_source["status"],
        "task_data_source_id": task_data_source["dataSourceId"],
        "task_data_source_status": task_data_source["status"],
        "ingestion_job_id": ingestion["ingestionJobId"],
        "ingestion_status": ingestion["status"],
        "direct_retrieve_result_count": len(retrieval_results),
        "gateway_id": gateway_id,
        "gateway_arn": gateway["gatewayArn"],
        "gateway_url": gateway["gatewayUrl"],
        "gateway_status": gateway["status"],
        "gateway_target_id": target["targetId"],
        "gateway_target_status": target["status"],
        "gateway_tool_name": f"{plan.gateway_target_name}___Retrieve",
        "runtime_role_arn": runtime_role_arn,
        "source_snapshot": bundle.commit_sha,
        "project_id": bundle.project_id,
        "repository": bundle.repository,
        "runtime_manifest_json": bundle.manifest.to_json(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="default")
    parser.add_argument("--region", required=True)
    parser.add_argument("--expected-account", required=True)
    parser.add_argument("--prefix", default="prd-agent-mock-e2e")
    parser.add_argument(
        "--model-family",
        choices=sorted(MODEL_FAMILIES),
        default=DEFAULT_MODEL_FAMILY,
        help="Claude model family; the inference profile is derived from --region",
    )
    parser.add_argument(
        "--model-id",
        help="explicit model or inference-profile ID (overrides --model-family)",
    )
    parser.add_argument(
        "--allow-global-inference",
        action="store_true",
        help="permit global.* inference (only if data-residency rules allow it)",
    )
    parser.add_argument(
        "--allow-unlisted-region",
        action="store_true",
        help="skip the managed-knowledge-base Region allow-list (AWS added a Region)",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        help="write the deployment state JSON here (required with --apply)",
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    if args.apply and args.state_file is None:
        parser.error("--state-file is required with --apply")
    if not args.allow_unlisted_region:
        assert_region_supported(args.region)
    model_id = args.model_id or default_model_id(
        args.region,
        allow_global_inference=args.allow_global_inference,
        family=args.model_family,
    )

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    identity = session.client("sts").get_caller_identity()
    if identity["Account"] != args.expected_account:
        raise RuntimeError(
            f"AWS account mismatch: expected {args.expected_account}, "
            f"received {identity['Account']}"
        )
    plan = DeploymentPlan(
        account_id=identity["Account"],
        region=args.region,
        prefix=args.prefix,
        model_id=model_id,
        allow_global_inference=args.allow_global_inference,
    )
    if not args.apply:
        print(json.dumps({"mode": "dry-run", **plan.summary()}, indent=2, sort_keys=True))
        return 0

    state = apply(plan, session)
    args.state_file.parent.mkdir(parents=True, exist_ok=True)
    args.state_file.write_text(
        json.dumps(state, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print("===PRD_AGENT_DEPLOYMENT_STATE===")
    print(json.dumps(state, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
