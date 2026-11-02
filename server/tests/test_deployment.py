from __future__ import annotations

import unittest

from pydantic import ValidationError

from prd_agent.deployment import (
    DeploymentPlan,
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


class DeploymentPlanTest(unittest.TestCase):
    def plan(self) -> DeploymentPlan:
        return DeploymentPlan(
            account_id="034362076319",
            region="us-east-1",
            prefix="prd-agent-mock-e2e",
        )

    def test_names_are_isolated_and_plan_has_no_deletes(self) -> None:
        plan = self.plan()
        summary = plan.summary()
        self.assertEqual(
            plan.bucket_name,
            "prd-agent-mock-e2e-034362076319-us-east-1",
        )
        self.assertEqual(plan.runtime_name, "prd_agent_mock_e2e_direct")
        self.assertEqual(summary["task_data_source_name"], "prd-agent-mock-e2e-custom")
        self.assertEqual(summary["deletion_operations"], [])
        self.assertEqual(summary["model_id"], "us.anthropic.claude-opus-4-6-v1")
        self.assertEqual(summary["tags"]["Environment"], "test-in-production-account")

    def test_prefix_and_account_are_validated(self) -> None:
        with self.assertRaises(ValidationError):
            DeploymentPlan(
                account_id="123",
                region="us-east-1",
                prefix="prd-agent-mock-e2e",
            )
        with self.assertRaisesRegex(ValidationError, "lowercase"):
            DeploymentPlan(
                account_id="034362076319",
                region="us-east-1",
                prefix="Bad_Prefix",
            )

    def test_trust_policies_are_account_and_resource_scoped(self) -> None:
        plan = self.plan()
        for policy, service, resource_kind in (
            (knowledge_base_trust(plan), "bedrock.amazonaws.com", "knowledge-base/*"),
            (gateway_trust(plan), "bedrock-agentcore.amazonaws.com", "gateway/*"),
            (runtime_trust(plan), "bedrock-agentcore.amazonaws.com", "runtime/*"),
        ):
            statement = policy["Statement"][0]
            self.assertEqual(statement["Principal"]["Service"], service)
            self.assertEqual(
                statement["Condition"]["StringEquals"]["aws:SourceAccount"],
                "034362076319",
            )
            self.assertIn(
                resource_kind,
                statement["Condition"]["ArnLike"]["aws:SourceArn"],
            )

    def test_knowledge_base_role_can_only_read_dedicated_bucket(self) -> None:
        policy = knowledge_base_permissions(self.plan())
        serialized = str(policy)
        self.assertIn("prd-agent-mock-e2e-034362076319-us-east-1", serialized)
        self.assertIn("s3:ListBucket", serialized)
        self.assertIn("s3:GetObject", serialized)
        self.assertNotIn("s3:PutObject", serialized)
        self.assertNotIn("s3:DeleteObject", serialized)
        self.assertNotIn("Resource': '*'", serialized)

    def test_gateway_role_is_scoped_to_one_kb(self) -> None:
        policy = gateway_permissions(self.plan(), "KB12345678")
        statement = policy["Statement"][0]
        self.assertEqual(
            statement["Action"],
            ["bedrock:GetKnowledgeBase", "bedrock:Retrieve"],
        )
        self.assertEqual(
            statement["Resource"],
            "arn:aws:bedrock:us-east-1:034362076319:knowledge-base/KB12345678",
        )

    def test_runtime_role_only_invokes_opus_and_its_own_knowledge_base(self) -> None:
        policy = runtime_permissions(self.plan(), "KB12345678")
        serialized = str(policy)
        self.assertIn("anthropic.claude-opus-4-6-v1", serialized)
        self.assertNotIn("iam:", serialized)
        self.assertNotIn("InvokeGateway", serialized)
        without_task_cleanup = serialized.replace("DeleteKnowledgeBaseDocuments", "")
        self.assertNotIn("bedrock:Delete", without_task_cleanup)
        kb_arn = "arn:aws:bedrock:us-east-1:034362076319:knowledge-base/KB12345678"
        by_sid = {statement["Sid"]: statement for statement in policy["Statement"]}
        self.assertEqual(
            by_sid["RetrieveTaskScopedEvidence"]["Action"], "bedrock:Retrieve"
        )
        self.assertEqual(
            by_sid["ManageTaskDocuments"]["Action"],
            [
                "bedrock:StartIngestionJob",
                "bedrock:IngestKnowledgeBaseDocuments",
                "bedrock:GetKnowledgeBaseDocuments",
                "bedrock:DeleteKnowledgeBaseDocuments",
            ],
        )
        for sid in ("RetrieveTaskScopedEvidence", "ManageTaskDocuments"):
            self.assertEqual(by_sid[sid]["Resource"], kb_arn)
        wildcard_statements = [
            statement
            for statement in policy["Statement"]
            if statement["Resource"] == "*"
        ]
        self.assertEqual(
            {statement["Sid"] for statement in wildcard_statements},
            {"WriteRuntimeTraces", "WriteAgentCoreMetrics"},
        )
        metrics = next(
            statement
            for statement in wildcard_statements
            if statement["Sid"] == "WriteAgentCoreMetrics"
        )
        self.assertEqual(
            metrics["Condition"],
            {"StringEquals": {"cloudwatch:namespace": "bedrock-agentcore"}},
        )

    def test_task_data_source_is_a_custom_connector(self) -> None:
        config = managed_custom_data_source_configuration()
        self.assertEqual(config["type"], "MANAGED_KNOWLEDGE_BASE_CONNECTOR")
        self.assertEqual(
            config["managedKnowledgeBaseConnectorConfiguration"]["connectorParameters"],
            {"type": "CUSTOM", "version": "1", "aclEnabled": False},
        )

    def test_managed_kb_and_s3_connector_are_native(self) -> None:
        self.assertEqual(managed_knowledge_base_configuration()["type"], "MANAGED")
        config = managed_s3_data_source_configuration(self.plan())
        self.assertEqual(config["type"], "MANAGED_KNOWLEDGE_BASE_CONNECTOR")
        connector = config["managedKnowledgeBaseConnectorConfiguration"]
        protection = connector["deletionProtectionConfiguration"]
        self.assertEqual(protection["deletionProtectionStatus"], "ENABLED")
        self.assertEqual(protection["deletionProtectionThreshold"], 20)
        filters = connector["connectorParameters"]["filterConfiguration"]
        self.assertEqual(filters, {"inclusionPrefixes": ["content/"]})

    def test_gateway_target_binds_kb_and_filter_server_side(self) -> None:
        config = gateway_target_configuration(
            self.plan(),
            "KB12345678",
            "source-current",
        )
        retrieve = config["mcp"]["connector"]["configurations"][0]
        values = retrieve["parameterValues"]
        self.assertEqual(values["knowledgeBaseId"], "KB12345678")
        managed = values["retrievalConfiguration"]["managedSearchConfiguration"]
        self.assertEqual(managed["numberOfResults"], 10)
        self.assertEqual(
            managed["filter"],
            {
                "orAll": [
                    {
                        "andAll": [
                            {"equals": {"key": "project_id", "value": "mock-prd-e2e"}},
                            {"equals": {"key": "source_type", "value": "document"}},
                        ]
                    },
                    {
                        "andAll": [
                            {"equals": {"key": "project_id", "value": "mock-prd-e2e"}},
                            {"equals": {"key": "source_type", "value": "code"}},
                            {"equals": {"key": "commit_sha", "value": "source-current"}},
                        ]
                    },
                ]
            },
        )
        override_paths = {item["path"] for item in retrieve["parameterOverrides"]}
        self.assertEqual(
            override_paths,
            {"$.retrievalQuery.text", "$.userContext"},
        )


if __name__ == "__main__":
    unittest.main()
