DB="${XDG_DATA_HOME:-$HOME/.local/share}/cmdvault/favorites.sync.sqlite3"

PYTHONPATH="$PWD" uv run python scripts/sync-approved.py \
    --db "$DB" --pull-only
