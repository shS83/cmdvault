"""Explicit Cloud Sync commands; no history import or UI side effects."""
import argparse
from getpass import getpass
from pathlib import Path
import socket
import sys

from .auth import (CredentialError, DeviceCredential, credential_path, load_credential,
                   parse_credential, remove_credential, store_credential)
from .client import SyncAPI, SyncHTTPError


def hostname():
    try:
        value = Path("/etc/hostname").read_text(encoding="utf-8").strip()
        if value and len(value) <= 100 and all(ord(c) >= 32 for c in value):
            return value
    except (OSError, UnicodeError):
        pass
    value = socket.gethostname().strip()
    return value[:100] or "cmdvault-device"


def main(argv=None, *, api_factory=SyncAPI, output=None, error=None):
    output = output or sys.stdout
    error = error or sys.stderr
    parser = argparse.ArgumentParser(prog="cmdvault sync")
    actions = parser.add_subparsers(dest="action", required=True)
    login = actions.add_parser("login", help="enroll this device")
    login.add_argument("username", nargs="?")
    logout = actions.add_parser("logout", help="revoke this credential")
    logout.add_argument("--local", action="store_true", help="forget an unusable local credential without server revocation")
    actions.add_parser("status", help="show authenticated server status")
    devices = actions.add_parser("devices", help="list or revoke devices")
    devices.add_argument("revoke", nargs="?", choices=["revoke"])
    devices.add_argument("device_id", nargs="?")
    now = actions.add_parser("now", help="check readiness or run an explicit synthetic E2EE probe")
    probe = now.add_mutually_exclusive_group()
    probe.add_argument("--test", action="store_true", help="push and pull one synthetic encrypted record")
    probe.add_argument("--verify", metavar="UUID", help="decrypt a synthetic record from another device")
    key = actions.add_parser("key", help="manage a passphrase-wrapped sync key")
    key_actions = key.add_subparsers(dest="key_action", required=True)
    key_actions.add_parser("init", help="create a new encrypted key file")
    export = key_actions.add_parser("export", help="copy the sealed key to a new private file")
    export.add_argument("path", type=Path)
    imported = key_actions.add_parser("import", help="import a sealed key after checking its passphrase")
    imported.add_argument("path", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.action == "key":
            from src.security.key_store import create_key, export_key, import_key
            if args.key_action == "init":
                phrase = getpass("Sync key passphrase: ")
                if phrase != getpass("Confirm passphrase: "):
                    raise ValueError("Passphrases do not match")
                create_key(phrase)
                print("cmdvault: Encrypted sync key created. Back up the sealed key and passphrase separately.", file=output)
            elif args.key_action == "export":
                export_key(args.path)
                print("cmdvault: Sealed key exported.", file=output)
            else:
                phrase = getpass("Sync key passphrase: ")
                import_key(args.path, phrase)
                print("cmdvault: Sealed key imported.", file=output)
            return 0
        current = load_credential()
        if args.action == "login":
            if current is not None:
                if current.is_expired():
                    raise CredentialError("Stored device credential has expired; use 'sync logout --local' before enrolling again")
                with api_factory(token=current.token) as api:
                    api.status()
                print("cmdvault: This device is already enrolled.", file=output)
                return 0
            username = args.username or input("Username: ").strip()
            password = getpass("Password: ")
            with api_factory() as api:
                response = api.login(username, password, hostname())
            credential = parse_credential(response)
            try:
                store_credential(credential)
            except BaseException:
                # The token was issued but never persisted. No token is shown.
                # Remote cleanup is impossible without its token if storage failed.
                with api_factory(token=credential.token) as api:
                    try:
                        api.logout()
                    except SyncHTTPError:
                        pass
                raise
            print("cmdvault: Device enrolled. History synchronization is disabled.", file=output)
            return 0
        if args.action == "logout":
            if current is None:
                raise CredentialError("No stored device credential")
            if not args.local:
                with api_factory(token=current.token) as api:
                    api.logout()
            remove_credential(current)
            print("cmdvault: Local device credential removed" + (" (server token may remain valid)." if args.local else " and server token revoked."), file=output)
            return 0
        if current is None:
            raise CredentialError("Not logged in; run 'cmdvault sync login'")
        if current.is_expired():
            raise CredentialError("Device token expired; run 'cmdvault sync logout --local' then login")
        with api_factory(token=current.token) as api:
            if args.action == "status":
                state = api.status()
                print("cmdvault: Device authenticated. Server sync: " +
                      ("enabled" if state["sync_enabled"] else "disabled") +
                      ". Remote sequence: " + str(state["sequence"]) + ".", file=output)
            elif args.action == "devices":
                if args.revoke:
                    if args.device_id is None:
                        parser.error("devices revoke requires a device UUID")
                    api.revoke_device(args.device_id)
                    if args.device_id == current.device_id:
                        remove_credential(current)
                    print("cmdvault: Device revoked.", file=output)
                elif args.device_id is not None:
                    parser.error("use 'devices revoke UUID'")
                else:
                    for device in api.devices():
                        if not isinstance(device, dict) or not {"id", "display_name", "revoked_at"} <= device.keys():
                            raise SyncHTTPError("invalid_server_response")
                        # Device names are user-controlled display metadata, never credentials.
                        name = "".join(c if c.isprintable() else " " for c in str(device["display_name"]))
                        print(str(device["id"]) + "  " + name +
                              ("  [revoked]" if device["revoked_at"] else ""), file=output)
            else:
                state = api.status()
                if args.test or args.verify:
                    if not state["sync_enabled"]:
                        raise ValueError("Server synchronization is disabled")
                    from src.security.key_store import load_key
                    from .synthetic import push_synthetic, verify_synthetic
                    key = load_key(getpass("Sync key passphrase: "))
                    if args.test:
                        record_uuid = push_synthetic(api, key)
                        print("cmdvault: Synthetic encrypted round trip passed. Record UUID: " + record_uuid, file=output)
                    else:
                        from uuid import UUID
                        try:
                            if str(UUID(args.verify)) != args.verify:
                                raise ValueError()
                        except (ValueError, TypeError, AttributeError):
                            raise ValueError("Expected a canonical record UUID") from None
                        verify_synthetic(api, key, args.verify)
                        print("cmdvault: Synthetic record decrypted and verified.", file=output)
                else:
                    print("cmdvault: " + ("Server ready; real history synchronization remains disabled." if state["sync_enabled"] else "Server synchronization is disabled."), file=output)
        return 0
    except (CredentialError, SyncHTTPError, ValueError, OSError) as exc:
        # All library errors are fixed codes. Never print HTTP bodies or credentials.
        print("cmdvault: sync: " + str(exc), file=error)
        return 1
