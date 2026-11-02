#!/usr/bin/env python3
"""Invoke the deployed Managed KB Gateway tool and print the raw MCP result."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from prd_agent.aws_auth import SigV4MCPClientFactory  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--tool", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--query", required=True)
    parser.add_argument("--user-id", default="mock-e2e-principal")
    args = parser.parse_args()

    factory = SigV4MCPClientFactory(url=args.url, region_name=args.region)
    with factory({}) as client:
        result = client.call_tool_sync(
            uuid4().hex,
            args.tool,
            {
                "retrievalQuery": {"text": args.query},
                "userContext": {"userId": args.user_id},
            },
        )
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 1 if result.get("status") != "success" else 0


if __name__ == "__main__":
    raise SystemExit(main())
