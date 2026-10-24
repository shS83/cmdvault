# cmdvault Cloud Sync client

The client uses `https://sync.prometheus-alexander.fi` with certificate verification,
a five second request timeout, a versioned JSON contract and per-device Bearer
tokens. The service URL is fixed in the client. No command accepts a token or
password as a command-line argument. The Bash wrapper forwards `cmdvault sync …`
without running a picker or evaluating shell output.

## Device enrollment

```sh
cmdvault sync login owner-name
cmdvault sync status
cmdvault sync devices
cmdvault sync devices revoke DEVICE_UUID
cmdvault sync logout
```

Login reads `/etc/hostname` for the display name (falls back to the system
hostname) and prompts for the account password using `getpass`. Successful login
stores the server-issued device ID, token and expiry in a new private mode-0600
`$XDG_CONFIG_HOME/cmdvault/device.json` (default
`~/.config/cmdvault/device.json`). A valid existing file is imported in place;
login checks its token and does not enroll another device. Invalid, expired or
revoked credentials are never silently replaced. Use `cmdvault sync logout
--local` to remove an unusable local file before a new login. This **does not
revoke** its server token; revoke it from another authenticated device if it may
still be valid. Normal logout revokes the server token before removing the file.

The file contains a Bearer token. Protect it, exclude it from backups that are
not encrypted, and never paste it into support messages. The client refuses files
that are symlinks, world/group readable, incorrectly owned, malformed or from an
unsupported protocol/server. Login responses are saved with exclusive file
creation; an existing credential is never overwritten.

Status and device listing do not inspect Bash history. `cmdvault sync now` checks
server readiness; it does not send real history. Network read failures receive
short bounded retries. Invalid credentials and rate limits are returned at once.
The client does not use HTTP proxies from environment variables; network access
requires a direct HTTPS route to the service.

## Encryption key and synthetic test

Create a 32-byte random sync key, wrapped under a passphrase with scrypt and
AES-256-GCM:

```sh
cmdvault sync key init
cmdvault sync key export /private-transfer/sealed-key.json
# Transfer this sealed file securely to the second device, separately from its passphrase.
cmdvault sync key import /private-transfer/sealed-key.json
```

`key init` and `key import` prompt for the passphrase. Export copies only the
sealed key material to a new mode-0600 file; it never prints or uploads the
plaintext key. Import verifies the passphrase and refuses to replace an existing
key. Keep the sealed file and its passphrase in separate safe places. Losing
either one can make remote ciphertext unrecoverable. The file transfer itself is
manual and is not implemented by the sync server. Store a backup of the sealed
file before relying on it. Passphrase changes and key rotation are not yet
implemented.

An explicit synthetic probe is available when the server reports
`sync_enabled=true`:

```sh
cmdvault sync now --test
# The command prints a synthetic record UUID. On a second device enrolled under
# the same account, with the same imported sealed key and its passphrase:
cmdvault sync now --verify RECORD_UUID
```

The probe encrypts `echo CMDVAULT_SYNC_TEST` locally with AES-256-GCM, checks the
outgoing JSON for plaintext, pushes one newly generated record, pulls the server
log, decrypts it and compares the exact content. The second device can pull and
verify that record independently. It does not read or write `.bash_history`,
`favorites.json` or the SQLite sidecar. Each `--test` invocation creates a new
synthetic remote record; use it sparingly. Do not run it against production until
you deliberately authorize creating synthetic data there. Local automated tests
use an isolated mock service and temporary directories only.

The envelope authenticates the record UUID, change ID, operation and base
revision as associated data. The server sees encrypted bytes, key version, UUIDs,
revision, operation, ciphertext size and timing. It cannot verify that a client
actually encrypted content before sending it. Normal history sync, which must
also handle encrypted tombstones and remote/local revision mapping, is a separate
unfinished path.

## Current readiness boundary

The existing SQLite sidecar has stable UUIDs, an outbox, revision counters,
tombstones and cursor slots, and the favorites bridge preserves the JSON source
of truth. The client now has a concrete conservative secret scanner based on the
same heuristics as the picker. Heuristics alone cannot guarantee that every
credential in arbitrary shell history will be detected. The production path
must additionally provide an explicit review/opt-in, reliable remote revision
tracking, conflict recovery and transactional pull into the favorites bridge.
For these reasons no real history/favorites/tags are sent, no historical import
occurs on login, and no startup/timer synchronization runs. There is no network
operation in the picker and the existing graphics/keyboard behavior is unchanged.

For developers, run `uv sync --extra graphics` and
`uv run --extra graphics python -m unittest discover -s tests -v` from the
repository root. Tests never contact the deployed service or a user's history.
