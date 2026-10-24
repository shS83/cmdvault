from __future__ import annotations
"""Private per-device credential storage. This module never prints credentials."""
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
from uuid import UUID

from .protocol import SCHEMA_VERSION

SERVER_URL = "https://sync.prometheus-alexander.fi"
_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}\Z")


class CredentialError(ValueError):
    pass


@dataclass(frozen=True)
class DeviceCredential:
    device_id: str
    token: str
    expires_at: datetime

    def is_expired(self) -> bool:
        return self.expires_at <= datetime.now(timezone.utc)


def credential_path() -> Path:
    root = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return root / "cmdvault" / "device.json"


def parse_credential(value) -> DeviceCredential:
    if not isinstance(value, dict) or not {"device_id", "token", "expires_at"} <= set(value):
        raise CredentialError("Invalid stored device credential")
    if set(value) - {"device_id", "token", "expires_at", "schema_version", "server_url"}:
        raise CredentialError("Invalid stored device credential")
    if type(value.get("schema_version", SCHEMA_VERSION)) is not int or value.get("schema_version", SCHEMA_VERSION) != SCHEMA_VERSION or value.get("server_url", SERVER_URL) != SERVER_URL:
        raise CredentialError("Stored credential belongs to an unsupported protocol or server")
    try:
        device_id = value["device_id"]
        if not isinstance(device_id, str) or str(UUID(device_id)) != device_id:
            raise ValueError()
        token = value["token"]
        if not isinstance(token, str) or not _TOKEN.fullmatch(token):
            raise ValueError()
        expires_at = datetime.fromisoformat(value["expires_at"])
        if expires_at.tzinfo is None:
            raise ValueError()
    except (ValueError, TypeError, KeyError, AttributeError):
        raise CredentialError("Invalid stored device credential") from None
    return DeviceCredential(device_id, token, expires_at)


def load_credential(path: Path | None = None) -> DeviceCredential | None:
    path = Path(path or credential_path())
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise CredentialError("Device credential must be a private regular file owned by this user")
    try:
        with path.open("r", encoding="utf-8") as source:
            raw = source.read(4097)
        if len(raw) > 4096:
            raise ValueError()
        return parse_credential(json.loads(raw))
    except (OSError, ValueError, UnicodeError):
        raise CredentialError("Invalid stored device credential") from None


def store_credential(value: DeviceCredential, path: Path | None = None) -> None:
    path = Path(path or credential_path())
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    parent = path.parent.stat()
    if parent.st_uid != os.getuid() or parent.st_mode & 0o077:
        raise CredentialError("Device credential directory must be private and owned by this user")
    data = {"schema_version": SCHEMA_VERSION, "server_url": SERVER_URL,
            "device_id": value.device_id, "token": value.token,
            "expires_at": value.expires_at.isoformat()}
    parse_credential(data)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError:
        raise CredentialError("A device credential already exists; no credential was overwritten") from None
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as target:
            json.dump(data, target, separators=(",", ":"))
            target.flush()
            os.fsync(target.fileno())
        parent_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def remove_credential(expected: DeviceCredential, path: Path | None = None) -> None:
    path = Path(path or credential_path())
    if load_credential(path) != expected:
        raise CredentialError("Device credential changed while logging out")
    path.unlink()
