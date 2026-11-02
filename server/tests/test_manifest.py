from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from prd_agent.code_ingestion import CodeChunk, RepositorySnapshot
from prd_agent.manifest import (
    FileSourceManifestProvider,
    ManifestIntegrityError,
    SourceManifestDocument,
    build_manifest_document,
    manifest_storage_key,
)


class SourceManifestTest(unittest.IsolatedAsyncioTestCase):
    def document(self) -> SourceManifestDocument:
        return SourceManifestDocument(
            project_id="project-1",
            repository="example/service",
            commit_sha="abc123",
            files={
                "src/imports.py": (
                    "ImportService",
                    "ImportService.create",
                )
            },
        )

    def test_serialization_is_deterministic_and_round_trips(self) -> None:
        document = self.document()
        outputs = {document.to_json() for _ in range(100)}
        self.assertEqual(len(outputs), 1)
        self.assertEqual(SourceManifestDocument.from_json(document.to_json()), document)
        self.assertEqual(json.loads(document.to_json())["schema_version"], "1")

    def test_storage_key_hides_untrusted_names_and_is_stable(self) -> None:
        first = manifest_storage_key(
            project_id="project-1",
            repository="organization/private-service",
            commit_sha="abc123",
        )
        second = manifest_storage_key(
            project_id="project-1",
            repository="organization/private-service",
            commit_sha="abc123",
        )
        self.assertEqual(first, second)
        self.assertTrue(first.startswith("source-manifests/"))
        self.assertNotIn("organization", first)
        self.assertNotIn("private-service", first)

    def test_manifest_rejects_unsafe_paths_and_unsorted_symbols(self) -> None:
        for path in ("/absolute.py", "../other-project.py", ""):
            with self.subTest(path=path):
                with self.assertRaisesRegex(ValidationError, "repository-relative"):
                    SourceManifestDocument(
                        project_id="project-1",
                        repository="example/service",
                        commit_sha="abc123",
                        files={path: ()},
                    )

        with self.assertRaisesRegex(ValidationError, "sorted and unique"):
            SourceManifestDocument(
                project_id="project-1",
                repository="example/service",
                commit_sha="abc123",
                files={"app.py": ("z", "a", "a")},
            )

    def test_builds_sorted_document_from_repository_snapshot(self) -> None:
        snapshot = RepositorySnapshot(
            chunks=tuple[CodeChunk, ...](),
            manifest={"src/app.py": frozenset({"z", "a"})},
        )
        document = build_manifest_document(
            snapshot,
            project_id="project-1",
            repository="example/service",
            commit_sha="abc123",
        )
        self.assertEqual(document.files["src/app.py"], ("a", "z"))

    async def test_file_provider_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            provider = FileSourceManifestProvider(Path(directory))
            path = provider.write(self.document())
            self.assertTrue(path.is_file())
            loaded = await provider.load(
                project_id="project-1",
                repository="example/service",
                commit_sha="abc123",
            )
            self.assertEqual(
                loaded,
                {"src/imports.py": ("ImportService", "ImportService.create")},
            )

    async def test_file_provider_fails_for_missing_or_wrong_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            provider = FileSourceManifestProvider(Path(directory))
            with self.assertRaisesRegex(ManifestIntegrityError, "does not exist"):
                await provider.load(
                    project_id="project-1",
                    repository="example/service",
                    commit_sha="abc123",
                )

            path = provider.path_for(
                project_id="project-1",
                repository="example/service",
                commit_sha="abc123",
            )
            path.parent.mkdir(parents=True)
            wrong = self.document().model_copy(update={"project_id": "project-2"})
            path.write_text(wrong.to_json(), encoding="utf-8")
            with self.assertRaisesRegex(ManifestIntegrityError, "does not match"):
                await provider.load(
                    project_id="project-1",
                    repository="example/service",
                    commit_sha="abc123",
                )


if __name__ == "__main__":
    unittest.main()
