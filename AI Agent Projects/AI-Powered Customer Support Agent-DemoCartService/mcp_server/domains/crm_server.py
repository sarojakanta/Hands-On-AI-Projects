"""crm-mcp server entrypoint (CRM: identity + customer profile).

Run: python -m mcp_server.domains.crm_server
"""

from __future__ import annotations

from mcp_server.domains.domain_server import run


def main() -> None:
    run("crm-mcp")


if __name__ == "__main__":
    main()
