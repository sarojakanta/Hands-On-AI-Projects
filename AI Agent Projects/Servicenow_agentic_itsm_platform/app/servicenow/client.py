"""Centralized ServiceNow Table API client (mock and live).

* Auth strategies (basic / OAuth), timeouts, retries with backoff, 429 handling.
* Pagination, response validation, configurable table/field mappings.
* Every call is logged (no payloads or credentials) to ``servicenow_call_logs``.
* Read/write separation: agents receive a ``ReadOnlyServiceNow`` view; writes need an
  ``ApprovalGrant`` whose payload exactly matches the write, an allow-listed field,
  and (in live mode) ``SERVICENOW_WRITE_ENABLED=true``.
"""
from __future__ import annotations

import copy
import logging
import re
import threading
import time
from typing import Any, Callable

from app.config import settings
from app.schemas import ITSMRecord
from app.security.sanitize import mask_secrets
from app.servicenow.auth import AuthStrategy, build_auth
from app.servicenow.errors import (
    ServiceNowAuthError,
    ServiceNowConfigError,
    ServiceNowError,
    ServiceNowRateLimitError,
    ServiceNowResponseError,
    ServiceNowTimeoutError,
    ServiceNowUnavailableError,
)
from app.servicenow.mappings import CANONICAL_FIELDS, CODE_FIELDS, TableMapping, load_mappings
from app.servicenow.mock_data import MOCK_ITEMS, MOCK_RECORDS
from app.servicenow.permissions import ApprovalGrant, WriteNotAllowedError

logger = logging.getLogger(__name__)

_ID = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
_log_lock = threading.Lock()
# Back-compat for callers that imported the table map from this module.
TABLES = {k: v.table for k, v in load_mappings().items()}


def _safe_term(term: str) -> str:
    """Strip anything that could alter a sysparm_query (^, =, operators, control chars)."""
    return re.sub(r"[^A-Za-z0-9 _.\-]", "", term or "").strip()[:80]


def _check_id(value: str, what: str = "identifier") -> str:
    if not isinstance(value, str) or not _ID.match(value):
        raise ValueError(f"Invalid {what}")
    return value


class ReadOnlyServiceNow:
    """Least-privilege view handed to agents: reads only, no write methods exist."""

    def __init__(self, client: "ServiceNowClient"):
        self._client = client

    def list_records(self, record_type: str, limit: int | None = None):
        return self._client.list_records(record_type, limit)

    def get_record(self, record_type: str, record_id: str):
        return self._client.get_record(record_type, record_id)

    def search_records(self, record_type: str, search: str = "", limit: int = 50, offset: int = 0):
        return self._client.search_records(record_type, search, limit, offset)

    def list_request_items(self, request_ref: str):
        return self._client.list_request_items(request_ref)


