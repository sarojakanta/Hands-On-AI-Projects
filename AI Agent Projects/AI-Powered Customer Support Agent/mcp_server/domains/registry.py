"""Domain MCP registries with per-tool access tiers.

Four domain servers, each fronting a (simulated) enterprise system:

    crm-mcp      -> CRM (e.g. Salesforce):  send_otp, verify_customer, get_customer_profile
    oms-mcp      -> OMS (e.g. SAP):         get_order, get_shipment_status, request_address_change
    helpdesk-mcp -> Helpdesk (ServiceNow):  create_ticket, escalate_to_human
    store-mcp    -> Store system:           find_store, get_store_hours

Each tool carries an access tier:
    read         - read-only
    write_low    - low-risk write
    write_guarded- sensitive write requiring confirmation/rule checks

A single source of truth: both the per-domain MCP servers and the multi-server
client build their tool surface from here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from mcp_server.tools import (
    auth_tools,
    customer_tools,
    order_tools,
    shipment_tools,
    store_tools,
    ticket_tools,
)

# Access tiers.
READ = "read"
WRITE_LOW = "write_low"
WRITE_GUARDED = "write_guarded"


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict
    handler: Callable[[dict], dict]
    access: str
    server: str


# ---- crm-mcp ----
_CRM = "crm-mcp"
_OMS = "oms-mcp"
_HELPDESK = "helpdesk-mcp"
_STORE = "store-mcp"


DOMAINS: dict[str, list[ToolSpec]] = {
    _CRM: [
        ToolSpec(
            name="send_otp",
            description="Send a one-time verification code to the customer's registered contact.",
            input_schema=auth_tools.INITIATE_OTP_SCHEMA,
            handler=auth_tools.initiate_otp,
            access=WRITE_LOW,
            server=_CRM,
        ),
        ToolSpec(
            name="verify_customer",
            description="Verify a one-time code for a customer and session.",
            input_schema=auth_tools.VERIFY_CUSTOMER_SCHEMA,
            handler=auth_tools.verify_customer,
            access=WRITE_LOW,
            server=_CRM,
        ),
        ToolSpec(
            name="get_customer_profile",
            description="Retrieve the customer's CRM profile (language, tier, masked contact). Requires verification.",
            input_schema=customer_tools.GET_CUSTOMER_PROFILE_SCHEMA,
            handler=customer_tools.get_customer_profile,
            access=READ,
            server=_CRM,
        ),
    ],
    _OMS: [
        ToolSpec(
            name="get_order",
            description="Retrieve order details. Requires a verified session for the order's customer.",
            input_schema=order_tools.GET_ORDER_SCHEMA,
            handler=order_tools.get_order,
            access=READ,
            server=_OMS,
        ),
        ToolSpec(
            name="get_shipment_status",
            description="Retrieve shipment/delivery status for an order. Requires verification.",
            input_schema=shipment_tools.GET_SHIPMENT_STATUS_SCHEMA,
            handler=shipment_tools.get_shipment_status,
            access=READ,
            server=_OMS,
        ),
        ToolSpec(
            name="request_address_change",
            description="Submit a delivery address-change request (guarded; only before dispatch).",
            input_schema=order_tools.REQUEST_ADDRESS_CHANGE_SCHEMA,
            handler=order_tools.request_address_change,
            access=WRITE_GUARDED,
            server=_OMS,
        ),
    ],
    _HELPDESK: [
        ToolSpec(
            name="create_ticket",
            description="Create a support ticket for a customer issue.",
            input_schema=ticket_tools.CREATE_SUPPORT_TICKET_SCHEMA,
            handler=ticket_tools.create_support_ticket,
            access=WRITE_LOW,
            server=_HELPDESK,
        ),
        ToolSpec(
            name="escalate_to_human",
            description="Escalate the conversation to a live human-agent queue with full context.",
            input_schema=ticket_tools.ESCALATE_TO_HUMAN_SCHEMA,
            handler=ticket_tools.escalate_to_human,
            access=WRITE_LOW,
            server=_HELPDESK,
        ),
    ],
    _STORE: [
        ToolSpec(
            name="find_store",
            description="Find physical stores by name, city, or address. Public information.",
            input_schema=store_tools.SEARCH_STORE_SCHEMA,
            handler=store_tools.search_store,
            access=READ,
            server=_STORE,
        ),
        ToolSpec(
            name="get_store_hours",
            description="Get the opening hours for a specific store. Public information.",
            input_schema=store_tools.GET_STORE_HOURS_SCHEMA,
            handler=store_tools.get_store_hours,
            access=READ,
            server=_STORE,
        ),
    ],
}

SERVER_NAMES = tuple(DOMAINS.keys())

# Flat name -> ToolSpec index across all domains.
ALL_TOOLS: dict[str, ToolSpec] = {
    spec.name: spec for specs in DOMAINS.values() for spec in specs
}

# Tool name -> owning server.
TOOL_SERVER: dict[str, str] = {name: spec.server for name, spec in ALL_TOOLS.items()}


def tools_for(server: str) -> list[ToolSpec]:
    return DOMAINS.get(server, [])


def call_tool(name: str, arguments: dict) -> dict:
    """Dispatch a tool call by name across all domains."""
    spec = ALL_TOOLS.get(name)
    if spec is None:
        return {
            "ok": False,
            "data": None,
            "error": "unknown_tool",
            "message": f"Tool '{name}' is not available.",
        }
    if not isinstance(arguments, dict):
        return {
            "ok": False,
            "data": None,
            "error": "invalid_arguments",
            "message": "Tool arguments must be an object.",
        }
    return spec.handler(arguments)
