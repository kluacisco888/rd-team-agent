from __future__ import annotations

import unittest
from pathlib import Path

from pydantic import ValidationError

from prd_agent.deployment import (
    MANAGED_KNOWLEDGE_BASE_REGIONS,
    DeploymentPlan,
    RegionSupportError,
    assert_region_supported,
    default_model_id,
    model_invocation_resources,
    runtime_environment,
    runtime_permissions,
)
from prd_agent.settings import load_settings

ACCOUNT = "111122223333"


def plan(region: str, model_id: str, *, allow_global: bool = False) -> DeploymentPlan:
    return DeploymentPlan(
        account_id=ACCOUNT,
        region=region,
        prefix="prd-agent-mock-e2e",
        model_id=model_id,
        allow_global_inference=allow_global,
    )


class RegionSupportTest(unittest.TestCase):
    def test_singapore_is_rejected_because_managed_kb_is_unavailable(self) -> None:
        with self.assertRaisesRegex(RegionSupportError, "managed knowledge base"):
            assert_region_supported("ap-southeast-1")

    def test_every_listed_region_resolves_a_model(self) -> None:
        for region in MANAGED_KNOWLEDGE_BASE_REGIONS:
            assert_region_supported(region)
            model_id = default_model_id(region, allow_global_inference=True)
            self.assertTrue(model_id.endswith("anthropic.claude-opus-4-6-v1"))

    def test_geo_profiles_are_preferred_over_global(self) -> None:
        self.assertEqual(
            default_model_id("ap-southeast-2", allow_global_inference=True),
            "au.anthropic.claude-opus-4-6-v1",
        )
        self.assertEqual(
            default_model_id("eu-west-2", allow_global_inference=False),
            "eu.anthropic.claude-opus-4-6-v1",
        )
        self.assertEqual(
            default_model_id("us-west-2", allow_global_inference=False),
            "us.anthropic.claude-opus-4-6-v1",
        )

    def test_global_only_region_requires_explicit_opt_in(self) -> None:
        with self.assertRaisesRegex(RegionSupportError, "allow-global-inference"):
            default_model_id("ap-northeast-1", allow_global_inference=False)
        self.assertEqual(
            default_model_id("ap-northeast-1", allow_global_inference=True),
            "global.anthropic.claude-opus-4-6-v1",
        )

    def test_plan_rejects_global_model_without_opt_in(self) -> None:
        with self.assertRaisesRegex(ValidationError, "global inference"):
            plan("ap-northeast-1", "global.anthropic.claude-opus-4-6-v1")

    def test_plan_rejects_malformed_region(self) -> None:
        with self.assertRaisesRegex(ValidationError, "Region code"):
            plan("Sydney", "au.anthropic.claude-opus-4-6-v1")


class RegionalIamTest(unittest.TestCase):
    def test_role_names_are_scoped_by_prefix_and_region(self) -> None:
        sydney = plan("ap-southeast-2", "au.anthropic.claude-opus-4-6-v1")
        tokyo = plan(
            "ap-northeast-1", "global.anthropic.claude-opus-4-6-v1", allow_global=True
        )
        self.assertEqual(
            sydney.runtime_role_name, "prd-agent-mock-e2e-ap-southeast-2-runtime"
        )
        self.assertNotEqual(sydney.kb_role_name, tokyo.kb_role_name)
        for name in (
            sydney.kb_role_name,
            sydney.gateway_role_name,
            sydney.runtime_role_name,
        ):
            self.assertLessEqual(len(name), 64)

    def test_geo_profile_resources(self) -> None:
        resources = model_invocation_resources(
            plan("ap-southeast-2", "au.anthropic.claude-opus-4-6-v1")
        )
        self.assertEqual(
            resources,
            [
                "arn:aws:bedrock:*::foundation-model/anthropic.claude-opus-4-6-v1",
                f"arn:aws:bedrock:ap-southeast-2:{ACCOUNT}:inference-profile/"
                "au.anthropic.claude-opus-4-6-v1",
            ],
        )

    def test_global_profile_includes_regionless_model_arn(self) -> None:
        resources = model_invocation_resources(
            plan(
                "ap-northeast-1",
                "global.anthropic.claude-opus-4-6-v1",
                allow_global=True,
            )
        )
        self.assertIn(
            "arn:aws:bedrock:::foundation-model/anthropic.claude-opus-4-6-v1", resources
        )
        self.assertIn(
            f"arn:aws:bedrock:ap-northeast-1:{ACCOUNT}:inference-profile/"
            "global.anthropic.claude-opus-4-6-v1",
            resources,
        )
        self.assertFalse(any("us." in resource for resource in resources))

    def test_runtime_can_read_only_its_own_package_prefix(self) -> None:
        sydney = plan("ap-southeast-2", "au.anthropic.claude-opus-4-6-v1")
        policy = runtime_permissions(sydney, "KB12345678")
        statement = next(
            s for s in policy["Statement"] if s["Sid"] == "ReadRuntimeDeploymentPackage"
        )
        self.assertEqual(statement["Action"], "s3:GetObject")
        self.assertEqual(
            statement["Resource"],
            f"arn:aws:s3:::{sydney.bucket_name}/runtime/prd_agent_mock_e2e_direct/*",
        )
        self.assertFalse(sydney.runtime_artifact_prefix.startswith("content/"))


class RuntimeEnvironmentTest(unittest.TestCase):
    def test_environment_loads_into_validated_settings(self) -> None:
        tokyo = plan(
            "ap-northeast-1", "global.anthropic.claude-opus-4-6-v1", allow_global=True
        )
        environment = runtime_environment(
            tokyo,
            environment="development",
            knowledge_base_id="KB12345678",
            task_data_source_id="DS12345678",
            prompt_version="mock-v5",
            runtime_version="mock-abc",
        )
        self.assertNotIn("PRD_CONTRACT_PATH", environment)
        settings = load_settings(
            {
                **environment,
                "PRD_CONTRACT_PATH": str(Path("/var/task/contracts/c.json")),
            }
        )
        self.assertEqual(settings.model_id, "global.anthropic.claude-opus-4-6-v1")
        self.assertTrue(settings.allow_global_inference)
        self.assertEqual(settings.aws_region, "ap-northeast-1")
        self.assertEqual(settings.task_data_source_id, "DS12345678")
        self.assertFalse(
            any(key.startswith("AGENTCORE_GATEWAY") for key in environment)
        )


if __name__ == "__main__":
    unittest.main()
