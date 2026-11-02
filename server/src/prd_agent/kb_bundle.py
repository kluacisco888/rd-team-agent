"""Build pre-split S3 objects for an AgentCore Managed Knowledge Base."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from prd_agent.code_ingestion import CodeChunk, build_repository_snapshot
from prd_agent.ingestion import KnowledgeChunk, chunk_markdown
from prd_agent.manifest import (
    FileSourceManifestProvider,
    SourceManifestDocument,
    build_manifest_document,
)


@dataclass(frozen=True, slots=True)
class BundleObject:
    key: str
    body: bytes
    content_type: str


@dataclass(frozen=True, slots=True)
class KnowledgeBaseBundle:
    project_id: str
    repository: str
    commit_sha: str
    objects: tuple[BundleObject, ...]
    manifest: SourceManifestDocument


def compute_source_snapshot_id(source_root: Path) -> str:
    """Hash source paths and bytes so every deployed manifest is immutable."""

    digest = hashlib.sha256()
    files = sorted(
        path
        for path in source_root.rglob("*.py")
        if path.is_file() and not path.is_symlink() and "__pycache__" not in path.parts
    )
    if not files:
        raise ValueError("source_root contains no Python source files")
    for path in files:
        relative = path.relative_to(source_root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return f"source-{digest.hexdigest()[:24]}"


def _string_attribute(value: str, *, embed: bool = False) -> dict[str, Any]:
    return {
        "value": {"type": "STRING", "stringValue": value},
        "includeForEmbedding": embed,
    }


def _number_attribute(value: int) -> dict[str, Any]:
    return {
        "value": {"type": "NUMBER", "numberValue": value},
        "includeForEmbedding": False,
    }


def _sidecar(attributes: dict[str, dict[str, Any]]) -> bytes:
    payload = json.dumps(
        {"metadataAttributes": attributes},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if len(payload) > 10_000:
        raise ValueError("metadata sidecar exceeds the 10 KB service limit")
    return payload


def _document_objects(chunk: KnowledgeChunk) -> tuple[BundleObject, BundleObject]:
    key = f"content/document/{chunk.chunk_id}.txt"
    metadata = {
        "chunk_id": _string_attribute(chunk.chunk_id),
        "project_id": _string_attribute(chunk.project_id),
        "source_type": _string_attribute("document"),
        "source_path": _string_attribute(chunk.source_path, embed=True),
        "document_version": _string_attribute(chunk.document_version),
        "document_hash": _string_attribute(chunk.document_hash),
        "heading": _string_attribute(chunk.heading, embed=True),
        "locator": _string_attribute(chunk.heading),
        "sequence": _number_attribute(chunk.sequence),
        "language": _string_attribute(chunk.language),
    }
    return (
        BundleObject(key, chunk.content.encode("utf-8"), "text/plain; charset=utf-8"),
        BundleObject(
            f"{key}.metadata.json",
            _sidecar(metadata),
            "application/json",
        ),
    )


def _code_objects(chunk: CodeChunk) -> tuple[BundleObject, BundleObject]:
    key = f"content/code/{chunk.chunk_id}.txt"
    locator = f"{chunk.file_path}:{chunk.line_start}-{chunk.line_end}"
    metadata = {
        "chunk_id": _string_attribute(chunk.chunk_id),
        "project_id": _string_attribute(chunk.project_id),
        "source_type": _string_attribute("code"),
        "repository": _string_attribute(chunk.repository, embed=True),
        "commit_sha": _string_attribute(chunk.commit_sha),
        "file_path": _string_attribute(chunk.file_path, embed=True),
        "language": _string_attribute(chunk.language),
        "line_start": _number_attribute(chunk.line_start),
        "line_end": _number_attribute(chunk.line_end),
        "locator": _string_attribute(locator),
        "source_hash": _string_attribute(chunk.source_hash),
    }
    return (
        BundleObject(key, chunk.content.encode("utf-8"), "text/plain; charset=utf-8"),
        BundleObject(
            f"{key}.metadata.json",
            _sidecar(metadata),
            "application/json",
        ),
    )


def build_mock_bundle(
    project_root: Path,
    *,
    project_id: str = "mock-prd-e2e",
    repository: str = "agentcore-prd-agent",
) -> KnowledgeBaseBundle:
    """Build the mock document + real local source bundle used by cloud smoke tests."""

    source_root = project_root / "src"
    commit_sha = compute_source_snapshot_id(source_root)
    mock_path = project_root / "examples" / "mock-csv-batch-user-import-prd.md"
    markdown = mock_path.read_text(encoding="utf-8")
    document_chunks = chunk_markdown(
        markdown,
        project_id=project_id,
        source_path="examples/mock-csv-batch-user-import-prd.md",
        document_version="mock-v0.1",
        max_chars=3000,
    )
    source_snapshot = build_repository_snapshot(
        source_root,
        project_id=project_id,
        repository=repository,
        commit_sha=commit_sha,
        max_chars=5000,
    )

    objects: list[BundleObject] = []
    for chunk in document_chunks:
        objects.extend(_document_objects(chunk))
    for chunk in source_snapshot.chunks:
        objects.extend(_code_objects(chunk))
    objects.sort(key=lambda item: item.key)

    manifest = build_manifest_document(
        source_snapshot,
        project_id=project_id,
        repository=repository,
        commit_sha=commit_sha,
    )
    return KnowledgeBaseBundle(
        project_id=project_id,
        repository=repository,
        commit_sha=commit_sha,
        objects=tuple(objects),
        manifest=manifest,
    )


def write_runtime_manifest(bundle: KnowledgeBaseBundle, root: Path) -> Path:
    """Write the exact manifest layout expected by FileSourceManifestProvider."""

    return FileSourceManifestProvider(root).write(bundle.manifest)
