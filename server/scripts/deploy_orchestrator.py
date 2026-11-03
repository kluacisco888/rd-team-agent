#!/usr/bin/env python3
"""Create or update the R&D team agent stack. Dry-run is the default.

Resources (each is found by name first, so re-running only updates):
  R1 Cognito user pool, hosted domain, the Mac app client (authorization code +
     PKCE) and a CLI test client (USER_PASSWORD_AUTH, for smoke tests only),
     plus test users created without a usable password
  R2 AgentCore Memory with user-preference, semantic and summary strategies
  R3 results bucket (Block Public Access, SSE-S3, TLS-only bucket policy)
  R5 Runtime execution role with a least-privilege inline policy, checked by
     IAM Access Analyzer before it is written
  R6 AgentCore Runtime with the Cognito JWT authorizer

Nothing is deleted. Test-user passwords are never handled here: set them with
  aws cognito-idp admin-set-user-password --permanent ...
The knowledge base (R7) must already hold the code synced by sync_code_repo.py.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
for path in (SRC, Path(__file__).resolve().parent):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from deploy_runtime import (  # noqa: E402
    CONTRACT_FILE,
    RUNTIME_LANGUAGE,
    _find_runtime,
    _retry_iam_propagation,
    _skip,
    _wait_ready,
    install_dependencies,
    write_zip,
)

from orchestrator.settings import load_orchestrator_settings  # noqa: E402
from prd_agent.deployment import DeploymentPlan, model_invocation_resources  # noqa: E402
from prd_agent.manifest import FileSourceManifestProvider  # noqa: E402

NAME = "rd-team-agent"
POOL_NAME = f"{NAME}-users"
MAC_CLIENT = f"{NAME}-mac"
CLI_CLIENT = f"{NAME}-cli-test"
MAC_CALLBACK = "rdteamagent://callback"
MAC_SIGNOUT = "rdteamagent://signout"
MEMORY_NAME = "rd_team_agent_memory"
RUNTIME_NAME = "rd_team_agent"
ROLE_PATH = f"/{NAME}/"
POLICY_NAME = "RdTeamAgentRuntime"
ENTRY_FILE = "orchestrator_app.py"
ACTOR_NAMESPACE = "/strategy/{memoryStrategyId}/actor/{actorId}/"
SESSION_NAMESPACE = ACTOR_NAMESPACE + "session/{sessionId}/"
STRATEGY_ENV = {
    "USER_PREFERENCE": "MEMORY_PREFERENCE_STRATEGY_ID",
    "SEMANTIC": "MEMORY_SEMANTIC_STRATEGY_ID",
    "SUMMARIZATION": "MEMORY_SUMMARY_STRATEGY_ID",
}


def log(message: str) -> None:
    print(message, file=sys.stderr)


def wait(description: str, read, *, ready: set[str], failed: set[str], timeout=900):
    deadline = time.monotonic() + timeout
    while True:
        status, value = read()
        log(f"WAIT {description}: {status}")
        if status in ready:
            return value
        if status in failed or time.monotonic() > deadline:
            raise RuntimeError(f"{description} is {status}: {value}")
        time.sleep(10)


class Names:
    def __init__(self, account: str, region: str) -> None:
        self.account = account
        self.region = region
        self.bucket = f"{NAME}-results-{account}-{region}"
        self.domain = f"{NAME}-{account}"
        self.role = f"{NAME}-{region}-runtime"
        self.tags = {"Project": NAME, "Environment": "demo"}


# ---------------------------------------------------------------- policies


def trust_policy(names: Names) -> dict[str, Any]:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {"aws:SourceAccount": names.account},
                    "ArnLike": {
                        "aws:SourceArn": (
                            f"arn:aws:bedrock-agentcore:{names.region}:"
                            f"{names.account}:runtime/*"
                        )
                    },
                },
            }
        ],
    }


def runtime_policy(
    names: Names, *, model_id: str, knowledge_base_id: str, memory_arn: str
) -> dict[str, Any]:
    plan = DeploymentPlan(
        account_id=names.account, region=names.region, prefix=NAME, model_id=model_id
    )
    kb_arn = (
        f"arn:aws:bedrock:{names.region}:{names.account}:"
        f"knowledge-base/{knowledge_base_id}"
    )
    logs = f"arn:aws:logs:{names.region}:{names.account}:log-group"
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "InvokeSelectedModel",
                "Effect": "Allow",
                "Action": [
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                ],
                "Resource": model_invocation_resources(plan),
            },
            {
                "Sid": "ReadRuntimePackage",
                "Effect": "Allow",
                "Action": "s3:GetObject",
                "Resource": f"arn:aws:s3:::{names.bucket}/runtime/*",
            },
            {
                # DeleteObject: deleting a session removes its PRD results.
                "Sid": "ReadWriteOwnResults",
                "Effect": "Allow",
                "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
                "Resource": f"arn:aws:s3:::{names.bucket}/results/*",
            },
            {
                # Without ListBucket, S3 answers 403 instead of 404 for a missing
                # result, which hides "no such task" behind an access error.
                "Sid": "ListResults",
                "Effect": "Allow",
                "Action": "s3:ListBucket",
                "Resource": f"arn:aws:s3:::{names.bucket}",
                "Condition": {"StringLike": {"s3:prefix": "results/*"}},
            },
            {
                "Sid": "RetrieveEvidence",
                "Effect": "Allow",
                "Action": "bedrock:Retrieve",
                "Resource": kb_arn,
            },
            {
                # Direct ingestion is also authorized against StartIngestionJob.
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
                # The two deletes serve session deletion: its events and the
                # summary records under its session namespace.
                "Sid": "UseMemory",
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:CreateEvent",
                    "bedrock-agentcore:GetEvent",
                    "bedrock-agentcore:ListEvents",
                    "bedrock-agentcore:DeleteEvent",
                    "bedrock-agentcore:ListSessions",
                    "bedrock-agentcore:ListMemoryRecords",
                    "bedrock-agentcore:RetrieveMemoryRecords",
                    "bedrock-agentcore:BatchDeleteMemoryRecords",
                ],
                "Resource": memory_arn,
            },
            {
                "Sid": "RuntimeLogGroups",
                "Effect": "Allow",
                "Action": ["logs:DescribeLogStreams", "logs:CreateLogGroup"],
                "Resource": f"{logs}:/aws/bedrock-agentcore/runtimes/*",
            },
            {
                "Sid": "DescribeLogGroups",
                "Effect": "Allow",
                "Action": "logs:DescribeLogGroups",
                "Resource": f"{logs}:*",
            },
            {
                "Sid": "WriteRuntimeLogs",
                "Effect": "Allow",
                "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
                "Resource": f"{logs}:/aws/bedrock-agentcore/runtimes/*:log-stream:*",
            },
            {
                "Sid": "WriteTraces",
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


def bucket_policy(names: Names) -> dict[str, Any]:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "DenyInsecureTransport",
                "Effect": "Deny",
                "Principal": "*",
                "Action": "s3:*",
                "Resource": [
                    f"arn:aws:s3:::{names.bucket}",
                    f"arn:aws:s3:::{names.bucket}/*",
                ],
                "Condition": {"Bool": {"aws:SecureTransport": "false"}},
            }
        ],
    }


# ---------------------------------------------------------------- R1 Cognito


def ensure_user_pool(cognito, names: Names, users: list[str]) -> dict[str, Any]:
    pool_id = None
    for page in cognito.get_paginator("list_user_pools").paginate(MaxResults=60):
        for pool in page.get("UserPools", []):
            if pool["Name"] == POOL_NAME:
                pool_id = pool["Id"]
    if pool_id is None:
        pool_id = cognito.create_user_pool(
            PoolName=POOL_NAME,
            AdminCreateUserConfig={"AllowAdminCreateUserOnly": True},
            Policies={
                "PasswordPolicy": {
                    "MinimumLength": 12,
                    "RequireUppercase": True,
                    "RequireLowercase": True,
                    "RequireNumbers": True,
                    "RequireSymbols": False,
                }
            },
            UsernameConfiguration={"CaseSensitive": False},
            MfaConfiguration="OFF",
            UserPoolTags=names.tags,
        )["UserPool"]["Id"]
        log(f"created user pool {pool_id}")

    domain = cognito.describe_user_pool_domain(Domain=names.domain).get(
        "DomainDescription", {}
    )
    if not domain.get("UserPoolId"):
        cognito.create_user_pool_domain(Domain=names.domain, UserPoolId=pool_id)
        log(f"created hosted domain {names.domain}")
    elif domain["UserPoolId"] != pool_id:
        raise RuntimeError(f"domain {names.domain} belongs to another user pool")

    clients = {}
    for page in cognito.get_paginator("list_user_pool_clients").paginate(
        UserPoolId=pool_id, MaxResults=60
    ):
        for client in page.get("UserPoolClients", []):
            clients[client["ClientName"]] = client["ClientId"]
    common = {
        "UserPoolId": pool_id,
        "PreventUserExistenceErrors": "ENABLED",
        "AccessTokenValidity": 60,
        "IdTokenValidity": 60,
        "RefreshTokenValidity": 7,
        "TokenValidityUnits": {
            "AccessToken": "minutes",
            "IdToken": "minutes",
            "RefreshToken": "days",
        },
        "EnableTokenRevocation": True,
    }
    mac = {
        **common,
        "ExplicitAuthFlows": ["ALLOW_REFRESH_TOKEN_AUTH"],
        "AllowedOAuthFlows": ["code"],
        "AllowedOAuthScopes": ["openid", "email", "profile"],
        "AllowedOAuthFlowsUserPoolClient": True,
        "CallbackURLs": [MAC_CALLBACK],
        "LogoutURLs": [MAC_SIGNOUT],
        "SupportedIdentityProviders": ["COGNITO"],
    }
    cli = {
        **common,
        "ExplicitAuthFlows": ["ALLOW_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"],
    }
    for client_name, config in ((MAC_CLIENT, mac), (CLI_CLIENT, cli)):
        if client_name in clients:
            cognito.update_user_pool_client(
                ClientId=clients[client_name], ClientName=client_name, **config
            )
        else:
            clients[client_name] = cognito.create_user_pool_client(
                ClientName=client_name, GenerateSecret=False, **config
            )["UserPoolClient"]["ClientId"]
            log(f"created app client {client_name}")

    for username in users:
        try:
            cognito.admin_get_user(UserPoolId=pool_id, Username=username)
        except cognito.exceptions.UserNotFoundException:
            # No TemporaryPassword and no message: the account cannot sign in
            # until its owner sets a permanent password.
            cognito.admin_create_user(
                UserPoolId=pool_id, Username=username, MessageAction="SUPPRESS"
            )
            log(f"created test user {username}")

    issuer = f"https://cognito-idp.{names.region}.amazonaws.com/{pool_id}"
    return {
        "user_pool_id": pool_id,
        "cognito_issuer": issuer,
        "hosted_domain": f"https://{names.domain}.auth.{names.region}.amazoncognito.com",
        "mac_client_id": clients[MAC_CLIENT],
        "cli_client_id": clients[CLI_CLIENT],
        "test_users": users,
    }


# ---------------------------------------------------------------- R2 Memory


def ensure_memory(control, names: Names) -> dict[str, Any]:
    memory_id = None
    for page in control.get_paginator("list_memories").paginate():
        for item in page.get("memories", []):
            if item.get("id", "").startswith(f"{MEMORY_NAME}-"):
                memory_id = item["id"]
    if memory_id is None:
        memory_id = control.create_memory(
            name=MEMORY_NAME,
            description="R&D team agent: per-user sessions and long-term memory",
            eventExpiryDuration=30,
            memoryStrategies=[
                {
                    "userPreferenceMemoryStrategy": {
                        "name": "preferences",
                        "namespaces": [ACTOR_NAMESPACE],
                    }
                },
                {
                    "semanticMemoryStrategy": {
                        "name": "facts",
                        "namespaces": [ACTOR_NAMESPACE],
                    }
                },
                {
                    "summaryMemoryStrategy": {
                        "name": "summaries",
                        "namespaces": [SESSION_NAMESPACE],
                    }
                },
            ],
            tags=names.tags,
        )["memory"]["id"]
        log(f"created memory {memory_id}")
    memory = wait(
        "memory",
        lambda: (
            (m := control.get_memory(memoryId=memory_id)["memory"])["status"],
            m,
        ),
        ready={"ACTIVE"},
        failed={"FAILED", "DELETING"},
    )
    strategies = {s["type"]: s["strategyId"] for s in memory.get("strategies", [])}
    missing = sorted(set(STRATEGY_ENV) - set(strategies))
    if missing:
        raise RuntimeError(f"memory {memory_id} lacks strategies {missing}")
    return {
        "memory_id": memory_id,
        "memory_arn": memory["arn"],
        "memory_strategies": {STRATEGY_ENV[k]: strategies[k] for k in STRATEGY_ENV},
    }


# ---------------------------------------------------------------- R3 bucket


def ensure_bucket(s3, names: Names) -> dict[str, Any]:
    try:
        s3.head_bucket(Bucket=names.bucket, ExpectedBucketOwner=names.account)
    except s3.exceptions.ClientError as error:
        if error.response.get("Error", {}).get("Code") not in {"404", "NoSuchBucket"}:
            raise
        s3.create_bucket(Bucket=names.bucket, ObjectOwnership="BucketOwnerEnforced")
        log(f"created bucket {names.bucket}")
    s3.put_public_access_block(
        Bucket=names.bucket,
        ExpectedBucketOwner=names.account,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )
    s3.put_bucket_encryption(
        Bucket=names.bucket,
        ExpectedBucketOwner=names.account,
        ServerSideEncryptionConfiguration={
            "Rules": [
                {"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}
            ]
        },
    )
    s3.put_bucket_policy(
        Bucket=names.bucket,
        ExpectedBucketOwner=names.account,
        Policy=json.dumps(bucket_policy(names)),
    )
    s3.put_bucket_tagging(
        Bucket=names.bucket,
        ExpectedBucketOwner=names.account,
        Tagging={"TagSet": [{"Key": k, "Value": v} for k, v in names.tags.items()]},
    )
    return {"results_bucket": names.bucket}


# ---------------------------------------------------------------- R5 role


def validate_policy(analyzer, policy: dict[str, Any]) -> None:
    findings = analyzer.validate_policy(
        policyDocument=json.dumps(policy), policyType="IDENTITY_POLICY"
    ).get("findings", [])
    blocking = [
        f for f in findings if f["findingType"] in {"ERROR", "SECURITY_WARNING"}
    ]
    for finding in findings:
        log(f"ACCESS ANALYZER {finding['findingType']}: {finding['issueCode']}")
    if blocking:
        raise RuntimeError(f"runtime policy has {len(blocking)} blocking finding(s)")


def ensure_role(iam, analyzer, names: Names, policy: dict[str, Any]) -> dict[str, Any]:
    validate_policy(analyzer, policy)
    try:
        arn = iam.get_role(RoleName=names.role)["Role"]["Arn"]
        iam.update_assume_role_policy(
            RoleName=names.role, PolicyDocument=json.dumps(trust_policy(names))
        )
    except iam.exceptions.NoSuchEntityException:
        arn = iam.create_role(
            RoleName=names.role,
            Path=ROLE_PATH,
            AssumeRolePolicyDocument=json.dumps(trust_policy(names)),
            Description="Execution role for the rd-team-agent AgentCore Runtime",
            Tags=[{"Key": k, "Value": v} for k, v in names.tags.items()],
        )["Role"]["Arn"]
        log(f"created role {arn}")
    iam.put_role_policy(
        RoleName=names.role, PolicyName=POLICY_NAME, PolicyDocument=json.dumps(policy)
    )
    return {"runtime_role_arn": arn}


# ---------------------------------------------------------------- R6 Runtime


def stage(package_dir: Path, manifest: Path) -> None:
    shutil.copy2(PROJECT_ROOT / ENTRY_FILE, package_dir / ENTRY_FILE)
    for source in sorted(SRC.rglob("*.py")):
        if _skip(source) or source.is_symlink():
            continue
        target = package_dir / "src" / source.relative_to(SRC)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    (package_dir / "contracts").mkdir()
    shutil.copy2(PROJECT_ROOT / "contracts" / CONTRACT_FILE, package_dir / "contracts")
    (package_dir / "source-manifests").mkdir()
    shutil.copy2(manifest, package_dir / "source-manifests" / manifest.name)


def build_package(build_dir: Path, manifest: Path) -> dict[str, Any]:
    if build_dir.exists():
        shutil.rmtree(build_dir)
    package_dir = build_dir / "package"
    package_dir.mkdir(parents=True)
    install_dependencies(PROJECT_ROOT, package_dir)
    stage(package_dir, manifest)
    return write_zip(package_dir, build_dir / "orchestrator_package.zip")


def deploy_runtime(
    session, names: Names, *, package: dict, role_arn: str, cognito: dict, env: dict
) -> dict[str, Any]:
    control = session.client("bedrock-agentcore-control")
    key = f"runtime/{package['sha256'][:16]}.zip"
    session.client("s3").upload_file(
        package["zip_path"],
        names.bucket,
        key,
        ExtraArgs={
            "ExpectedBucketOwner": names.account,
            "ServerSideEncryption": "AES256",
        },
    )
    common = {
        "agentRuntimeArtifact": {
            "codeConfiguration": {
                "code": {"s3": {"bucket": names.bucket, "prefix": key}},
                "runtime": RUNTIME_LANGUAGE,
                "entryPoint": [ENTRY_FILE],
            }
        },
        "roleArn": role_arn,
        "networkConfiguration": {"networkMode": "PUBLIC"},
        "protocolConfiguration": {"serverProtocol": "HTTP"},
        "authorizerConfiguration": {
            "customJWTAuthorizer": {
                "discoveryUrl": (
                    f"{cognito['cognito_issuer']}/.well-known/openid-configuration"
                ),
                "allowedClients": [cognito["mac_client_id"], cognito["cli_client_id"]],
            }
        },
        "requestHeaderConfiguration": {"requestHeaderAllowlist": ["Authorization"]},
        "environmentVariables": env,
        "description": "R&D team agent orchestrator (JWT inbound)",
    }
    existing = _find_runtime(control, RUNTIME_NAME)
    if existing:
        runtime_id = existing["agentRuntimeId"]
        _retry_iam_propagation(
            lambda: control.update_agent_runtime(agentRuntimeId=runtime_id, **common)
        )
    else:
        runtime_id = _retry_iam_propagation(
            lambda: control.create_agent_runtime(
                agentRuntimeName=RUNTIME_NAME, tags=names.tags, **common
            )
        )["agentRuntimeId"]
        log(f"created runtime {runtime_id}")
    runtime = _wait_ready(control, runtime_id)
    return {
        "runtime_id": runtime_id,
        "runtime_arn": runtime["agentRuntimeArn"],
        "runtime_version": runtime["agentRuntimeVersion"],
        "runtime_artifact_key": key,
        "runtime_artifact_sha256": package["sha256"],
    }


def runtime_env(args, cognito: dict, memory: dict, names: Names, version: str) -> dict:
    return {
        "AWS_REGION": names.region,
        "AWS_ACCOUNT_ID": names.account,
        "BEDROCK_MODEL_ID": args.model_id,
        "KNOWLEDGE_BASE_ID": args.knowledge_base_id,
        "TASK_DATA_SOURCE_ID": args.task_data_source_id,
        "PROJECT_ID": args.project_id,
        "REPOSITORY_NAME": args.repository_name,
        "REPOSITORY_COMMIT": args.repository_commit,
        "PRD_CONTRACT_VERSION": args.prd_contract_version,
        "MEMORY_ID": memory["memory_id"],
        **memory["memory_strategies"],
        "RESULTS_BUCKET": names.bucket,
        "COGNITO_ISSUER": cognito["cognito_issuer"],
        "ALLOWED_CLIENT_IDS": f"{cognito['mac_client_id']},{cognito['cli_client_id']}",
        "PROMPT_VERSION": args.prompt_version,
        "RUNTIME_VERSION": version,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--profile")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--expected-account", required=True)
    parser.add_argument("--model-id", default="us.anthropic.claude-opus-5-5")
    parser.add_argument("--knowledge-base-id", required=True)
    parser.add_argument("--task-data-source-id", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--repository-name", required=True)
    parser.add_argument("--repository-commit", required=True)
    parser.add_argument("--prd-contract-version", default="mock-csv-import-v0.1")
    parser.add_argument("--prompt-version", default="orchestrator-v1")
    parser.add_argument("--test-users", default="tester1,tester2,tester3")
    parser.add_argument(
        "--manifest-root", type=Path, default=PROJECT_ROOT / "deploy" / "out"
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=PROJECT_ROOT / "deploy" / "out" / "rd-team-agent.json",
    )
    parser.add_argument(
        "--build-dir", type=Path, default=PROJECT_ROOT / "build" / "orchestrator"
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    import boto3

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    account = session.client("sts").get_caller_identity()["Account"]
    if account != args.expected_account:
        raise RuntimeError(
            f"AWS account mismatch: {account} != {args.expected_account}"
        )
    names = Names(account, args.region)
    manifest = FileSourceManifestProvider(args.manifest_root).path_for(
        project_id=args.project_id,
        repository=args.repository_name,
        commit_sha=args.repository_commit,
    )
    if not manifest.is_file():
        raise RuntimeError(f"run sync_code_repo.py --apply first; missing {manifest}")
    users = [u.strip() for u in args.test_users.split(",") if u.strip()]
    plan = {
        "user_pool": POOL_NAME,
        "app_clients": [MAC_CLIENT, CLI_CLIENT],
        "hosted_domain": names.domain,
        "test_users": users,
        "memory": MEMORY_NAME,
        "results_bucket": names.bucket,
        "runtime_role": names.role,
        "runtime": RUNTIME_NAME,
        "model_id": args.model_id,
        "knowledge_base_id": args.knowledge_base_id,
        "repository": f"{args.repository_name}@{args.repository_commit[:12]}",
        "manifest": str(manifest),
    }
    print(
        json.dumps(
            {"mode": "apply" if args.apply else "dry-run", "plan": plan}, indent=2
        )
    )
    if not args.apply:
        return 0

    state: dict[str, Any] = {"account_id": account, "region": args.region}
    state.update(ensure_user_pool(session.client("cognito-idp"), names, users))
    state.update(ensure_memory(session.client("bedrock-agentcore-control"), names))
    state.update(ensure_bucket(session.client("s3"), names))
    policy = runtime_policy(
        names,
        model_id=args.model_id,
        knowledge_base_id=args.knowledge_base_id,
        memory_arn=state["memory_arn"],
    )
    state.update(
        ensure_role(
            session.client("iam"), session.client("accessanalyzer"), names, policy
        )
    )
    package = build_package(args.build_dir, manifest)
    version = f"orchestrator-{package['sha256'][:12]}"
    env = runtime_env(args, state, state, names, version)
    # Fail closed locally on any setting the Runtime would reject at start.
    load_orchestrator_settings(
        {
            **env,
            "PRD_CONTRACT_PATH": "/opt/contract.json",
            "SOURCE_MANIFEST_ROOT": "/opt",
        }
    )
    state.update(
        deploy_runtime(
            session,
            names,
            package=package,
            role_arn=state["runtime_role_arn"],
            cognito=state,
            env=env,
        )
    )
    state["runtime_environment"] = env
    args.state_file.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.state_file.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temporary, args.state_file)
    digest = hashlib.sha256(args.state_file.read_bytes()).hexdigest()[:12]
    print(
        json.dumps(
            {"state_file": str(args.state_file), "sha256": digest, **state}, indent=2
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
