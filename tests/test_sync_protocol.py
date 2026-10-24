import base64
import copy
import unittest
from uuid import uuid4
from src.sync.protocol import validate_push


class ProtocolTests(unittest.TestCase):
    def test_shared_contract_accepts_envelope_and_rejects_plaintext(self):
        payload = {"schema_version": 1, "mutations": [{"change_id": str(uuid4()),
            "record_uuid": str(uuid4()), "base_revision": 0, "operation": "upsert",
            "envelope": {"key_version": 1, "nonce": base64.b64encode(b"n" * 12).decode(),
                         "ciphertext": base64.b64encode(b"x" * 32).decode()}}]}
        self.assertIs(validate_push(payload), payload)
        for key, value in (("schema_version", 2), ("schema_version", True), ("plaintext", "echo secret")):
            bad = copy.deepcopy(payload)
            bad[key] = value
            with self.assertRaises(ValueError):
                validate_push(bad)
        payload["mutations"][0]["envelope"]["command"] = "echo secret"
        with self.assertRaises(ValueError):
            validate_push(payload)
