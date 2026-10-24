"""Manual Ctrl-R Cloud Sync; transfers only previously approved outbox records."""
from getpass import getpass
import os
import stat
from pathlib import Path
from .auth import load_credential
from .client import SyncAPI
from .store import LocalStore
from .two_way import TwoWaySync
from ..security.key_store import load_key


def _sync_passphrase(get_phrase):
    """Optional headless unlock for Ctrl-R; never treat a passphrase as a raw AES key."""
    key_file = os.environ.get("CMDVAULT_SYNC_KEY_FILE")
    if key_file:
        path = Path(key_file).expanduser()
        meta = path.stat()
        if meta.st_uid != os.getuid() or (stat.S_IMODE(meta.st_mode) & 0o077):
            raise ValueError("CMDVAULT_SYNC_KEY_FILE must be owned by you with permissions 600")
        return path.read_text(encoding="utf-8").rstrip("\r\n")
    phrase = os.environ.get("CMD_SYNC_KEY")
    if phrase is not None:
        if not phrase:
            raise ValueError("CMD_SYNC_KEY is empty")
        return phrase
    return get_phrase("Sync key passphrase: ")


def sync_now(db_path, *, get_phrase=getpass, api_factory=SyncAPI, history_path=None, favorites_path=None, priority_path=None):
    db_path = Path(db_path)
    if not db_path.is_file():
        raise ValueError("No local sync database; initialize the sidecar first")
    cred = load_credential()
    if cred is None or cred.is_expired():
        raise ValueError("Cloud Sync login missing or expired")
    if history_path is not None:
        from .trusted_history import prepare
        imported = prepare(db_path, history_path, favorites_path, priority_path)
    else:
        imported = None
    key = load_key(_sync_passphrase(get_phrase))
    with api_factory(token=cred.token) as api, LocalStore(db_path) as store:
        if not api.status()["sync_enabled"]:
            raise ValueError("Cloud Sync disabled on server")
        sync = TwoWaySync(store,api,key)
        received = sync.pull()
        pushed = 0
        while True:
            count = sync.push()
            pushed += count
            if count == 0:
                break
        received += sync.pull()
        pending = store.connection.execute("SELECT COUNT(*) FROM cv_outbox").fetchone()[0]
        return {"pushed":pushed,"received":received,"pending":pending,"imported":imported}
