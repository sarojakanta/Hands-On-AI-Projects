"""Configurable table and field mappings. Nothing instance-specific is hardcoded in the client.

Defaults cover a standard ServiceNow instance. Override any part with a JSON file
(``SERVICENOW_FIELD_MAP_FILE``), e.g.::

    {"incident": {"table": "u_incident", "fields": {"assignment_group": "u_group"},
                  "write_fields": ["work_notes"]}}
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path

CANONICAL_FIELDS = ["sys_id", "number", "short_description", "description", "priority", "state", "category",
                    "impact", "urgency", "assignment_group"]
# Fields where the machine value (e.g. "1") is wanted rather than the display label.
CODE_FIELDS = {"priority", "impact", "urgency"}

_DEFAULTS: dict[str, dict] = {
    "incident": {"table": "incident", "extra_fields": ["cmdb_ci", "business_service", "opened_at"],
                 "write_fields": ["work_notes", "priority"]},
    "problem": {"table": "problem", "extra_fields": ["cmdb_ci", "business_service"], "write_fields": ["work_notes"]},
    "change": {"table": "change_request", "extra_fields": ["cmdb_ci", "type", "risk", "start_date", "end_date"],
               "write_fields": ["work_notes"]},
    "request": {"table": "sc_request", "extra_fields": ["requested_for", "request_state"],
                "write_fields": ["work_notes"], "items_table": "sc_req_item", "items_parent_field": "request"},
}


@dataclass
class TableMapping:
    record_type: str
    table: str
    fields: dict[str, str] = field(default_factory=lambda: {f: f for f in CANONICAL_FIELDS})
    extra_fields: list[str] = field(default_factory=list)
    write_fields: list[str] = field(default_factory=list)  # canonical names allowed to be written
    items_table: str = ""
    items_parent_field: str = ""

    def sn(self, canonical: str) -> str:
        return self.fields.get(canonical, canonical)

    def query_fields(self) -> str:
        return ",".join(dict.fromkeys([self.sn(f) for f in CANONICAL_FIELDS] + self.extra_fields))

    def write_names(self) -> set[str]:
        return {self.sn(f) for f in self.write_fields} | set(self.write_fields)


def load_mappings(path: str = "") -> dict[str, TableMapping]:
    merged = copy.deepcopy(_DEFAULTS)
    if path:
        override = json.loads(Path(path).read_text(encoding="utf-8"))
        for rtype, cfg in override.items():
            if rtype not in merged:
                raise ValueError(f"Unknown record type in field map: {rtype}")
            for key, value in cfg.items():
                if key == "fields":
                    merged[rtype].setdefault("fields", {}).update(value)
                else:
                    merged[rtype][key] = value
    out = {}
    for rtype, cfg in merged.items():
        fields = {f: f for f in CANONICAL_FIELDS}
        fields.update(cfg.get("fields", {}))
        out[rtype] = TableMapping(
            record_type=rtype, table=cfg["table"], fields=fields, extra_fields=list(cfg.get("extra_fields", [])),
            write_fields=list(cfg.get("write_fields", [])), items_table=cfg.get("items_table", ""),
            items_parent_field=cfg.get("items_parent_field", ""))
    return out
