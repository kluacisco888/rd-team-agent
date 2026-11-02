from __future__ import annotations

import unittest

from prd_agent.ingestion import chunk_markdown


class MarkdownIngestionTest(unittest.TestCase):
    def chunk(self, markdown: str, **overrides: object):
        arguments = {
            "project_id": "project-1",
            "source_path": "docs/product.md",
            "document_version": "v1",
        }
        arguments.update(overrides)
        return chunk_markdown(markdown, **arguments)  # type: ignore[arg-type]

    def test_preserves_heading_hierarchy_and_metadata(self) -> None:
        chunks = self.chunk(
            """Preamble text.

# Payments
Payment overview.

## Refunds
Refund details.
"""
        )
        self.assertEqual(
            [chunk.heading for chunk in chunks],
            ["(document root)", "Payments", "Payments > Refunds"],
        )
        record = chunks[-1].as_ingestion_record()
        self.assertEqual(record["metadata"]["source_type"], "document")
        self.assertEqual(record["metadata"]["source_path"], "docs/product.md")
        self.assertEqual(record["metadata"]["heading"], "Payments > Refunds")
        self.assertIn("Source heading: Payments > Refunds", record["content"])

    def test_heading_like_text_inside_fence_is_not_a_section(self) -> None:
        chunks = self.chunk(
            """# API

```markdown
# This is example data, not a heading
```

API details.
"""
        )
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].heading, "API")
        self.assertIn("# This is example data", chunks[0].content)

    def test_chunk_ids_are_stable_and_version_sensitive(self) -> None:
        first = self.chunk("# Scope\n\nSupport refunds.")
        second = self.chunk("# Scope\n\nSupport refunds.")
        changed_version = self.chunk(
            "# Scope\n\nSupport refunds.", document_version="v2"
        )
        self.assertEqual(first[0].chunk_id, second[0].chunk_id)
        self.assertNotEqual(first[0].chunk_id, changed_version[0].chunk_id)

    def test_long_sections_split_without_exceeding_limit(self) -> None:
        paragraphs = [f"Paragraph {index} " + ("content " * 20) for index in range(8)]
        chunks = self.chunk(
            "# Large section\n\n" + "\n\n".join(paragraphs),
            max_chars=320,
        )
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk.content) <= 320 for chunk in chunks))
        combined = " ".join(chunk.content for chunk in chunks)
        for index in range(8):
            self.assertIn(f"Paragraph {index}", combined)

    def test_normalizes_newlines_before_hashing(self) -> None:
        unix = self.chunk("# Scope\n\nSupport refunds.\n")
        windows = self.chunk("# Scope\r\n\r\nSupport refunds.\r\n")
        self.assertEqual(unix[0].document_hash, windows[0].document_hash)
        self.assertEqual(unix[0].chunk_id, windows[0].chunk_id)

    def test_rejects_empty_documents_and_unsafe_paths(self) -> None:
        with self.assertRaisesRegex(ValueError, "non-whitespace"):
            self.chunk("  \n")

        for source_path in ("", "/absolute/doc.md", "../other-project/doc.md"):
            with self.subTest(source_path=source_path):
                with self.assertRaisesRegex(ValueError, "repository-relative"):
                    self.chunk("Content", source_path=source_path)

    def test_rejects_invalid_chunk_size(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least 256"):
            self.chunk("Content", max_chars=255)


if __name__ == "__main__":
    unittest.main()
