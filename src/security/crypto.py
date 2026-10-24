"""Contract only: this phase deliberately supplies no plaintext cipher."""
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class EncryptedPayload:
    ciphertext: bytes
    algorithm: str
    key_id: str


class PayloadCipher(Protocol):
    def encrypt(self, plaintext: bytes, *, associated_data: bytes) -> EncryptedPayload:
        """Future authenticated client-side encryption, including metadata AAD."""
        ...


# The existing PayloadCipher protocol above remains for the local outbox adapter.
# These helpers implement the versioned server envelope for explicit synthetic tests.
import base64
import json
import os
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from src.sync.protocol import SCHEMA_VERSION, validate_envelope


def _aad(record_uuid, change_id, base_revision):
    return json.dumps({"schema_version": SCHEMA_VERSION, "record_uuid": record_uuid,
                       "change_id": change_id, "operation": "upsert",
                       "base_revision": base_revision}, sort_keys=True, separators=(",", ":")).encode()


def seal_record(key: bytes, plaintext: bytes, record_uuid: str, change_id: str, base_revision: int):
    if len(key) != 32 or not isinstance(plaintext, bytes):
        raise ValueError("Invalid encryption input")
    nonce = os.urandom(12)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, _aad(record_uuid, change_id, base_revision))
    envelope = {"key_version": 1, "nonce": base64.b64encode(nonce).decode(),
                "ciphertext": base64.b64encode(ciphertext).decode()}
    validate_envelope(envelope)
    return envelope


def open_record(key: bytes, envelope, record_uuid: str, change_id: str, revision: int):
    validate_envelope(envelope)
    if len(key) != 32 or envelope["key_version"] != 1 or type(revision) is not int or revision < 1:
        raise ValueError("Unsupported encryption key version or revision")
    try:
        return AESGCM(key).decrypt(base64.b64decode(envelope["nonce"]),
                                   base64.b64decode(envelope["ciphertext"]),
                                   _aad(record_uuid, change_id, revision - 1))
    except InvalidTag:
        raise ValueError("Encrypted record authentication failed") from None