class ServiceNowClient:
    def __init__(self, mode: str | None = None, http: Any = None, auth: AuthStrategy | None = None,
                 sleep: Callable[[float], None] = time.sleep, mappings: dict[str, TableMapping] | None = None,
                 log_calls: bool = True):
        self.mode = (mode or settings.servicenow_mode).lower()
        self.mock = self.mode != "live"
        self.mappings = mappings or load_mappings(settings.servicenow_field_map_file)
        self._http = http
        self._auth = auth
        self._sleep = sleep
        self._log_calls = log_calls
        self._mock_rows = copy.deepcopy(MOCK_RECORDS)

    def read_only(self) -> ReadOnlyServiceNow:
        return ReadOnlyServiceNow(self)

    # ------------------------------------------------------------------ reads
    def list_records(self, record_type: str, limit: int | None = None) -> list[ITSMRecord]:
        m = self._mapping(record_type)
        if self.mock:
            self._log("GET", m.table, "", 200, 0.0, 1, True, None)
            rows = self._mock_rows[record_type][: limit or None]
            return [self._to_record(r, record_type, mock=True) for r in rows]
        rows, _ = self._get_all(m, {}, limit)
        return [self._to_record(r, record_type) for r in rows]

    def search_records(self, record_type: str, search: str = "", limit: int = 50,
                       offset: int = 0) -> tuple[list[ITSMRecord], int | None]:
        """One page of records plus the total match count when known."""
        m = self._mapping(record_type)
        term = _safe_term(search)
        limit = max(1, min(int(limit), settings.servicenow_max_records))
        offset = max(0, int(offset))
        if self.mock:
            self._log("GET", m.table, "", 200, 0.0, 1, True, None)
            rows = [r for r in self._mock_rows[record_type]
                    if not term or term.lower() in f"{r['number']} {r['short_description']} {r['description']}".lower()]
            return [self._to_record(r, record_type, mock=True) for r in rows[offset:offset + limit]], len(rows)
        query = ""
        if term:
            query = "^OR".join(f"{m.sn(f)}LIKE{term}" for f in ("number", "short_description", "description"))
        body, headers = self._request("GET", m.table, params=self._params(m, query, limit, offset))
        rows = self._validated_list(body)
        total = headers.get("X-Total-Count")
        return [self._to_record(r, record_type) for r in rows], int(total) if total and total.isdigit() else None

    def get_record(self, record_type: str, record_id: str) -> ITSMRecord:
        m = self._mapping(record_type)
        record_id = _check_id(record_id, "record id")
        if self.mock:
            self._log("GET", m.table, "", 200, 0.0, 1, True, None)
            for row in self._mock_rows[record_type]:
                if record_id in (row["number"], row["sys_id"]):
                    return self._to_record(row, record_type, mock=True)
            raise KeyError(f"{record_type} record not found: {record_id}")
        query = f"{m.sn('number')}={record_id}^OR{m.sn('sys_id')}={record_id}"
        body, _ = self._request("GET", m.table, params=self._params(m, query, 1, 0, order=False))
        rows = self._validated_list(body)
        if not rows:
            raise KeyError(f"{record_type} record not found: {record_id}")
        return self._to_record(rows[0], record_type)

    def list_request_items(self, request_ref: str) -> list[dict]:
        """Requested items (RITMs) that belong to a service request (number or sys_id)."""
        m = self._mapping("request")
        parent = self.get_record("request", request_ref)
        if self.mock:
            return copy.deepcopy(MOCK_ITEMS.get(parent.sys_id, []))
        if not m.items_table:
            return []
        query = f"{m.items_parent_field}={_check_id(parent.sys_id, 'sys_id')}"
        params = {"sysparm_query": query, "sysparm_limit": str(settings.servicenow_page_size),
                  "sysparm_display_value": "all", "sysparm_exclude_reference_link": "true",
                  "sysparm_fields": "sys_id,number,short_description,state"}
        body, _ = self._request("GET", m.items_table, params=params)
        return [{k: self._val(v, k) for k, v in row.items()} for row in self._validated_list(body)]

    # ----------------------------------------------------------------- writes
    def update_record(self, record: ITSMRecord, fields: dict, grant: ApprovalGrant | None = None) -> dict:
        """Write approved fields. Raises WriteNotAllowedError unless a matching grant is supplied."""
        m = self._mapping(record.record_type)
        self._authorize_write(m, record, fields, grant)
        if self.mock:
            row = next(r for r in self._mock_rows[record.record_type] if r["number"] == record.number)
            for key, value in fields.items():
                if key == "work_notes":
                    row.setdefault("work_notes_log", []).append(str(value))
                else:
                    row[key] = value
            self._log("PATCH", m.table, "", 200, 0.0, 1, True, None)
            return {"mode": "mock", "table": m.table, "record": record.number, "updated_fields": fields,
                    "approval_id": grant.approval_id}
        payload = {m.sn(k): v for k, v in fields.items()}
        body, _ = self._request("PATCH", m.table, suffix=f"/{_check_id(record.sys_id, 'sys_id')}", json_body=payload)
        result = body.get("result")
        if not isinstance(result, dict):
            raise ServiceNowResponseError("Unexpected update response from ServiceNow")
        return {"mode": "live", "table": m.table, "record": record.number, "updated_fields": sorted(fields),
                "approval_id": grant.approval_id}

    def _authorize_write(self, m: TableMapping, record: ITSMRecord, fields: dict, grant: ApprovalGrant | None):
        if not isinstance(grant, ApprovalGrant) or grant.status != "approved":
            raise WriteNotAllowedError("A human-approved ApprovalGrant is required for ServiceNow writes")
        if (grant.record_number, grant.record_type) != (record.number, record.record_type):
            raise WriteNotAllowedError("Approval does not cover this record")
        if fields != grant.payload:
            raise WriteNotAllowedError("Write content differs from what was approved")
        disallowed = set(fields) - m.write_names()
        if disallowed:
            raise WriteNotAllowedError(f"Field(s) not permitted for write: {', '.join(sorted(disallowed))}")
        if not self.mock and not settings.servicenow_write_enabled:
            raise WriteNotAllowedError("ServiceNow writes are disabled (SERVICENOW_WRITE_ENABLED=false)")

    # --------------------------------------------------------------- internals
    def _mapping(self, record_type: str) -> TableMapping:
        if record_type not in self.mappings:
            raise ValueError(f"Unsupported ITSM record type: {record_type}")
        return self.mappings[record_type]

    def _params(self, m: TableMapping, query: str, limit: int, offset: int, order: bool = True) -> dict:
        if order:  # stable ordering is required for offset pagination
            query = f"{query}^ORDERBY{m.sn('number')}" if query else f"ORDERBY{m.sn('number')}"
        params = {"sysparm_limit": str(limit), "sysparm_offset": str(offset), "sysparm_fields": m.query_fields(),
                  "sysparm_display_value": "all", "sysparm_exclude_reference_link": "true"}
        if query:
            params["sysparm_query"] = query
        return params

    def _get_all(self, m: TableMapping, extra: dict, limit: int | None) -> tuple[list[dict], int | None]:
        cap = min(limit or settings.servicenow_max_records, settings.servicenow_max_records)
        page, offset, rows = max(1, settings.servicenow_page_size), 0, []
        total = None
        while len(rows) < cap:
            size = min(page, cap - len(rows))
            body, headers = self._request("GET", m.table, params=self._params(m, "", size, offset))
            batch = self._validated_list(body)
            rows += batch
            header_total = headers.get("X-Total-Count")
            if header_total and header_total.isdigit():
                total = int(header_total)
            if len(batch) < size or (total is not None and offset + len(batch) >= total):
                break
            offset += len(batch)
        return rows, total

    @staticmethod
    def _validated_list(body: Any) -> list[dict]:
        result = body.get("result") if isinstance(body, dict) else None
        if not isinstance(result, list) or not all(isinstance(r, dict) for r in result):
            raise ServiceNowResponseError("Unexpected response shape from ServiceNow (expected a result list)")
        return result

    def _session(self):
        if self._http is None:
            import requests

            self._http = requests.Session()
        return self._http

    def _auth_strategy(self) -> AuthStrategy:
        if self._auth is None:
            self._auth = build_auth(self._session())
        return self._auth

    def _request(self, method: str, table: str, suffix: str = "", params: dict | None = None,
                 json_body: dict | None = None) -> tuple[Any, dict]:
        import requests

        if not settings.servicenow_instance:
            raise ServiceNowConfigError("SERVICENOW_INSTANCE is required in live mode")
        url = f"{settings.servicenow_instance.rstrip('/')}/api/now/table/{table}{suffix}"
        max_attempts = 1 + max(0, settings.servicenow_max_retries)
        attempts, reauthed = 0, False
        while True:
            attempts += 1
            started = time.perf_counter()
            status: int | None = None
            try:
                kwargs = self._auth_strategy().apply({
                    "headers": {"Accept": "application/json", "Content-Type": "application/json"},
                    "params": params, "json": json_body, "timeout": settings.servicenow_timeout})
                try:
                    resp = self._session().request(method, url, **kwargs)
                except requests.Timeout as exc:
                    raise ServiceNowTimeoutError("ServiceNow request timed out") from exc
                except requests.ConnectionError as exc:
                    raise ServiceNowUnavailableError("Could not connect to ServiceNow") from exc
                status = resp.status_code
                if status == 401 and not reauthed and self._auth_strategy().invalidate():
                    reauthed = True
                    attempts -= 1
                    continue
                self._raise_for_status(resp)
                try:
                    body = resp.json()
                except ValueError as exc:
                    raise ServiceNowResponseError("ServiceNow returned a non-JSON response") from exc
                self._log(method, table, suffix, status, (time.perf_counter() - started) * 1000, attempts, True, None)
                return body, dict(resp.headers or {})
            except ServiceNowError as exc:
                self._log(method, table, suffix, status, (time.perf_counter() - started) * 1000, attempts,
                          False, str(exc))
                if exc.retriable and attempts < max_attempts:
                    delay = getattr(exc, "retry_after", None) or min(0.5 * 2 ** (attempts - 1), 8.0)
                    self._sleep(min(float(delay), 30.0))
                    continue
                raise

    @staticmethod
    def _raise_for_status(resp) -> None:
        status = resp.status_code
        if status < 400:
            return
        detail = ""
        try:
            detail = str((resp.json().get("error") or {}).get("message", ""))[:200]
        except Exception:  # noqa: BLE001, S110 - the error body is optional detail
            pass
        detail = f": {mask_secrets(detail)}" if detail else ""
        if status in (401, 403):
            raise ServiceNowAuthError(f"ServiceNow denied access (HTTP {status}){detail}")
        if status == 429:
            try:
                retry_after = float(resp.headers.get("Retry-After", ""))
            except (TypeError, ValueError):
                retry_after = None
            raise ServiceNowRateLimitError("ServiceNow rate limit reached (HTTP 429)", retry_after)
        if status >= 500:
            raise ServiceNowUnavailableError(f"ServiceNow server error (HTTP {status}){detail}")
        raise ServiceNowError(f"ServiceNow request failed (HTTP {status}){detail}")

    def _log(self, method, table, suffix, status, duration_ms, attempts, success, error) -> None:
        if not self._log_calls:
            return
        try:
            from app.db.base import session_scope
            from app.db.models import ServiceNowCallLog

            with _log_lock, session_scope() as session:
                session.add(ServiceNowCallLog(
                    method=method, table=table, path=f"/api/now/table/{table}{'/{sys_id}' if suffix else ''}",
                    status_code=status, duration_ms=round(duration_ms, 2), attempts=attempts, success=success,
                    error=mask_secrets(error)[:500] if error else None, mode=self.mode))
        except Exception as exc:  # noqa: BLE001 - logging must never break an API call
            logger.warning("Could not persist ServiceNow call log: %s", mask_secrets(str(exc)))

    # ---------------------------------------------------------------- mapping
    @staticmethod
    def _val(raw: Any, canonical: str) -> str:
        if isinstance(raw, dict):
            if canonical in CODE_FIELDS:
                return str(raw.get("value") or raw.get("display_value") or "")
            return str(raw.get("display_value") or raw.get("value") or "")
        return "" if raw is None else str(raw)

    def _to_record(self, row: dict, record_type: str, mock: bool = False) -> ITSMRecord:
        m = self.mappings[record_type]
        get = (lambda c: self._val(row.get(c), c)) if mock else (lambda c: self._val(row.get(m.sn(c)), c))
        sys_id, number = get("sys_id"), get("number")
        if not sys_id or not number:
            raise ServiceNowResponseError("ServiceNow record is missing sys_id or number")
        mapped = {m.sn(c) for c in CANONICAL_FIELDS} | set(CANONICAL_FIELDS)
        extra = {k: self._val(v, k) for k, v in row.items() if k not in mapped and self._val(v, k)}
        return ITSMRecord(
            sys_id=sys_id, number=number, short_description=get("short_description"),
            description=get("description"), priority=get("priority") or "3", state=get("state") or "New",
            category=get("category") or "general", impact=get("impact") or "3", urgency=get("urgency") or "3",
            assignment_group=get("assignment_group"), record_type=record_type,
            source="mock" if mock else "servicenow", extra=extra)
