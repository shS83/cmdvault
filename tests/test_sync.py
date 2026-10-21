"""Offline foundation tests use disposable records, never the user's history."""
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from uuid import UUID

from src.database import connect, migrate, transaction, utc_now
from src.models import SyncState, validate_uuid
from src.security.crypto import EncryptedPayload
from src.security.secrets import ScanResult
from src.sync.engine import prepare_change
from src.sync.favorites import FavoritesBridge
from src.sync.store import LocalStore


class AllowScanner:
    def scan(self, payload):
        return ScanResult(True)


class BlockScanner:
    def scan(self, payload):
        return ScanResult(False,('possible_secret',))


class FoundationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root/'local.sqlite3'
        self.store = LocalStore(self.path)
        self.addCleanup(self.store.close)

    def eligible(self, record):
        return self.store.set_sync_state(record.uuid,SyncState.ELIGIBLE,scanner=AllowScanner())

    def test_defaults_are_local_and_ids_survive_reopen(self):
        first = self.store.put_command('printf "ää\\n"\nsecond line')
        self.assertEqual(UUID(first.uuid).version,4)
        self.assertEqual(first.sync_state,SyncState.LOCAL_ONLY)
        self.assertEqual(self.store.outbox.pending(),[])
        with LocalStore(self.path) as other:
            self.assertEqual(other.get(first.uuid),first)
            self.assertEqual(other.put_command(first.data['command']).uuid,first.uuid)
            self.assertEqual(other.state.device_id,self.store.state.device_id)
        if os.name == 'posix':
            self.assertEqual(self.path.stat().st_mode & 0o777,0o600)

    def test_many_uuids_and_canonical_validation(self):
        records = [self.store.put_command('echo '+str(i)) for i in range(50)]
        self.assertEqual(len({r.uuid for r in records}),50)
        for bad in ('',None,'not-a-uuid',records[0].uuid.upper(),records[0].uuid.replace('-','')):
            with self.subTest(bad=bad),self.assertRaises(ValueError):
                validate_uuid(bad)
        with self.assertRaises(sqlite3.IntegrityError),transaction(self.store.connection):
            self.store.connection.execute('UPDATE cv_records SET uuid=? WHERE uuid=?',
                                          (records[1].uuid,records[0].uuid))

    def test_eligibility_requires_explicit_successful_scan(self):
        record = self.store.put_command('echo secret-placeholder')
        with self.assertRaises(ValueError):
            self.store.set_sync_state(record.uuid,SyncState.ELIGIBLE)
        with self.assertRaises(ValueError):
            self.store.set_sync_state(record.uuid,SyncState.ELIGIBLE,scanner=BlockScanner())
        self.assertEqual(self.store.get(record.uuid),record)
        self.assertEqual(self.store.outbox.pending(),[])
        promoted = self.eligible(record)
        self.assertEqual(promoted.revision,2)
        self.assertEqual(self.store.outbox.pending()[0].operation,'upsert')

    def test_queue_survives_restart_and_stale_ack_cannot_drop_edit(self):
        record = self.eligible(self.store.put_command('echo before'))
        old = self.store.outbox.pending()[0]
        updated = self.store.edit_command(record.uuid,'echo after')
        self.assertEqual(updated.uuid,record.uuid)
        self.assertEqual(updated.revision,record.revision+1)
        self.assertFalse(self.store.outbox.acknowledge(old.event_id))
        with LocalStore(self.path) as reopened:
            pending = reopened.outbox.pending()
            self.assertEqual(len(pending),1)
            self.assertEqual(pending[0].revision,updated.revision)
            self.assertNotEqual(pending[0].event_id,old.event_id)
            self.assertTrue(reopened.outbox.fail(pending[0].event_id,'encryption_unavailable'))
        event = self.store.outbox.pending()[0]
        self.assertEqual(event.attempts,1)
        self.assertEqual(event.error_code,'encryption_unavailable')
        self.assertTrue(self.store.outbox.acknowledge(event.event_id))
        self.assertEqual(self.store.outbox.pending(),[])

    def test_record_change_and_outbox_are_atomic(self):
        record = self.eligible(self.store.put_command('echo before'))
        before = self.store.outbox.pending()
        with patch.object(self.store.outbox,'enqueue',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.store.edit_command(record.uuid,'echo after')
        self.assertEqual(self.store.get(record.uuid),record)
        self.assertEqual(self.store.outbox.pending(),before)
        with self.assertRaises(ValueError):
            self.store.outbox.enqueue(record.uuid,99,'upsert')

    def test_tombstones_strip_payload_and_readd_gets_new_identity(self):
        record = self.eligible(self.store.put_command('sensitive-placeholder'))
        deleted = self.store.delete(record.uuid)
        self.assertTrue(deleted.deleted)
        self.assertIsNone(deleted.data)
        self.assertEqual(deleted.uuid,record.uuid)
        event = self.store.outbox.pending()[0]
        self.assertEqual(event.operation,'delete')
        self.assertEqual(event.revision,deleted.revision)
        self.assertEqual(self.store.delete(record.uuid),deleted)
        self.assertEqual(self.store.outbox.pending(),[event])
        self.assertNotEqual(self.store.put_command('sensitive-placeholder').uuid,record.uuid)
        # Queue rows intentionally have no command/payload column at all.
        self.assertNotIn('sensitive-placeholder',repr(list(self.store.connection.execute('SELECT * FROM cv_outbox'))))

    def test_local_delete_never_enters_outbox(self):
        record = self.store.put_command('local only')
        self.store.delete(record.uuid)
        self.assertTrue(self.store.get(record.uuid).deleted)
        self.assertEqual(self.store.outbox.pending(),[])
        with self.assertRaises(ValueError):
            self.eligible(record)

    def test_withdrawal_enqueues_remote_delete_without_deleting_local_data(self):
        record = self.eligible(self.store.put_command('echo keep locally'))
        self.store.outbox.acknowledge(self.store.outbox.pending()[0].event_id)
        local = self.store.set_sync_state(record.uuid,SyncState.LOCAL_ONLY)
        self.assertFalse(local.deleted)
        self.assertEqual(local.data,record.data)
        self.assertEqual(self.store.outbox.pending()[0].operation,'delete')
        self.store.edit_command(record.uuid,'echo edited locally')
        self.assertEqual(self.store.outbox.pending()[0].operation,'delete')
        self.assertEqual(self.store.outbox.pending()[0].revision,self.store.get(record.uuid).revision)

    def test_tag_links_have_uuids_and_cascade_tombstones(self):
        command = self.eligible(self.store.put_command('echo tagged'))
        tag = self.eligible(self.store.put_tag('development'))
        link = self.eligible(self.store.link_tag(command.uuid,tag.uuid))
        self.assertEqual(self.store.link_tag(command.uuid,tag.uuid),link)
        self.assertEqual(len({command.uuid,tag.uuid,link.uuid}),3)
        self.store.delete(command.uuid)
        self.assertTrue(self.store.get(link.uuid).deleted)
        self.assertFalse(self.store.get(tag.uuid).deleted)
        deletes = [e for e in self.store.outbox.pending() if e.operation == 'delete']
        self.assertEqual([e.record_uuid for e in deletes],[link.uuid,command.uuid])
        self.assertEqual(list(self.store.connection.execute('PRAGMA foreign_key_check')),[])

    def test_tag_link_cannot_expose_local_only_parent(self):
        command = self.store.put_command('local')
        tag = self.store.put_tag('local-tag')
        link = self.store.link_tag(command.uuid,tag.uuid)
        with self.assertRaises(ValueError):
            self.eligible(link)
        self.eligible(command)
        self.eligible(tag)
        self.eligible(link)
        self.store.set_sync_state(tag.uuid,SyncState.LOCAL_ONLY)
        self.assertEqual(self.store.get(link.uuid).sync_state,SyncState.LOCAL_ONLY)
        events = {e.record_uuid:e.operation for e in self.store.outbox.pending()}
        self.assertEqual(events[link.uuid],'delete')
        self.assertEqual(events[tag.uuid],'delete')

    def test_device_and_scoped_cursors_are_durable_and_opaque(self):
        device = self.store.state.device_id
        validate_uuid(device)
        self.assertIsNone(self.store.state.cursor())
        self.store.state.set_cursor('opaque:ä/000')
        self.store.state.set_cursor('other-cursor','second-account')
        with LocalStore(self.path) as other:
            self.assertEqual(other.state.device_id,device)
            self.assertEqual(other.state.cursor(),'opaque:ä/000')
            self.assertEqual(other.state.cursor('second-account'),'other-cursor')
            other.state.set_cursor(None)
        self.assertIsNone(self.store.state.cursor())

    def test_uniqueness_failure_rolls_back_edit(self):
        a = self.eligible(self.store.put_command('a'))
        self.store.put_command('b')
        events = self.store.outbox.pending()
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.edit_command(a.uuid,'b')
        self.assertEqual(self.store.get(a.uuid),a)
        self.assertEqual(self.store.outbox.pending(),events)

    def test_offline_operations_never_open_a_socket(self):
        with patch('socket.socket',side_effect=AssertionError('network prohibited')):
            command = self.eligible(self.store.put_command('offline'))
            tag = self.store.put_tag('offline-tag')
            self.store.link_tag(command.uuid,tag.uuid)
            self.store.state.set_cursor('local-test')
            self.store.delete(command.uuid)
            with LocalStore(self.path) as reopened:
                self.assertEqual(reopened.outbox.pending()[0].operation,'delete')


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'schema.sqlite3'

    def schema_one(self):
        connection = sqlite3.connect(self.path,isolation_level=None)
        migrate(connection,target=1)
        stamp = utc_now()
        data = json.dumps({'command':'printf "ää"\nnext','favorite':True},ensure_ascii=False)
        connection.execute('INSERT INTO cv_records VALUES (?,?,?,?,?,?)',
                           (17,'command','legacy-key',data,stamp,stamp))
        connection.execute('CREATE TABLE unrelated (value TEXT)')
        connection.execute("INSERT INTO unrelated VALUES ('keep')")
        connection.execute('PRAGMA user_version=73')
        return connection,data,stamp

    def test_additive_upgrade_preserves_ids_payloads_and_unrelated_tables(self):
        original,data,stamp = self.schema_one()
        original.close()
        connection = connect(self.path)
        self.addCleanup(connection.close)
        row = connection.execute('SELECT * FROM cv_records').fetchone()
        self.assertEqual((row['id'],row['data'],row['created_at']),(17,data,stamp))
        validate_uuid(row['uuid'])
        self.assertEqual(row['sync_state'],'local_only')
        self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0],73)
        self.assertEqual(connection.execute('SELECT * FROM unrelated').fetchone()[0],'keep')
        record_uuid = row['uuid']
        migrate(connection)
        self.assertEqual(connection.execute('SELECT uuid FROM cv_records').fetchone()[0],record_uuid)
        self.assertEqual(connection.execute('SELECT count(*) FROM cv_outbox').fetchone()[0],0)

    def test_failed_upgrade_rolls_back_ddl_and_backfill_then_can_retry(self):
        import src.database as database
        connection,_,_ = self.schema_one()
        original = database._schema_two
        def fail_after_ddl(conn):
            original(conn)
            raise OSError('interrupted migration')
        with patch.object(database,'_schema_two',side_effect=fail_after_ddl):
            with self.assertRaises(OSError):
                migrate(connection)
        columns = [r[1] for r in connection.execute('PRAGMA table_info(cv_records)')]
        self.assertNotIn('uuid',columns)
        self.assertEqual(connection.execute('SELECT count(*) FROM cv_schema_migrations').fetchone()[0],1)
        self.assertFalse(connection.execute("SELECT name FROM sqlite_master WHERE name='cv_outbox'").fetchall())
        migrate(connection)
        validate_uuid(connection.execute('SELECT uuid FROM cv_records').fetchone()[0])
        connection.close()

    def test_future_schema_is_refused_without_changing_user_data(self):
        connection = connect(self.path)
        connection.execute('INSERT INTO cv_schema_migrations VALUES (?,?)',(3,utc_now()))
        with self.assertRaises(ValueError):
            migrate(connection)
        self.assertEqual(connection.execute('SELECT max(version) FROM cv_schema_migrations').fetchone()[0],3)
        connection.close()

    def test_unrecognized_existing_table_is_not_rewritten(self):
        connection = sqlite3.connect(self.path,isolation_level=None)
        connection.execute('CREATE TABLE cv_records (original TEXT)')
        connection.execute("INSERT INTO cv_records VALUES ('preserve')")
        connection.close()
        with self.assertRaises(sqlite3.Error):
            connect(self.path)
        connection = sqlite3.connect(self.path)
        self.assertEqual(connection.execute('SELECT original FROM cv_records').fetchone()[0],'preserve')
        self.assertFalse(connection.execute("SELECT name FROM sqlite_master WHERE name='cv_schema_migrations'").fetchall())
        connection.close()


class LegacyBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'favorites.json'
        self.bridge = FavoritesBridge(self.path)

    def test_import_keeps_json_bytes_and_existing_order_exact(self):
        # Use JSON serializer to avoid conflating JSON and shell escape rules.
        raw = json.dumps(['echo old','printf "ää"\nnext','echo old'],ensure_ascii=False,indent=4).encode()
        self.path.write_bytes(raw)
        self.assertEqual(self.bridge.read(),['echo old','printf "ää"\nnext'])
        self.assertEqual(self.path.read_bytes(),raw)
        with LocalStore(self.bridge.database_path) as store:
            before = [r.uuid for r in store.records()]
            self.assertTrue(all(r.sync_state == SyncState.LOCAL_ONLY for r in store.records()))
            self.assertEqual(store.outbox.pending(),[])
        self.bridge.read()
        with LocalStore(self.bridge.database_path) as store:
            self.assertEqual([r.uuid for r in store.records()],before)

    def test_empty_read_does_not_create_files_and_invalid_input_is_preserved(self):
        self.assertEqual(self.bridge.read(),[])
        self.assertEqual(list(self.path.parent.iterdir()),[])
        self.path.write_bytes(b'invalid JSON')
        with self.assertRaises(ValueError):
            self.bridge.update('echo new')
        self.assertEqual(self.path.read_bytes(),b'invalid JSON')
        self.assertFalse(self.bridge.database_path.exists())

    def test_crash_before_json_replace_rolls_forward_on_next_open(self):
        self.bridge.update('before')
        with patch.object(self.bridge,'_write_json',side_effect=OSError('power loss')):
            with self.assertRaises(OSError):
                self.bridge.update('after')
        self.assertEqual(json.loads(self.path.read_text()),['before'])
        reopened = FavoritesBridge(self.path)
        self.assertEqual(reopened.read(),['before','after'])
        with LocalStore(reopened.database_path) as store:
            self.assertEqual(store.connection.execute('SELECT count(*) FROM cv_favorites_intent').fetchone()[0],0)
            self.assertEqual(len(store.records()),2)

    def test_crash_after_json_replace_clears_intent_without_rewriting_json(self):
        original = self.bridge._write_json
        def replace_then_fail(raw):
            original(raw)
            raise OSError('lost power after rename')
        with patch.object(self.bridge,'_write_json',side_effect=replace_then_fail):
            with self.assertRaises(OSError):
                self.bridge.update('after')
        before = self.path.read_bytes()
        reopened = FavoritesBridge(self.path)
        with patch.object(reopened,'_write_json',side_effect=AssertionError('unexpected rewrite')):
            self.assertEqual(reopened.read(),['after'])
        self.assertEqual(self.path.read_bytes(),before)

    def test_recovery_never_overwrites_conflicting_external_changes(self):
        self.bridge.update('old')
        with patch.object(self.bridge,'_write_json',side_effect=OSError('interrupted')):
            with self.assertRaises(OSError):
                self.bridge.update('new')
        self.path.write_text('["external edit"]')
        with self.assertRaisesRegex(ValueError,'interrupted save'):
            self.bridge.read()
        self.assertEqual(self.path.read_text(),'["external edit"]')
        with LocalStore(self.bridge.database_path) as store:
            self.assertEqual(store.connection.execute('SELECT count(*) FROM cv_favorites_intent').fetchone()[0],1)

    def test_removing_eligible_favorite_creates_payload_free_delete(self):
        self.bridge.update('delete-placeholder')
        with LocalStore(self.bridge.database_path) as store:
            record = store.records()[0]
            store.set_sync_state(record.uuid,SyncState.ELIGIBLE,scanner=AllowScanner())
        self.bridge.update('delete-placeholder',remove=True)
        self.assertEqual(self.bridge.read(),[])
        with LocalStore(self.bridge.database_path) as store:
            self.assertIsNone(store.get(record.uuid).data)
            self.assertEqual(store.outbox.pending()[0].operation,'delete')
            self.assertEqual(store.connection.execute('SELECT count(*) FROM cv_favorites_intent').fetchone()[0],0)

    def test_external_json_edits_are_reconciled_and_history_is_never_ingested(self):
        self.bridge.update('tracked')
        with LocalStore(self.bridge.database_path) as store:
            record = store.records()[0]
            store.set_sync_state(record.uuid,SyncState.ELIGIBLE,scanner=AllowScanner())
        history = self.path.parent/'history'
        history.write_bytes(b'untracked history\n')
        self.path.write_text('["external favorite"]')
        self.assertEqual(self.bridge.read(),['external favorite'])
        self.assertEqual(history.read_bytes(),b'untracked history\n')
        with LocalStore(self.bridge.database_path) as store:
            self.assertTrue(store.get(record.uuid).deleted)
            self.assertEqual(store.records()[0].data['command'],'external favorite')
            self.assertEqual(store.records()[0].sync_state,SyncState.LOCAL_ONLY)

    def test_unfavorite_keeps_tags_but_full_delete_tombstones_relationships(self):
        self.bridge.update('tagged')
        with LocalStore(self.bridge.database_path) as store:
            record = store.records()[0]
            tag = store.put_tag('keep-tag')
            link = store.link_tag(record.uuid,tag.uuid)
        self.bridge.update('tagged',remove=True)
        with LocalStore(self.bridge.database_path) as store:
            self.assertFalse(store.get(record.uuid).deleted)
            self.assertFalse(store.get(record.uuid).data['favorite'])
        self.bridge.update('tagged',remove=True,delete_record=True)
        with LocalStore(self.bridge.database_path) as store:
            self.assertTrue(store.get(record.uuid).deleted)
            self.assertTrue(store.get(link.uuid).deleted)
            self.assertFalse(store.get(tag.uuid).deleted)


