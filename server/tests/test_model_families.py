from __future__ import annotations

import unittest

from pydantic import ValidationError

from prd_agent.contracts import VersionSet
from prd_agent.deployment import (
    DeploymentPlan,
    RegionSupportError,
    default_model_id,
    foundation_model_id,
    model_invocation_resources,
)
from prd_agent.settings import load_settings

ACCOUNT = "111122223333"


class Opus55SelectionTest(unittest.TestCase):
    def test_tokyo_gets_japan_geo_profile_without_global(self) -> None:
        self.assertEqual(
            default_model_id(
                "ap-northeast-1", allow_global_inference=False, family="opus-5-5"
            ),
            "jp.anthropic.claude-opus-5-5",
        )

    def test_sydney_gets_australia_geo_profile(self) -> None:
        self.assertEqual(
            default_model_id(
                "ap-southeast-2", allow_global_inference=False, family="opus-5-5"
            ),
            "au.anthropic.claude-opus-5-5",
        )

    def test_default_family_is_unchanged(self) -> None:
        with self.assertRaises(RegionSupportError):
            default_model_id("ap-northeast-1", allow_global_inference=False)

    def test_unknown_family_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown model family"):
            default_model_id("us-east-1", allow_global_inference=False, family="opus-9")

    def test_profile_prefix_is_stripped(self) -> None:
        self.assertEqual(
            foundation_model_id("jp.anthropic.claude-opus-5-5"),
            "anthropic.claude-opus-5-5",
        )
        self.assertEqual(
            foundation_model_id("anthropic.claude-opus-4-6-v1"),
            "anthropic.claude-opus-4-6-v1",
        )


class Opus55IamAndSettingsTest(unittest.TestCase):
    def test_runtime_may_invoke_only_opus_55(self) -> None:
        plan = DeploymentPlan(
            account_id=ACCOUNT,
            region="ap-northeast-1",
            prefix="prd-agent-mock-e2e",
            model_id="jp.anthropic.claude-opus-5-5",
        )
        self.assertEqual(
            model_invocation_resources(plan),
            [
                "arn:aws:bedrock:*::foundation-model/anthropic.claude-opus-5-5",
                f"arn:aws:bedrock:ap-northeast-1:{ACCOUNT}:inference-profile/"
                "jp.anthropic.claude-opus-5-5",
            ],
        )

    def test_settings_and_version_set_accept_opus_55(self) -> None:
        settings = load_settings(
            {
                "DEPLOYMENT_ENV": "development",
                "AWS_ACCOUNT_ID": ACCOUNT,
                "AWS_REGION": "ap-southeast-2",
                "BEDROCK_MODEL_ID": "au.anthropic.claude-opus-5-5",
                "KNOWLEDGE_BASE_ID": "KB12345678",
                "TASK_DATA_SOURCE_ID": "DS12345678",
                "PROJECT_ID": "p",
                "REPOSITORY_NAME": "r",
                "PRD_CONTRACT_PATH": "/var/task/contracts/c.json",
                "PROMPT_VERSION": "v",
                "RUNTIME_VERSION": "v",
            }
        )
        self.assertEqual(settings.model_id, "au.anthropic.claude-opus-5-5")
        VersionSet(
            knowledge_base_snapshot="k",
            repository_commit="c",
            prd_contract_version="p",
            prompt_version="v",
            runtime_version="v",
            model_id="jp.anthropic.claude-opus-5-5",
        )

    def test_unlisted_models_are_still_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            DeploymentPlan(
                account_id=ACCOUNT,
                region="us-east-1",
                prefix="prd-agent-mock-e2e",
                model_id="us.anthropic.claude-sonnet-4-6",
            )


if __name__ == "__main__":
    unittest.main()
