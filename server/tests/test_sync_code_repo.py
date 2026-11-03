from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from botocore.exceptions import ClientError

from prd_agent.kb_bundle import BundleObject, build_code_bundle

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


sync = _load("sync_code_repo")

SHA = "a" * 40
FILES = {
    "services/api/src/app.py": "def create_app():\n    return 'app'\n",
    "admin/src/App.tsx": "export function App() { return null; }\n",
}


def _write_tree(root: Path, files: dict[str, str]) -> None:
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _git(repo: Path, *args: str, stdin: str | None = None) -> str:
    env = dict(
        os.environ,
        GIT_AUTHOR_NAME="test",
        GIT_AUTHOR_EMAIL="test@example.invalid",
        GIT_COMMITTER_NAME="test",
        GIT_COMMITTER_EMAIL="test@example.invalid",
    )
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        input=stdin,
        capture_output=True,
        text=True,
        check=True,
        env=env,
    ).stdout.strip()


def _commit(repo: Path, files: dict[str, str]) -> str:
    """Commit ``files`` with plumbing commands, which run no hooks."""

    _write_tree(repo, files)
    _git(repo, "update-index", "--add", *files)
    tree = _git(repo, "write-tree")
    sha = _git(repo, "commit-tree", tree, "-m", "fixture")
    _git(repo, "update-ref", "refs/heads/main", sha)
    return sha


def _bundle(root: Path, files: dict[str, str] = FILES):
    _write_tree(root, files)
    return build_code_bundle(
        root, project_id="demo", repository="sample", commit_sha=SHA
    )


class BuildCodeBundleTest(unittest.TestCase):
    def test_objects_are_code_pairs_pinned_to_the_commit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = _bundle(Path(directory))
        keys = [item.key for item in bundle.objects]
        content = sorted(key for key in keys if key.endswith(".txt"))
        self.assertEqual(len(content), 2)
        self.assertEqual(
            sorted(key for key in keys if key.endswith(".metadata.json")),
            [f"{key}.metadata.json" for key in content],
        )
        self.assertTrue(all(key.startswith("content/code/") for key in keys))
        for item in bundle.objects:
            if not item.key.endswith(".metadata.json"):
                continue
            attributes = json.loads(item.body)["metadataAttributes"]
            value = {k: v["value"].get("stringValue") for k, v in attributes.items()}
            self.assertEqual(value["project_id"], "demo")
            self.assertEqual(value["source_type"], "code")
            self.assertEqual(value["commit_sha"], SHA)
            self.assertEqual(value["repository"], "sample")
            self.assertIn(value["file_path"], FILES)
        self.assertEqual(set(bundle.manifest.files), set(FILES))
        self.assertEqual(bundle.manifest.commit_sha, SHA)
        self.assertEqual(
            bundle.manifest.files["services/api/src/app.py"], ("create_app",)
        )

    def test_bundle_is_deterministic(self) -> None:
        with (
            tempfile.TemporaryDirectory() as first,
            tempfile.TemporaryDirectory() as second,
        ):
            self.assertEqual(_bundle(Path(first)), _bundle(Path(second)))

    def test_tree_without_source_files_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "notes.txt").write_text("hello\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                build_code_bundle(
                    Path(directory),
                    project_id="demo",
                    repository="sample",
                    commit_sha=SHA,
                )


class GitExportTest(unittest.TestCase):
    def test_export_contains_only_committed_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory) / "repo"
            repo.mkdir()
            _git(repo, "init", "-q")
            sha = _commit(repo, FILES)
            _write_tree(repo, {"admin/src/draft.ts": "export const draft = 1;\n"})
            _write_tree(
                repo, {"admin/node_modules/x/index.js": "module.exports = 1;\n"}
            )

            self.assertEqual(sync.resolve_commit(repo, "main"), sha)
            tree = Path(directory) / "tree"
            tree.mkdir()
            sync.export_commit(repo, sha, tree)
            exported = sorted(
                path.relative_to(tree).as_posix()
                for path in tree.rglob("*")
                if path.is_file()
            )
            self.assertEqual(exported, sorted(FILES))

    def test_resolve_commit_rejects_options_and_unknown_revisions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            _git(repo, "init", "-q")
            _commit(repo, FILES)
            with self.assertRaises(sync.SyncError):
                sync.resolve_commit(repo, "--all")
            with self.assertRaises(sync.SyncError):
                sync.resolve_commit(repo, "no-such-branch")


