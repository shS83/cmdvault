# cmdvault: approved E2EE transport (phase 1)

This is deliberately **not** a finished cross-device history merge.

Files added:
- `src/sync/two_way.py`: explicit Push/Pull, persistent encrypted request staging, server revision tracking, transactional cursor, quarantined decrypted inbox.
- `tests/test_two_way.py`: network-response-loss idempotency, two-device encrypted transfer, and rejection of unapproved local records.
- `scripts/sync-approved.py`: opt-in client for an **existing** sync SQLite sidecar, not run by picker/CLI by default.

## Test

From repository root:

```sh
PYTHONPATH="$PWD" uv run --extra graphics python -m unittest discover -s tests -p test_two_way.py -v
```

## Optional controlled transfer

Use only after backing up the database and confirming credentials/key and the contents of the eligible queue. Do not test initially against irreplaceable live history.

```sh
PYTHONPATH="$PWD" uv run --extra graphics python scripts/sync-approved.py --db /path/to/favorites.sync.sqlite3 --pull-only
```

Without `--pull-only`, the command sends *only* local records explicitly marked `sync_eligible`. It does not import Bash history or change favorites.json. Server records are decrypted and held in `cv_remote_inbox` pending reviewed merge. The `key.json` and `device.json` are never printed or uploaded.

## Known limits

- Full bidirectional **visible** history is not yet implemented: remote records are quarantined in an inbox until deduplication, conflict handling, tag/favorite/priority merge and user review are implemented.
- No automatic startup sync, Ctrl-R wiring or background task.
- No history mass import; nothing becomes eligible automatically.
- No conflict auto-merge: in-flight changes with a changed remote revision halt rather than overwriting.
- Server's preexisting synthetic probe is recognized but not imported into history.
- The outgoing ciphertext is persisted in `cv_sync_requests` (a private SQLite file) so an HTTP timeout can safely retry the *same* mutation.
- `cv_remote_inbox` contains locally decrypted commands; protect the sidecar with filesystem permissions and encrypted backups.
- Do not run parallel sync processes on one sidecar yet; multi-process locking still needs implementation.
