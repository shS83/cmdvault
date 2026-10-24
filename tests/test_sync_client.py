"""No production HTTP or user files: isolated credentials and mock transport."""
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx

from src.sync.auth import (CredentialError, DeviceCredential, load_credential,
                           parse_credential, store_credential)
from src.sync.cli import main as sync_main
from src.sync.client import SyncAPI, SyncHTTPError
from src.sync.protocol import SCHEMA_VERSION


class MockService:
    def __init__(self):
        self.requests = []
        self.token = "x" * 43
        self.device_id = str(uuid4())
        self.login_count = 0
        self.disabled = True
        self.fail = None

    def __call__(self, request):
        self.requests.append(request)
        if self.fail:
            return self.fail(request)
        if request.url.path == "/api/v1/auth/login":
            self.login_count += 1
            payload = json.loads(request.content)
            assert set(payload) == {"schema_version", "username", "password", "display_name"}
            assert payload["schema_version"] == SCHEMA_VERSION
            return httpx.Response(201, json={"schema_version": SCHEMA_VERSION,
                "device_id": self.device_id, "token": self.token,
                "expires_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()})
        if request.headers.get("Authorization") != "Bearer " + self.token:
            return httpx.Response(401, json={"detail": "invalid_device_token"})
        if request.url.path == "/api/v1/sync/status":
            return httpx.Response(200, json={"schema_version": SCHEMA_VERSION,
                "sync_enabled": not self.disabled, "sequence": 0, "records": 0})
        if request.url.path == "/api/v1/devices":
            return httpx.Response(200, json={"schema_version": SCHEMA_VERSION,
                "devices": [{"id": self.device_id, "display_name": "Test", "revoked_at": None}]})
        if request.url.path in ("/api/v1/auth/logout", "/api/v1/devices/" + self.device_id):
            return httpx.Response(204)
        return httpx.Response(404)

    def factory(self, token=None):
        return SyncAPI(token=token, transport=httpx.MockTransport(self))


class CredentialTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "device.json"
        self.credential = DeviceCredential(str(uuid4()), "a" * 43,
            datetime.now(timezone.utc) + timedelta(days=1))

    def test_import_existing_valid_credential_without_overwrite(self):
        self.path.write_text(json.dumps({"device_id": self.credential.device_id,
            "token": self.credential.token, "expires_at": self.credential.expires_at.isoformat()}))
        self.path.chmod(0o600)
        original = self.path.read_bytes()
        self.assertEqual(load_credential(self.path), self.credential)
        with self.assertRaises(CredentialError):
            store_credential(self.credential, self.path)
        self.assertEqual(self.path.read_bytes(), original)

    def test_private_new_credential_and_bad_files(self):
        store_credential(self.credential, self.path)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(load_credential(self.path), self.credential)
        self.path.chmod(0o644)
        with self.assertRaises(CredentialError):
            load_credential(self.path)
        self.path.unlink()
        self.path.symlink_to(Path(self.temp.name) / "target")
        with self.assertRaises(CredentialError):
            load_credential(self.path)

    def test_rejects_bad_structure_version_token_and_expiry(self):
        base = {"device_id": self.credential.device_id, "token": self.credential.token,
                "expires_at": self.credential.expires_at.isoformat()}
        for field, value in (("device_id", "wrong"), ("token", "secret"),
                             ("expires_at", "yesterday"), ("schema_version", 2),
                             ("server_url", "https://example.org"), ("command", "echo secret")):
            bad = dict(base, **{field: value})
            with self.subTest(field=field), self.assertRaises(CredentialError):
                parse_credential(bad)


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.service = MockService()

    def test_login_version_bearer_and_devices(self):
        with self.service.factory() as api:
            result = api.login("owner", "private-password", "Laptop")
        self.assertEqual(result["device_id"], self.service.device_id)
        with self.service.factory(token=self.service.token) as api:
            self.assertEqual(api.status()["sequence"], 0)
            self.assertEqual(len(api.devices()), 1)
            api.revoke_device(self.service.device_id)
        self.assertEqual(self.service.login_count, 1)
        self.assertEqual([r.url.path for r in self.service.requests],
            ["/api/v1/auth/login", "/api/v1/sync/status", "/api/v1/devices",
             "/api/v1/devices/" + self.service.device_id])
        self.assertNotIn("Authorization", self.service.requests[0].headers)

    def test_invalid_expired_revoked_tokens_never_retry(self):
        self.service.fail = lambda req: httpx.Response(401, json={"detail": "invalid_device_token"})
        with self.service.factory(token="bad") as api:
            with self.assertRaises(SyncHTTPError) as caught:
                api.status()
        self.assertEqual(caught.exception.code, "invalid_credentials")
        self.assertEqual(len(self.service.requests), 1)

    def test_timeout_retry_and_offline_error(self):
        calls = []
        def timeout(_):
            raise httpx.ReadTimeout("sensitive request detail")
        self.service.fail = timeout
        with SyncAPI(token=self.service.token, transport=httpx.MockTransport(self.service), sleep=calls.append) as api:
            with self.assertRaises(SyncHTTPError) as caught:
                api.status()
        self.assertEqual(str(caught.exception), "offline")
        self.assertEqual(calls, [.25, .5])
        self.assertEqual(len(self.service.requests), 3)

    def test_push_conflict_and_invalid_pull_are_safe(self):
        from src.security.crypto import seal_record
        record, change = str(uuid4()), str(uuid4())
        mutation = {"change_id": change, "record_uuid": record, "base_revision": 0,
                    "operation": "upsert", "envelope": seal_record(b"k" * 32, b"synthetic", record, change, 0)}
        self.service.fail = lambda req: httpx.Response(409, json={"code": "revision_conflict",
            "current_revision": 1})
        with self.service.factory(token=self.service.token) as api:
            with self.assertRaises(SyncHTTPError) as caught:
                api.push([mutation])
        self.assertEqual(caught.exception.code, "conflict")
        self.assertEqual(len(self.service.requests), 1)
        self.service.fail = lambda req: httpx.Response(200, json={"schema_version": 1,
            "changes": [{"command": "echo plaintext"}], "cursor": "opaque", "has_more": False})
        with self.service.factory(token=self.service.token) as api:
            with self.assertRaises(SyncHTTPError) as caught:
                api.pull()
        self.assertEqual(caught.exception.code, "invalid_server_response")

    def test_rate_limit_and_wrong_version(self):
        self.service.fail = lambda req: httpx.Response(429, headers={"Retry-After": "999"})
        with self.service.factory(token=self.service.token) as api:
            with self.assertRaises(SyncHTTPError) as caught:
                api.status()
        self.assertEqual(caught.exception.code, "rate_limited")
        self.assertEqual(len(self.service.requests), 1)
        self.service.fail = lambda req: httpx.Response(200, json={"schema_version": 2,
            "sync_enabled": True, "sequence": 0, "records": 0})
        with self.service.factory(token=self.service.token) as api:
            with self.assertRaises(SyncHTTPError) as caught:
                api.status()
        self.assertEqual(caught.exception.code, "unsupported_protocol_version")


class CLIIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {"XDG_CONFIG_HOME": self.temp.name})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.service = MockService()
        self.output = io.StringIO()
        self.error = io.StringIO()

    def run_sync(self, *args):
        return sync_main(list(args), api_factory=self.service.factory,
                         output=self.output, error=self.error)

    def test_login_reuses_existing_token_without_new_device(self):
        with patch("src.sync.cli.getpass", return_value="private-password"), \
             patch("src.sync.cli.hostname", return_value="TestHost"):
            self.assertEqual(self.run_sync("login", "owner"), 0)
        self.assertEqual(self.service.login_count, 1)
        self.assertEqual(self.run_sync("login", "owner"), 0)
        self.assertEqual(self.service.login_count, 1)
        self.assertEqual(self.run_sync("status"), 0)
        self.assertIn("Server sync: disabled", self.output.getvalue())
        self.assertNotIn(self.service.token, self.output.getvalue())
        self.assertEqual(self.run_sync("devices"), 0)
        self.assertEqual(self.run_sync("now"), 0)
        self.assertEqual(self.run_sync("logout"), 0)
        self.assertEqual(self.run_sync("status"), 1)
        self.assertNotIn(self.service.token, self.error.getvalue())

    def test_offline_existing_credential_does_not_enroll_duplicate(self):
        from src.sync.auth import credential_path
        store_credential(DeviceCredential(self.service.device_id, self.service.token,
            datetime.now(timezone.utc) + timedelta(days=1)), credential_path())
        def offline(_):
            raise httpx.ConnectError("private network detail")
        self.service.fail = offline
        with patch("src.sync.client.time.sleep"):
            self.assertEqual(self.run_sync("login", "owner"), 1)
        self.assertEqual(self.service.login_count, 0)
        self.assertIsNotNone(load_credential())
        self.assertNotIn("private network detail", self.error.getvalue())

    def test_top_level_command_dispatch_preserves_shell_wrapper(self):
        import importlib.machinery
        import importlib.util
        from unittest.mock import patch as mock_patch
        loader = importlib.machinery.SourceFileLoader("cmdvault_sync_test", str(Path(__file__).resolve().parents[1] / "cmdvault"))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        vault = importlib.util.module_from_spec(spec)
        loader.exec_module(vault)
        with mock_patch("src.sync.cli.main", return_value=7) as command:
            self.assertEqual(vault.main(["--shell-output", "/tmp/unused", "sync", "status"]), 7)
        command.assert_called_once_with(["status"])

    def test_expired_credential_is_preserved_until_explicit_local_logout(self):
        expired = DeviceCredential(self.service.device_id, self.service.token,
            datetime.now(timezone.utc) - timedelta(days=1))
        from src.sync.auth import credential_path
        store_credential(expired, credential_path())
        self.assertEqual(self.run_sync("login", "owner"), 1)
        self.assertEqual(self.service.login_count, 0)
        self.assertIsNotNone(load_credential())
        self.assertEqual(self.run_sync("logout", "--local"), 0)
        self.assertIsNone(load_credential())


if __name__ == "__main__":
    unittest.main()