class FakeBody:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


class FakeS3:
    def __init__(self, existing: dict[str, bytes] | None = None) -> None:
        self.existing = dict(existing or {})
        self.puts: list[dict] = []

    def get_paginator(self, name: str):
        assert name == "list_objects_v2"
        fake = self

        class Paginator:
            def paginate(self, *, Bucket: str, Prefix: str):
                yield {
                    "Contents": [
                        {"Key": k} for k in fake.existing if k.startswith(Prefix)
                    ]
                }

        return Paginator()

    def get_object(self, *, Bucket: str, Key: str):
        return {"Body": FakeBody(self.existing[Key])}

    def put_object(self, **kwargs):
        if kwargs.get("IfNoneMatch") == "*" and kwargs["Key"] in self.existing:
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
        self.puts.append(kwargs)
        self.existing[kwargs["Key"]] = kwargs["Body"]
        return {}


def _obj(key: str, body: bytes = b"x") -> BundleObject:
    return BundleObject(key, body, "text/plain; charset=utf-8")


class UploadTest(unittest.TestCase):
    def test_plan_splits_new_and_identical(self) -> None:
        s3 = FakeS3({"content/code/a.txt": b"same"})
        new, identical = sync.plan_uploads(
            s3,
            "bucket",
            [_obj("content/code/a.txt", b"same"), _obj("content/code/b.txt")],
        )
        self.assertEqual([item.key for item in new], ["content/code/b.txt"])
        self.assertEqual([item.key for item in identical], ["content/code/a.txt"])

    def test_plan_refuses_to_overwrite_different_content(self) -> None:
        s3 = FakeS3({"content/code/a.txt": b"old"})
        with self.assertRaises(sync.SyncError):
            sync.plan_uploads(s3, "bucket", [_obj("content/code/a.txt", b"new")])

    def test_plan_rejects_keys_outside_code_prefix(self) -> None:
        with self.assertRaises(sync.SyncError):
            sync.plan_uploads(FakeS3(), "bucket", [_obj("content/document/a.txt")])

    def test_upload_is_create_only_and_encrypted(self) -> None:
        s3 = FakeS3()
        count = sync.upload(
            s3, "bucket", [_obj("content/code/a.txt")], tags={"Project": "demo"}
        )
        self.assertEqual(count, 1)
        put = s3.puts[0]
        self.assertEqual(put["IfNoneMatch"], "*")
        self.assertEqual(put["ServerSideEncryption"], "AES256")
        self.assertEqual(put["Tagging"], "Project=demo")

    def test_upload_stops_when_key_appeared_meanwhile(self) -> None:
        s3 = FakeS3({"content/code/a.txt": b"other"})
        with self.assertRaises(sync.SyncError):
            sync.upload(s3, "bucket", [_obj("content/code/a.txt")], tags={})


class FakeAgent:
    def __init__(self, statuses: list[dict]) -> None:
        self.statuses = list(statuses)
        self.started = 0

    def start_ingestion_job(self, **kwargs):
        self.started += 1
        return {"ingestionJob": {"ingestionJobId": "JOB1"}}

    def get_ingestion_job(self, **kwargs):
        job = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        return {"ingestionJob": {"ingestionJobId": "JOB1", **job}}


class IngestionTest(unittest.TestCase):
    def _run(self, agent: FakeAgent, **kwargs):
        with redirect_stderr(io.StringIO()):
            return sync.run_ingestion(
                agent,
                knowledge_base_id="KB",
                data_source_id="DS",
                description="d",
                sleep=lambda _: None,
                **kwargs,
            )

    def test_waits_until_complete(self) -> None:
        agent = FakeAgent(
            [
                {"status": "IN_PROGRESS"},
                {"status": "COMPLETE", "statistics": {"numberOfDocumentsFailed": 0}},
            ]
        )
        self.assertEqual(self._run(agent)["status"], "COMPLETE")
        self.assertEqual(agent.started, 1)

    def test_failed_documents_or_job_raise(self) -> None:
        for job in (
            {"status": "COMPLETE", "statistics": {"numberOfDocumentsFailed": 2}},
            {"status": "FAILED", "failureReasons": ["boom"]},
        ):
            with self.subTest(job=job["status"]), self.assertRaises(sync.SyncError):
                self._run(FakeAgent([job]))

    def test_times_out(self) -> None:
        ticks = iter([0.0, 1.0, 2.0, 99.0])
        with self.assertRaises(sync.SyncError):
            self._run(
                FakeAgent([{"status": "IN_PROGRESS"}]),
                timeout_seconds=10,
                clock=lambda: next(ticks),
            )


