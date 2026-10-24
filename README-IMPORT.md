# cmdvault: reviewed history import (client-side phase)

Run from the **repository root** on ALPHATAURI, not in `srv/`:

```bash
PYTHONPATH="$PWD" uv run python scripts/import-history-review.py --review
```

Specify an alternate file with `--history PATH`, optionally repeated. Choose individual indices (for example `1,3,6`) or `all`; then type **APPROVE** exactly. Enter without a selection cancels. Review the displayed commands carefully. **Only choose `all` if you have reviewed every command.**

The command never makes network requests and does not modify `.bash_history` or `favorites.json`. It uses the existing `CommandSecretScanner` (heuristic, not infallible) and stages only selected commands as `sync_eligible` in `favorites.sync.sqlite3`, creating local outbox entries. Existing favorite flags are preserved. No sync or `Ctrl-R` command is called. The list itself shows commands in the terminal, so avoid saving/copying its output if it contains sensitive information.

Verify counts after approval:

```bash
DB="${XDG_DATA_HOME:-$HOME/.local/share}/cmdvault/favorites.sync.sqlite3"
sqlite3 "$DB" "SELECT sync_state, COUNT(*) FROM cv_records GROUP BY sync_state;"
sqlite3 "$DB" "SELECT COUNT(*) FROM cv_outbox;"
```

**Important:** Because records are marked eligible and placed in the outbox, future `sync-approved.py` Push (or an eventual auto-sync) **may upload them encrypted**. Do not approve anything you do not intend to sync. Run with an empty selection to preview only. This release does not merge remote records into the visible GLOBAL history, track per-device counts, or sync priority favorites.

Test:

```bash
PYTHONPATH="$PWD" uv run python -m unittest discover -s tests -p test_import_review.py -v
```
