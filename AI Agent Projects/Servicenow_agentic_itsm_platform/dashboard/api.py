"""Thin API client for the dashboard. The HTTP layer is injectable (tests pass a FastAPI TestClient)."""
from __future__ import annotations

import os
from typing import Any

API_URL = os.getenv("API_URL", "http://localhost:8000").rstrip("/")
V1 = "/api/v1"
TIMEOUT = float(os.getenv("API_TIMEOUT", "120"))


class ApiError(Exception):
    def __init__(self, message: str, status: int = 0, code: str = "error", request_id: str = ""):
        super().__init__(message)
        self.message, self.status, self.code, self.request_id = message, status, code, request_id

    def __str__(self) -> str:
        suffix = f" (request {self.request_id})" if self.request_id else ""
        return f"{self.message}{suffix}"


def make_http():
    import requests

    return requests.Session()


class ApiClient:
    def __init__(self, base_url: str = API_URL, http: Any = None, token: str | None = None,
                 timeout: float | None = TIMEOUT):
        self.base_url = base_url.rstrip("/")
        self.http = http or make_http()
        self.token = token
        self.timeout = timeout  # None for in-process clients (tests) that do not accept it

    # ------------------------------------------------------------------ core
    def request(self, method: str, path: str, **kw) -> Any:
        headers = dict(kw.pop("headers", {}))
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if self.timeout is not None:
            kw["timeout"] = self.timeout
        try:
            response = self.http.request(method, f"{self.base_url}{path}", headers=headers, **kw)
        except Exception as exc:  # noqa: BLE001 - connection problems are shown, not crashed on
            raise ApiError(f"Cannot reach the API at {self.base_url}: {type(exc).__name__}", code="unreachable") from exc
        if response.status_code >= 400:
            try:
                err = response.json()["error"]
                raise ApiError(err.get("message", "Request failed"), response.status_code, err.get("code", "error"),
                               err.get("request_id", ""))
            except (ValueError, KeyError, TypeError):
                raise ApiError(f"HTTP {response.status_code}", response.status_code) from None
        return response

    def get(self, path: str, **params) -> Any:
        params = {k: v for k, v in params.items() if v not in (None, "")}
        return self.request("GET", f"{V1}{path}", params=params).json()

    def post(self, path: str, body: dict | None = None, **kw) -> Any:
        return self.request("POST", f"{V1}{path}", json=body, **kw).json()

    def patch(self, path: str, body: dict) -> Any:
        return self.request("PATCH", f"{V1}{path}", json=body).json()

    def delete(self, path: str) -> Any:
        return self.request("DELETE", f"{V1}{path}").json()

    def download(self, path: str, **params) -> bytes:
        return self.request("GET", f"{V1}{path}", params=params).content

    def upload(self, path: str, filename: str, data: bytes, metadata: str = "{}") -> Any:
        return self.request("POST", f"{V1}{path}", files={"file": (filename, data)}, data={"metadata": metadata}).json()

    # ---------------------------------------------------------------- session
    def login(self, username: str, password: str) -> dict:
        body = self.request("POST", f"{V1}/auth/login", json={"username": username, "password": password}).json()
        self.token = body["access_token"]
        return body

    def me(self) -> dict:
        return self.get("/auth/me")

    def logout(self) -> None:
        self.token = None
