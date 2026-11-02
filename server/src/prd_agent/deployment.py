"""Pure deployment plans and least-privilege policy builders."""

from __future__ import annotations

import re
from typing import Any, Self

from pydantic import ConfigDict, Field, model_validator

from prd_agent.contracts import StrictModel, SupportedModelId

_PREFIX = re.compile(r"^[a-z][a-z0-9-]{2,30}$")
_REGION = re.compile(r"^[a-z]{2}(-gov)?-[a-z]+-\d$")

# Regions where Amazon Bedrock *managed* knowledge bases (with the AgentCore
# Gateway connector) are available. AgentCore Runtime and Gateway are available
# in a superset of these Regions, so this list is the binding constraint.
# Source: docs.aws.amazon.com/bedrock/latest/userguide/kb-managed-regions.html
# (verified 2026-10-16). Re-verify before relying on it for a new Region.
MANAGED_KNOWLEDGE_BASE_REGIONS = frozenset(
    {
        "us-east-1",
        "us-west-2",
        "eu-west-1",
        "eu-west-2",
        "eu-central-1",
        "ap-northeast-1",
        "ap-southeast-2",
    }
)

_US_SOURCES = ("us-east-1", "us-east-2", "us-west-1", "us-west-2", "ca-central-1", "ca-west-1")
_EU_SOURCES = (
    "eu-central-1",
    "eu-central-2",
    "eu-north-1",
    "eu-south-1",
    "eu-south-2",
    "eu-west-1",
    "eu-west-2",
    "eu-west-3",
)

# Per model family: the Bedrock foundation-model ID and, for each source Region
# that has one, the Geo cross-Region inference prefix (data stays inside that
# geography). Any other Region can reach the model only via Global inference.
# Sources: model-card-anthropic-claude-opus-4-6.html and
# model-card-anthropic-claude-opus-5-5.html (verified 2026-10-16).
MODEL_FAMILIES: dict[str, dict[str, Any]] = {
    "opus-4-6": {
        "foundation_model": "anthropic.claude-opus-4-6-v1",
        "geo_by_source_region": {
            **dict.fromkeys(_US_SOURCES, "us"),
            **dict.fromkeys(_EU_SOURCES, "eu"),
            **dict.fromkeys(("ap-southeast-2", "ap-southeast-4", "ap-southeast-6"), "au"),
        },
    },
    "opus-5-5": {
        "foundation_model": "anthropic.claude-opus-5-5",
        "geo_by_source_region": {
            **dict.fromkeys(_US_SOURCES, "us"),
            **dict.fromkeys(_EU_SOURCES, "eu"),
            **dict.fromkeys(("ap-southeast-2", "ap-southeast-4"), "au"),
            **dict.fromkeys(("ap-northeast-1", "ap-northeast-3"), "jp"),
        },
    },
}
DEFAULT_MODEL_FAMILY = "opus-4-6"
OPUS_46_FOUNDATION_MODEL = MODEL_FAMILIES["opus-4-6"]["foundation_model"]
_PROFILE_PREFIXES = ("us.", "eu.", "au.", "jp.", "global.")


class RegionSupportError(ValueError):
    """Raised when a Region cannot host the fixed architecture as designed."""


def assert_region_supported(region: str) -> None:
    """Fail closed when the managed knowledge base is unavailable in a Region."""

    if region not in MANAGED_KNOWLEDGE_BASE_REGIONS:
        supported = ", ".join(sorted(MANAGED_KNOWLEDGE_BASE_REGIONS))
        raise RegionSupportError(
            f"Region {region!r} has no Amazon Bedrock managed knowledge base, which "
            f"this architecture requires as its only retrieval layer. Supported: "
            f"{supported}."
        )


def foundation_model_id(model_id: str) -> str:
    """Strip a cross-Region inference-profile prefix from a model ID."""

    for prefix in _PROFILE_PREFIXES:
        if model_id.startswith(prefix):
            return model_id[len(prefix):]
    return model_id


def default_model_id(
    region: str,
    *,
    allow_global_inference: bool,
    family: str = DEFAULT_MODEL_FAMILY,
) -> str:
    """Pick the narrowest inference profile a model family offers from a Region."""

    if family not in MODEL_FAMILIES:
        raise ValueError(f"unknown model family {family!r}; use {sorted(MODEL_FAMILIES)}")
    spec = MODEL_FAMILIES[family]
    geography = spec["geo_by_source_region"].get(region)
    if geography:
        return f"{geography}.{spec['foundation_model']}"
    if allow_global_inference:
        return f"global.{spec['foundation_model']}"
    raise RegionSupportError(
        f"{family} has no Geo inference profile for {region!r}; only Global "
        "cross-Region inference is available. Confirm that data-residency rules "
        "permit Global inference, then pass --allow-global-inference."
    )


