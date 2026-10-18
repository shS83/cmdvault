#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
install -Dm755 cmdvault "${XDG_BIN_HOME:-$HOME/.local/bin}/cmdvault"
printf 'Installed cmdvault to %s/cmdvault\n' "${XDG_BIN_HOME:-$HOME/.local/bin}"
printf 'Try: history -a && cmdvault\n'

install -Dm644 cmdvault.bash "${XDG_DATA_HOME:-$HOME/.local/share}/cmdvault/shell.bash"
printf 'For automatic history saving, add to ~/.bashrc:\n'
printf '%s\n' 'source "${XDG_DATA_HOME:-$HOME/.local/share}/cmdvault/shell.bash"'
