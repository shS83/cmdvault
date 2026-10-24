# CMDVAULT Ctrl-H - help modal / compact header

Includes `cmdvault` and `src/visuals/picker.py`. Based on supplied project snapshot and latest Ctrl-R no-flicker patch. Back up local changes before replacement; this ZIP does not include other files.

- Kitty picker reserves four top rows for `CMDVAULT v<version>` and right-side `CTRL-H for help` with separator.
- The old long shortcuts header is moved to a centered Ctrl-H help overlay.
- Esc, Enter or Ctrl-H closes overlay; fzf gets Ctrl-L to redraw without exiting.
- Ctrl-R remains read-only Cloud Sync status check using `transform-header`.
- Existing Pulse rendering untouched.
- Narrow terminal fallback (under 50 columns or 18 rows): modal not drawn; enlarge terminal.

Install from project root: `unzip -o /path/to/cmdvault-help-modal.zip 'cmdvault' 'src/visuals/picker.py'`, then `./cmdvault --graphics kitty`. If satisfied, `./install.sh`.

Testing was syntax/static verification only. No interactive Kitty terminal is available in this environment. Notes: Ctrl-H is ASCII backspace byte `0x08` in most terminals, and interception occurs only in the Kitty PTY picker. Another key binding can be chosen if this conflicts with Backspace on your terminal. 
