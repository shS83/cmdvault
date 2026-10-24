# Private cmdvault Cloud Sync server

Django 5.2 LTS + DRF + PostgreSQL + Granian. This directory has its own
`pyproject.toml`, `uv.lock` and `.venv`; Django is not a client dependency.
The whole existing repository is needed for the shared
[API contract](../docs/sync-api.md) in `src/sync/protocol.py`.

**Real history sync is disabled.** The existing client has local storage/outbox
and security interfaces, and now has an explicit authenticated CLI, a sealed-key workflow and a synthetic
probe. Real record mapping, review and conflict recovery are unfinished.
Use synthetic encrypted envelopes only for server development. Keep
`SYNC_ENABLED=false` for deployment until the client security work passes
end-to-end tests. The server never requests or stores encryption keys.

## Local setup

Python >=3.12, uv and PostgreSQL are required. From this directory:

```sh
uv sync --locked
cp .env.example .env
chmod 600 .env
# Edit .env: random DJANGO_SECRET_KEY, local database credentials,
# TRUST_PROXY=false and SECURE_SSL_REDIRECT=false for localhost development.
set -a
. ./.env
set +a
uv run --locked python manage.py migrate
uv run --locked python manage.py create_owner your-name
uv run --locked granian --interface asgi --host 127.0.0.1 --port 8017 config.asgi:application
```

Environment files are not automatically read by Django; export them as above or
use systemd EnvironmentFile. Generate secrets with a password manager or
`python -c 'import secrets; print(secrets.token_urlsafe(64))'`, and place them only
in the private environment file. Do not use the example placeholders.
`create_owner` prompts without echo, validates password strength, and refuses a
second initial owner. `python manage.py changepassword your-name` resets its
password; it cannot recover client encryption keys. There is no public registration.

### Tests and schema checks

