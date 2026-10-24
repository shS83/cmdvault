# CMDVAULT Sync Pulse — lower-right layout revision

- Six bottom terminal rows are painted in the same opaque panel background `(11, 8, 18)`; result lines are not drawn in this reserved area.
- Orb is 80 pixels and has stronger but blurred light. It is placed using measured Kitty cell geometry and X/Y pixel offsets rather than hard-coded terminal row/column coordinates.
- The orb is inset approximately 12 pixels from the **inside of the neon border** (estimated border reservation of 20 pixels, hence a 32 px margin from the terminal edge). Tune `margin = 32` in `KittyPulseOverlay.packet` for different borders.
- Text status is positioned to the left of the orb, not on top of it.
- This updates the Kitty renderer only. Existing source/layout code inside fzf may still need a reduced `--height`/item viewport so results scroll instead of disappearing under the reserved rows. This patch does not modify that separate picker logic.
- Requires Pillow for the Kitty backend; run `uv sync --extra graphics`.
- Tests: `PYTHONPATH="$PWD" uv run --extra graphics python -m unittest discover -s tests -p 'test_*pulse*.py' -v`
- Backup changed files first and copy `src/visuals/kitty.py` and `src/visuals/sync_pulse.py` into project; also copy updated tests.
