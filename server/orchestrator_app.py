"""AgentCore Runtime entrypoint for the R&D team agent orchestrator (JWT inbound)."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from orchestrator.app import build_orchestrator_app  # noqa: E402
from orchestrator.settings import load_orchestrator_settings  # noqa: E402

logging.basicConfig(level=logging.INFO)

environment = dict(os.environ)
environment.setdefault(
    "PRD_CONTRACT_PATH",
    str(ROOT / "contracts" / "mock-csv-batch-user-import.contract.json"),
)
# The deploy script packages source-manifests/<hash>.json at the package root.
environment.setdefault("SOURCE_MANIFEST_ROOT", str(ROOT))
app = build_orchestrator_app(load_orchestrator_settings(environment))

if __name__ == "__main__":
    app.run(port=8080)
