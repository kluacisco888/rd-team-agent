"""Deployable Amazon Bedrock AgentCore Runtime entrypoint."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from prd_agent.application import build_runtime_app  # noqa: E402
from prd_agent.settings import load_settings  # noqa: E402

runtime_environment = dict(os.environ)
runtime_environment.setdefault(
    "PRD_CONTRACT_PATH",
    str(ROOT / "contracts" / "mock-csv-batch-user-import.contract.json"),
)
app = build_runtime_app(
    load_settings(runtime_environment),
    source_root=SRC,
)

if __name__ == "__main__":
    app.run(port=8080)