class FakeRuntime:
    def __init__(self, responses: list[list[dict]]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def retrieve(self, **kwargs):
        self.calls.append(kwargs)
        return {"retrievalResults": self.responses.pop(0)}


def _hit(**metadata):
    base = {
        "project_id": "demo",
        "source_type": "code",
        "commit_sha": SHA,
        "file_path": "services/api/src/app.py",
        "locator": "services/api/src/app.py:1-2",
    }
    base.update(metadata)
    return {"metadata": base, "score": 0.5, "content": {"text": "x"}}


class RetrievalCheckTest(unittest.TestCase):
    def _check(self, runtime: FakeRuntime, attempts: int = 2):
        with redirect_stderr(io.StringIO()):
            return sync.check_retrieval(
                runtime,
                knowledge_base_id="KB",
                project_id="demo",
                commit_sha=SHA,
                manifest_files=FILES,
                query="q",
                attempts=attempts,
                sleep=lambda _: None,
            )

    def test_uses_prd_filter_and_returns_hits(self) -> None:
        runtime = FakeRuntime([[_hit()]])
        hits = self._check(runtime)
        self.assertEqual(hits[0]["file_path"], "services/api/src/app.py")
        search = runtime.calls[0]["retrievalConfiguration"][
            "managedSearchConfiguration"
        ]
        code_branch = search["filter"]["orAll"][1]["andAll"]
        self.assertIn({"equals": {"key": "commit_sha", "value": SHA}}, code_branch)
        self.assertIn({"equals": {"key": "source_type", "value": "code"}}, code_branch)

    def test_passage_from_another_commit_or_file_fails(self) -> None:
        for metadata in (
            {"commit_sha": "b" * 40},
            {"file_path": "elsewhere.py"},
            {"project_id": "other"},
        ):
            with self.subTest(metadata=metadata), self.assertRaises(sync.SyncError):
                self._check(FakeRuntime([[_hit(**metadata)]]))

    def test_retries_then_fails_when_nothing_is_indexed(self) -> None:
        runtime = FakeRuntime([[], []])
        with self.assertRaises(sync.SyncError):
            self._check(runtime, attempts=2)
        self.assertEqual(len(runtime.calls), 2)


class DryRunTest(unittest.TestCase):
    def test_dry_run_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory) / "repo"
            repo.mkdir()
            _git(repo, "init", "-q")
            sha = _commit(repo, FILES)
            manifest_root = Path(directory) / "manifests"
            s3 = FakeS3()
            agent = mock.Mock()
            agent.get_data_source.return_value = {
                "dataSource": {"status": "AVAILABLE", "name": "ds"}
            }
            session = mock.Mock()
            session.client.side_effect = lambda name: {
                "s3": s3,
                "bedrock-agent": agent,
            }[name]
            out = io.StringIO()
            with (
                mock.patch.object(sync.boto3, "Session", return_value=session),
                redirect_stdout(out),
                redirect_stderr(io.StringIO()),
            ):
                code = sync.main(
                    [
                        "--repo",
                        str(repo),
                        "--commit",
                        "main",
                        "--project-id",
                        "demo",
                        "--repository-name",
                        "sample",
                        "--bucket",
                        "b",
                        "--knowledge-base-id",
                        "KB",
                        "--data-source-id",
                        "DS",
                        "--manifest-root",
                        str(manifest_root),
                    ]
                )
            self.assertEqual(code, 0)
            self.assertEqual(s3.puts, [])
            agent.start_ingestion_job.assert_not_called()
            self.assertFalse(manifest_root.exists())
            report = json.loads(out.getvalue())
            self.assertEqual(report["mode"], "dry-run")
            self.assertEqual(report["plan"]["commit_sha"], sha)
            self.assertEqual(report["plan"]["objects_to_upload"], 4)


if __name__ == "__main__":
    unittest.main()
