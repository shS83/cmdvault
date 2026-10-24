# Cmdvault Ctrl-R trusted history + full-command preview update

From project root, back up and copy these four paths from the archive.
Run: python -m unittest discover -s tests -p 'test_trusted_history.py' -v
Run: python -m unittest discover -s tests -p 'test_global*.py' -v
Run: ./install.sh

Ctrl-R now stages all commands in current HISTFILE or ~/.bash_history with hostname and count, excluding commands flagged by the scanner, stages approved favorites and priority metadata, then runs encrypted Push/Pull. It never rewrites Bash history.
Press Ctrl-O to toggle the full-command preview; right/left arrow also shows/hides it; Alt-right/Alt-left scroll preview pages.
LIMITATION: Cross-device favorite/priority edits are projected into GLOBAL, not yet reconciled back to each machine's favorites.json and priority JSON. Also Bash shell must write current commands with history -a before Ctrl-R if those commands are not yet on disk.
