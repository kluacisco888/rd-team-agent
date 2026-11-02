from __future__ import annotations

import unittest

from prd_agent.validation import (
    validate_claims,
    validate_clarification_questions,
    validate_code_impacts,
    validate_generation_payload,
)


class ClaimValidationTest(unittest.TestCase):
    def test_confirmed_claim_requires_citation(self) -> None:
        issues = validate_claims(
            [{"status": "confirmed", "text": "Existing behavior", "citations": []}],
            retrieved_chunk_ids=[],
        )
        self.assertEqual([issue.rule_id for issue in issues], ["evidence.citations.required"])

    def test_citation_must_come_from_current_retrieval(self) -> None:
        issues = validate_claims(
            [
                {
                    "status": "confirmed",
                    "text": "Existing behavior",
                    "citations": [{"chunk_id": "invented"}],
                }
            ],
            retrieved_chunk_ids=["actual"],
        )
        self.assertEqual(
            [issue.rule_id for issue in issues],
            ["evidence.chunk_id.not_retrieved"],
        )

    def test_valid_confirmed_claim_passes(self) -> None:
        issues = validate_claims(
            [
                {
                    "status": "confirmed",
                    "text": "Existing behavior",
                    "citations": [{"chunk_id": "chunk-1"}],
                }
            ],
            retrieved_chunk_ids=["chunk-1"],
        )
        self.assertEqual(issues, [])


class ClarificationValidationTest(unittest.TestCase):
    def test_question_budget_and_blocked_fields_are_enforced(self) -> None:
        questions = [
            {"question": f"Question {index}", "blocked_fields": []}
            for index in range(4)
        ]
        issues = validate_clarification_questions(questions)
        rule_ids = [issue.rule_id for issue in issues]
        self.assertEqual(rule_ids.count("clarification.budget.exceeded"), 1)
        self.assertEqual(rule_ids.count("clarification.blocked_fields.required"), 4)


class CodeImpactValidationTest(unittest.TestCase):
    MANIFEST = {
        "src/payment/refund.py": {
            "RefundService",
            "RefundService.create_refund",
        }
    }

    def test_known_file_and_symbol_pass(self) -> None:
        issues = validate_code_impacts(
            [
                {
                    "file_path": "src/payment/refund.py",
                    "symbol": "RefundService.create_refund",
                }
            ],
            self.MANIFEST,
        )
        self.assertEqual(issues, [])

    def test_unknown_file_and_symbol_fail(self) -> None:
        missing_file = validate_code_impacts(
            [{"file_path": "src/payment/missing.py", "symbol": "Missing"}],
            self.MANIFEST,
        )
        missing_symbol = validate_code_impacts(
            [{"file_path": "src/payment/refund.py", "symbol": "Missing"}],
            self.MANIFEST,
        )
        self.assertEqual(missing_file[0].rule_id, "code.path.not_found")
        self.assertEqual(missing_symbol[0].rule_id, "code.symbol.not_found")

    def test_absolute_and_traversal_paths_fail(self) -> None:
        for path in ("/etc/passwd", "../secrets.txt", ""):
            with self.subTest(path=path):
                issues = validate_code_impacts(
                    [{"file_path": path, "symbol": None}],
                    self.MANIFEST,
                )
                self.assertEqual(issues[0].rule_id, "code.path.invalid")


class ValidationBundleTest(unittest.TestCase):
    def test_valid_payload_passes_all_gates(self) -> None:
        issues = validate_generation_payload(
            claims=[
                {
                    "status": "confirmed",
                    "text": "Refunds already exist",
                    "citations": [{"chunk_id": "chunk-1"}],
                }
            ],
            retrieved_chunk_ids=["chunk-1"],
            clarification_questions=[
                {
                    "question": "What is the refund window?",
                    "blocked_fields": ["functional_requirements.refund_window"],
                }
            ],
            code_impacts=[
                {
                    "file_path": "src/payment/refund.py",
                    "symbol": "RefundService",
                }
            ],
            source_manifest={"src/payment/refund.py": {"RefundService"}},
        )
        self.assertEqual(issues, [])


if __name__ == "__main__":
    unittest.main()
