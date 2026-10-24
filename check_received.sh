#!/bin/bash
DB="$HOME/.local/share/cmdvault/favorites.sync.sqlite3"
sqlite3 "$DB" "
SELECT COUNT(*) AS received FROM cv_remote_inbox;
SELECT COUNT(*) AS revisions FROM cv_remote_revisions;
SELECT * FROM cv_sync_state;
"
