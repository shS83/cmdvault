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
