"""Client for the shared Customers identity service."""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

import requests


class CustomersClientError(RuntimeError):
    """Raised when the Customers service cannot satisfy a request."""


class CustomersClient:
    def __init__(self, base_url: str, *, api_token: Optional[str] = None, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_token = (api_token or "").strip() or None
        self.timeout = max(1.0, float(timeout))
        self._session = requests.Session()

    @classmethod
    def from_env(cls) -> Optional["CustomersClient"]:
        base_url = _env_first("BILLING_CUSTOMERS_API_BASE", "CUSTOMERS_API_BASE")
        if not base_url:
            return None
        timeout_raw = _env_first("BILLING_CUSTOMERS_TIMEOUT", default="10")
        try:
            timeout = float(timeout_raw)
        except Exception:
            timeout = 10.0
        return cls(
            base_url,
            api_token=_env_first("BILLING_CUSTOMERS_API_TOKEN"),
            timeout=timeout,
        )

    def resolve_session(self, *, authorization: Optional[str], cookie_header: Optional[str]) -> Dict[str, Any]:
        headers = {"Accept": "application/json"}
        if authorization:
            headers["Authorization"] = authorization
        if cookie_header:
            headers["Cookie"] = cookie_header
        return self._request("GET", "/api/session", headers=headers, require_app_auth=False)

    def verify_credentials(self, username: str, password: str) -> Dict[str, Any]:
        return self._request(
            "POST",
            "/api/internal/credentials/verify",
            json_body={"username": username, "password": password},
            require_app_auth=True,
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        headers: Optional[Dict[str, str]] = None,
        json_body: Optional[Dict[str, Any]] = None,
        require_app_auth: bool,
    ) -> Dict[str, Any]:
        request_headers = {"Accept": "application/json"}
        request_headers.update(headers or {})
        if json_body is not None:
            request_headers["Content-Type"] = "application/json"
        if require_app_auth:
            if not self.api_token:
                raise CustomersClientError("customers_app_token_missing")
            request_headers["Authorization"] = f"Bearer {self.api_token}"
        url = f"{self.base_url}{path if path.startswith('/') else '/' + path}"
        try:
            response = self._session.request(
                method=method.upper(),
                url=url,
                headers=request_headers,
                json=json_body,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise CustomersClientError(f"customers request failed: {exc}") from exc
        try:
            payload = response.json()
        except ValueError:
            payload = {"error": response.text or "invalid_json_response"}
        if not response.ok:
            if response.status_code in {401, 403, 404, 409} and isinstance(payload, dict):
                return payload
            message = payload.get("error") if isinstance(payload, dict) else None
            raise CustomersClientError(str(message or f"customers returned {response.status_code}"))
        if not isinstance(payload, dict):
            raise CustomersClientError("customers response was not a JSON object")
        return payload


def _env_first(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value is None:
            continue
        value = value.strip()
        if value:
            return value
    return default
