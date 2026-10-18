# cmdvault 0.1.6

Find the useful commands buried in your own Bash history. Search with fzf,
keep favorites, run commands, or copy an exact command to the clipboard.

## Install

Requires Linux, Python 3.9+, Bash and fzf. Copying calls your `copy()`
function from `~/.functions`, passing the selected command on standard input.
Its output goes directly to the controlling terminal, including OSC 52 escape
sequences. The terminal must permit OSC 52 clipboard access.

```bash
# Arch Linux:
sudo pacman -S --needed python fzf
./install.sh
```

Make sure `~/.local/bin` is on PATH. The installer copies the executable and Bash integration;
it does not edit shell configuration. `XDG_BIN_HOME` overrides the executable destination.
For Enter to run in your existing shell, add this at the end of `~/.bashrc` and source it in open terminals:

```bash
source "${XDG_DATA_HOME:-$HOME/.local/share}/cmdvault/shell.bash"
```

Remove an old `cmdvault` alias if present (`unalias cmdvault`) so the function is used.

## Use

```bash
history -a                   # Flush this Bash session's recent commands
cmdvault                     # Browse history and favorites
cmdvault ffmpeg               # Start with a search
cmdvault --print openssl      # Print without executing
cmdvault --copy openssl       # Copy the selected command
cmdvault --favorites          # Browse saved commands
cmdvault --list sshfs         # Plain output, no fzf required
cmdvault --favorites --list
cmdvault --history /path/to/bash_history certbot
cmdvault --add 'git log --oneline --graph --all'
cmdvault --remove 'git log --oneline --graph --all'
```

Inside the picker:

| Key | Action |
| --- | --- |
| Enter | Run the original command in Bash |
| Ctrl-S / Alt-F | Save a favorite, turn the row yellow and stay in the picker |
| Ctrl-D | Ask “Deleting COMMAND, are you sure? y/n”; y + Enter deletes all exact matches from history and favorites |
| Ctrl-Y | Copy the original command to the clipboard and exit |
| Esc | Cancel |

With `--copy`, Enter copies the selection. Ctrl-S / Alt-F only save; they do not copy or exit. Ctrl-Y only copies; it does not save a favorite. If the clipboard is
unavailable or `copy()` fails, the command is printed instead and cmdvault exits
with an error. OSC 52 has no confirmation of clipboard delivery, so a successful
copy reports that the command was sent to `copy()`.
With the Bash integration loaded, Enter executes the original selected command in the current shell, including pipes, redirections, aliases and functions. It does not reload `.bashrc`. `cd` changes the terminal's directory, and unexported variables such as `$HOME2` are available. The selected command's exit status is returned. A private temporary file transfers only an Enter selection and is removed before execution; list/print/copy output is never evaluated. Commands such as `exit` affect the current shell too.

Without integration (or with `command cmdvault` / a direct executable path), Enter still uses a child interactive Bash. That fallback loads `.bashrc`; directory changes and assignments cannot affect the parent terminal.

Use `--print` for the previous print-only behavior. `--copy`, Ctrl-Y, saving favorites and browsing never execute the selected command. Diagnostic messages go to stderr.

Saved rows are yellow and marked with a star. Saving preserves the search query, list order and selected result position; Esc afterwards does not undo the save. Deletion and cancelling deletion also preserve the query and position (the next available result is selected after removal, or the last result at the end). Favorites move to the top only when starting a new picker session. Alt-F is available if the terminal intercepts Ctrl-S.

Duplicates are combined, with favorites first and then most recently used
commands. The `Nx` column counts occurrences in the currently loaded history
file; favorites absent from that file display `0x`.
The picker uses fzf's fuzzy search; `--list` uses a case-insensitive substring
filter. The picker escapes newlines, tabs, quotes and control characters for
display, while saving, printing and copying preserve the original command.

## History and storage

Possible secrets are detected locally and shown first in **red**, ahead of yellow
favorites, with a `POSSIBLE SECRET` label and reason. This also applies to saved
favorites. Detection covers literal credential assignments/options, Bearer tokens,
several recognizable API-token prefixes, and PEM/OpenSSH private-key headers.
Contiguous body/footer rows of pasted untimestamped private keys are also flagged.
Variable references such as `$API_KEY`, public SSH keys and private-key file paths
are not treated as literal secrets. Search still filters the list, but preserves
priority order rather than reordering by fuzzy score.

These are heuristics, not verification: false positives and missed secrets are
possible. No network calls or extra credential reports are made. Original commands
remain visible and unchanged; Ctrl-D still requires confirmation, and nothing is
automatically deleted. `--list` remains plain command output, without color/labels.

The default input is the exported `HISTFILE`, or `~/.bash_history` when it is
unset. A child process cannot read unsaved history from your running Bash:
run `history -a` before searching for something you have just used.
Timestamped Bash history preserves multiline entry boundaries. In history
without timestamps, each physical line is treated as a separate command.
Only valid UTF-8 history is accepted; decoding errors are reported rather than
silently changing the commands. Browsing is read-only; Ctrl-D explicitly modifies the selected history file.

Favorites are stored locally at `~/.local/share/cmdvault/favorites.json`, or
under `$XDG_DATA_HOME/cmdvault`. The file is created with mode `0600`; updates
are locked and written atomically. Favorites remain available after history
has been cleared. Invalid favorites files are reported and left untouched.
Personal `FZF_DEFAULT_OPTS` and `FZF_DEFAULT_OPTS_FILE` are ignored to keep
picker bindings consistent.

## Development

No Python packages are needed.

```bash
uv run python -m unittest discover -s tests -v
uv run python cmdvault --help
bash -n install.sh
```

Tests use temporary history and favorites, plus a fake fzf and an isolated test `copy()` function. They do not inspect your shell history or use your clipboard.

## Automatic Bash history and deletion

After `bash install.sh`, add this line at the END of `~/.bashrc`:

```bash
source "${XDG_DATA_HOME:-$HOME/.local/share}/cmdvault/shell.bash"
```

Then run `source ~/.bashrc` in every already-open Bash terminal before using deletion.
The integration preserves existing PROMPT_COMMAND entries, enables histappend,
exports HISTFILE, and performs `history -a`, `history -c`, `history -r` under a
shared lock before each prompt. Thus new commands are saved and stale in-memory
entries are replaced by the file contents. It needs util-linux `flock` and GNU
`readlink`, available on Arch. Existing hooks that independently rewrite history
or custom EXIT traps may interfere; remove conflicting history-writing hooks.

Ctrl-D removes every exact occurrence of the selected logical command (including
its timestamp and multiline body) from the active `--history`/HISTFILE file, and
removes it from favorites, only after y + Enter confirms the displayed command.
Any other answer cancels deletion. The current query is retained to preserve your place.
No backup containing the deleted secret is created. Other history entries are
preserved. Deletion errors are shown without repeating the selected text.

This is logical deletion, not secure disk erasure: it cannot erase terminal
scrollback, clipboard contents, backups, logs or other history files. Unintegrated
shells can restore stale history. In any such open shell after deletion, use
`history -c; history -r` to discard its in-memory history (this also discards any
unsaved commands in that shell). If a real credential was exposed, rotate it.
Concurrent writes are serialized only for cooperating integrations; arbitrary
external writers cannot be made transaction-safe by this program alone.

Validation on the editing host: 23 tests passed; 9 Linux integration tests skipped.
Bash/WSL execution is blocked by the host sandbox; run the full suite in WSL.
