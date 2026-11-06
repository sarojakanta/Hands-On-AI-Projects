"""store-mcp server entrypoint (Store system: locator + hours).

Run: python -m mcp_server.domains.store_server
"""

from __future__ import annotations

from mcp_server.domains.domain_server import run


def main() -> None:
    run("store-mcp")


if __name__ == "__main__":
    main()