class DeploymentPlan(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    account_id: str = Field(pattern=r"^\d{12}$")
    region: str = Field(min_length=1)
    prefix: str
    project_id: str = "mock-prd-e2e"
    repository: str = "agentcore-prd-agent"
    model_id: SupportedModelId = "us.anthropic.claude-opus-4-6-v1"
    allow_global_inference: bool = False

    @model_validator(mode="after")
    def validate_prefix(self) -> Self:
        if not _PREFIX.fullmatch(self.prefix):
            raise ValueError("prefix must be lowercase alphanumeric with hyphens")
        if not _REGION.fullmatch(self.region):
            raise ValueError("region must be an AWS Region code such as ap-southeast-2")
        if self.model_id.startswith("global.") and not self.allow_global_inference:
            raise ValueError("global inference requires allow_global_inference=True")
        return self

    @property
    def bucket_name(self) -> str:
        return f"{self.prefix}-{self.account_id}-{self.region}"

    @property
    def knowledge_base_name(self) -> str:
        return f"{self.prefix}-kb"

    @property
    def data_source_name(self) -> str:
        return f"{self.prefix}-s3"

    @property
    def task_data_source_name(self) -> str:
        # Custom data source for per-invocation documents (direct ingestion).
        return f"{self.prefix}-custom"

    @property
    def gateway_name(self) -> str:
        return f"{self.prefix}-gw"

    @property
    def gateway_target_name(self) -> str:
        return f"{self.prefix}-kb"

    @property
    def runtime_name(self) -> str:
        return f"{self.prefix.replace('-', '_')}_direct"

    @property
    def runtime_artifact_prefix(self) -> str:
        # Outside content/, so the knowledge base data source never ingests it.
        return f"runtime/{self.runtime_name}/"

    # IAM is global; scoping role names by prefix and Region keeps two regional
    # stacks in one account from overwriting each other's inline policies.
    @property
    def kb_role_name(self) -> str:
        return f"{self.prefix}-{self.region}-kb"

    @property
    def gateway_role_name(self) -> str:
        return f"{self.prefix}-{self.region}-gateway"

    @property
    def runtime_role_name(self) -> str:
        return f"{self.prefix}-{self.region}-runtime"

    @property
    def tags(self) -> dict[str, str]:
        return {
            "Project": "AgentCorePrdAgent",
            "Environment": "test-in-production-account",
            "ManagedBy": "prd-agent-provisioner",
            "Purpose": "mock-e2e",
        }

    def summary(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "region": self.region,
            "prefix": self.prefix,
            "bucket_name": self.bucket_name,
            "knowledge_base_name": self.knowledge_base_name,
            "data_source_name": self.data_source_name,
            "task_data_source_name": self.task_data_source_name,
            "gateway_name": self.gateway_name,
            "gateway_target_name": self.gateway_target_name,
            "runtime_name": self.runtime_name,
            "runtime_artifact_prefix": self.runtime_artifact_prefix,
            "roles": {
                "knowledge_base": self.kb_role_name,
                "gateway": self.gateway_role_name,
                "runtime": self.runtime_role_name,
            },
            "model_id": self.model_id,
            "allow_global_inference": self.allow_global_inference,
            "deletion_operations": [],
            "tags": self.tags,
        }


def _trust_policy(
    *,
    service: str,
    account_id: str,
    source_arn: str,
) -> dict[str, Any]:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": service},
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {"aws:SourceAccount": account_id},
                    "ArnLike": {"aws:SourceArn": source_arn},
                },
            }
        ],
    }


def knowledge_base_trust(plan: DeploymentPlan) -> dict[str, Any]:
    return _trust_policy(
        service="bedrock.amazonaws.com",
        account_id=plan.account_id,
        source_arn=(
            f"arn:aws:bedrock:{plan.region}:{plan.account_id}:knowledge-base/*"
        ),
    )


def knowledge_base_permissions(plan: DeploymentPlan) -> dict[str, Any]:
    bucket_arn = f"arn:aws:s3:::{plan.bucket_name}"
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "ListDedicatedSourceBucket",
                "Effect": "Allow",
                "Action": "s3:ListBucket",
                "Resource": bucket_arn,
                "Condition": {
                    "StringEquals": {"aws:ResourceAccount": plan.account_id}
                },
            },
            {
                "Sid": "ReadDedicatedSourceObjects",
                "Effect": "Allow",
                "Action": "s3:GetObject",
                "Resource": f"{bucket_arn}/content/*",
                "Condition": {
                    "StringEquals": {"aws:ResourceAccount": plan.account_id}
                },
            },
        ],
    }


