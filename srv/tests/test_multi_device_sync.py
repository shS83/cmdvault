"""Multi-device regression coverage for the existing encrypted change-log API.

No plaintext history is stored or transmitted by these tests.
"""
import base64
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient

from synchronization.models import ChangeLog, SyncRecord


def encrypted_change(record_uuid=None, base_revision=0, operation="upsert"):
    return {
        "change_id": str(uuid4()),
        "record_uuid": record_uuid or str(uuid4()),
        "base_revision": base_revision,
        "operation": operation,
        "envelope": None if operation == "delete" else {
            "key_version": 1,
            "nonce": base64.b64encode(b"N" * 12).decode("ascii"),
            "ciphertext": base64.b64encode(b"opaque-test-data-and-tag").decode("ascii"),
        },
    }


@override_settings(SECURE_SSL_REDIRECT=False, SYNC_ENABLED=True, LOGIN_LIMIT=100)
class MultiDeviceSyncTests(TransactionTestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(
            username="multi-device-owner", password="long password for test owner"
        )
        self.first = self.enroll("ALPHATAURI")
        self.second = self.enroll("OUROBOROS")

    def enroll(self, display_name):
        client = APIClient()
        response = client.post("/api/v1/auth/login", {
            "schema_version": 1,
            "username": "multi-device-owner",
            "password": "long password for test owner",
            "display_name": display_name,
        }, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        client.credentials(HTTP_AUTHORIZATION="Bearer " + response.data["token"])
        return client

    def push(self, client, *items):
        return client.post("/api/v1/sync/push", {
            "schema_version": 1, "mutations": list(items)
        }, format="json")

    def pull(self, client, cursor=None, limit=100):
        params = {"schema_version": 1, "limit": limit}
        if cursor is not None:
            params["cursor"] = cursor
        return client.get("/api/v1/sync/pull", params)

    def test_devices_share_one_owner_log_without_plaintext_fields(self):
        first_command = encrypted_change()
        second_command = encrypted_change()
        self.assertEqual(self.push(self.first, first_command).status_code, 200)
        self.assertEqual(self.push(self.second, second_command).status_code, 200)

        first_pull = self.pull(self.first)
        second_pull = self.pull(self.second)
        self.assertEqual(first_pull.status_code, 200)
        self.assertEqual(first_pull.data["changes"], second_pull.data["changes"])
        self.assertEqual(
            [c["record_uuid"] for c in first_pull.data["changes"]],
            [first_command["record_uuid"], second_command["record_uuid"]],
        )
        self.assertEqual([c["sequence"] for c in first_pull.data["changes"]], [1, 2])
        self.assertEqual(SyncRecord.objects.filter(owner=self.owner).count(), 2)
        self.assertEqual(ChangeLog.objects.filter(owner=self.owner).count(), 2)
        for change in first_pull.data["changes"]:
            self.assertEqual(set(change["envelope"]), {"key_version", "nonce", "ciphertext"})
            self.assertNotIn("command", change)
            self.assertNotIn("favorite", change)

    def test_incremental_pull_and_remote_deletion_cross_devices(self):
        item = encrypted_change()
        self.assertEqual(self.push(self.first, item).status_code, 200)
        page = self.pull(self.second, limit=1)
        self.assertEqual(page.status_code, 200)
        self.assertEqual(len(page.data["changes"]), 1)
        cursor = page.data["cursor"]

        tombstone = encrypted_change(item["record_uuid"], 1, "delete")
        self.assertEqual(self.push(self.second, tombstone).status_code, 200)
        next_page = self.pull(self.first, cursor=cursor, limit=1)
        self.assertEqual(next_page.status_code, 200)
        self.assertEqual(len(next_page.data["changes"]), 1)
        self.assertEqual(next_page.data["changes"][0]["operation"], "delete")
        self.assertIsNone(next_page.data["changes"][0]["envelope"])
        self.assertTrue(SyncRecord.objects.get(owner=self.owner).deleted)

    def test_stale_cross_device_revision_is_rejected_without_dropping_records(self):
        item = encrypted_change()
        self.assertEqual(self.push(self.first, item).status_code, 200)
        stale = encrypted_change(item["record_uuid"], 0)
        conflict = self.push(self.second, stale)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.data["current_revision"], 1)
        self.assertEqual(ChangeLog.objects.filter(owner=self.owner).count(), 1)
        self.assertEqual(SyncRecord.objects.get(owner=self.owner).revision, 1)

    def test_postgresql_backing_for_production_semantics(self):
        self.assertEqual(connection.vendor, "postgresql")
