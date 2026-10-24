import base64
import copy
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4
from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from accounts.models import LoginBucket
from devices.models import Device, DeviceCredential
from synchronization.models import ChangeLog, SyncRecord


def mutation(record=None, revision=0, operation="upsert"):
    return {"change_id": str(uuid4()), "record_uuid": record or str(uuid4()),
            "base_revision": revision, "operation": operation,
            "envelope": None if operation == "delete" else {
                "key_version": 1, "nonce": base64.b64encode(b"n" * 12).decode(),
                "ciphertext": base64.b64encode(b"synthetic-ciphertext-and-auth-tag").decode()}}


@override_settings(SECURE_SSL_REDIRECT=False, SYNC_ENABLED=True, LOGIN_LIMIT=100)
class APITests(TransactionTestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user("owner", password="a long testing password")
        self.other = get_user_model().objects.create_user("other", password="another testing password")
        self.client, self.token, self.device = self.login()

    def login(self, username="owner", password="a long testing password"):
        client = APIClient()
        response = client.post("/api/v1/auth/login", {"schema_version": 1, "username": username,
            "password": password, "display_name": "Test device"}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        client.credentials(HTTP_AUTHORIZATION="Bearer " + response.data["token"])
        return client, response.data["token"], response.data["device_id"]

    def push(self, *items, client=None):
        return (client or self.client).post("/api/v1/sync/push",
            {"schema_version": 1, "mutations": list(items)}, format="json")

    def pull(self, client=None, **query):
        return (client or self.client).get("/api/v1/sync/pull", {"schema_version": 1, **query})

    def test_unauthenticated_access_and_no_registration(self):
        client = APIClient()
        for method, url in [("get", "devices"), ("delete", "devices/" + self.device),
                            ("post", "auth/logout"), ("post", "sync/push"),
                            ("get", "sync/pull"), ("get", "sync/status")]:
            self.assertEqual(getattr(client, method)("/api/v1/" + url).status_code, 401)
        self.assertEqual(client.post("/api/v1/auth/register").status_code, 404)

    def test_credentials_are_hashed_expire_and_logout(self):
        credential = DeviceCredential.objects.get(device_id=self.device)
        self.assertNotEqual(credential.hashed_token, self.token)
        self.assertEqual(credential.hashed_token, hashlib.sha256(self.token.encode()).hexdigest())
        self.assertTrue(self.owner.password.startswith("argon2$"))
        self.assertEqual(self.client.get("/api/v1/sync/status").status_code, 200)
        self.assertIsNotNone(Device.objects.get(pk=self.device).last_seen_at)
        credential.expires_at = timezone.now() - timedelta(seconds=1)
        credential.save()
        self.assertEqual(self.client.get("/api/v1/sync/status").status_code, 401)
        client, _, _ = self.login()
        self.assertEqual(client.post("/api/v1/auth/logout").status_code, 204)
        self.assertEqual(client.get("/api/v1/devices").status_code, 401)

    def test_device_revocation_isolated_and_credentials_unique(self):
        client2, token2, device2 = self.login()
        self.assertNotEqual(token2, self.token)
        self.assertEqual(self.client.delete("/api/v1/devices/" + device2).status_code, 204)
        self.assertEqual(client2.get("/api/v1/sync/status").status_code, 401)
        self.assertEqual(self.client.get("/api/v1/sync/status").status_code, 200)
        self.assertEqual(self.client.delete("/api/v1/devices/" + self.device).status_code, 204)
        self.assertEqual(self.client.get("/api/v1/devices").status_code, 401)

    def test_cross_account_devices_records_and_cursors(self):
        other, _, device = self.login("other", "another testing password")
        item = mutation()
        self.assertEqual(self.push(item).status_code, 200)
        self.assertEqual(self.pull(client=other).data["changes"], [])
        self.assertEqual(other.delete("/api/v1/devices/" + self.device).status_code, 404)
        self.assertEqual([str(d["id"]) for d in other.get("/api/v1/devices").data["devices"]], [device])
        self.assertEqual(self.pull(client=other, cursor=self.pull().data["cursor"]).status_code, 400)
        # Same UUID and change ID in another owner's namespace are independent.
        self.assertEqual(self.push(item, client=other).status_code, 200)
        self.assertEqual(SyncRecord.objects.count(), 2)
        changed = mutation(item["record_uuid"], 1)
        self.assertEqual(self.push(changed, client=other).status_code, 200)
        self.assertEqual(SyncRecord.objects.get(owner=self.owner).revision, 1)

    def test_idempotency_and_conflict(self):
        item = mutation()
        first = self.push(item)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(self.push(item).data, first.data)
        changed = copy.deepcopy(item)
        changed["envelope"]["key_version"] = 2
        self.assertEqual(self.push(changed).status_code, 409)
        conflict = self.push(mutation(item["record_uuid"]))
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.data["current_revision"], 1)
        self.assertEqual(ChangeLog.objects.count(), 1)
        self.assertEqual(self.push(mutation(item["record_uuid"], 1)).status_code, 200)
        self.assertEqual(self.push(item).data, first.data)
        self.assertEqual(SyncRecord.objects.get().revision, 2)

    def test_incremental_pages_immutable_snapshots_and_tombstones(self):
        item = mutation()
        update = mutation(item["record_uuid"], 1)
        update["envelope"]["key_version"] = 2
        deletion = mutation(item["record_uuid"], 2, "delete")
        self.assertEqual(self.push(item, update, deletion).status_code, 200)
        page1 = self.pull(limit=1).data
        self.assertTrue(page1["has_more"])
        self.assertEqual(page1["changes"][0]["envelope"], item["envelope"])
        page2 = self.pull(limit=1, cursor=page1["cursor"]).data
        self.assertEqual(page2["changes"][0]["revision"], 2)
        page3 = self.pull(limit=1, cursor=page2["cursor"]).data
        self.assertFalse(page3["has_more"])
        self.assertEqual(page3["changes"][0]["operation"], "delete")
        self.assertIsNone(page3["changes"][0]["envelope"])
        self.assertEqual(self.pull(cursor=page3["cursor"]).data["changes"], [])
        record = SyncRecord.objects.get()
        self.assertTrue(record.deleted)
        self.assertIsNone(record.encrypted_payload)
        self.assertEqual(self.push(mutation(item["record_uuid"], 0)).status_code, 409)
        self.assertEqual(self.pull(cursor="tampered").status_code, 400)
        self.assertEqual(self.pull(limit=501).status_code, 400)

    def test_transaction_rolls_back_record_log_and_batch(self):
        first = mutation()
        with patch("synchronization.views.ChangeLog.objects.create", side_effect=RuntimeError("injected")):
            with self.assertRaises(RuntimeError):
                self.push(first)
        self.assertEqual(SyncRecord.objects.count(), 0)
        self.assertEqual(ChangeLog.objects.count(), 0)
        self.assertEqual(self.push(first, mutation(revision=5)).status_code, 409)
        self.assertEqual(SyncRecord.objects.count(), 0)
        self.assertEqual(ChangeLog.objects.count(), 0)
        self.assertEqual(self.push(first).data["accepted"][0]["sequence"], 1)

    def test_rejects_malformed_oversized_and_plaintext_fields(self):
        bad_values = [None, {}, {"key_version": 1, "nonce": "x", "ciphertext": "echo secret"}]
        for value in bad_values:
            item = mutation()
            item["envelope"] = value
            self.assertEqual(self.push(item).status_code, 400)
        for field, value in [("ciphertext", base64.b64encode(b"x" * 65537).decode()),
                             ("nonce", base64.b64encode(b"x" * 13).decode()),
                             ("key_version", True), ("command", "echo plaintext")]:
            item = mutation()
            item["envelope"][field] = value
            self.assertEqual(self.push(item).status_code, 400)
        item = mutation()
        item["command"] = "echo plaintext"
        self.assertEqual(self.push(item).status_code, 400)
        item = mutation()
        item["base_revision"] = True
        self.assertEqual(self.push(item).status_code, 400)
        self.assertEqual(self.push(*[mutation() for _ in range(21)]).status_code, 400)
        response = self.client.post("/api/v1/sync/push", b"x" * (2 * 1024 * 1024 + 1), content_type="application/json")
        self.assertEqual(response.status_code, 413)
        self.assertEqual(SyncRecord.objects.count(), 0)

    def test_only_opaque_envelopes_stored(self):
        self.assertEqual(self.push(mutation()).status_code, 200)
        record = SyncRecord.objects.get()
        self.assertEqual(set(record.encrypted_payload), {"key_version", "nonce", "ciphertext"})
        for model in (SyncRecord, ChangeLog):
            fields = {f.name for f in model._meta.fields}
            self.assertFalse(fields & {"command", "description", "tags", "favorite"})
        self.assertNotIn(self.token, json.dumps(list(DeviceCredential.objects.values()), default=str))

    @override_settings(SYNC_ENABLED=False)
    def test_sync_is_disabled_by_default_gate(self):
        self.assertEqual(self.push(mutation()).status_code, 503)
        self.assertEqual(self.pull().status_code, 503)
        self.assertFalse(self.client.get("/api/v1/sync/status").data["sync_enabled"])

    @override_settings(LOGIN_LIMIT=2)
    def test_login_rate_limit_across_workers_and_generic_failure(self):
        LoginBucket.objects.all().delete()
        client = APIClient()
        for username in ("owner", "nonexistent", "third"):
            response = client.post("/api/v1/auth/login", {"schema_version": 1,
                "username": username, "password": "wrong", "display_name": "Test"}, format="json")
            self.assertEqual(response.status_code, 429 if username == "third" else 401)
            if username != "third":
                self.assertEqual(str(response.data["detail"]), "invalid_credentials")
        self.assertEqual(Device.objects.count(), 1)

    def concurrent_pushes(self, items):
        self.assertEqual(connection.vendor, "postgresql", "Concurrency tests MUST run on PostgreSQL")
        barrier = Barrier(len(items))
        token = self.token
        def worker(item):
            close_old_connections()
            try:
                client = APIClient()
                client.credentials(HTTP_AUTHORIZATION="Bearer " + token)
                barrier.wait(timeout=10)
                return client.post("/api/v1/sync/push", {"schema_version": 1, "mutations": [item]}, format="json").status_code
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=len(items)) as pool:
            return list(pool.map(worker, items))

    def test_concurrent_creation_and_updates_conflict(self):
        record = str(uuid4())
        self.assertEqual(sorted(self.concurrent_pushes([mutation(record), mutation(record)])), [200, 409])
        self.assertEqual(sorted(self.concurrent_pushes([mutation(record, 1), mutation(record, 1)])), [200, 409])
        self.assertEqual(SyncRecord.objects.get().revision, 2)
        self.assertEqual(list(ChangeLog.objects.order_by("sequence").values_list("sequence", flat=True)), [1, 2])

    def test_concurrent_idempotent_retry(self):
        item = mutation()
        self.assertEqual(self.concurrent_pushes([item, item]), [200, 200])
        self.assertEqual(ChangeLog.objects.count(), 1)

    def test_revocation_rechecked_after_initial_authentication(self):
        credential = DeviceCredential.objects.get(device_id=self.device)
        # Simulate authentication completing just before another request revokes it.
        self.client.force_authenticate(user=self.owner, token=credential)
        DeviceCredential.objects.filter(pk=credential.pk).update(revoked_at=timezone.now())
        self.assertEqual(self.push(mutation()).status_code, 401)
        self.assertEqual(ChangeLog.objects.count(), 0)

    def test_disabled_owner_and_bad_token_are_rejected(self):
        self.owner.is_active = False
        self.owner.save()
        self.assertEqual(self.client.get("/api/v1/devices").status_code, 401)
        for token in ("Bearer invalid", "Basic abc", "Bearer " + "x" * 43):
            self.client.credentials(HTTP_AUTHORIZATION=token)
            self.assertEqual(self.client.get("/api/v1/sync/status").status_code, 401)

    @override_settings(LOGIN_LIMIT=2)
    def test_malformed_json_cannot_bypass_ip_limit(self):
        LoginBucket.objects.all().delete()
        client = APIClient()
        for expected in (400, 400, 429):
            response = client.post("/api/v1/auth/login", b"{", content_type="application/json")
            self.assertEqual(response.status_code, expected)

    @override_settings(LOGIN_LIMIT=2)
    def test_concurrent_login_limit_is_atomic(self):
        LoginBucket.objects.all().delete()
        barrier = Barrier(4)
        def worker(_):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return APIClient().post("/api/v1/auth/login", {"schema_version": 1,
                    "username": "owner", "password": "wrong", "display_name": "Test"}, format="json").status_code
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(sorted(pool.map(worker, range(4))), [401, 401, 429, 429])


