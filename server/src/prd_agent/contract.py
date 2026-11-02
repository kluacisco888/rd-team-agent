"""Versioned PRD contract models and deterministic Markdown rendering."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

NonEmptyText = Annotated[str, Field(min_length=1)]
_TABLE_SEPARATOR = re.compile(r"^:?-{3,}:?$")


class ContractViolation(ValueError):
    """Raised when a typed PRD does not satisfy its selected contract."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SectionKind(StrEnum):
    PARAGRAPH = "paragraph"
    BULLET_LIST = "bullet_list"
    TABLE = "table"
    MARKDOWN = "markdown"


class EmbeddedTableContract(StrictModel):
    columns: list[NonEmptyText] = Field(min_length=1)
    minimum_rows: int = Field(default=1, ge=0)

    @model_validator(mode="after")
    def require_unique_columns(self) -> Self:
        if len(self.columns) != len(set(self.columns)):
            raise ValueError("embedded table column names must be unique")
        return self


class SectionContract(StrictModel):
    section_id: NonEmptyText
    title: NonEmptyText
    level: int = Field(default=2, ge=2, le=6)
    kind: SectionKind
    required: bool = True
    table_columns: list[NonEmptyText] = Field(default_factory=list)
    required_subheadings: list[NonEmptyText] = Field(default_factory=list)
    embedded_tables: list[EmbeddedTableContract] = Field(default_factory=list)
    required_markers: list[NonEmptyText] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_shape(self) -> Self:
        if self.kind is SectionKind.TABLE and not self.table_columns:
            raise ValueError("table sections require at least one column")
        if self.kind is not SectionKind.TABLE and self.table_columns:
            raise ValueError("only table sections may define table columns")
        if len(self.table_columns) != len(set(self.table_columns)):
            raise ValueError("table column names must be unique")
        if self.kind is not SectionKind.MARKDOWN and (
            self.required_subheadings
            or self.embedded_tables
            or self.required_markers
        ):
            raise ValueError(
                "mixed Markdown constraints require a markdown section"
            )
        if len(self.required_subheadings) != len(set(self.required_subheadings)):
            raise ValueError("required subheadings must be unique")
        if len(self.required_markers) != len(set(self.required_markers)):
            raise ValueError("required markers must be unique")
        return self


class PrdContract(StrictModel):
    version: NonEmptyText
    document_title: NonEmptyText
    language: Literal["en-US"] = "en-US"
    preamble: str | None = None
    source_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    sections: list[SectionContract] = Field(min_length=1)

    @model_validator(mode="after")
    def require_unique_sections(self) -> Self:
        ids = [section.section_id for section in self.sections]
        if len(ids) != len(set(ids)):
            raise ValueError("section IDs must be unique")
        return self


class ParagraphSection(StrictModel):
    section_id: NonEmptyText
    kind: Literal[SectionKind.PARAGRAPH] = SectionKind.PARAGRAPH
    paragraphs: list[NonEmptyText] = Field(min_length=1)


class BulletListSection(StrictModel):
    section_id: NonEmptyText
    kind: Literal[SectionKind.BULLET_LIST] = SectionKind.BULLET_LIST
    items: list[NonEmptyText] = Field(min_length=1)


class TableSection(StrictModel):
    section_id: NonEmptyText
    kind: Literal[SectionKind.TABLE] = SectionKind.TABLE
    rows: list[dict[NonEmptyText, str]] = Field(min_length=1)


class MarkdownSection(StrictModel):
    section_id: NonEmptyText
    kind: Literal[SectionKind.MARKDOWN] = SectionKind.MARKDOWN
    body: NonEmptyText


SectionContent = Annotated[
    ParagraphSection | BulletListSection | TableSection | MarkdownSection,
    Field(discriminator="kind"),
]


class PrdDocument(StrictModel):
    contract_version: NonEmptyText
    sections: list[SectionContent]

    @model_validator(mode="after")
    def require_unique_sections(self) -> Self:
        ids = [section.section_id for section in self.sections]
        if len(ids) != len(set(ids)):
            raise ValueError("document section IDs must be unique")
        return self


def _outside_fences(markdown: str) -> list[str]:
    output: list[str] = []
    fence_marker: str | None = None
    for line in markdown.splitlines():
        stripped = line.lstrip()
        marker = stripped[:3] if stripped.startswith(("```", "~~~")) else None
        if marker is not None:
            if fence_marker is None:
                fence_marker = marker
            elif marker == fence_marker:
                fence_marker = None
            output.append("")
        elif fence_marker is None:
            output.append(line)
        else:
            output.append("")
    return output


