# cmdvault Cloud Sync API v1

Base URL: `https://sync.prometheus-alexander.fi/api/v1`. No trailing slash.
The executable, dependency-free request contract is `src/sync/protocol.py`,
imported directly by the server and covered by both test suites. Deploy the whole
checkout: `srv/` is not a standalone repository. Schema version 1 is explicit in
login/push JSON, pull query parameters, and successful JSON responses. Unsupported
request versions are rejected, not silently coerced. Status/devices are v1 by URL.
All requests and responses use JSON except empty 204 responses. Unknown request
fields are rejected on login/push; plaintext metadata does not belong in mutations.

## Authentication and devices

`POST /auth/login`, no existing token:

```json
{"schema_version":1,"username":"owner","password":"<password>","display_name":"Laptop"}
```

201: `{schema_version, device_id, token, expires_at}`. Each login enrolls a **new**
device with a separate 256-bit random token; the token is returned only once.
Use `Authorization: Bearer <token>` on every other endpoint. Never put tokens in
URLs, command arguments, logs or shell history. Only SHA-256 token digests are
stored. Random high-entropy tokens do not need a slow password KDF; owner passwords
use Argon2. Default token lifetime is 90 days, with no automatic renewal. Login
again and revoke the old device to replace an expired token.

- `POST /auth/logout`: revoke the current credential, 204.
- `GET /devices`: `{schema_version, devices:[{id,display_name,created_at,last_seen_at,revoked_at}]}`.
- `DELETE /devices/{uuid}`: revoke that owner's device and all its credentials,
  204 (including an already revoked device); another owner's UUID returns 404.
- `GET /sync/status`: `{schema_version,sync_enabled,sequence,records}`; `records`
  counts current nondeleted records. Available even when synchronization is gated.

There is no registration or web admin route. `create_owner` creates the initial
owner; `changepassword` performs password recovery. Password changes do not
invalidate device credentials: revoke devices separately when credentials leak.
Display names and account names are intentionally visible metadata. Never use
command content or secrets as display names.

Login has database-backed atomic fixed-window counters for both source IP and
account (10 attempts per 300 seconds by default), plus nginx's independent limit.
Every attempt counts, successful or not. Invalid credentials return the same 401
response. Invalid JSON also consumes the IP budget. 429 includes Retry-After.
Proxy headers are trusted only with `TRUST_PROXY=true` behind the provided proxy,
which overwrites client-supplied headers. Granian must remain bound to localhost.

## Push

`POST /sync/push`:

```json
{
  "schema_version": 1,
  "mutations": [{
    "change_id": "40e0f351-cf96-4b9b-bfbf-fae1732aa58d",
    "record_uuid": "127600bc-7303-4672-9277-c2cb1ed943a4",
    "base_revision": 0,
    "operation": "upsert",
    "envelope": {
      "key_version": 1,
      "nonce": "bm5ubm5ubm5ubm5u",
      "ciphertext": "c3ludGhldGljLWNpcGhlcnRleHQtYW5kLWF1dGgtdGFn"
    }
  }]
}
```

The example uses **synthetic bytes, not encrypted command content**. Production
v1 envelopes are reserved for AES-256-GCM: a unique 12-byte nonce per key, a
positive integer key version, and canonical standard-base64 ciphertext including
the 16-byte authentication tag (16..65536 decoded bytes). The client now uses
AES-GCM and a manually transferred passphrase-wrapped key for synthetic probes.
The full real-record adapter and recovery workflow remain unfinished.
The server validates structure and size only; it cannot verify encryption or tags
without the client's key. Base64 alone is not encryption. HTTPS is also required.

1..20 mutations per batch, maximum HTTP body 2 MiB; UUIDs must be canonical lower
case. `base_revision=0` means no remote record has existed. For an existing record,
provide the last known **server** revision, including tombstones. Each accepted
mutation increments that revision by one. Local outbox revisions are independent
and must never be substituted for the base revision.

