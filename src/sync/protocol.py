"""Cloud Sync v1 wire validation; stdlib only, no transport or encryption.

Server imports this exact contract. Local outbox revisions are NOT server revisions.
See docs/sync-api.md before implementing transport or cipher adapters.
"""
import base64
import binascii
from uuid import UUID

SCHEMA_VERSION = 1
MAX_CIPHERTEXT_BYTES = 65536
MAX_MUTATIONS = 20
MAX_REVISION = 9223372036854775806


def _fields(value, expected):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ValueError("invalid_fields")


def _integer(value, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("invalid_integer")


def _uuid(value):
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError("invalid_uuid")


def _binary(value, minimum, maximum):
    if not isinstance(value, str) or len(value) > 4 * ((maximum + 2) // 3):
        raise ValueError("invalid_base64")
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError("invalid_base64") from None
    if not minimum <= len(raw) <= maximum or base64.b64encode(raw).decode("ascii") != value:
        raise ValueError("invalid_binary_size")


def validate_envelope(value):
    _fields(value, ("key_version", "nonce", "ciphertext"))
    _integer(value["key_version"], 1, 2147483647)
    _binary(value["nonce"], 12, 12)
    _binary(value["ciphertext"], 16, MAX_CIPHERTEXT_BYTES)


def validate_push(value):
    _fields(value, ("schema_version", "mutations"))
    _integer(value["schema_version"], SCHEMA_VERSION, SCHEMA_VERSION)
    mutations = value["mutations"]
    if not isinstance(mutations, list) or not 1 <= len(mutations) <= MAX_MUTATIONS:
        raise ValueError("invalid_batch_size")
    ids = set()
    for item in mutations:
        _fields(item, ("change_id", "record_uuid", "base_revision", "operation", "envelope"))
        _uuid(item["change_id"])
        _uuid(item["record_uuid"])
        _integer(item["base_revision"], 0, MAX_REVISION)
        if item["change_id"] in ids:
            raise ValueError("duplicate_change_id")
        ids.add(item["change_id"])
        if item["operation"] == "upsert":
            validate_envelope(item["envelope"])
        elif item["operation"] != "delete" or item["envelope"] is not None:
            raise ValueError("invalid_operation")
    return value


def validate_login(value):
    _fields(value, ("schema_version", "username", "password", "display_name"))
    _integer(value["schema_version"], SCHEMA_VERSION, SCHEMA_VERSION)
    for name, maximum in (("username", 150), ("password", 1024), ("display_name", 100)):
        text = value[name]
        if not isinstance(text, str) or not text.strip() or len(text) > maximum or any(ord(c) < 32 for c in text):
            raise ValueError("invalid_login_field")


def validate_pull_response(value):
    _fields(value, ("schema_version", "changes", "cursor", "has_more"))
    _integer(value["schema_version"], SCHEMA_VERSION, SCHEMA_VERSION)
    if not isinstance(value["changes"], list) or len(value["changes"]) > 500:
        raise ValueError("invalid_changes")
    if not isinstance(value["cursor"], str) or not value["cursor"] or len(value["cursor"]) > 1024:
        raise ValueError("invalid_cursor")
    if type(value["has_more"]) is not bool:
        raise ValueError("invalid_has_more")
    previous = 0
    for change in value["changes"]:
        _fields(change, ("change_id", "record_uuid", "revision", "sequence",
                         "operation", "envelope", "timestamp"))
        _uuid(change["change_id"])
        _uuid(change["record_uuid"])
        _integer(change["revision"], 1, MAX_REVISION)
        _integer(change["sequence"], previous + 1, MAX_REVISION)
        previous = change["sequence"]
        if not isinstance(change["timestamp"], str) or len(change["timestamp"]) > 64:
            raise ValueError("invalid_timestamp")
        if change["operation"] == "upsert":
            validate_envelope(change["envelope"])
        elif change["operation"] != "delete" or change["envelope"] is not None:
            raise ValueError("invalid_operation")
    return value
