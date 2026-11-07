"""Isolated update state, customer-data preservation and interruption recovery."""
from __future__ import annotations

import base64
from contextlib import closing
import copy
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'packaging')]
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from build_update_patch import export_patch
from desktop_updates.bootstrap import BASE_DIGEST
from desktop_updates.data_guard import (atomic_json, assert_attachments, assert_current, assert_retained,
    database_path, digest_file, logical_digest, prepare_candidate, snapshot)
from desktop_updates.manager import UpdateManager, _FileLock
from desktop_updates.protocol import UpdateError, canonical_json


class UpdateManagerTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix='ljqc-update-state-')
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.root = self.folder / 'customer'
        self.db = self.root / 'data/qc_lj_app.db'
        self.db.parent.mkdir(parents=True)
        self.attachment = b'original attachment bytes'
        import hashlib
        sha = hashlib.sha256(self.attachment).hexdigest()
        with closing(sqlite3.connect(self.db)) as connection:
            connection.executescript('''
                CREATE TABLE projects(id INTEGER PRIMARY KEY,name TEXT);
                CREATE TABLE batches(id INTEGER PRIMARY KEY,project_id INTEGER REFERENCES projects(id));
                CREATE TABLE results(id INTEGER PRIMARY KEY,batch_id INTEGER,test_time TEXT,value REAL);
                CREATE TABLE report_exports(id INTEGER PRIMARY KEY,pdf BLOB);
                CREATE TABLE app_settings(key TEXT PRIMARY KEY,value TEXT);
                CREATE TABLE qc_managed_files(relative_path TEXT,size_bytes INTEGER,sha256 TEXT);
                INSERT INTO projects VALUES(1,'customer project');
                INSERT INTO batches VALUES(1,1);
                INSERT INTO results VALUES(1,1,'2026-11-07',123.5);
                INSERT INTO app_settings VALUES('first_use_onboarding','{"step":"done","status":"completed"}');
            ''')
            connection.execute('INSERT INTO report_exports VALUES(1,?)', (b'%PDF original frozen report',))
            connection.execute('INSERT INTO qc_managed_files VALUES(?,?,?)', ('one/report.txt', len(self.attachment), sha))
            connection.commit()
        file = self.db.parent / 'qc_lj_app_files/one/report.txt'
        file.parent.mkdir(parents=True)
        file.write_bytes(self.attachment)
        self.db.chmod(0o600)
        self.key = Ed25519PrivateKey.generate()
        public = base64.b64encode(self.key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
        self.trust = self.folder / 'trust.json'
        self.trust.write_text(json.dumps({'format_version': 1, 'product': 'LJQCApp', 'channel': 'dev',
            'base_release': '1.0.0-2026110606', 'origin': 'https://159.75.243.120', 'keys': {'qa': public}}))
        self.manager = UpdateManager(self.root, 'windows-x86_64', self.trust)
        base = self.folder / 'base'
        base.mkdir()
        (base / 'LJQCApp.exe').write_bytes(b'isolated base fixture')
        self.manager.register_base(base, 'LJQCApp.exe', target='windows-x86_64', source_digest=BASE_DIGEST)
        payload = self.folder / 'new-program'
        payload.mkdir()
        (payload / 'LJQCApp.exe').write_bytes(b'isolated new program fixture')
        metadata = {'version': '1.0.1', 'build_number': 2026110701, 'data_format_version': 1,
            'target': 'windows-x86_64', 'git_commit': 'a' * 40, 'source_digest': 'b' * 64, 'source_dirty': False}
        output = self.folder / 'export'
        self.manifest = export_patch(payload, metadata, output)
        self.archive = output / self.manifest['artifact']['url'].rsplit('/', 1)[-1]
        self.original = logical_digest(self.db)

    def signed(self, manifest=None):
        value = manifest or self.manifest
        signature = self.key.sign(canonical_json(value))
        return json.dumps({'signed': value, 'key_id': 'qa', 'signature': base64.b64encode(signature).decode()}).encode()

    def accept(self, manifest=None):
        with patch('desktop_updates.manager.fetch_manifest', return_value=self.signed(manifest)):
            return self.manager.check()

    def fake_preflight(self, candidate, manifest, directory, backup, receipt):
        database = prepare_candidate(backup, directory / 'candidate-data', receipt)
        assert_retained(backup / 'database.db', database)
        assert_attachments(database, receipt['files'])
        return database

    def test_snapshot_retains_all_typed_cells_report_blob_and_registered_file(self):
        receipt = snapshot(self.root, self.folder / 'snapshot')
        self.assertEqual(logical_digest(self.folder / 'snapshot/database.db'), self.original)
        self.assertEqual((self.folder / 'snapshot/files/one/report.txt').read_bytes(), self.attachment)
        self.assertEqual(receipt['logical_digest'], self.original)
        assert_current(self.root, receipt)

    def test_additive_migration_retains_original_columns_and_cells(self):
        target = self.folder / 'candidate.db'
        shutil.copy2(self.db, target)
        with closing(sqlite3.connect(target)) as connection:
            connection.execute('ALTER TABLE projects ADD COLUMN additional_setting TEXT')
            connection.execute('CREATE TABLE future_settings(id INTEGER PRIMARY KEY,value TEXT)')
            connection.commit()
        assert_retained(self.db, target)
        with closing(sqlite3.connect(target)) as connection:
            connection.execute("UPDATE projects SET name='lost original'")
            connection.commit()
        with self.assertRaises(UpdateError):
            assert_retained(self.db, target)

    def test_changed_pdf_or_missing_attachment_blocks_upgrade(self):
        target = self.folder / 'changed.db'
        shutil.copy2(self.db, target)
        with closing(sqlite3.connect(target)) as connection:
            connection.execute("UPDATE report_exports SET pdf=x'01'")
            connection.commit()
        with self.assertRaises(UpdateError):
            assert_retained(self.db, target)
        (self.db.parent / 'qc_lj_app_files/one/report.txt').unlink()
        with self.assertRaises(UpdateError):
            snapshot(self.root, self.folder / 'bad-backup')

    def test_equal_sequence_changed_body_and_lower_build_are_rejected(self):
        self.accept()
        changed = copy.deepcopy(self.manifest)
        changed['release_notes'] = 'same sequence altered'
        with self.assertRaises(UpdateError):
            self.accept(changed)
        changed['sequence'] += 1
        changed['build_number'] = 1
        with self.assertRaises(UpdateError):
            self.accept(changed)

    def test_apply_uses_cached_signature_not_mutated_ui_dictionary(self):
        manifest = self.accept()
        manifest['artifact']['url'] = 'https://evil.invalid/file'
        with patch.object(self.manager, '_preflight', side_effect=self.fake_preflight):
            state = self.manager.apply(manifest, self.archive)
        self.assertEqual(state['current']['build_number'], 2026110701)
        self.assertEqual(logical_digest(self.db), self.original)
        self.assertEqual(state['base']['build_number'], 2026110606)

    def test_modified_cached_envelope_is_rejected_before_switch(self):
        manifest = self.accept()
        path = self.manager.directory / 'metadata' / (manifest['_metadata_digest'] + '.json')
        cached = json.loads(path.read_text())
        cached['signed']['version'] = '9.9.9'
        path.write_text(json.dumps(cached))
        with self.assertRaises(UpdateError):
            self.manager.apply(manifest, self.archive)
        self.assertEqual(self.manager.get_state()['current']['build_number'], 2026110606)

    def test_running_desktop_prevents_switch(self):
        manifest = self.accept()
        with _FileLock(self.root / 'runtime/desktop.lock'):
            with self.assertRaises(UpdateError):
                self.manager.apply(manifest, self.archive)
        self.assertEqual(logical_digest(self.db), self.original)

    def test_external_database_setting_and_corrupt_config_never_create_empty_db(self):
        external = self.folder / 'external/customer.db'
        external.parent.mkdir()
        shutil.copy2(self.db, external)
        config = self.root / 'config/storage_config.json'
        atomic_json(config, {'database_path': str(external)})
        self.assertEqual(database_path(self.root), external)
        config.write_text('{broken')
        with self.assertRaises(RuntimeError):
            snapshot(self.root, self.folder / 'must-not-create')
        self.assertEqual(logical_digest(external), self.original)

    def test_migration_switch_preserves_file_mode_and_original_report(self):
        manifest = self.accept()
        def additive(candidate, metadata, directory, backup, receipt):
            path = self.fake_preflight(candidate, metadata, directory, backup, receipt)
            with closing(sqlite3.connect(path)) as connection:
                connection.execute('CREATE TABLE added_feature(id INTEGER PRIMARY KEY)')
                connection.commit()
            return path
        with patch.object(self.manager, '_preflight', side_effect=additive):
            state = self.manager.apply(manifest, self.archive)
        backup = next((self.manager.directory / 'transactions').glob('*/backup/database.db'))
        assert_retained(backup, self.db)
        self.assertEqual(state['current']['version'], '1.0.1')
        if os.name != 'nt':
            self.assertEqual(self.db.stat().st_mode & 0o777, 0o600)

    def test_committed_recovery_does_not_restore_over_new_records(self):
        atomic_json(self.manager.journal_path, {'phase': 'committed'})
        with closing(sqlite3.connect(self.db)) as connection:
            connection.execute("INSERT INTO projects VALUES(2,'saved after update')")
            connection.commit()
        fresh = logical_digest(self.db)
        with self.manager.maintenance():
            self.manager._recover()
        self.assertEqual(logical_digest(self.db), fresh)
        self.assertFalse(self.manager.journal_path.exists())

    def test_interruption_between_database_renames_recovers_forward(self):
        manifest = self.accept()
        original_switch = self.manager._switch_data
        def interrupted(journal):
            original_switch(journal)
            raise OSError('simulated power interruption before program commit')
        def additive(candidate, metadata, directory, backup, receipt):
            path = self.fake_preflight(candidate, metadata, directory, backup, receipt)
            with closing(sqlite3.connect(path)) as connection:
                connection.execute('CREATE TABLE after_update(id INTEGER PRIMARY KEY)')
                connection.commit()
            return path
        with patch.object(self.manager, '_preflight', side_effect=additive), patch.object(self.manager, '_switch_data', side_effect=interrupted):
            with self.assertRaises(OSError):
                self.manager.apply(manifest, self.archive)
        with self.manager.maintenance():
            self.manager._recover()
        self.assertEqual(self.manager.get_state()['current']['build_number'], 2026110701)
        self.assertFalse(self.manager.journal_path.exists())

    def test_custom_database_missing_between_renames_can_recover(self):
        atomic_json(self.root / 'config/storage_config.json', {'database_path': str(self.db)})
        manifest = self.accept()
        real_replace = os.replace
        def replace(source, destination):
            if Path(source).name.startswith('.' + self.db.name + '.update-') and Path(destination) == self.db:
                raise OSError('simulated interruption between the two DB renames')
            return real_replace(source, destination)
        def additive(candidate, metadata, directory, backup, receipt):
            path = self.fake_preflight(candidate, metadata, directory, backup, receipt)
            with closing(sqlite3.connect(path)) as connection:
                connection.execute('CREATE TABLE recovered_addition(id INTEGER PRIMARY KEY)')
                connection.commit()
            return path
        with patch.object(self.manager, '_preflight', side_effect=additive), patch('desktop_updates.manager.os.replace', side_effect=replace):
            with self.assertRaises(OSError):
                self.manager.apply(manifest, self.archive)
        self.assertFalse(self.db.exists())
        with self.manager.maintenance():
            self.manager._recover()
        self.assertTrue(self.db.is_file())
        self.assertEqual(self.manager.get_state()['current']['build_number'], 2026110701)

    def test_new_records_after_interruption_are_never_overwritten(self):
        manifest = self.accept()
        original_switch = self.manager._switch_data
        def interrupted(journal):
            original_switch(journal)
            raise OSError('simulated interruption')
        def additive(candidate, metadata, directory, backup, receipt):
            path = self.fake_preflight(candidate, metadata, directory, backup, receipt)
            with closing(sqlite3.connect(path)) as connection:
                connection.execute('CREATE TABLE new_feature(id INTEGER PRIMARY KEY)')
                connection.commit()
            return path
        with patch.object(self.manager, '_preflight', side_effect=additive), patch.object(self.manager, '_switch_data', side_effect=interrupted):
            with self.assertRaises(OSError):
                self.manager.apply(manifest, self.archive)
        with closing(sqlite3.connect(self.db)) as connection:
            connection.execute("INSERT INTO projects VALUES(2,'new record after interruption')")
            connection.commit()
        current = logical_digest(self.db)
        with self.manager.maintenance():
            with self.assertRaises(UpdateError):
                self.manager._recover()
        self.assertEqual(logical_digest(self.db), current)

    def test_real_preflight_initializes_only_candidate_database(self):
        candidate = self.folder / 'real-candidate'
        candidate.mkdir()
        atomic_json(candidate / 'update-candidate.json', {'format': 'ljqc-update-candidate', 'base_release': '1.0.0-2026110606'})
        result_file = candidate / 'preflight.json'
        result = subprocess.run([sys.executable, str(ROOT / 'run_app.py'), '--update-preflight', '--update-result', str(result_file)],
            env={**os.environ, 'LJQC_DATA_ROOT': str(candidate), 'LJQC_UPDATE_PREFLIGHT': '1'},
            capture_output=True, timeout=45)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
        self.assertEqual(json.loads(result_file.read_text())['status'], 'passed')
        self.assertTrue((candidate / 'data/qc_lj_app.db').is_file())
        self.assertEqual(logical_digest(self.db), self.original)
        # The legacy initializer refreshes reference timestamps on each normal
        # run. Upgrade preflight must not confuse that with customer-data edits.
        before = self.folder / 'real-before.db'
        shutil.copy2(candidate / 'data/qc_lj_app.db', before)
        time.sleep(1.1)
        repeated = subprocess.run([sys.executable, str(ROOT / 'run_app.py'), '--update-preflight', '--update-result', str(result_file)],
            env={**os.environ, 'LJQC_DATA_ROOT': str(candidate), 'LJQC_UPDATE_PREFLIGHT': '1'},
            capture_output=True, timeout=45)
        self.assertEqual(repeated.returncode, 0, repeated.stderr.decode(errors='replace'))
        assert_retained(before, candidate / 'data/qc_lj_app.db')

    def test_preflight_without_explicit_candidate_marker_refuses_initialization(self):
        candidate = self.folder / 'not-a-candidate'
        candidate.mkdir()
        result = subprocess.run([sys.executable, str(ROOT / 'run_app.py'), '--update-preflight', '--update-result', str(candidate / 'result.json')],
            env={**os.environ, 'LJQC_DATA_ROOT': str(candidate), 'LJQC_UPDATE_PREFLIGHT': '1'},
            capture_output=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(list(candidate.rglob('*.db')))


if __name__ == '__main__':
    unittest.main()
