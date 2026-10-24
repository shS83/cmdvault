# Cmdvault: Space multiselect + Ctrl-D batch deletion

Applies to the **current** `cmdvault` launcher, rather than overwriting it with an old snapshot.

- **Space**: mark/unmark a command in fzf. (Space is then no longer inserted into fzf search; use e.g. `Ctrl-V Space` for a literal space if supported by your fzf version.)
- **Ctrl-D**: delete all marked commands, with **one** y/N confirmation. If none are marked, fzf usually submits its current highlighted row for deletion.
- Deletes via the existing `delete_entry()` function, so it removes matching history occurrences and favorites metadata. The deleted count is shown afterwards.
- All selected commands, even ones flagged as potential secrets, are shown without printing their contents in the confirmation. Make a history backup first.
- This is not an atomic multi-command transaction: if deleting one item fails, earlier successful deletions are kept and an error notice is shown. Do not assume all-or-nothing.
- This modifies only `cmdvault`; Kitty visuals and `picker.py` remain unchanged. Existing picker restarts after deletion can still flicker — this is not a fix for that separate problem.

## Install

From the repository root:

```bash
cp "${HISTFILE:-$HOME/.bash_history}" "${HISTFILE:-$HOME/.bash_history}.before-cmdvault-multidelete.bak"
python /path/to/apply.py ./cmdvault
python -m py_compile ./cmdvault
./cmdvault --graphics kitty
```

The installer itself creates `cmdvault.before-multidelete.bak`, preserving the current launcher exactly. It refuses unfamiliar source layouts instead of overwriting them. If the displayed existing `Ctrl-D` branch differs, share the **current** `cmdvault` file and apply a tailored patch instead.

When tested: `./install.sh` to update installed executable, then commit/push.

## Known caveat

fzf's `--multi` returns marked selections to the parent on Ctrl-D, but when there are no marks, selecting the current row depends on fzf's normal submit behavior. Avoid typing a literal space in the search query without using a quoted/paste workaround.