Use a disposable PostgreSQL database/role (the test role needs CREATEDB).
Run server tests **from srv/** to avoid discovering the root client tests:

```sh
uv run --locked python manage.py test tests.test_api --verbosity 2
uv run --locked python manage.py makemigrations --check --dry-run
uv run --locked python manage.py migrate --check
uv run --locked python manage.py check
# Run with production environment values:
uv run --locked python manage.py check --deploy
```

Concurrency tests require PostgreSQL and never substitute SQLite. Tests create
and destroy `test_<PGDATABASE>`; never point tests at a production instance.
For a separate temporary cluster, run as an ordinary local user:

```sh
mkdir -m 700 /tmp/cmdvault-dev-socket
initdb -D /tmp/cmdvault-dev-pg -A trust --no-locale --encoding=UTF8
pg_ctl -D /tmp/cmdvault-dev-pg -l /tmp/cmdvault-dev-pg.log \
  -o "-k /tmp/cmdvault-dev-socket -p 55439 -h ''" start
export PGHOST=/tmp/cmdvault-dev-socket PGPORT=55439 PGUSER="$(id -un)" PGDATABASE=cmdvault
createdb cmdvault
# Run the migration/test commands above; then:
pg_ctl -D /tmp/cmdvault-dev-pg stop
```

The trust-authenticated example is limited to a private mode-0700 Unix socket
and is for throwaway local development only. Production must use SCRAM credentials.

Full client regressions, from the repository root:

```sh
uv sync --locked --extra graphics
uv run --locked --extra graphics python -m unittest discover -s tests -v
```

Pillow and `fzf` enable the optional graphics/terminal integration tests.

## Deployment examples — operator-run only

No production infrastructure is configured by this implementation. Intended
endpoint: `https://sync.prometheus-alexander.fi`.

1. Provision PostgreSQL, nginx, TLS certificates and a nonlogin `cmdvault` OS user.
   Restrict inbound access to HTTPS; PostgreSQL and Granian are not public.
2. Place the **whole repository** at `/opt/cmdvault`, owned by the deployment user,
   readable but not writable by the service account. From `/opt/cmdvault/srv`,
   run `uv sync --locked --no-dev`. Preserve the sibling `src/` directory.
3. Review [deploy/postgresql.sql](deploy/postgresql.sql), run it interactively as
   the PostgreSQL administrator, and configure `pg_hba.conf` with SCRAM-SHA-256
   for this role/database on localhost. Do not grant the production role CREATEDB.
4. Copy `.env.example` to `/etc/cmdvault/server.env`, substitute real secrets,
   set owner root and mode 0600. Set the public allowed host, `TRUST_PROXY=true`,
   `SECURE_SSL_REDIRECT=true`, and leave `SYNC_ENABLED=false`. The system service
   manager reads this file; do not make secrets world-readable.
5. In a protected operator shell, export that environment and run
   `.venv/bin/python manage.py migrate`, `create_owner your-name`,
   `makemigrations --check --dry-run`, `migrate --check`, and `check --deploy`.
   Migrations are an explicit release step, not an automatic service startup hook.
6. Install [deploy/cmdvault-sync.service](deploy/cmdvault-sync.service) under
   `/etc/systemd/system/`. The service binds Granian to `127.0.0.1:8017`, with two
   workers and no HTTP access logging. Review paths and service account, then run
   `systemctl daemon-reload` and `systemctl enable --now cmdvault-sync`.
7. Install [deploy/cmdvault-proxy.conf](deploy/cmdvault-proxy.conf) as
   `/etc/nginx/snippets/cmdvault-proxy.conf`, and include
   [deploy/nginx.conf](deploy/nginx.conf) inside nginx's `http` context. Provision
   and renew the referenced TLS certificates separately. Run `nginx -t` before
   reloading nginx. The proxy overwrites forwarded headers; never expose Granian
   on a public address or trust forwarded headers from arbitrary clients.

The deployment check intentionally reports `security.W005` and `security.W021`:
HSTS is enabled for this host, but subdomain coverage and browser preload are not
assumed. Enable those only after the operator verifies domain policy; these
warnings do not indicate missing HTTPS on this service.

No tokens/passwords belong in URLs or logs. App request/exception logging is
suppressed to prevent request bodies/headers leaking, including error reports;
use nginx's sanitized method/path/status/timing access log and external health
monitoring. Granian access logging must remain disabled. Do not enable Django
DEBUG, request tracing, error mail or proxy debug logging on the service. Logs
cannot provide detailed application error diagnostics in this initial version.

### Configuration

| Variable | Purpose/default |
| --- | --- |
| `DJANGO_SECRET_KEY` | Required random Django/cursor-signing secret |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated hosts; localhost by default |
| `PGDATABASE`, `PGUSER`, `PGPASSWORD`, `PGHOST`, `PGPORT` | PostgreSQL connection |
| `DEVICE_TOKEN_DAYS` | Credential lifetime, default 90 |
| `LOGIN_LIMIT` | Attempts per IP and account per 300-second window, default 10 |
| `TRUST_PROXY` | Trust nginx headers only, default false |
| `SECURE_SSL_REDIRECT` | Require HTTPS, default true |
| `SYNC_ENABLED` | Push/pull gate, default false; synthetic tests only for now |

### Health checks

`GET /healthz` is the sole unauthenticated operational endpoint. It performs
`SELECT 1` and returns `{"status":"ok"}` or generic 503; it reveals no credentials,
records or database error. Check both `curl --fail https://sync.prometheus-alexander.fi/healthz`
and `systemctl is-active cmdvault-sync`. For a local production proxy-bypass check,
use `curl --fail -H 'X-Forwarded-Proto: https' http://127.0.0.1:8017/healthz`.
For plain local development, the header is unnecessary with SSL redirect disabled.
Health proves process/database reachability; `migrate --check` separately verifies
schema readiness. Monitor 5xx/429 rates, database/disk capacity and TLS renewal.

### Backups and restoration

Use a restricted backup role or the database owner, with a mode-0600 `.pgpass` or
protected environment, never a password in command arguments. Example:

```sh
umask 077
pg_dump --format=custom --no-owner --file=/secure-backups/cmdvault.dump cmdvault
pg_restore --list /secure-backups/cmdvault.dump
```

Back up the entire database consistently: owner accounts, device credential
hashes, record revisions, immutable changelog, idempotency receipts and tombstones.
Encrypt off-host backups and retain multiple generations. Back up the private
server environment/signing secret separately using the organization's secret
storage. Client encryption keys/recovery material are **not** in server backups;
server restoration alone cannot recover encrypted content.

Test restoration periodically to a new isolated database, never over a running
production database:

```sh
createdb --owner=cmdvault cmdvault_restore
pg_restore --exit-on-error --single-transaction --no-owner --role=cmdvault \
  --dbname=cmdvault_restore /secure-backups/cmdvault.dump
# Export PGDATABASE=cmdvault_restore in an isolated operator shell, then:
uv run --locked python manage.py migrate
uv run --locked python manage.py migrate --check
```

Verify counts, revisions, tombstones, replay and health against the restored copy.
For actual recovery, stop the service, restore to a new database, point the
protected environment to it, validate, then restart. An older backup rolls back
acknowledged mutations: clients must perform explicit reconciliation/full replay,
not simply trust stored cursors or lost outbox acknowledgements. Cursors ahead of
the restored sequence are rejected. Preserve offline client databases until this
recovery tooling exists. After a suspected credential compromise, revoke **all**
restored device credentials before reopening traffic; otherwise restored tokens
could become valid again. Signing-secret rotation invalidates all cursors.

### Current limits

- The client now has authenticated CLI commands and an explicit synthetic AEAD
  probe with a passphrase-wrapped key. Real-record transport, historical import,
  remote revision/conflict handling and automatic history sync remain disabled.
- The server verifies envelope shape, not cryptographic validity; metadata
  (account/device identities, UUIDs, sizes, revisions, operations, timing) is visible.
- Current tombstones are not client-signed. See the API contract's integrity note.
- Changelog/receipt/tombstone retention is indefinite; encrypted historical data
  survives deletion. Do not prune without a new resync/idempotency policy.
- Owner-level serialization favors correctness for one owner over throughput.
  Foreign keys and uniqueness scopes support future multiple owners, but there
  is no public onboarding, quota/billing or multi-user administration UI.
- PostgreSQL rate-limit rows persist; operators can periodically delete stale
  `LoginBucket` rows with `window` older than the current window (never current).
- Automated recovery reconciliation, credential renewal and operational metrics
  beyond health/status/access logs remain future work.
