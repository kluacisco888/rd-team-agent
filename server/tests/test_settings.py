from __future__ import annotations

import unittest

from pydantic import ValidationError

from prd_agent.settings import DeploymentEnvironment, load_settings


class SettingsTest(unittest.TestCase):
    def environment(self, **updates: str) -> dict[str, str]:
        values = {
            "DEPLOYMENT_ENV": "test",
            "AWS_ACCOUNT_ID": "123456789012",
            "AWS_REGION": "us-east-1",
            "BEDROCK_MODEL_ID": "us.anthropic.claude-opus-4-6-v1",
            "KNOWLEDGE_BASE_ID": "MGDYVQP2FE",
            "TASK_DATA_SOURCE_ID": "JGWXFQIQRL",
            "PROJECT_ID": "mock-prd-e2e",
            "REPOSITORY_NAME": "agentcore-prd-agent",
            "PRD_CONTRACT_PATH": "/app/contracts/prd-v1.json",
            "PROMPT_VERSION": "prompt-v1",
            "RUNTIME_VERSION": "runtime-v1",
        }
        values.update(updates)
        return values

    def test_loads_explicit_secret_free_configuration(self) -> None:
        settings = load_settings(self.environment())
        self.assertEqual(settings.environment, DeploymentEnvironment.TEST)
        self.assertEqual(settings.aws_account_id, "123456789012")
        self.assertEqual(settings.model_id, "us.anthropic.claude-opus-4-6-v1")
        self.assertEqual(settings.model_read_timeout_seconds, 600)
        self.assertEqual(settings.retrieval_top_k, 10)
        self.assertEqual(settings.retrieval_max_queries, 12)
        self.assertFalse(settings.allow_global_inference)
        self.assertEqual(settings.knowledge_base_id, "MGDYVQP2FE")
        self.assertEqual(settings.task_data_source_id, "JGWXFQIQRL")
        dumped = settings.model_dump(mode="json")
        forbidden_keys = {
            "access_token",
            "client_secret",
            "aws_access_key_id",
            "aws_secret_access_key",
            "aws_session_token",
        }
        self.assertTrue(forbidden_keys.isdisjoint(dumped))

    def test_missing_required_keys_are_reported_together(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "AWS_ACCOUNT_ID.*KNOWLEDGE_BASE_ID.*TASK_DATA_SOURCE_ID",
        ):
            load_settings({"DEPLOYMENT_ENV": "test"})

    def test_environment_and_account_are_strictly_validated(self) -> None:
        with self.assertRaises(ValidationError):
            load_settings(self.environment(DEPLOYMENT_ENV="staging"))
        with self.assertRaises(ValidationError):
            load_settings(self.environment(AWS_ACCOUNT_ID="123"))

    def test_only_opus_46_model_ids_are_accepted(self) -> None:
        with self.assertRaises(ValidationError):
            load_settings(
                self.environment(BEDROCK_MODEL_ID="anthropic.other-model-v1")
            )

    def test_knowledge_base_ids_and_runtime_paths_are_safe(self) -> None:
        for key in ("KNOWLEDGE_BASE_ID", "TASK_DATA_SOURCE_ID"):
            for value in ("short", "MGDYVQP2FE/x", "MGDYVQP2FE1"):
                with (
                    self.subTest(key=key, value=value),
                    self.assertRaises(ValidationError),
                ):
                    load_settings(self.environment(**{key: value}))
        with self.assertRaisesRegex(ValidationError, "must be absolute"):
            load_settings(self.environment(PRD_CONTRACT_PATH="contracts/prd.json"))

    def test_global_inference_requires_explicit_acknowledgement(self) -> None:
        global_environment = self.environment(
            BEDROCK_MODEL_ID="global.anthropic.claude-opus-4-6-v1"
        )
        with self.assertRaisesRegex(ValidationError, "explicit"):
            load_settings(global_environment)

        global_environment["ALLOW_GLOBAL_INFERENCE"] = "true"
        settings = load_settings(global_environment)
        self.assertTrue(settings.allow_global_inference)

    def test_optional_numeric_and_boolean_values_are_strict(self) -> None:
        settings = load_settings(
            self.environment(
                RETRIEVAL_TOP_K="25",
                RETRIEVAL_MAX_QUERIES="8",
                ALLOW_GLOBAL_INFERENCE="false",
            )
        )
        self.assertEqual(settings.retrieval_top_k, 25)
        self.assertEqual(settings.retrieval_max_queries, 8)

        with self.assertRaisesRegex(ValueError, "must be an integer"):
            load_settings(self.environment(RETRIEVAL_TOP_K="many"))
        with self.assertRaisesRegex(ValueError, "must be true or false"):
            load_settings(self.environment(ALLOW_GLOBAL_INFERENCE="maybe"))


if __name__ == "__main__":
    unittest.main()