class PreparationTests(unittest.TestCase):
    setUp = FoundationTests.setUp
    eligible = FoundationTests.eligible
    def test_prepare_requires_scanner_and_cipher_and_keeps_queue_on_failure(self):
        record = self.eligible(self.store.put_command('echo prepared'))
        event = self.store.outbox.pending()[0]
        class Cipher:
            def encrypt(self, plaintext, *, associated_data):
                self.plaintext,self.aad = plaintext,associated_data
                return EncryptedPayload(b'test-ciphertext','test-only','test-key')
        cipher = Cipher()
        with self.assertRaises(ValueError):
            prepare_change(self.store,event,scanner=BlockScanner(),cipher=cipher)
        prepared = prepare_change(self.store,event,scanner=AllowScanner(),cipher=cipher)
        self.assertEqual(json.loads(cipher.plaintext)['command'],'echo prepared')
        self.assertEqual(cipher.aad,prepared.associated_data)
        self.assertEqual(prepared.payload.ciphertext,b'test-ciphertext')
        self.assertEqual(self.store.outbox.pending(),[event])
        self.store.edit_command(record.uuid,'edited')
        with self.assertRaises(ValueError):
            prepare_change(self.store,event,scanner=AllowScanner(),cipher=cipher)

    def test_delete_preparation_never_reads_or_encrypts_old_content(self):
        record = self.eligible(self.store.put_command('deleted-placeholder'))
        self.store.delete(record.uuid)
        event = self.store.outbox.pending()[0]
        class Forbidden:
            def scan(self, payload):
                raise AssertionError('delete must not scan content')
            def encrypt(self, payload, **kwargs):
                raise AssertionError('delete must not encrypt content')
        prepared = prepare_change(self.store,event,scanner=Forbidden(),cipher=Forbidden())
        self.assertIsNone(prepared.payload)
        self.assertNotIn(b'deleted-placeholder',prepared.associated_data)