def _table_cells(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return []
    return [cell.strip() for cell in stripped[1:-1].split("|")]


def inspect_markdown_structure(
    markdown: str,
) -> tuple[list[str], list[tuple[list[str], int]]]:
    """Return H3 headings and embedded table signatures outside code fences."""

    lines = _outside_fences(markdown)
    subheadings: list[str] = []
    tables: list[tuple[list[str], int]] = []
    for line in lines:
        if match := re.match(r"^###\s+(.+?)\s*$", line):
            subheadings.append(match.group(1))

    index = 0
    while index + 1 < len(lines):
        columns = _table_cells(lines[index])
        separators = _table_cells(lines[index + 1])
        if (
            columns
            and len(columns) == len(separators)
            and all(_TABLE_SEPARATOR.fullmatch(cell) for cell in separators)
        ):
            row_count = 0
            cursor = index + 2
            while cursor < len(lines) and _table_cells(lines[cursor]):
                row_count += 1
                cursor += 1
            tables.append((columns, row_count))
            index = cursor
        else:
            index += 1
    return subheadings, tables


def _validate_markdown_section(
    section: SectionContract,
    content: MarkdownSection,
) -> None:
    structural_lines = _outside_fences(content.body)
    if any(re.match(r"^#{1,2}\s+", line) for line in structural_lines):
        raise ContractViolation(
            f"section {section.section_id!r} contains a forbidden H1/H2 heading"
        )

    subheadings, tables = inspect_markdown_structure(content.body)
    if subheadings != section.required_subheadings:
        raise ContractViolation(
            f"section {section.section_id!r} subheadings do not match the contract"
        )
    if len(tables) != len(section.embedded_tables):
        raise ContractViolation(
            f"section {section.section_id!r} embedded table count does not match"
        )
    for index, ((columns, row_count), expected) in enumerate(
        zip(tables, section.embedded_tables, strict=True)
    ):
        if columns != expected.columns:
            raise ContractViolation(
                f"section {section.section_id!r} table {index} columns do not match"
            )
        if row_count < expected.minimum_rows:
            raise ContractViolation(
                f"section {section.section_id!r} table {index} requires at least "
                f"{expected.minimum_rows} data rows"
            )
    missing_markers = [
        marker for marker in section.required_markers if marker not in content.body
    ]
    if missing_markers:
        raise ContractViolation(
            f"section {section.section_id!r} is missing markers: {missing_markers}"
        )


def _escape_table_cell(value: str) -> str:
    return value.replace("\n", "<br>").replace("|", "\\|")


def _render_section(section: SectionContract, content: SectionContent) -> list[str]:
    heading = f"{'#' * section.level} {section.title}"

    if isinstance(content, ParagraphSection):
        return [heading, "", "\n\n".join(content.paragraphs)]
    if isinstance(content, BulletListSection):
        return [heading, "", *[f"- {item}" for item in content.items]]
    if isinstance(content, MarkdownSection):
        _validate_markdown_section(section, content)
        return [heading, "", content.body.strip()]

    expected_columns = section.table_columns
    for row_index, row in enumerate(content.rows):
        if set(row) != set(expected_columns):
            missing = sorted(set(expected_columns) - set(row))
            extra = sorted(set(row) - set(expected_columns))
            raise ContractViolation(
                f"section {section.section_id!r} row {row_index} has invalid columns; "
                f"missing={missing}, extra={extra}"
            )

    header = "| " + " | ".join(expected_columns) + " |"
    separator = "| " + " | ".join("---" for _ in expected_columns) + " |"
    rows = [
        "| "
        + " | ".join(_escape_table_cell(row[column]) for column in expected_columns)
        + " |"
        for row in content.rows
    ]
    return [heading, "", header, separator, *rows]


def render_markdown(contract: PrdContract, document: PrdDocument) -> str:
    """Render a typed PRD in contract order or fail closed."""

    if document.contract_version != contract.version:
        raise ContractViolation(
            f"document contract version {document.contract_version!r} does not match "
            f"selected contract {contract.version!r}"
        )

    contract_ids = {section.section_id for section in contract.sections}
    content_by_id = {section.section_id: section for section in document.sections}
    unknown_ids = sorted(set(content_by_id) - contract_ids)
    if unknown_ids:
        raise ContractViolation(f"document contains unknown sections: {unknown_ids}")

    rendered: list[str] = [f"# {contract.document_title}"]
    if contract.preamble:
        rendered.extend(["", contract.preamble.strip()])
    for section in contract.sections:
        content = content_by_id.get(section.section_id)
        if content is None:
            if section.required:
                raise ContractViolation(
                    f"required section {section.section_id!r} is missing"
                )
            continue

        if content.kind != section.kind:
            raise ContractViolation(
                f"section {section.section_id!r} requires kind {section.kind.value!r}, "
                f"received {content.kind.value!r}"
            )

        rendered.extend(["", *_render_section(section, content)])

    return "\n".join(rendered) + "\n"
