from __future__ import annotations

import hashlib
import unittest
from pathlib import Path

from prd_agent.contract import (
    ContractViolation,
    MarkdownSection,
    PrdDocument,
    render_markdown,
)
from prd_agent.contract_compiler import (
    ContractCompilationError,
    compile_prd_contract,
    contract_json,
)


class ContractCompilerTest(unittest.TestCase):
    def sample(self) -> str:
        return """# MOCK Product Requirements Document

> **MOCK / NON-AUTHORITATIVE SAMPLE**

## 1. Summary

### 1.1 Context

A summary paragraph.

| Field | Value |
|---|---|
| Locale | en-US |

```markdown
## This is data, not a section
### This is data, not a subsection
```

## 2. Requirements

### FR-001: First Requirement

The system must do the thing.
"""

    def test_compiles_and_round_trips_mixed_markdown(self) -> None:
        contract, document = compile_prd_contract(self.sample(), version="mock-v1")
        self.assertEqual(contract.document_title, "MOCK Product Requirements Document")
        self.assertEqual(len(contract.sections), 2)
        self.assertEqual(
            contract.sections[0].required_subheadings,
            ["1.1 Context"],
        )
        self.assertEqual(
            contract.sections[0].embedded_tables[0].columns,
            ["Field", "Value"],
        )
        self.assertEqual(render_markdown(contract, document), self.sample())

    def test_contract_json_is_stable_and_excludes_sample_body(self) -> None:
        contract, _ = compile_prd_contract(self.sample(), version="mock-v1")
        outputs = {contract_json(contract) for _ in range(100)}
        self.assertEqual(len(outputs), 1)
        serialized = outputs.pop()
        self.assertNotIn("A summary paragraph", serialized)
        self.assertIn('"source_sha256"', serialized)

    def test_structure_tampering_fails_closed(self) -> None:
        contract, document = compile_prd_contract(self.sample(), version="mock-v1")
        changed_body = document.sections[0].body.replace("### 1.1 Context\n\n", "")
        changed = PrdDocument(
            contract_version=document.contract_version,
            sections=[
                MarkdownSection(
                    section_id=document.sections[0].section_id,
                    body=changed_body,
                ),
                *document.sections[1:],
            ],
        )
        with self.assertRaisesRegex(ContractViolation, "subheadings"):
            render_markdown(contract, changed)

    def test_rejects_ambiguous_or_nonsequential_structure(self) -> None:
        with self.assertRaisesRegex(ContractCompilationError, "exactly one H1"):
            compile_prd_contract("# One\n\n# Two\n\n## 1. Body\n\nText\n", version="v1")
        with self.assertRaisesRegex(ContractCompilationError, "expected H2 section 2"):
            compile_prd_contract(
                "# PRD\n\n## 1. One\n\nText\n\n## 3. Three\n\nText\n",
                version="v1",
            )

    def test_compiles_full_mock_fixture_to_24_section_contract(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        source_path = project_root / "examples" / "mock-csv-batch-user-import-prd.md"
        source = source_path.read_text(encoding="utf-8")
        contract, document = compile_prd_contract(source, version="mock-csv-import-v0.1")

        self.assertEqual(len(contract.sections), 24)
        self.assertEqual(contract.sections[0].section_id, "section-01-document-control")
        self.assertEqual(contract.sections[-1].section_id, "section-24-definition-of-done")
        self.assertEqual(
            sum(len(section.embedded_tables) for section in contract.sections),
            18,
        )
        self.assertEqual(
            len(contract.sections[9].required_subheadings),
            21,
        )
        normalized = source.replace("\r\n", "\n").replace("\r", "\n").strip() + "\n"
        self.assertEqual(render_markdown(contract, document), normalized)
        self.assertEqual(
            contract.source_sha256,
            hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        )
    def test_committed_contract_matches_compiler_output(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        source = (
            project_root / "examples" / "mock-csv-batch-user-import-prd.md"
        ).read_text(encoding="utf-8")
        stored = (
            project_root
            / "contracts"
            / "mock-csv-batch-user-import.contract.json"
        ).read_text(encoding="utf-8")
        contract, _ = compile_prd_contract(
            source,
            version="mock-csv-import-v0.1",
        )
        self.assertEqual(stored, contract_json(contract))


if __name__ == "__main__":
    unittest.main()
