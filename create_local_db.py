import os
from pathlib import Path
from src.sync.store import LocalStore
from src.sync.favorites import FavoritesBridge

root = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
bridge = FavoritesBridge(root / "cmdvault/favorites.json")
bridge.database_path.parent.mkdir(parents=True, exist_ok=True)

with LocalStore(bridge.database_path):
    print("SQLite initialized:", bridge.database_path)
