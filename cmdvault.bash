# Source from ~/.bashrc; requires Bash and util-linux flock.
[[ $- == *i* ]] || return

# Execute Enter selections in the caller so cd, aliases and unexported
# variables use the existing shell without sourcing .bashrc again.
cmdvault() {
    local _cmdvault_selection_file _cmdvault_selected _cmdvault_status
    _cmdvault_selection_file=$(mktemp "${TMPDIR:-/tmp}/cmdvault-selection.XXXXXXXXXX") || return
    if command cmdvault --shell-output "$_cmdvault_selection_file" "$@"; then
        _cmdvault_status=0
    else
        _cmdvault_status=$?
    fi
    if (( _cmdvault_status != 0 )); then
        command rm -f -- "$_cmdvault_selection_file"
        return "$_cmdvault_status"
    fi
    if IFS= read -r -d '' _cmdvault_selected < "$_cmdvault_selection_file"; then
        command rm -f -- "$_cmdvault_selection_file" || return
        builtin eval -- "$_cmdvault_selected"
    else
        command rm -f -- "$_cmdvault_selection_file"
    fi
}

command -v flock >/dev/null || { printf 'cmdvault: install util-linux for history synchronization.\n' >&2; return; }
[[ ${HISTFILE+x} ]] || HISTFILE=$HOME/.bash_history
export HISTFILE
shopt -s histappend

_cmdvault_sync_history() {
    local previous_status=$? history_target history_fd
    [[ -n $HISTFILE ]] || return "$previous_status"
    history_target=$(readlink -m -- "$HISTFILE") || return "$previous_status"
    if exec {history_fd}>>"${history_target}.cmdvault.lock"; then
        if flock -x "$history_fd"; then
            # Append only this session's new entries, then drop stale memory.
            # Never clear memory when saving fails.
            if builtin history -a "$history_target"; then
                builtin history -c
                builtin history -r "$history_target"
            fi
            flock -u "$history_fd"
        fi
        exec {history_fd}>&-
    fi
    return "$previous_status"
}

# Keep existing prompt hooks and register ours only once (also supports arrays).
_cmdvault_hook_present=
for _cmdvault_hook in "${PROMPT_COMMAND[@]}"; do
    [[ $_cmdvault_hook == _cmdvault_sync_history ]] && _cmdvault_hook_present=yes
done
if [[ $_cmdvault_hook_present != yes ]]; then
    PROMPT_COMMAND=(_cmdvault_sync_history "${PROMPT_COMMAND[@]}")
fi
unset _cmdvault_hook _cmdvault_hook_present
