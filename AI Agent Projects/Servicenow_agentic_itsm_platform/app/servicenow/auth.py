"""Authentication abstraction: Basic (existing) and OAuth 2.0 (preferred)."""
from __future__ import annotations

import threading
import time
from typing import Any

from app.config import settings
from app.security.sanitize import mask_secrets
from app.servicenow.errors import ServiceNowAuthError, ServiceNowConfigError


class AuthStrategy:
    name = "base"

    def apply(self, request_kwargs: dict) -> dict:
        """Return request kwargs augmented with credentials."""
        raise NotImplementedError

    def invalidate(self) -> bool:
        """Drop cached credentials. Return True if a retry could help."""
        return False


class BasicAuth(AuthStrategy):
    name = "basic"

    def __init__(self, username: str, password: str):
        if not username or not password:
            raise ServiceNowConfigError("SERVICENOW_USERNAME and SERVICENOW_PASSWORD are required for basic auth")
        self._creds = (username, password)

    def apply(self, request_kwargs: dict) -> dict:
        return {**request_kwargs, "auth": self._creds}


class OAuthAuth(AuthStrategy):
    """OAuth 2.0 against ``/oauth_token.do``.

    Uses the password grant when a username is configured, otherwise client credentials.
    Tokens are cached until shortly before expiry and refreshed on 401.
    """

    name = "oauth"

    def __init__(self, instance: str, client_id: str, client_secret: str, username: str = "", password: str = "",
                 http: Any = None, timeout: float = 20, clock=time.time):
        if not instance or not client_id or not client_secret:
            raise ServiceNowConfigError(
                "SERVICENOW_INSTANCE, SERVICENOW_CLIENT_ID and SERVICENOW_CLIENT_SECRET are required for OAuth")
        self.token_url = f"{instance.rstrip('/')}/oauth_token.do"
        self._id, self._secret, self._user, self._pass = client_id, client_secret, username, password
        self._http, self._timeout, self._clock = http, timeout, clock
        self._token: str | None = None
        self._refresh: str | None = None
        self._expires_at = 0.0
        self._lock = threading.Lock()

    def _post(self, data: dict) -> dict:
        import requests

        http = self._http or requests
        try:
            resp = http.post(self.token_url, data=data, timeout=self._timeout)
        except Exception as exc:  # noqa: BLE001
            raise ServiceNowAuthError(mask_secrets(f"OAuth token request failed: {type(exc).__name__}")) from exc
        if resp.status_code >= 400:
            raise ServiceNowAuthError(f"OAuth token request rejected (HTTP {resp.status_code})")
        try:
            body = resp.json()
        except ValueError as exc:
            raise ServiceNowAuthError("OAuth token response was not JSON") from exc
        if "access_token" not in body:
            raise ServiceNowAuthError("OAuth token response did not contain an access token")
        return body

    def _fetch(self) -> None:
        base = {"client_id": self._id, "client_secret": self._secret}
        if self._refresh:
            try:
                body = self._post({**base, "grant_type": "refresh_token", "refresh_token": self._refresh})
            except ServiceNowAuthError:
                self._refresh = None
                body = None
        else:
            body = None
        if body is None:
            if self._user:
                body = self._post({**base, "grant_type": "password", "username": self._user, "password": self._pass})
            else:
                body = self._post({**base, "grant_type": "client_credentials"})
        self._token = body["access_token"]
        self._refresh = body.get("refresh_token") or self._refresh
        self._expires_at = self._clock() + float(body.get("expires_in", 1800)) - 60  # refresh early

    def token(self) -> str:
        with self._lock:
            if not self._token or self._clock() >= self._expires_at:
                self._fetch()
            return self._token  # type: ignore[return-value]

    def apply(self, request_kwargs: dict) -> dict:
        headers = {**request_kwargs.get("headers", {}), "Authorization": f"Bearer {self.token()}"}
        return {**request_kwargs, "headers": headers}

    def invalidate(self) -> bool:
        with self._lock:
            self._token = None
            self._expires_at = 0.0
        return True


def build_auth(http: Any = None) -> AuthStrategy:
    mode = settings.servicenow_auth.lower()
    if mode == "oauth":
        return OAuthAuth(settings.servicenow_instance, settings.servicenow_client_id,
                         settings.servicenow_client_secret.get_secret_value(), settings.servicenow_username,
                         settings.servicenow_password.get_secret_value(), http=http,
                         timeout=settings.servicenow_timeout)
    if mode == "basic":
        return BasicAuth(settings.servicenow_username, settings.servicenow_password.get_secret_value())
    raise ServiceNowConfigError(f"Unsupported SERVICENOW_AUTH value: {mode}")
