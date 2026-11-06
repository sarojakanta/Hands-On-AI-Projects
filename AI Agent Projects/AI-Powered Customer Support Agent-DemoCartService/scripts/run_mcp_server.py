"""Launch a domain MCP server as a standalone process (for debugging).

Usage (from project root, Windows PowerShell):
    python scripts/run_mcp_server.py crm        # crm-mcp
    python scripts/run_mcp_server.py oms         # oms-mcp
    python scripts/run_mcp_server.py helpdesk    # helpdesk-mcp
    python scripts/run_mcp_server.py store       # store-mcp

Normally the agent's multi-server MCP client launches these automatically over
stdio; this script is for manual inspection.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp_server.domains.domain_server import run  # noqa: E402

_ALIASES = {
    "crm": "crm-mcp",
    "oms": "oms-mcp",
    "helpdesk": "helpdesk-mcp",
    "store": "store-mcp",
}


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in _ALIASES:
        print("Usage: python scripts/run_mcp_server.py [crm|oms|helpdesk|store]")
        raise SystemExit(2)
    run(_ALIASES[sys.argv[1]])


if __name__ == "__main__":
    main()
