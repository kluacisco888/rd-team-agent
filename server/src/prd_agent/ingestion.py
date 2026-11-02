"""Deterministic Markdown preprocessing for AgentCore Knowledge Base ingestion."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_BLANK_LINE = re.compile(r"\n[ \t]*\n")


@dataclass(frozen=True, slots=True)
class KnowledgeChunk:
    """A stable document and metadata payload ready for KB ingestion."""

    chunk_id: str
    content: str
    project_id: str
    source_path: str
    document_version: str
    document_hash: str
    heading_path: tuple[str, ...]
    sequence: int
    language: str = "en-US"

    @property
    def heading(self) -> str:
        return " > ".join(self.heading_path) or "(document root)"

    def as_ingestion_record(self) -> dict[str, Any]:
        """Return a provider-neutral record for the AgentCore KB adapter."""

        return {
            "id": self.chunk_id,
            "content": self.content,
            "metadata": {
                "chunk_id": self.chunk_id,
                "project_id": self.project_id,
                "source_type": "document",
                "source_path": self.source_path,
                "document_version": self.document_version,
                "document_hash": self.document_hash,
                "heading": self.heading,
                "sequence": self.sequence,
                "language": self.language,
            },
        }


def _validate_source_path(source_path: str) -> None:
    path = PurePosixPath(source_path)
    if not source_path or path.is_absolute() or ".." in path.parts:
        raise ValueError("source_path must be a repository-relative POSIX path")


def _split_long_block(block: str, limit: int) -> list[str]:
    parts: list[str] = []
    remaining = block.strip()
    while len(remaining) > limit:
        cut = remaining.rfind("\n", 0, limit + 1)
        if cut < limit // 2:
            cut = remaining.rfind(" ", 0, limit + 1)
        if cut <= 0:
            cut = limit
        parts.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        parts.append(remaining)
    return parts


def _split_body(body: str, limit: int) -> list[str]:
    paragraphs = [part.strip() for part in _BLANK_LINE.split(body) if part.strip()]
    output: list[str] = []
    current = ""

    for paragraph in paragraphs:
        if len(paragraph) > limit:
            if current:
                output.append(current)
                current = ""
            long_parts = _split_long_block(paragraph, limit)
            output.extend(long_parts[:-1])
            if long_parts:
                current = long_parts[-1]
            continue

        candidate = paragraph if not current else f"{current}\n\n{paragraph}"
        if len(candidate) <= limit:
            current = candidate
        else:
            output.append(current)
            current = paragraph

    if current:
        output.append(current)
    return output


def _parse_sections(markdown: str) -> list[tuple[tuple[str, ...], str]]:
    sections: list[tuple[tuple[str, ...], str]] = []
    headings: list[tuple[int, str]] = []
    body_lines: list[str] = []
    fence_marker: str | None = None

    def flush() -> None:
        body = "\n".join(body_lines).strip()
        if body:
            sections.append((tuple(title for _, title in headings), body))
        body_lines.clear()

    for line in markdown.splitlines():
        stripped = line.lstrip()
        marker = stripped[:3] if stripped.startswith(("```", "~~~")) else None
        if marker is not None:
            if fence_marker is None:
                fence_marker = marker
            elif marker == fence_marker:
                fence_marker = None
            body_lines.append(line)
            continue

        match = _HEADING.match(line) if fence_marker is None else None
        if match is None:
            body_lines.append(line)
            continue

        flush()
        level = len(match.group(1))
        title = match.group(2).strip()
        headings = [(existing_level, name) for existing_level, name in headings if existing_level < level]
        headings.append((level, title))

    flush()
    return sections


def chunk_markdown(
    markdown: str,
    *,
    project_id: str,
    source_path: str,
    document_version: str,
    max_chars: int = 4000,
) -> list[KnowledgeChunk]:
    """Create stable, heading-aware chunks without invoking a model.

    The chunk IDs are deterministic for a given project, source version, heading,
    sequence, and chunk body. Source headings are included in content so semantic
    retrieval retains hierarchy even when metadata is not shown to the model.
    """

    if not project_id:
        raise ValueError("project_id is required")
    if not document_version:
        raise ValueError("document_version is required")
    if max_chars < 256:
        raise ValueError("max_chars must be at least 256")
    _validate_source_path(source_path)

    normalized = markdown.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        raise ValueError("markdown must contain non-whitespace content")

    document_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    chunks: list[KnowledgeChunk] = []

    for heading_path, body in _parse_sections(normalized):
        heading = " > ".join(heading_path) or "(document root)"
        prefix = f"Source heading: {heading}\n\n"
        body_limit = max_chars - len(prefix)
        if body_limit < 128:
            raise ValueError("max_chars is too small for the heading metadata")

        for body_part in _split_body(body, body_limit):
            sequence = len(chunks)
            content = f"{prefix}{body_part}"
            identity = "\0".join(
                (
                    project_id,
                    source_path,
                    document_version,
                    heading,
                    str(sequence),
                    body_part,
                )
            )
            chunk_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
            chunks.append(
                KnowledgeChunk(
                    chunk_id=chunk_id,
                    content=content,
                    project_id=project_id,
                    source_path=source_path,
                    document_version=document_version,
                    document_hash=document_hash,
                    heading_path=heading_path,
                    sequence=sequence,
                )
            )

    if not chunks:
        raise ValueError("markdown did not produce any content chunks")
    return chunks
