# CmdVault gold logo in the Kitty picker

Adds the repository's existing transparent `cmdvault.png` as a static header graphic **inside the animated neon tube** and above the fzf search/results area. The neon animation stays intact.

## Install the source change

From your local checkout, with the existing `cmdvault.png` in its root:

```bash
cd ~/Code/cmdvault
python /path/to/add_cmdvault_logo.py --project . --dry-run
python /path/to/add_cmdvault_logo.py --project .
./install.sh
```

You may place `add_cmdvault_logo.py` in the repository directory and run `python add_cmdvault_logo.py` directly instead. The script is idempotent and checks all expected code anchors, then validates Python syntax before modifying any file. It makes `.bak` backups of changed files. Check `git diff` afterward.

## Behavior

- Displays the already-existing `cmdvault.png` (preserves the PNG alpha channel).
- Centers it under the animated top neon border in a six-row header margin.
- Reserves the top margin for fzf so the image cannot overlap the result list.
- Repaints the logo on resize/redraw and removes its Kitty image on exit.
- Only enables the image with Kitty graphics and sufficiently large terminals (at least 56 columns and 24 rows).
- For small terminals, ANSI or SIXEL rendering, the old layout is retained.
- Installs the PNG into `~/.local/share/cmdvault/app/cmdvault.png` via `install.sh`.

## Notes

The only UI change is the new static logo. All fzf operations, keyboard shortcuts, syncing, sync pulse and animated neon frame remain in their existing modules. Installed `cmdvault` uses the source file only after reinstalling with `./install.sh`.

To undo, restore `*.bak` for the four edited files and rerun `./install.sh`.