def gateway_trust(plan: DeploymentPlan) -> dict[str, Any]:
    return _trust_policy(
        service="bedrock-agentcore.amazonaws.com",
        account_id=plan.account_id,
        source_arn=(
            f"arn:aws:bedrock-agentcore:{plan.region}:{plan.account_id}:gateway/*"
        ),
    )


def gateway_permissions(plan: DeploymentPlan, knowledge_base_id: str) -> dict[str, Any]:
    kb_arn = (
        f"arn:aws:bedrock:{plan.region}:{plan.account_id}:"
        f"knowledge-base/{knowledge_base_id}"
    )
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "ValidateAndRetrieveManagedKnowledgeBase",
                "Effect": "Allow",
                "Action": ["bedrock:GetKnowledgeBase", "bedrock:Retrieve"],
                "Resource": kb_arn,
            }
        ],
    }


def runtime_trust(plan: DeploymentPlan) -> dict[str, Any]:
    return _trust_policy(
        service="bedrock-agentcore.amazonaws.com",
        account_id=plan.account_id,
        source_arn=(
            f"arn:aws:bedrock-agentcore:{plan.region}:{plan.account_id}:runtime/*"
        ),
    )


def model_invocation_resources(plan: DeploymentPlan) -> list[str]:
    """ARNs needed to invoke the plan's model through its inference profile.

    Cross-Region profiles route to destination Regions that AWS extends over
    time, so the foundation-model ARN is Region-wildcarded but pinned to the
    single selected model. Global profiles additionally require the
    Region-less foundation-model ARN.
    """

    model = foundation_model_id(plan.model_id)
    resources = [f"arn:aws:bedrock:*::foundation-model/{model}"]
    if plan.model_id.startswith("global."):
        resources.append(f"arn:aws:bedrock:::foundation-model/{model}")
    if plan.model_id != model:
        resources.append(
            f"arn:aws:bedrock:{plan.region}:{plan.account_id}:"
            f"inference-profile/{plan.model_id}"
        )
    return resources


def runtime_permissions(plan: DeploymentPlan, knowledge_base_id: str) -> dict[str, Any]:
    kb_arn = (
        f"arn:aws:bedrock:{plan.region}:{plan.account_id}:"
        f"knowledge-base/{knowledge_base_id}"
    )
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "InvokeSelectedClaudeModel",
                "Effect": "Allow",
                "Action": [
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                ],
                "Resource": model_invocation_resources(plan),
            },
            {
                "Sid": "ReadRuntimeDeploymentPackage",
                "Effect": "Allow",
                "Action": "s3:GetObject",
                "Resource": (
                    f"arn:aws:s3:::{plan.bucket_name}/{plan.runtime_artifact_prefix}*"
                ),
                "Condition": {
                    "StringEquals": {"aws:ResourceAccount": plan.account_id}
                },
            },
            {
                "Sid": "RetrieveTaskScopedEvidence",
                "Effect": "Allow",
                "Action": "bedrock:Retrieve",
                "Resource": kb_arn,
            },
            {
                # Direct ingestion is also authorized against StartIngestionJob
                # (kb-direct-ingestion-prereq.html); without it Ingest and
                # Delete fail with AccessDenied.
                "Sid": "ManageTaskDocuments",
                "Effect": "Allow",
                "Action": [
                    "bedrock:StartIngestionJob",
                    "bedrock:IngestKnowledgeBaseDocuments",
                    "bedrock:GetKnowledgeBaseDocuments",
                    "bedrock:DeleteKnowledgeBaseDocuments",
                ],
                "Resource": kb_arn,
            },
            {
                "Sid": "RuntimeLogGroups",
                "Effect": "Allow",
                "Action": ["logs:DescribeLogStreams", "logs:CreateLogGroup"],
                "Resource": (
                    f"arn:aws:logs:{plan.region}:{plan.account_id}:"
                    "log-group:/aws/bedrock-agentcore/runtimes/*"
                ),
            },
            {
                "Sid": "RuntimeLogResourcePolicy",
                "Effect": "Allow",
                "Action": "logs:PutResourcePolicy",
                "Resource": (
                    f"arn:aws:logs:{plan.region}:{plan.account_id}:"
                    f"log-group:/aws/bedrock-agentcore/runtimes/{plan.runtime_name}-*"
                ),
            },
            {
                "Sid": "DescribeRuntimeLogGroups",
                "Effect": "Allow",
                "Action": "logs:DescribeLogGroups",
                "Resource": (
                    f"arn:aws:logs:{plan.region}:{plan.account_id}:log-group:*"
                ),
            },
            {
                "Sid": "WriteRuntimeLogs",
                "Effect": "Allow",
                "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
                "Resource": (
                    f"arn:aws:logs:{plan.region}:{plan.account_id}:"
                    "log-group:/aws/bedrock-agentcore/runtimes/*:log-stream:*"
                ),
            },
            {
                "Sid": "WriteRuntimeTraces",
                "Effect": "Allow",
                "Action": [
                    "xray:PutTraceSegments",
                    "xray:PutTelemetryRecords",
                    "xray:GetSamplingRules",
                    "xray:GetSamplingTargets",
                ],
                "Resource": "*",
            },
            {
                "Sid": "WriteAgentCoreMetrics",
                "Effect": "Allow",
                "Action": "cloudwatch:PutMetricData",
                "Resource": "*",
                "Condition": {
                    "StringEquals": {"cloudwatch:namespace": "bedrock-agentcore"}
                },
            },
        ],
    }


