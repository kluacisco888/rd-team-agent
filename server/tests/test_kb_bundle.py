from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from prd_agent.kb_bundle import (
    build_mock_bundle,
    compute_source_snapshot_id,
    write_runtime_manifest,
)
from prd_agent.manifest import FileSourceManifestProvider


class KnowledgeBaseBundleTest(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def project_root(cls) -> Path:
        return Path(__file__).resolve().parents[1]

    def test_source_snapshot_is_stable_and_content_sensitive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.py").write_text("VALUE = 1\n", encoding="utf-8")
            first = compute_source_snapshot_id(root)
            second = compute_source_snapshot_id(root)
            self.assertEqual(first, second)
            (root / "a.py").write_text("VALUE = 2\n", encoding="utf-8")
            self.assertNotEqual(first, compute_source_snapshot_id(root))

    def test_bundle_has_content_metadata_pairs_and_filter_fields(self) -> None:
        bundle = build_mock_bundle(self.project_root())
        objects = {item.key: item for item in bundle.objects}
        content_keys = sorted(
            key for key in objects if key.endswith(".txt")
        )
        metadata_keys = sorted(
            key for key in objects if key.endswith(".txt.metadata.json")
        )
        self.assertGreater(len(content_keys), 20)
        self.assertEqual(
            metadata_keys,
            [f"{key}.metadata.json" for key in content_keys],
        )
        for key in metadata_keys:
            item = objects[key]
            self.assertLessEqual(len(item.body), 10_000)
            attributes = json.loads(item.body)["metadataAttributes"]
            self.assertEqual(
                attributes["project_id"]["value"]["stringValue"],
                "mock-prd-e2e",
            )
            self.assertIn(
                attributes["source_type"]["value"]["stringValue"],
                {"document", "code"},
            )
            self.assertIn("chunk_id", attributes)
            self.assertIn("locator", attributes)

    def test_bundle_is_deterministic(self) -> None:
        first = build_mock_bundle(self.project_root())
        second = build_mock_bundle(self.project_root())
        self.assertEqual(first, second)

    async def test_runtime_manifest_round_trip(self) -> None:
        bundle = build_mock_bundle(self.project_root())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = write_runtime_manifest(bundle, root)
            self.assertTrue(path.is_file())
            loaded = await FileSourceManifestProvider(root).load(
                project_id=bundle.project_id,
                repository=bundle.repository,
                commit_sha=bundle.commit_sha,
            )
            self.assertEqual(loaded, dict(bundle.manifest.files))

    def test_bundle_uses_real_source_snapshot_not_git_claim(self) -> None:
        bundle = build_mock_bundle(self.project_root())
        self.assertTrue(bundle.commit_sha.startswith("source-"))
        self.assertEqual(bundle.manifest.commit_sha, bundle.commit_sha)
        self.assertIn("prd_agent/workflow.py", bundle.manifest.files)


if __name__ == "__main__":
    unittest.main()
