import copy
import json
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

from src.security.crypto import open_record, seal_record
from src.security.key_store import KeyStoreError, create_key, export_key, import_key, load_key
from src.security.secrets import CommandSecretScanner


class SyncSecurityTests(unittest.TestCase):
    def test_encryption_round_trip_and_metadata_tampering(self):
        key = b"k" * 32
        record, change = str(uuid4()), str(uuid4())
        plaintext = b"echo CMDVAULT_SYNC_TEST"
        envelope = seal_record(key, plaintext, record, change, 0)
        self.assertEqual(open_record(key, envelope, record, change, 1), plaintext)
        self.assertNotIn("CMDVAULT_SYNC_TEST", json.dumps(envelope))
        for args in ((b"z" * 32, envelope, record, change, 1),
                     (key, envelope, str(uuid4()), change, 1),
                     (key, envelope, record, str(uuid4()), 1),
                     (key, envelope, record, change, 2)):
            with self.assertRaises(ValueError):
                open_record(*args)
        bad = copy.deepcopy(envelope)
        bad["key_version"] = 2
        with self.assertRaises(ValueError):
            open_record(key, bad, record, change, 1)

    def test_passphrase_wrap_export_import_two_devices(self):
        with tempfile.TemporaryDirectory() as folder:
            first = Path(folder) / "first" / "key.json"
            transfer = Path(folder) / "transfer" / "key.json"
            second = Path(folder) / "second" / "key.json"
            create_key("correct horse battery staple", first)
            key = load_key("correct horse battery staple", first)
            self.assertEqual(len(key), 32)
            with self.assertRaises(KeyStoreError):
                load_key("wrong very long passphrase", first)
            export_key(transfer, first)
            self.assertNotIn(key, transfer.read_bytes())
            import_key(transfer, "correct horse battery staple", second)
            self.assertEqual(load_key("correct horse battery staple", second), key)
            self.assertEqual(second.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(KeyStoreError):
                create_key("correct horse battery staple", first)
            with self.assertRaises(KeyStoreError):
                import_key(transfer, "correct horse battery staple", second)

    def test_scanner_rejects_known_secrets_and_unknown_shapes(self):
        scanner = CommandSecretScanner()
        self.assertTrue(scanner.scan({"command": "echo CMDVAULT_SYNC_TEST", "favorite": False}).allowed)
        self.assertFalse(scanner.scan({"command": "export API_KEY=abcdefghijklmno"}).allowed)
        self.assertFalse(scanner.scan({"command": "echo harmless", "mystery": "data"}).allowed)
        self.assertFalse(scanner.scan({"tags": ["sk-proj-abcdefghijklmnopqrstuvwxyz012345"]}).allowed)


if __name__ == "__main__":
    unittest.main()
