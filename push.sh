#!/bin/bash

cd ~/Code/cmdvault


DB="${XDG_DATA_HOME:-$HOME/.local/share}/cmdvault/favorites.sync.sqlite3"

read -rsp 'Sync key passphrase: ' PASSPHRASE
echo

while (( $(sqlite3 "$DB" 'SELECT COUNT(*) FROM cv_outbox;') > 0 )); do
    before=$(sqlite3 "$DB" 'SELECT COUNT(*) FROM cv_outbox;')

    CMDVAULT_PASSPHRASE_FD=3 \
        PYTHONPATH="$PWD" \
        uv run python scripts/sync-approved.py --db "$DB" \
        3<<<"$PASSPHRASE" || break

    after=$(sqlite3 "$DB" 'SELECT COUNT(*) FROM cv_outbox;')
    echo "Pending: $after"

    (( after < before )) || break
done

unset PASSPHRASE
