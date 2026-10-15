# cmdvault 0.1.0

Find the useful commands buried in your own Bash history. Search with fzf,
keep favorites, and copy an exact command to the clipboard.

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

Make sure `~/.local/bin` is on PATH. The installer copies only the executable;
it does not edit shell configuration. `XDG_BIN_HOME` overrides its destination.

## Use

```bash
history -a                   # Flush this Bash session's recent commands
cmdvault                     # Browse history and favorites
cmdvault ffmpeg               # Start with a search
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
| Enter | Print the original command and exit |
| Ctrl-S | Save a favorite, print the command and exit |
| Ctrl-Y | Copy the original command to the clipboard and exit |
| Esc | Cancel |

With `--copy`, Enter and Ctrl-S also copy the selection. If the clipboard is
unavailable or `copy()` fails, the command is printed instead and cmdvault exits
with an error. OSC 52 has no confirmation of clipboard delivery, so a successful
copy reports that the command was sent to `copy()`.
Selected commands are never executed. Diagnostic messages go to stderr.

Duplicates are combined, with favorites first and then most recently used
commands. The `Nx` column counts occurrences in the currently loaded history
file; favorites absent from that file display `0x`.
The picker uses fzf's fuzzy search; `--list` uses a case-insensitive substring
filter. The picker escapes newlines, tabs, quotes and control characters for
display, while saving, printing and copying preserve the original command.

## History and storage

The default input is the exported `HISTFILE`, or `~/.bash_history` when it is
unset. A child process cannot read unsaved history from your running Bash:
run `history -a` before searching for something you have just used.
Timestamped Bash history preserves multiline entry boundaries. In history
without timestamps, each physical line is treated as a separate command.
Only valid UTF-8 history is accepted; decoding errors are reported rather than
silently changing the commands. History is read without modifying it.

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
