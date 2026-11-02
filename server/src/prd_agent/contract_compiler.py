"""Compile an English Markdown PRD sample into a versioned structural contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from prd_agent.contract import (
    EmbeddedTableContract,
    MarkdownSection,
    PrdContract,
    PrdDocument,
    SectionContract,
    SectionKind,
    inspect_markdown_structure,
    render_markdown,
)

_H1 = re.compile(r"^#\s+(.+?)\s*$")
_H2 = re.compile(r"^##\s+(.+?)\s*$")
_NUMBERED_TITLE = re.compile(r"^(\d+)\.\s+(.+)$")
_NON_SLUG = re.compile(r"[^a-z0-9]+")


class ContractCompilationError(ValueError):
    """Raised when a Markdown sample cannot define an unambiguous contract."""


def _normalize(markdown: str) -> str:
    return markdown.replace("\r\n", "\n").replace("\r", "\n").strip() + "\n"


def _slug(value: str) -> str:
    slug = _NON_SLUG.sub("-", value.lower()).strip("-")
    if not slug:
        raise ContractCompilationError(f"cannot derive section ID from {value!r}")
    return slug


def _structural_line_indexes(lines: list[str]) -> tuple[list[int], list[int]]:
    h1_indexes: list[int] = []
    h2_indexes: list[int] = []
    fence_marker: str | None = None
    for index, line in enumerate(lines):
        stripped = line.lstrip()
        marker = stripped[:3] if stripped.startswith(("```", "~~~")) else None
        if marker is not None:
            if fence_marker is None:
                fence_marker = marker
            elif marker == fence_marker:
                fence_marker = None
            continue
        if fence_marker is not None:
            continue
        if _H1.fullmatch(line):
            h1_indexes.append(index)
        elif _H2.fullmatch(line):
            h2_indexes.append(index)
    return h1_indexes, h2_indexes


def compile_prd_contract(
    markdown: str,
    *,
    version: str,
) -> tuple[PrdContract, PrdDocument]:
    """Compile a strict structural contract and a round-trip Golden document."""

    if not version:
        raise ContractCompilationError("version is required")
    normalized = _normalize(markdown)
    lines = normalized.rstrip("\n").split("\n")
    h1_indexes, h2_indexes = _structural_line_indexes(lines)
    if h1_indexes != [0]:
        raise ContractCompilationError(
            "the PRD must contain exactly one H1 title as its first line"
        )
    if not h2_indexes:
        raise ContractCompilationError("the PRD must contain at least one H2 section")

    title_match = _H1.fullmatch(lines[0])
    assert title_match is not None
    document_title = title_match.group(1)
    preamble = "\n".join(lines[1 : h2_indexes[0]]).strip() or None

    contracts: list[SectionContract] = []
    contents: list[MarkdownSection] = []
    seen_titles: set[str] = set()
    expected_number = 1

    for position, start in enumerate(h2_indexes):
        end = h2_indexes[position + 1] if position + 1 < len(h2_indexes) else len(lines)
        title_match = _H2.fullmatch(lines[start])
        assert title_match is not None
        section_title = title_match.group(1)
        if section_title in seen_titles:
            raise ContractCompilationError(
                f"duplicate H2 section title: {section_title!r}"
            )
        seen_titles.add(section_title)

        numbered = _NUMBERED_TITLE.fullmatch(section_title)
        if numbered is None:
            raise ContractCompilationError(
                f"H2 section is not numbered: {section_title!r}"
            )
        section_number = int(numbered.group(1))
        if section_number != expected_number:
            raise ContractCompilationError(
                f"expected H2 section {expected_number}, received {section_number}"
            )
        expected_number += 1

        body = "\n".join(lines[start + 1 : end]).strip()
        if not body:
            raise ContractCompilationError(
                f"H2 section {section_title!r} has no content"
            )
        subheadings, tables = inspect_markdown_structure(body)
        section_id = f"section-{section_number:02d}-{_slug(numbered.group(2))}"
        contracts.append(
            SectionContract(
                section_id=section_id,
                title=section_title,
                kind=SectionKind.MARKDOWN,
                required=True,
                required_subheadings=subheadings,
                embedded_tables=[
                    EmbeddedTableContract(columns=columns, minimum_rows=1)
                    for columns, _ in tables
                ],
            )
        )
        contents.append(MarkdownSection(section_id=section_id, body=body))

    contract = PrdContract(
        version=version,
        document_title=document_title,
        language="en-US",
        preamble=preamble,
        source_sha256=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        sections=contracts,
    )
    document = PrdDocument(contract_version=version, sections=contents)
    rendered = render_markdown(contract, document)
    if rendered != normalized:
        raise ContractCompilationError(
            "compiled contract failed byte-for-byte Markdown round-trip"
        )
    return contract, document


def contract_json(contract: PrdContract) -> str:
    """Serialize a Contract deterministically for review and source control."""

    return json.dumps(
        contract.model_dump(mode="json"),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    contract, _ = compile_prd_contract(
        args.source.read_text(encoding="utf-8"),
        version=args.version,
    )
    print(contract_json(contract), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
