"""Store MCP tools: search_store, get_store_hours.

Store information is public, so these tools do NOT require authentication.
"""

from __future__ import annotations

from app.services import store_service
from mcp_server.tools.base import ok, err, require_string


SEARCH_STORE_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "Free-text search (name/city/address)."},
        "city": {"type": "string", "description": "Filter by city."},
    },
    "required": [],
    "additionalProperties": False,
}

GET_STORE_HOURS_SCHEMA = {
    "type": "object",
    "properties": {
        "store_id": {"type": "string", "description": "Store reference, e.g. STORE-01."},
    },
    "required": ["store_id"],
    "additionalProperties": False,
}


def search_store(args: dict) -> dict:
    query = args.get("query")
    city = args.get("city")
    if query is not None and not isinstance(query, str):
        return err("invalid_arguments", "'query' must be a string.")
    if city is not None and not isinstance(city, str):
        return err("invalid_arguments", "'city' must be a string.")

    stores = store_service.search_stores(query=query, city=city)
    return ok(data={"stores": stores, "count": len(stores)}, message="Store search complete.")


def get_store_hours(args: dict) -> dict:
    store_id, e = require_string(args, "store_id")
    if e:
        return e
    hours = store_service.get_store_hours(store_id)
    if not hours:
        return err("not_found", "No such store was found.")
    return ok(data=hours, message="Store hours retrieved.")
