"""Passphrase-wrapped sync key. The sealed file may be copied between devices."""
from __future__ import annotations
import base64
import binascii
from dataclasses import dataclass
import json
import os
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt


class KeyStoreError(ValueError):
    pass


def key_path() -> Path:
    root = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return root / "cmdvault" / "key.json"


def _b64(raw):
    return base64.b64encode(raw).decode("ascii")


def _decode(value, length=None):
    if not isinstance(value, str) or len(value) > 512:
        raise KeyStoreError("Invalid sealed key file")
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        raise KeyStoreError("Invalid sealed key file") from None
    if length is not None and len(raw) != length or _b64(raw) != value:
        raise KeyStoreError("Invalid sealed key file")
    return raw


def _derive(passphrase, salt):
    if not isinstance(passphrase, str) or len(passphrase) < 14:
        raise KeyStoreError("Use a passphrase of at least 14 characters")
    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(passphrase.encode("utf-8"))


def _validate(data):
    if not isinstance(data, dict) or set(data) != {"format", "key_version", "kdf", "salt", "nonce", "wrapped_key"}:
        raise KeyStoreError("Invalid sealed key file")
    if data["format"] != 1 or type(data["key_version"]) is not int or data["key_version"] != 1 or data["kdf"] != "scrypt-n32768-r8-p1":
        raise KeyStoreError("Unsupported sealed key format")
    _decode(data["salt"], 16)
    _decode(data["nonce"], 12)
    _decode(data["wrapped_key"], 48)
    return data


def _private_read(path):
    import stat
    path = Path(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise KeyStoreError("Sealed key file must be private and owned by this user")
    try:
        with path.open("rb") as source:
            raw = source.read(2049)
        if len(raw) > 2048:
            raise ValueError()
        return _validate(json.loads(raw))
    except (ValueError, UnicodeError):
        raise KeyStoreError("Invalid sealed key file") from None


def _private_create(path, data):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.parent.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise KeyStoreError("Sealed key directory must be private and owned by this user")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError:
        raise KeyStoreError("Sealed key file already exists") from None
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(_validate(data), output, separators=(",", ":"))
            output.flush()
            os.fsync(output.fileno())
        parent_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def create_key(passphrase, path=None):
    path = Path(path or key_path())
    if path.exists():
        raise KeyStoreError("Sealed key already exists")
    key = os.urandom(32)
    salt, nonce = os.urandom(16), os.urandom(12)
    wrapping_key = _derive(passphrase, salt)
    data = {"format": 1, "key_version": 1, "kdf": "scrypt-n32768-r8-p1",
            "salt": _b64(salt), "nonce": _b64(nonce),
            "wrapped_key": _b64(AESGCM(wrapping_key).encrypt(nonce, key, b"cmdvault-key-v1"))}
    _private_create(path, data)


def load_key(passphrase, path=None):
    data = _private_read(path or key_path())
    wrapping_key = _derive(passphrase, _decode(data["salt"], 16))
    try:
        return AESGCM(wrapping_key).decrypt(_decode(data["nonce"], 12),
                                             _decode(data["wrapped_key"], 48), b"cmdvault-key-v1")
    except InvalidTag:
        raise KeyStoreError("Incorrect passphrase or damaged sealed key") from None


def export_key(target, path=None):
    # Copies only sealed material; destination must not exist.
    data = _private_read(path or key_path())
    _private_create(target, data)


def import_key(source, passphrase, path=None):
    data = _private_read(source)
    # Verify before persisting an imported file; no plaintext export.
    load_key(passphrase, source)
    _private_create(path or key_path(), data)
