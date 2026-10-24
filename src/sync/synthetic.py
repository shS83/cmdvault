"""Explicit synthetic E2EE probe. Does not read or write history/favorites/SQLite."""
from __future__ import annotations
import json
from uuid import uuid4

from src.security.crypto import open_record, seal_record
from src.security.secrets import CommandSecretScanner
from .protocol import SCHEMA_VERSION

TEST_COMMAND = "echo CMDVAULT_SYNC_TEST"


class ProbeError(ValueError):
    pass


def verify_synthetic(api, key: bytes, record_uuid: str):
    cursor = None
    found = None
    for _ in range(1000):
        page = api.pull(cursor=cursor, limit=500)
        for change in page["changes"]:
            if change.get("record_uuid") == record_uuid:
                found = change
        if not page["has_more"]:
            break
        if page["cursor"] == cursor:
            raise ProbeError("Server cursor did not advance")
        cursor = page["cursor"]
    else:
        raise ProbeError("Too many server changes to inspect")
    if found is None or found.get("operation") != "upsert":
        raise ProbeError("Synthetic record was not found")
    try:
        raw = open_record(key, found["envelope"], record_uuid, found["change_id"], found["revision"])
        content = json.loads(raw)
    except (ValueError, KeyError, TypeError, UnicodeError):
        raise ProbeError("Synthetic record failed authenticated decryption") from None
    if content != {"kind": "synthetic", "command": TEST_COMMAND}:
        raise ProbeError("Synthetic record content did not match")
    return True


def push_synthetic(api, key: bytes):
    status = api.status()
    if not status["sync_enabled"]:
        raise ProbeError("Server synchronization is disabled")
    payload = {"kind": "synthetic", "command": TEST_COMMAND}
    scan = CommandSecretScanner().scan({"command": TEST_COMMAND})
    if not scan.allowed or scan.findings:
        raise ProbeError("Synthetic content failed secret screening")
    record_uuid, change_id = str(uuid4()), str(uuid4())
    plaintext = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    envelope = seal_record(key, plaintext, record_uuid, change_id, 0)
    mutation = {"change_id": change_id, "record_uuid": record_uuid,
                "base_revision": 0, "operation": "upsert", "envelope": envelope}
    # A defensive assertion at the transport boundary, independent of the cipher.
    wire = json.dumps({"schema_version": SCHEMA_VERSION, "mutations": [mutation]})
    if TEST_COMMAND in wire or "command" in wire:
        raise ProbeError("Plaintext appeared in the outgoing request")
    api.push([mutation])
    verify_synthetic(api, key, record_uuid)
    return record_uuid
