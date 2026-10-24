"""Two-device synthetic round trip against an isolated in-memory HTTP service."""
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

from src.security.key_store import create_key, export_key, import_key, load_key
from src.sync.auth import DeviceCredential, credential_path, store_credential
from src.sync.cli import main as sync_main
from src.sync.client import SyncAPI
from src.sync.synthetic import TEST_COMMAND, push_synthetic, verify_synthetic


class SyntheticService:
    def __init__(self):
        self.tokens = {"a" * 43, "b" * 43}
        self.changes = []
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        if request.headers.get("Authorization") not in {"Bearer " + token for token in self.tokens}:
            return httpx.Response(401)
        if request.url.path == "/api/v1/sync/status":
            return httpx.Response(200, json={"schema_version": 1,
                "sync_enabled": True, "sequence": len(self.changes), "records": len(self.changes)})
        if request.url.path == "/api/v1/sync/push":
            data = json.loads(request.content)
            item = data["mutations"][0]
            if item["base_revision"] != 0:
                return httpx.Response(409)
            change = dict(item, revision=1, sequence=len(self.changes) + 1, timestamp="2026-10-24T00:00:00Z")
            del change["base_revision"]
            self.changes.append(change)
            return httpx.Response(200, json={"schema_version": 1, "accepted": [{
                "change_id": item["change_id"], "record_uuid": item["record_uuid"],
                "revision": 1, "sequence": change["sequence"]}]})
        if request.url.path == "/api/v1/sync/pull":
            cursor = int(request.url.params.get("cursor", "0"))
            limit = int(request.url.params["limit"])
            page = self.changes[cursor:cursor + limit]
            return httpx.Response(200, json={"schema_version": 1, "changes": page,
                "cursor": str(cursor + len(page)), "has_more": cursor + limit < len(self.changes)})
        return httpx.Response(404)

    def factory(self, token=None):
        return SyncAPI(token=token, transport=httpx.MockTransport(self))


class ProbeTests(unittest.TestCase):
    def test_synthetic_round_trip_two_devices_without_local_history(self):
        service = SyntheticService()
        phrase = "long separate recovery passphrase"
        with tempfile.TemporaryDirectory() as folder:
            first = Path(folder) / "first"
            second = Path(folder) / "second"
            transfer = Path(folder) / "transfer" / "sealed.json"
            first.mkdir()
            second.mkdir()
            history = Path(folder) / ".bash_history"
            history.write_text("echo preserved\n")
            favorite = Path(folder) / "favorites.json"
            favorite.write_text('["echo favorite"]\n')
            with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(first)}):
                create_key(phrase)
                export_key(transfer)
                store_credential(DeviceCredential(str(uuid4()), "a" * 43,
                    datetime.now(timezone.utc) + timedelta(days=1)), credential_path())
                with patch("src.sync.cli.getpass", return_value=phrase):
                    out = io.StringIO()
                    self.assertEqual(sync_main(["now", "--test"], api_factory=service.factory,
                                               output=out, error=io.StringIO()), 0)
                record_uuid = out.getvalue().split("Record UUID: ")[1].strip()
            with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(second)}):
                import_key(transfer, phrase)
                self.assertEqual(load_key(phrase), load_key(phrase, first / "cmdvault" / "key.json"))
                store_credential(DeviceCredential(str(uuid4()), "b" * 43,
                    datetime.now(timezone.utc) + timedelta(days=1)), credential_path())
                with patch("src.sync.cli.getpass", return_value=phrase):
                    output = io.StringIO()
                    self.assertEqual(sync_main(["now", "--verify", record_uuid],
                        api_factory=service.factory, output=output, error=io.StringIO()), 0)
                self.assertIn("decrypted and verified", output.getvalue())
            self.assertEqual(history.read_text(), "echo preserved\n")
            self.assertEqual(favorite.read_text(), '["echo favorite"]\n')
            self.assertEqual(len(service.changes), 1)
            self.assertFalse(any(TEST_COMMAND in request.content.decode() for request in service.requests))
            self.assertFalse((first / "cmdvault" / "favorites.json").exists())
            self.assertFalse((second / "cmdvault" / "favorites.json").exists())
            self.assertFalse((first / "cmdvault" / "favorites.sync.sqlite3").exists())

    def test_wrong_key_and_tombstone_fail_closed(self):
        service = SyntheticService()
        with service.factory(token="a" * 43) as api:
            record_uuid = push_synthetic(api, b"k" * 32)
        with service.factory(token="b" * 43) as api:
            with self.assertRaises(ValueError):
                verify_synthetic(api, b"z" * 32, record_uuid)
            service.changes.append(dict(service.changes[0], operation="delete", envelope=None,
                                        revision=2, sequence=2, change_id=str(uuid4())))
            with self.assertRaises(ValueError):
                verify_synthetic(api, b"k" * 32, record_uuid)


if __name__ == "__main__":
    unittest.main()
