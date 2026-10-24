#!/usr/bin/env python3
"""Manual explicit transfer of approved records; no automatic import."""
import argparse
from getpass import getpass
from pathlib import Path
from src.security.key_store import load_key
from src.sync.auth import load_credential
from src.sync.client import SyncAPI
from src.sync.store import LocalStore
from src.sync.two_way import TwoWaySync
import os


def main():
	parser = argparse.ArgumentParser(description='Explicit E2EE transfer to local review inbox')
	parser.add_argument('--db', type=Path, required=True, help='Existing cmdvault *.sync.sqlite3 sidecar')
	parser.add_argument('--pull-only', action='store_true', help='Fetch remote records without uploading')
	args = parser.parse_args()
	if not args.db.is_file():
		parser.error('Existing sidecar required; refusing to create a new database by mistake')
	cred = load_credential()
	if cred is None or cred.is_expired():
		parser.error('Log in with cmdvault sync login before transferring')

	passphrase_fd = os.environ.get("CMDVAULT_PASSPHRASE_FD")

	if passphrase_fd is not None:
		fd = int(passphrase_fd)
		with os.fdopen(fd, "r", closefd=False) as source:
			passphrase = source.readline().rstrip("\n")
	else:
		passphrase = getpass("Sync key passphrase: ")

	try:
		key = load_key(passphrase)
	finally:
		passphrase = None

	with SyncAPI(token=cred.token) as api, LocalStore(args.db) as store:
		sync = TwoWaySync(store, api, key)
		status = api.status()
		if not status['sync_enabled']:
			parser.error('Server sync is disabled')
		received = sync.pull()
		pushed = 0 if args.pull_only else sync.push()
		pending = store.connection.execute('SELECT count(*) FROM cv_remote_inbox').fetchone()[0]
	print(f'Encrypted transfer complete: pushed={pushed}, received={received}, pending_review={pending}')
	print('No Bash history or favorites.json data was imported or modified.')

if __name__ == '__main__':
	main()
