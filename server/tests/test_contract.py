from __future__ import annotations

import unittest

from pydantic import ValidationError

from prd_agent.contract import (
    BulletListSection,
    ContractViolation,
    ParagraphSection,
    PrdContract,
    PrdDocument,
    SectionContract,
    SectionKind,
    TableSection,
    render_markdown,
)


class ContractRendererTest(unittest.TestCase):
    def contract(self) -> PrdContract:
        return PrdContract(
            version="v1",
            document_title="Product Requirements Document",
            sections=[
                SectionContract(
                    section_id="background",
                    title="1. Background",
                    kind=SectionKind.PARAGRAPH,
                ),
                SectionContract(
                    section_id="goals",
                    title="2. Goals",
                    kind=SectionKind.BULLET_LIST,
                ),
                SectionContract(
                    section_id="requirements",
                    title="3. Functional Requirements",
                    kind=SectionKind.TABLE,
                    table_columns=["ID", "Requirement", "Acceptance Criteria"],
                ),
                SectionContract(
                    section_id="notes",
                    title="4. Notes",
                    kind=SectionKind.PARAGRAPH,
                    required=False,
                ),
            ],
        )

    def document(self) -> PrdDocument:
        return PrdDocument(
            contract_version="v1",
            sections=[
                TableSection(
                    section_id="requirements",
                    rows=[
                        {
                            "ID": "FR-001",
                            "Requirement": "Support CSV | XLSX imports.",
                            "Acceptance Criteria": "A valid file is accepted.\nErrors are listed.",
                        }
                    ],
                ),
                ParagraphSection(
                    section_id="background",
                    paragraphs=["Users currently import records one at a time."],
                ),
                BulletListSection(
                    section_id="goals",
                    items=["Reduce manual work.", "Preserve validation behavior."],
                ),
            ],
        )

    def test_renders_exact_contract_order_and_markdown(self) -> None:
        expected = (
            "# Product Requirements Document\n"
            "\n"
            "## 1. Background\n"
            "\n"
            "Users currently import records one at a time.\n"
            "\n"
            "## 2. Goals\n"
            "\n"
            "- Reduce manual work.\n"
            "- Preserve validation behavior.\n"
            "\n"
            "## 3. Functional Requirements\n"
            "\n"
            "| ID | Requirement | Acceptance Criteria |\n"
            "| --- | --- | --- |\n"
            "| FR-001 | Support CSV \\| XLSX imports. | "
            "A valid file is accepted.<br>Errors are listed. |\n"
        )
        self.assertEqual(render_markdown(self.contract(), self.document()), expected)

    def test_rendering_is_deterministic(self) -> None:
        contract = self.contract()
        document = self.document()
        outputs = {render_markdown(contract, document) for _ in range(100)}
        self.assertEqual(len(outputs), 1)

    def test_missing_required_section_fails_closed(self) -> None:
        document = self.document().model_copy(
            update={
                "sections": [
                    section
                    for section in self.document().sections
                    if section.section_id != "goals"
                ]
            }
        )
        with self.assertRaisesRegex(ContractViolation, "required section 'goals'"):
            render_markdown(self.contract(), document)

    def test_optional_section_may_be_absent(self) -> None:
        self.assertNotIn("4. Notes", render_markdown(self.contract(), self.document()))

    def test_unknown_section_fails_closed(self) -> None:
        document = self.document().model_copy(
            update={
                "sections": [
                    *self.document().sections,
                    ParagraphSection(section_id="invented", paragraphs=["Do not render."]),
                ]
            }
        )
        with self.assertRaisesRegex(ContractViolation, "unknown sections"):
            render_markdown(self.contract(), document)

    def test_kind_mismatch_fails_closed(self) -> None:
        document = self.document().model_copy(
            update={
                "sections": [
                    ParagraphSection(section_id="goals", paragraphs=["Wrong shape."]),
                    *[
                        section
                        for section in self.document().sections
                        if section.section_id != "goals"
                    ],
                ]
            }
        )
        with self.assertRaisesRegex(ContractViolation, "requires kind 'bullet_list'"):
            render_markdown(self.contract(), document)

    def test_table_column_mismatch_fails_closed(self) -> None:
        document = self.document().model_copy(
            update={
                "sections": [
                    TableSection(
                        section_id="requirements",
                        rows=[{"ID": "FR-001", "Requirement": "Missing criteria"}],
                    ),
                    *[
                        section
                        for section in self.document().sections
                        if section.section_id != "requirements"
                    ],
                ]
            }
        )
        with self.assertRaisesRegex(ContractViolation, "invalid columns"):
            render_markdown(self.contract(), document)

    def test_contract_version_mismatch_fails_closed(self) -> None:
        document = self.document().model_copy(update={"contract_version": "v2"})
        with self.assertRaisesRegex(ContractViolation, "does not match"):
            render_markdown(self.contract(), document)

    def test_contract_rejects_duplicate_ids_and_invalid_columns(self) -> None:
        with self.assertRaisesRegex(ValidationError, "section IDs must be unique"):
            PrdContract(
                version="v1",
                document_title="PRD",
                sections=[
                    SectionContract(
                        section_id="same",
                        title="One",
                        kind=SectionKind.PARAGRAPH,
                    ),
                    SectionContract(
                        section_id="same",
                        title="Two",
                        kind=SectionKind.PARAGRAPH,
                    ),
                ],
            )

        with self.assertRaisesRegex(ValidationError, "table sections require"):
            SectionContract(
                section_id="table",
                title="Table",
                kind=SectionKind.TABLE,
            )


if __name__ == "__main__":
    unittest.main()
