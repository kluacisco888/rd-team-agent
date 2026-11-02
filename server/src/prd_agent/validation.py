"""Deterministic validation gates for generated PRD evidence and code impact.

This module intentionally uses only the Python standard library. It can run in
CI before model, Strands, or AgentCore dependencies are installed.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any


@dataclass(frozen=True, slots=True)
class Issue:
    """A stable, machine-readable validation failure."""

    rule_id: str
    field_path: str
    message: str


def _is_repo_relative_path(value: str) -> bool:
    path = PurePosixPath(value)
    return bool(value) and not path.is_absolute() and ".." not in path.parts


def validate_claims(
    claims: Iterable[Mapping[str, Any]],
    retrieved_chunk_ids: Iterable[str],
    *,
    field_path: str = "claims",
) -> list[Issue]:
    """Validate evidence states and ensure citations came from this retrieval run."""

    known_chunks = frozenset(retrieved_chunk_ids)
    issues: list[Issue] = []

    for index, claim in enumerate(claims):
        claim_path = f"{field_path}[{index}]"
        status = claim.get("status")
        citations = claim.get("citations", [])

        if status not in {"confirmed", "assumption", "unresolved"}:
            issues.append(
                Issue(
                    "evidence.status.invalid",
                    f"{claim_path}.status",
                    "status must be confirmed, assumption, or unresolved",
                )
            )
            continue

        if not isinstance(citations, Sequence) or isinstance(citations, (str, bytes)):
            issues.append(
                Issue(
                    "evidence.citations.invalid",
                    f"{claim_path}.citations",
                    "citations must be a list",
                )
            )
            continue

        if status == "confirmed" and not citations:
            issues.append(
                Issue(
                    "evidence.citations.required",
                    f"{claim_path}.citations",
                    "confirmed claims require at least one citation",
                )
            )

        for citation_index, citation in enumerate(citations):
            citation_path = f"{claim_path}.citations[{citation_index}]"
            if not isinstance(citation, Mapping):
                issues.append(
                    Issue(
                        "evidence.citation.invalid",
                        citation_path,
                        "citation must be an object",
                    )
                )
                continue

            chunk_id = citation.get("chunk_id")
            if not isinstance(chunk_id, str) or not chunk_id:
                issues.append(
                    Issue(
                        "evidence.chunk_id.required",
                        f"{citation_path}.chunk_id",
                        "citation requires a non-empty chunk_id",
                    )
                )
            elif chunk_id not in known_chunks:
                issues.append(
                    Issue(
                        "evidence.chunk_id.not_retrieved",
                        f"{citation_path}.chunk_id",
                        "citation was not returned by this retrieval run",
                    )
                )

    return issues


def validate_clarification_questions(
    questions: Sequence[Mapping[str, Any]],
    *,
    maximum: int = 3,
    field_path: str = "clarification_questions",
) -> list[Issue]:
    """Enforce a bounded, actionable clarification interaction."""

    issues: list[Issue] = []
    if len(questions) > maximum:
        issues.append(
            Issue(
                "clarification.budget.exceeded",
                field_path,
                f"at most {maximum} clarification questions are allowed",
            )
        )

    for index, question in enumerate(questions):
        blocked_fields = question.get("blocked_fields")
        if not isinstance(blocked_fields, Sequence) or isinstance(
            blocked_fields, (str, bytes)
        ):
            blocked_fields = []
        if not blocked_fields:
            issues.append(
                Issue(
                    "clarification.blocked_fields.required",
                    f"{field_path}[{index}].blocked_fields",
                    "each question must identify at least one blocked PRD field",
                )
            )

    return issues


def validate_code_impacts(
    impacts: Sequence[Mapping[str, Any]],
    source_manifest: Mapping[str, Iterable[str]],
    *,
    field_path: str = "code_impacts",
) -> list[Issue]:
    """Reject hallucinated files and symbols against an immutable source manifest."""

    normalized_manifest = {
        path: frozenset(symbols) for path, symbols in source_manifest.items()
    }
    issues: list[Issue] = []

    for index, impact in enumerate(impacts):
        impact_path = f"{field_path}[{index}]"
        file_path = impact.get("file_path")
        symbol = impact.get("symbol")

        if not isinstance(file_path, str) or not _is_repo_relative_path(file_path):
            issues.append(
                Issue(
                    "code.path.invalid",
                    f"{impact_path}.file_path",
                    "file_path must be a non-empty repository-relative POSIX path",
                )
            )
            continue

        if file_path not in normalized_manifest:
            issues.append(
                Issue(
                    "code.path.not_found",
                    f"{impact_path}.file_path",
                    "file_path does not exist in the pinned repository commit",
                )
            )
            continue

        if symbol is not None:
            if not isinstance(symbol, str) or not symbol:
                issues.append(
                    Issue(
                        "code.symbol.invalid",
                        f"{impact_path}.symbol",
                        "symbol must be a non-empty string when provided",
                    )
                )
            elif symbol not in normalized_manifest[file_path]:
                issues.append(
                    Issue(
                        "code.symbol.not_found",
                        f"{impact_path}.symbol",
                        "symbol does not exist in the pinned repository commit",
                    )
                )

    return issues


def validate_generation_payload(
    *,
    claims: Sequence[Mapping[str, Any]],
    retrieved_chunk_ids: Iterable[str],
    clarification_questions: Sequence[Mapping[str, Any]],
    code_impacts: Sequence[Mapping[str, Any]],
    source_manifest: Mapping[str, Iterable[str]],
) -> list[Issue]:
    """Run the first deterministic G1 validation bundle."""

    return [
        *validate_claims(claims, retrieved_chunk_ids),
        *validate_clarification_questions(clarification_questions),
        *validate_code_impacts(code_impacts, source_manifest),
    ]
