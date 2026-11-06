"""oms-mcp server entrypoint (Order Management: orders, shipments, address change).

Run: python -m mcp_server.domains.oms_server
"""

from __future__ import annotations

from mcp_server.domains.domain_server import run


def main() -> None:
    run("oms-mcp")


if __name__ == "__main__":
    main()
