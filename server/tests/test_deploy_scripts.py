from __future__ import annotations

import importlib.util
import json
import stat
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


deploy_runtime = _load("deploy_runtime")
invoke_runtime = _load("invoke_runtime")


class PackagingTest(unittest.TestCase):
    def test_stage_copies_runtime_files_without_bytecode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp)
            deploy_runtime.stage_application(ROOT, package)
            self.assertTrue((package / "app.py").is_file())
            self.assertTrue((package / "src" / "prd_agent" / "workflow.py").is_file())
            self.assertTrue(
                (package / "contracts" / deploy_runtime.CONTRACT_FILE).is_file()
            )
            self.assertEqual(list(package.rglob("__pycache__")), [])

    def test_zip_is_deterministic_with_runtime_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp) / "package"
            (package / "pkg" / "__pycache__").mkdir(parents=True)
            (package / "pkg" / "__pycache__" / "x.cpython-312.pyc").write_bytes(b"x")
            (package / "pkg" / "mod.py").write_text("VALUE = 1\n")
            tool = package / "bin" / "tool"
            tool.parent.mkdir()
            tool.write_text("#!/bin/sh\n")
            tool.chmod(0o700)
            first = deploy_runtime.write_zip(package, Path(tmp) / "a.zip")
            second = deploy_runtime.write_zip(package, Path(tmp) / "b.zip")
            self.assertEqual(first["sha256"], second["sha256"])
            with zipfile.ZipFile(Path(tmp) / "a.zip") as archive:
                modes = {
                    info.filename: stat.S_IMODE(info.external_attr >> 16)
                    for info in archive.infolist()
                }
            self.assertEqual(modes, {"bin/tool": 0o755, "pkg/mod.py": 0o644})


class InvocationHelpersTest(unittest.TestCase):
    def test_parse_sse_ignores_blank_and_comment_lines(self) -> None:
        lines = [
            b'data: {"event": "started", "run_id": "r"}',
            b"",
            b": keep-alive",
            b'data: {"event": "result", "result": {"status": "completed"}}',
        ]
        events = list(invoke_runtime.parse_sse(lines))
        self.assertEqual([event["event"] for event in events], ["started", "result"])

    def test_request_is_pinned_to_snapshot_and_carries_documents(self) -> None:
        template = json.loads((ROOT / "deploy" / "cloud-e2e-request.json").read_text())
        state = {
            "project_id": "mock-prd-e2e",
            "repository": "agentcore-prd-agent",
            "source_snapshot": "source-new",
            "ingestion_job_id": "JOB123",
        }
        documents = [{"path": "a.md", "content": "# A\n\nB"}]
        request = invoke_runtime.build_request(
            {**template, "knowledge_base_snapshot": "stale"}, state, "e2e-1", documents
        )
        self.assertEqual(request["repository_commit"], "source-new")
        self.assertNotIn("knowledge_base_snapshot", request)
        self.assertEqual(request["documents"], documents)
        self.assertEqual(request["mode"], "one_shot")
        self.assertEqual(request["prd_contract_version"], "mock-csv-import-v0.1")
        from prd_agent.contracts import InvocationRequest

        InvocationRequest.model_validate(
            {key: value for key, value in request.items() if key != "run_id"}
        )

    def test_documents_are_loaded_with_relative_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "specs").mkdir()
            (root / "specs" / "b.md").write_text("# B\n\nbody", encoding="utf-8")
            (root / "a.md").write_text("# A\n\nbody", encoding="utf-8")
            (root / "notes.txt").write_text("ignored", encoding="utf-8")
            (root / ".hidden").mkdir()
            (root / ".hidden" / "c.md").write_text("# C", encoding="utf-8")
            documents = invoke_runtime.load_documents(root)
            self.assertEqual(
                [item["path"] for item in documents], ["a.md", "specs/b.md"]
            )
            with self.assertRaisesRegex(RuntimeError, "not a directory"):
                invoke_runtime.load_documents(root / "specs" / "missing")
            (root / "empty").mkdir()
            with self.assertRaisesRegex(RuntimeError, "no Markdown"):
                invoke_runtime.load_documents(root / "empty")

    def test_default_documents_are_a_valid_task_payload(self) -> None:
        documents = invoke_runtime.load_documents(ROOT / "examples")
        self.assertEqual(
            [item["path"] for item in documents], ["mock-csv-batch-user-import-prd.md"]
        )

    def test_summary_reports_validation_outcome(self) -> None:
        summary = invoke_runtime.summarize(
            [
                {"event": "started"},
                {"event": "progress"},
                {
                    "event": "result",
                    "result": {
                        "status": "completed",
                        "validation_report": {"passed": True, "repair_attempts": 1},
                    },
                },
            ]
        )
        self.assertEqual(summary["final_event"], "result")
        self.assertTrue(summary["validation_passed"])
        self.assertEqual(summary["event_counts"]["progress"], 1)


if __name__ == "__main__":
    unittest.main()
