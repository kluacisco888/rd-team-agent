from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from prd_agent.code_ingestion import (
    build_repository_snapshot,
    extract_python_symbols,
)


class PythonSymbolTest(unittest.TestCase):
    def test_extracts_qualified_symbols_without_execution(self) -> None:
        symbols = extract_python_symbols(
            """def top_level():
    pass

class RefundService:
    async def create_refund(self):
        def nested():
            return 1
        return nested()
"""
        )
        self.assertEqual(
            symbols,
            (
                "top_level",
                "RefundService",
                "RefundService.create_refund",
                "RefundService.create_refund.nested",
            ),
        )

    def test_invalid_python_returns_no_symbols(self) -> None:
        self.assertEqual(extract_python_symbols("def broken("), ())


class RepositorySnapshotTest(unittest.TestCase):
    def build(self, root: Path, **overrides: object):
        arguments = {
            "project_id": "project-1",
            "repository": "example/service",
            "commit_sha": "abc123",
        }
        arguments.update(overrides)
        return build_repository_snapshot(root, **arguments)  # type: ignore[arg-type]

    def test_builds_stable_chunks_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "src" / "refund.py"
            source.parent.mkdir()
            source.write_text(
                "class RefundService:\n    def create(self):\n        return True\n",
                encoding="utf-8",
            )

            first = self.build(root)
            second = self.build(root)

            self.assertEqual(first.chunks, second.chunks)
            self.assertEqual(
                first.manifest["src/refund.py"],
                frozenset({"RefundService", "RefundService.create"}),
            )
            record = first.chunks[0].as_ingestion_record()
            self.assertEqual(record["metadata"]["source_type"], "code")
            self.assertEqual(record["metadata"]["commit_sha"], "abc123")
            self.assertIn("File: src/refund.py", record["content"])

    def test_filters_dependencies_locks_binary_and_unsupported_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
            (root / "README.md").write_text("not code input", encoding="utf-8")
            (root / "package-lock.json").write_text("{}", encoding="utf-8")
            (root / "binary.py").write_bytes(b"source\x00binary")
            dependency = root / "node_modules" / "dependency.js"
            dependency.parent.mkdir()
            dependency.write_text("export const value = 1;", encoding="utf-8")

            snapshot = self.build(root)
            self.assertEqual(set(snapshot.manifest), {"app.py"})
            self.assertEqual({chunk.file_path for chunk in snapshot.chunks}, {"app.py"})

    def test_skips_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside.py"
            outside.write_text("SECRET = 'not ingested through symlink'\n", encoding="utf-8")
            link = root / "linked.py"
            try:
                os.symlink(outside, link)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks are unavailable")

            snapshot = self.build(root)
            self.assertIn("outside.py", snapshot.manifest)
            self.assertNotIn("linked.py", snapshot.manifest)

    def test_splits_long_files_and_preserves_line_ranges(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "service.py"
            source.write_text(
                "".join(f"VALUE_{index} = {index}\n" for index in range(150)),
                encoding="utf-8",
            )

            snapshot = self.build(root, max_chars=512)
            self.assertGreater(len(snapshot.chunks), 1)
            self.assertEqual(snapshot.chunks[0].line_start, 1)
            self.assertEqual(snapshot.chunks[-1].line_end, 150)
            self.assertTrue(all(len(chunk.content) <= 512 for chunk in snapshot.chunks))

    def test_commit_changes_chunk_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
            first = self.build(root, commit_sha="commit-1")
            second = self.build(root, commit_sha="commit-2")
            self.assertNotEqual(first.chunks[0].chunk_id, second.chunks[0].chunk_id)

    def test_skips_oversized_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "large.py").write_text("x" * 100, encoding="utf-8")
            snapshot = self.build(root, max_file_bytes=50)
            self.assertEqual(snapshot.chunks, ())
            self.assertEqual(snapshot.manifest, {})

    def test_rejects_missing_identity_and_invalid_limits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "required"):
                self.build(root, commit_sha="")
            with self.assertRaisesRegex(ValueError, "at least 512"):
                self.build(root, max_chars=511)


if __name__ == "__main__":
    unittest.main()
