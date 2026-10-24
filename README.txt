Sync Pulse color and preview modification.

Recommended: apply patch from project root with:
  git apply --check picker-colors-demo.patch
  git apply picker-colors-demo.patch

If your picker.py has custom edits, patch may fail. Do not overwrite it;
use the included new version only if your other local edits are already backed up.

Preview without real sync:
  CMDVAULT_PULSE_DEMO=syncing ./cmdvault --graphics kitty
  CMDVAULT_PULSE_DEMO=synced ./cmdvault --graphics kitty
  CMDVAULT_PULSE_DEMO=error ./cmdvault --graphics kitty
  CMDVAULT_PULSE_DEMO=offline ./cmdvault --graphics kitty
  CMDVAULT_PULSE_DEMO=ready ./cmdvault --graphics kitty

Only status display changes. Real history sync remains unavailable.