@override_settings(SECURE_SSL_REDIRECT=False)
class OwnerAndHealthTests(TransactionTestCase):
    def test_owner_command_hashes_password_and_refuses_second_owner(self):
        from django.core.management import call_command
        from django.core.management.base import CommandError
        from io import StringIO
        password = "long-private-test-passphrase-719!"
        with patch("accounts.management.commands.create_owner.getpass", return_value=password):
            call_command("create_owner", "private-owner", stdout=StringIO())
        user = get_user_model().objects.get()
        self.assertTrue(user.check_password(password))
        self.assertTrue(user.password.startswith("argon2$"))
        with self.assertRaises(CommandError):
            call_command("create_owner", "second-owner", stdout=StringIO())

    def test_owner_command_rejects_weak_password(self):
        from django.core.management import call_command
        from django.core.management.base import CommandError
        with patch("accounts.management.commands.create_owner.getpass", return_value="short"):
            with self.assertRaises(CommandError):
                call_command("create_owner", "owner")
        self.assertFalse(get_user_model().objects.exists())

    def test_health_database_failure_is_generic(self):
        client = APIClient()
        self.assertEqual(client.get("/healthz").json(), {"status": "ok"})
        with patch("config.urls.connection.cursor", side_effect=RuntimeError("secret DB details")):
            response = client.get("/healthz")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "unavailable"})
