# Cmdvault GLOBAL v2 (incremental update)

- Ctrl-G shows [HOST] prefixes and merged approved history.
- Legacy records without a hostname are labeled ALPHATAURI as explicitly requested.
- Newly approved imports acquire the actual machine hostname.
- Counts sum across distinct hosts using per-host maxima; historic counts unavailable stay estimates.
- Ctrl-R uses the already implemented interactive Pull / approved-outbox Push.
- Existing favorite/priority metadata is projected; *full cross-device state reconciliation and conflict resolution are not implemented in this package*.
- No Bash history overwrite or automatic approval.

Install: from project root, unzip the archive overwriting the corresponding source files, run
`python -m unittest discover -s tests -p 'test_global*.py' -v`, then `./install.sh`.
Backup/commit local modifications before overwriting. Install on each client; server update is not required.
