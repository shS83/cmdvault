from __future__ import annotations
"""Versioned HTTPS API client. Exceptions contain fixed codes, never request data."""
import time
from typing import Callable

import httpx

from .auth import SERVER_URL
from .protocol import SCHEMA_VERSION, validate_login, validate_push, validate_pull_response


class SyncHTTPError(Exception):
    def __init__(self, code: str, status: int | None = None):
        super().__init__(code)
        self.code, self.status = code, status


class SyncAPI:
    def __init__(self, token: str | None = None, *, transport=None,
                 base_url: str = SERVER_URL, sleep: Callable = time.sleep):
        # A custom transport/base is only for in-process isolated tests.
        if base_url != SERVER_URL and transport is None:
            raise ValueError("Only the configured HTTPS service is supported")
        if not base_url.startswith("https://") and transport is None:
            raise ValueError("HTTPS is required")
        self.token = token
        self.sleep = sleep
        self.http = httpx.Client(base_url=base_url, transport=transport,
                                 verify=True, timeout=httpx.Timeout(5.0, connect=3.0),
                                 follow_redirects=False, trust_env=False)

    def close(self):
        self.http.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _request(self, method, path, *, data=None, params=None, retry=False, authenticated=True):
        headers = {"Accept": "application/json"}
        if authenticated:
            if not self.token:
                raise SyncHTTPError("not_logged_in")
            headers["Authorization"] = "Bearer " + self.token
        for attempt in range(3 if retry else 1):
            try:
                response = self.http.request(method, path, json=data, params=params, headers=headers)
            except (httpx.TimeoutException, httpx.NetworkError, httpx.ProtocolError):
                if attempt < (2 if retry else 0):
                    self.sleep(0.25 * (2 ** attempt))
                    continue
                raise SyncHTTPError("offline") from None
            if response.status_code in (502, 503, 504) and attempt < (2 if retry else 0):
                self.sleep(0.25 * (2 ** attempt))
                continue
            if response.status_code in (401, 403):
                raise SyncHTTPError("invalid_credentials", response.status_code)
            if response.status_code == 429:
                raise SyncHTTPError("rate_limited", 429)
            if response.status_code == 409:
                raise SyncHTTPError("conflict", 409)
            if response.status_code == 503:
                raise SyncHTTPError("sync_unavailable", 503)
            if response.status_code >= 400 or response.is_redirect:
                raise SyncHTTPError("server_rejected_request", response.status_code)
            if response.status_code == 204:
                return None
            try:
                result = response.json()
            except (ValueError, UnicodeError):
                raise SyncHTTPError("invalid_server_response") from None
            if not isinstance(result, dict) or result.get("schema_version") != SCHEMA_VERSION:
                raise SyncHTTPError("unsupported_protocol_version")
            return result
        raise SyncHTTPError("offline")

    def login(self, username, password, display_name):
        data = {"schema_version": SCHEMA_VERSION, "username": username,
                "password": password, "display_name": display_name}
        validate_login(data)
        result = self._request("POST", "/api/v1/auth/login", data=data, authenticated=False)
        if not {"device_id", "token", "expires_at"} <= result.keys():
            raise SyncHTTPError("invalid_server_response")
        return result

    def logout(self):
        return self._request("POST", "/api/v1/auth/logout")

    def status(self):
        result = self._request("GET", "/api/v1/sync/status", retry=True)
        if type(result.get("sync_enabled")) is not bool or type(result.get("sequence")) is not int or type(result.get("records")) is not int:
            raise SyncHTTPError("invalid_server_response")
        return result

    def devices(self):
        result = self._request("GET", "/api/v1/devices", retry=True)
        if not isinstance(result.get("devices"), list):
            raise SyncHTTPError("invalid_server_response")
        return result["devices"]

    def revoke_device(self, device_id):
        from uuid import UUID
        try:
            if str(UUID(device_id)) != device_id:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise SyncHTTPError("invalid_device_id") from None
        return self._request("DELETE", "/api/v1/devices/" + device_id, retry=True)

    def push(self, mutations):
        data = validate_push({"schema_version": SCHEMA_VERSION, "mutations": mutations})
        result = self._request("POST", "/api/v1/sync/push", data=data, retry=True)
        if not isinstance(result.get("accepted"), list) or len(result["accepted"]) != len(mutations):
            raise SyncHTTPError("invalid_server_response")
        for request, receipt in zip(mutations, result["accepted"]):
            if not isinstance(receipt, dict) or receipt.get("change_id") != request["change_id"] or receipt.get("record_uuid") != request["record_uuid"] or type(receipt.get("revision")) is not int or type(receipt.get("sequence")) is not int:
                raise SyncHTTPError("invalid_server_response")
        return result["accepted"]

    def pull(self, cursor=None, limit=100):
        params = {"schema_version": SCHEMA_VERSION, "limit": limit}
        if cursor is not None:
            params["cursor"] = cursor
        result = self._request("GET", "/api/v1/sync/pull", params=params, retry=True)
        try:
            return validate_pull_response(result)
        except (ValueError, TypeError, AttributeError):
            raise SyncHTTPError("invalid_server_response") from None
