#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
install -Dm755 cmdvault "${XDG_BIN_HOME:-$HOME/.local/bin}/cmdvault"
printf 'Installed cmdvault to %s/cmdvault\n' "${XDG_BIN_HOME:-$HOME/.local/bin}"
printf 'Try: history -a && cmdvault\n'
