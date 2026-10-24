from pathlib import Path
from src.visuals.sync_pulse import SyncPulse, SyncState

pulse = SyncPulse()

for state in SyncState:
    pulse.set_state(state)
    pulse.frame().save(Path(f"/tmp/cmdvault-{state.value}.png"))
