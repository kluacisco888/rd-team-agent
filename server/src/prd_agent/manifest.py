"""Immutable source-manifest documents used to validate code impact claims."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Literal, Self

from pydantic import ConfigDict, Field, model_validator

from prd_agent.code_ingestion import RepositorySnapshot
from prd_agent.contracts import StrictModel


class ManifestIntegrityError(ValueError):
    """Raised when stored manifest identity or content is invalid."""


class SourceManifestDocument(StrictModel):
    """A deterministic manifest pinned to one repository commit."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1"] = "1"
    project_id: str = Field(min_length=1)
    repository: str = Field(min_length=1)
    commit_sha: str = Field(min_length=1)
    files: dict[str, tuple[str, ...]]

    @model_validator(mode="after")
    def validate_paths_and_symbols(self) -> Self:
        for file_path, symbols in self.files.items():
            path = PurePosixPath(file_path)
            if not file_path or path.is_absolute() or ".." in path.parts:
                raise ValueError(
                    "manifest file paths must be repository-relative POSIX paths"
                )
            if tuple(sorted(set(symbols))) != symbols:
                raise ValueError("manifest symbols must be sorted and unique")
        return self

    def to_json(self) -> str:
        """Serialize in a byte-stable representation suitable for hashing/storage."""

        return json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ) + "\n"

    @classmethod
    def from_json(cls, payload: str) -> SourceManifestDocument:
        return cls.model_validate_json(payload)


def build_manifest_document(
    snapshot: RepositorySnapshot,
    *,
    project_id: str,
    repository: str,
    commit_sha: str,
) -> SourceManifestDocument:
    return SourceManifestDocument(
        project_id=project_id,
        repository=repository,
        commit_sha=commit_sha,
        files={
            path: tuple(sorted(symbols))
            for path, symbols in sorted(snapshot.manifest.items())
        },
    )


def manifest_storage_key(
    *,
    project_id: str,
    repository: str,
    commit_sha: str,
) -> str:
    if not project_id or not repository or not commit_sha:
        raise ValueError("project_id, repository, and commit_sha are required")
    identity = "\0".join((project_id, repository, commit_sha))
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return f"source-manifests/{digest}.json"


class FileSourceManifestProvider:
    """Local/test provider mirroring the immutable key used by an S3 provider."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def path_for(
        self,
        *,
        project_id: str,
        repository: str,
        commit_sha: str,
    ) -> Path:
        return self._root / manifest_storage_key(
            project_id=project_id,
            repository=repository,
            commit_sha=commit_sha,
        )

    def write(self, document: SourceManifestDocument) -> Path:
        path = self.path_for(
            project_id=document.project_id,
            repository=document.repository,
            commit_sha=document.commit_sha,
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(document.to_json(), encoding="utf-8")
        return path

    async def load(
        self,
        *,
        project_id: str,
        repository: str,
        commit_sha: str,
    ) -> dict[str, tuple[str, ...]]:
        path = self.path_for(
            project_id=project_id,
            repository=repository,
            commit_sha=commit_sha,
        )
        try:
            payload = await asyncio.to_thread(path.read_text, encoding="utf-8")
        except FileNotFoundError as error:
            raise ManifestIntegrityError("source manifest does not exist") from error

        try:
            document = SourceManifestDocument.from_json(payload)
        except ValueError as error:
            raise ManifestIntegrityError("source manifest is invalid") from error

        expected = (project_id, repository, commit_sha)
        actual = (document.project_id, document.repository, document.commit_sha)
        if actual != expected:
            raise ManifestIntegrityError(
                "source manifest identity does not match the requested snapshot"
            )
        return dict(document.files)
