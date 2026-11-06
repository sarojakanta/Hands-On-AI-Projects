"""helpdesk-mcp server entrypoint (Helpdesk: tickets + escalation).

Run: python -m mcp_server.domains.helpdesk_server
"""

from __future__ import annotations

from mcp_server.domains.domain_server import run


def main() -> None:
    run("helpdesk-mcp")


if __name__ == "__main__":
    main()