200: `{schema_version,accepted:[{change_id,record_uuid,revision,sequence}]}` in input
order. Every batch is atomic. A stale base revision returns 409 with
`{code:"revision_conflict",record_uuid,current_revision}` and rolls back the whole
batch. Pull, decrypt and resolve locally before making a new mutation/change ID.
No last-write-wins behavior exists. Reusing an accepted change ID with exactly the
same mutation returns its original receipt, even after later revisions. Different
content with that ID returns 409 `change_id_reused`. IDs are scoped to the owner.

A deletion uses `operation:"delete", envelope:null`. Unknown record + base 0
creates a tombstone; this supports withdrawal of a never-uploaded local record.
A tombstone retains UUID/revision; a subsequent upsert must explicitly reference
that revision. Deleted records cannot be accidentally recreated at base 0.
Deletion clears the current record's payload. Historical encrypted snapshots
remain in the changelog/backups; deletion is not cryptographic erasure.

Owner row locks serialize pushes, pulls, enrollment and revocation for that owner.
Sequence allocation occurs inside this lock and transaction, avoiding the
commit-order gaps possible with a bare auto-increment sequence. Different owners
have independent sequences. The database also enforces uniqueness for record UUID,
change ID, sequence and record revision within the relevant owner scope.

## Incremental pull

`GET /sync/pull?schema_version=1&limit=100&cursor=<opaque>`; omit cursor initially.
Limit is 1..500. 200:

```text
{schema_version, changes:[{change_id,record_uuid,revision,sequence,operation,
                          envelope,timestamp}], cursor, has_more}
```

Changes are immutable encrypted snapshots in ascending sequence order, including
tombstones and the caller's own changes. `cursor` points to the last **returned**
change, not the current head; an empty page retains the position. Cursors are
signed and owner-bound. Cross-owner/tampered/future cursors return 400. Pages are
incremental, not a frozen multi-page snapshot; concurrent new changes appear on
later pages. No compaction/expiry policy is currently implemented: keep all logs,
idempotency receipts and tombstones. Signing-key changes invalidate cursors and
require a replay from the beginning with local revision deduplication.

The future **real-history** client must atomically apply a pull page and persist its cursor in its
local SQLite transaction. A push receipt must **not** advance the pull cursor or
acknowledge newer coalesced outbox events. Preserve exact sent envelopes/change IDs
across network retries, track remote revisions separately, and acknowledge only
the accepted event ID. Existing `Outbox.acknowledge`, `SyncStateStore` and
`prepare_change` remain the integration points; networking is not enabled.

The synthetic probe authenticates a stable canonical metadata representation
that can be reconstructed from pull (including UUID, change ID, operation and
remote base revision); record kind belongs inside the ciphertext. The existing
preparation AAD uses local revision and kind, so it cannot be directly mapped to
this transport. Finalizing and testing that adapter is part of the deferred
client security work. Tombstones currently rely on authenticated device access
and HTTPS, not a client cryptographic deletion proof; hostile-server integrity
requires a versioned signed/encrypted tombstone design before an E2EE claim.

## Errors and readiness gate

400 malformed schema/envelope/cursor; 401 missing/expired/revoked credentials;
404 foreign/missing device; 409 conflict; 413 oversized request; 415 non-JSON;
429 login throttle; 503 sync disabled. DRF errors use `detail` or the conflict
object above. Failed batches have no accepted mutations.

`SYNC_ENABLED=false` is the default. Push/pull remain disabled for real history
until the complete real-record encryption/recovery, secret review, remote revision
mapping, conflict handling and end-to-end client sync have been implemented and
tested. Tests override this gate for synthetic envelopes. The explicit client
probe is not a claim of working end-to-end encryption for real history.

The current client can run an explicit synthetic AES-GCM round trip using the
versioned API. It does not consume the real outbox or apply remote favorites.
See [Cloud Sync client](sync-client.md).
