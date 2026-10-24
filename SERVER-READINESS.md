# cmdvault Cloud Sync — server readiness

The current Django models and views already implement a **single owner-scoped opaque change stream**. Neither the database nor the server can deduplicate commands based on plaintext: deduplication, usage counters, favorites, priority ordering, and merge rules belong **inside the decrypted client-side model**. Do not add plaintext `command` columns to the server.

The included additional tests demonstrate that two enrolled devices can push into the same owner log, retrieve each other's ciphertext, page across tombstones, and receive HTTP 409 for stale revisions. `test_multi_device_sync.py` is additive; there is **no database migration or runtime server change**.

## Validate in a safe PostgreSQL test environment

The Django settings import `src.sync.protocol`, so this archive alone is insufficient: run tests in the **full cmdvault repository**, not only the extracted `srv` directory. From `srv`:

```bash
uv run python manage.py check
uv run python manage.py test tests.test_multi_device_sync --verbosity 2
uv run python manage.py test --verbosity 1
```

Django's PostgreSQL test runner needs permission to create its isolated `test_cmdvault` database. Provision an independent test DB/role if preferred; if temporarily granting `CREATEDB` to the app role, revoke the grant afterwards. Never point tests at a live database without Django's test DB isolation.

## Production safety

- Preserve `SYNC_ENABLED` gating. Server enabled does **not** imply that real-history upload is ready.
- Preserve the per-owner `select_for_update` lock and `transaction.atomic` around a whole Push batch.
- No migrations are required for this verification-only patch.
- `ChangeLog` is immutable: do not modify or delete existing ciphertext during client integration.
- Keep a PostgreSQL backup before any future schema changes.

## Next step: client integration

1. Version the *encrypted* client payload schema (command identity, origin device, per-device usage counters, favorite flag, priority order, tags).
2. Store remote revision separately from local edit revision.
3. Pull and validate/decrypt first; apply locally in a transaction; only then advance the persisted pull cursor.
4. Push using stable `change_id` for retries and the correct server `base_revision`.
5. Handle 409 by retaining pending edits and re-pulling/reconciling, never silently replacing local data.
6. Before importing shell history, review commands flagged as secrets and require explicit consent. Real-history sync should remain opt-in until this is complete.
