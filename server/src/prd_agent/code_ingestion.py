"""Safe source-repository preprocessing for AgentCore Knowledge Base ingestion."""

from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_EXCLUDED_DIRECTORIES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "target",
        "vendor",
    }
)
_EXCLUDED_FILE_NAMES = frozenset(
    {
        "Cargo.lock",
        "package-lock.json",
        "pnpm-lock.yaml",
        "poetry.lock",
        "yarn.lock",
    }
)
_LANGUAGE_BY_SUFFIX = {
    ".c": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cs": "csharp",
    ".go": "go",
    ".h": "c",
    ".hpp": "cpp",
    ".java": "java",
    ".js": "javascript",
    ".json": "json",
    ".jsx": "javascript",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".php": "php",
    ".py": "python",
    ".rb": "ruby",
    ".rs": "rust",
    ".scala": "scala",
    ".sql": "sql",
    ".swift": "swift",
    ".toml": "toml",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".xml": "xml",
    ".yaml": "yaml",
    ".yml": "yaml",
}


@dataclass(frozen=True, slots=True)
class CodeChunk:
    """A source-code chunk anchored to an immutable repository commit."""

    chunk_id: str
    content: str
    project_id: str
    repository: str
    commit_sha: str
    file_path: str
    language: str
    line_start: int
    line_end: int
    symbols: tuple[str, ...]
    source_hash: str

    def as_ingestion_record(self) -> dict[str, Any]:
        return {
            "id": self.chunk_id,
            "content": self.content,
            "metadata": {
                "chunk_id": self.chunk_id,
                "project_id": self.project_id,
                "source_type": "code",
                "repository": self.repository,
                "commit_sha": self.commit_sha,
                "file_path": self.file_path,
                "language": self.language,
                "line_start": self.line_start,
                "line_end": self.line_end,
                "symbols": list(self.symbols),
                "source_hash": self.source_hash,
            },
        }


@dataclass(frozen=True, slots=True)
class RepositorySnapshot:
    """Generated KB records plus the manifest used by deterministic validation."""

    chunks: tuple[CodeChunk, ...]
    manifest: dict[str, frozenset[str]]


class _PythonSymbolVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.stack: list[str] = []
        self.symbols: list[str] = []

    def _visit_named(self, node: ast.AST, name: str) -> None:
        qualified = ".".join((*self.stack, name))
        self.symbols.append(qualified)
        self.stack.append(name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._visit_named(node, node.name)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_named(node, node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_named(node, node.name)


def extract_python_symbols(source: str) -> tuple[str, ...]:
    """Return qualified Python symbols without importing or executing source."""

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return ()
    visitor = _PythonSymbolVisitor()
    visitor.visit(tree)
    return tuple(visitor.symbols)


def _split_source(source: str, limit: int) -> list[tuple[str, int, int]]:
    lines = source.splitlines(keepends=True)
    chunks: list[tuple[str, int, int]] = []
    current: list[str] = []
    current_length = 0
    start_line = 1

    def flush(end_line: int) -> None:
        nonlocal current, current_length, start_line
        body = "".join(current).rstrip()
        if body:
            chunks.append((body, start_line, end_line))
        current = []
        current_length = 0

    for line_number, line in enumerate(lines, start=1):
        if len(line) > limit:
            if current:
                flush(line_number - 1)
            remaining = line.rstrip("\n")
            while remaining:
                part = remaining[:limit]
                remaining = remaining[limit:]
                chunks.append((part, line_number, line_number))
            start_line = line_number + 1
            continue

        if current and current_length + len(line) > limit:
            flush(line_number - 1)
            start_line = line_number
        elif not current:
            start_line = line_number

        current.append(line)
        current_length += len(line)

    if current:
        flush(len(lines))
    return chunks


def _candidate_files(root: Path) -> list[Path]:
    candidates: list[Path] = []
    for path in root.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        relative = path.relative_to(root)
        if any(part in _EXCLUDED_DIRECTORIES for part in relative.parts[:-1]):
            continue
        if path.name in _EXCLUDED_FILE_NAMES:
            continue
        if path.suffix.lower() not in _LANGUAGE_BY_SUFFIX:
            continue
        candidates.append(path)
    return sorted(candidates, key=lambda item: item.relative_to(root).as_posix())


def build_repository_snapshot(
    root: Path,
    *,
    project_id: str,
    repository: str,
    commit_sha: str,
    max_file_bytes: int = 1_000_000,
    max_chars: int = 8000,
) -> RepositorySnapshot:
    """Build source records without executing code or invoking external tools."""

    if not project_id or not repository or not commit_sha:
        raise ValueError("project_id, repository, and commit_sha are required")
    if max_file_bytes < 1:
        raise ValueError("max_file_bytes must be positive")
    if max_chars < 512:
        raise ValueError("max_chars must be at least 512")

    resolved_root = root.resolve()
    if not resolved_root.is_dir():
        raise ValueError("root must be an existing directory")

    chunks: list[CodeChunk] = []
    manifest: dict[str, frozenset[str]] = {}

    for path in _candidate_files(resolved_root):
        if path.stat().st_size > max_file_bytes:
            continue
        raw = path.read_bytes()
        if b"\x00" in raw:
            continue
        try:
            source = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if not source.strip():
            continue

        relative_path = path.relative_to(resolved_root).as_posix()
        language = _LANGUAGE_BY_SUFFIX[path.suffix.lower()]
        symbols = extract_python_symbols(source) if language == "python" else ()
        manifest[relative_path] = frozenset(symbols)
        source_hash = hashlib.sha256(raw).hexdigest()
        displayed_symbols = ", ".join(symbols[:50]) or "(not extracted)"
        prefix_base = (
            f"Repository: {repository}\n"
            f"Commit: {commit_sha}\n"
            f"File: {relative_path}\n"
            f"Language: {language}\n"
            f"Symbols: {displayed_symbols}\n"
        )
        body_limit = max_chars - len(prefix_base) - 40
        if body_limit < 128:
            raise ValueError("max_chars is too small for source metadata")

        for body, line_start, line_end in _split_source(source, body_limit):
            content = (
                f"{prefix_base}Lines: {line_start}-{line_end}\n\n{body}"
            )
            identity = "\0".join(
                (
                    project_id,
                    repository,
                    commit_sha,
                    relative_path,
                    str(line_start),
                    str(line_end),
                    body,
                )
            )
            chunk_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
            chunks.append(
                CodeChunk(
                    chunk_id=chunk_id,
                    content=content,
                    project_id=project_id,
                    repository=repository,
                    commit_sha=commit_sha,
                    file_path=relative_path,
                    language=language,
                    line_start=line_start,
                    line_end=line_end,
                    symbols=symbols,
                    source_hash=source_hash,
                )
            )

    return RepositorySnapshot(chunks=tuple(chunks), manifest=manifest)
