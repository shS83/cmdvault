#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
# Keep imports available without changing the user's Python environment.
package_root="${XDG_DATA_HOME:-$HOME/.local/share}/cmdvault/app"
install -d "$package_root/src/visuals" "$package_root/src/sync" "$package_root/src/security"
install -m755 cmdvault "$package_root/cmdvault"
install -m644 src/visuals/*.py "$package_root/src/visuals/"
install -m644 src/database.py src/models.py "$package_root/src/"
install -m644 src/sync/*.py "$package_root/src/sync/"
install -m644 src/security/*.py "$package_root/src/security/"
python_executable=python3
if command -v uv >/dev/null 2>&1; then
    install -m644 pyproject.toml uv.lock "$package_root/"
    uv sync --project "$package_root" --extra graphics --frozen
    python_executable="$package_root/.venv/bin/python"
else
    printf 'uv not found: installing ANSI-only launcher. Install uv and rerun for Kitty graphics.\n' >&2
fi
install -d "${XDG_BIN_HOME:-$HOME/.local/bin}"
cat > "${XDG_BIN_HOME:-$HOME/.local/bin}/cmdvault" <<EOF
#!/usr/bin/env bash
exec "$python_executable" "$package_root/cmdvault" "\$@"
EOF
chmod 755 "${XDG_BIN_HOME:-$HOME/.local/bin}/cmdvault"
printf 'Installed cmdvault to %s/cmdvault\n' "${XDG_BIN_HOME:-$HOME/.local/bin}"
printf 'Try: history -a && cmdvault\n'

install -Dm644 cmdvault.bash "${XDG_DATA_HOME:-$HOME/.local/share}/cmdvault/shell.bash"
printf 'For Enter execution in the current shell and automatic history saving, add to ~/.bashrc:\n'
printf '%s\n' 'source "${XDG_DATA_HOME:-$HOME/.local/share}/cmdvault/shell.bash"'