class BoundaryRegressionTests(unittest.TestCase):
    setUp = FoundationTests.setUp
    eligible = FoundationTests.eligible

    def test_queue_cannot_enqueue_local_content_or_wrong_revision(self):
        local = self.store.put_command('local')
        with self.assertRaises(ValueError),transaction(self.store.connection):
            self.store.outbox.enqueue(local.uuid,local.revision,'upsert')
        eligible = self.eligible(local)
        with self.assertRaises(ValueError),transaction(self.store.connection):
            self.store.outbox.enqueue(eligible.uuid,eligible.revision+1,'upsert')

    def test_forged_operation_is_not_prepared(self):
        from dataclasses import replace
        self.eligible(self.store.put_command('safe-placeholder'))
        event = replace(self.store.outbox.pending()[0],operation='delete')
        with self.assertRaises(ValueError):
            prepare_change(self.store,event,scanner=AllowScanner(),cipher=None)

    def test_cipher_failure_preserves_the_outbox(self):
        self.eligible(self.store.put_command('safe-placeholder'))
        event = self.store.outbox.pending()[0]
        class UnavailableCipher:
            def encrypt(self, plaintext, **kwargs):
                raise RuntimeError('key unavailable')
        with self.assertRaises(RuntimeError):
            prepare_change(self.store,event,scanner=AllowScanner(),cipher=UnavailableCipher())
        self.assertEqual(self.store.outbox.pending(),[event])

    def test_database_rejects_noncanonical_uuid_at_insert(self):
        with self.assertRaises(sqlite3.IntegrityError),transaction(self.store.connection):
            now = utc_now()
            self.store.connection.execute('''INSERT INTO cv_records
                (kind,created_at,updated_at,uuid) VALUES (?,?,?,?)''',('tag',now,now,'x'*36))


@unittest.skipUnless(os.name == 'posix','favorites locking uses POSIX flock')
class ConcurrentFavoritesTests(unittest.TestCase):
    def test_two_processes_keep_both_additions_and_stable_metadata(self):
        import subprocess
        import sys
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'favorites.json'
            script = ('from pathlib import Path; from src.sync.favorites import FavoritesBridge; '
                      'import sys; FavoritesBridge(Path(sys.argv[1])).update(sys.argv[2])')
            processes = [subprocess.Popen([sys.executable,'-c',script,str(path),command],
                            stdout=subprocess.PIPE,stderr=subprocess.PIPE)
                         for command in ('first','second')]
            for process in processes:
                stdout,stderr = process.communicate(timeout=10)
                self.assertEqual(process.returncode,0,stderr.decode())
            bridge = FavoritesBridge(path)
            self.assertEqual(set(bridge.read()),{'first','second'})
            with LocalStore(bridge.database_path) as store:
                self.assertEqual(len(store.records()),2)
                self.assertEqual(store.outbox.pending(),[])