def managed_knowledge_base_configuration() -> dict[str, Any]:
    return {
        "type": "MANAGED",
        "managedKnowledgeBaseConfiguration": {"embeddingModelType": "MANAGED"},
    }


def managed_s3_data_source_configuration(plan: DeploymentPlan) -> dict[str, Any]:
    return {
        "type": "MANAGED_KNOWLEDGE_BASE_CONNECTOR",
        "managedKnowledgeBaseConnectorConfiguration": {
            "connectorParameters": {
                "type": "S3",
                "version": "1",
                "connectionConfiguration": {
                    "bucketName": plan.bucket_name,
                    "bucketOwnerAccountId": plan.account_id,
                },
                "filterConfiguration": {
                    "inclusionPrefixes": ["content/"],
                },
            },
            "deletionProtectionConfiguration": {
                "deletionProtectionStatus": "ENABLED",
                "deletionProtectionThreshold": 20,
            },
        },
    }


def managed_custom_data_source_configuration() -> dict[str, Any]:
    """Custom connector: documents are ingested and deleted directly, never synced."""

    return {
        "type": "MANAGED_KNOWLEDGE_BASE_CONNECTOR",
        "managedKnowledgeBaseConnectorConfiguration": {
            "connectorParameters": {
                "type": "CUSTOM",
                "version": "1",
                "aclEnabled": False,
            },
        },
    }


def gateway_target_configuration(
    plan: DeploymentPlan,
    knowledge_base_id: str,
    source_snapshot: str,
) -> dict[str, Any]:
    version_filter = {
        "orAll": [
            {
                "andAll": [
                    {"equals": {"key": "project_id", "value": plan.project_id}},
                    {"equals": {"key": "source_type", "value": "document"}},
                ]
            },
            {
                "andAll": [
                    {"equals": {"key": "project_id", "value": plan.project_id}},
                    {"equals": {"key": "source_type", "value": "code"}},
                    {
                        "equals": {
                            "key": "commit_sha",
                            "value": source_snapshot,
                        }
                    },
                ]
            },
        ]
    }
    return {
        "mcp": {
            "connector": {
                "source": {"connectorId": "bedrock-knowledge-bases"},
                "configurations": [
                    {
                        "name": "Retrieve",
                        "description": (
                            "Retrieve evidence for English PRD generation from the "
                            "dedicated mock knowledge base."
                        ),
                        "parameterValues": {
                            "knowledgeBaseId": knowledge_base_id,
                            "retrievalConfiguration": {
                                "managedSearchConfiguration": {
                                    "numberOfResults": 10,
                                    "overrideSearchType": "HYBRID",
                                    "filter": version_filter,
                                }
                            },
                        },
                        "parameterOverrides": [
                            {
                                "path": "$.retrievalQuery.text",
                                "description": "Specific evidence search query.",
                                "visible": True,
                            },
                            {
                                "path": "$.userContext",
                                "description": "Trusted caller context.",
                                "visible": True,
                            },
                        ],
                    }
                ],
            }
        }
    }


def runtime_environment(
    plan: DeploymentPlan,
    *,
    environment: str,
    knowledge_base_id: str,
    task_data_source_id: str,
    prompt_version: str,
    runtime_version: str,
) -> dict[str, str]:
    """Non-secret Runtime configuration; validated by load_settings before deploy.

    PRD_CONTRACT_PATH is deliberately absent: app.py resolves the packaged
    Contract to an absolute path inside the Runtime filesystem.
    """

    return {
        "DEPLOYMENT_ENV": environment,
        "AWS_ACCOUNT_ID": plan.account_id,
        "AWS_REGION": plan.region,
        "BEDROCK_MODEL_ID": plan.model_id,
        "ALLOW_GLOBAL_INFERENCE": "true" if plan.allow_global_inference else "false",
        "KNOWLEDGE_BASE_ID": knowledge_base_id,
        "TASK_DATA_SOURCE_ID": task_data_source_id,
        "PROJECT_ID": plan.project_id,
        "REPOSITORY_NAME": plan.repository,
        "PROMPT_VERSION": prompt_version,
        "RUNTIME_VERSION": runtime_version,
    }
