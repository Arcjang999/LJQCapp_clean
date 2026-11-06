"""Fresh-directory checks for fixed desktop paths and explicit legacy adoption."""
from contextlib import closing, contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import database as db
from services import storage_service as storage
from services.storage_paths import get_storage_paths, read_storage_config, StoragePathError
from services.schema_compatibility import inspect_database, SchemaCompatibilityError


PDF = b'%PDF-1.4\nretained-original-report\n%%EOF\n'
ZIP = b'PK\x03\x04retained-original-archive'


@contextmanager
def bind_database(path):
    names = ('DB_PATH', 'DEFAULT_DB_PATH', 'STORAGE_CONFIG_PATH', 'LEGACY_DB_CANDIDATES')
    old = {key: getattr(db, key) for key in names}
    db.DB_PATH = db.DEFAULT_DB_PATH = path
    db.STORAGE_CONFIG_PATH = path.parent / 'storage_config.json'
    db.LEGACY_DB_CANDIDATES = []
    try:
        yield
    finally:
        for key, value in old.items():
            setattr(db, key, value)


def seed_source(path):
    from services.out_of_control_attachment_service import stage_attachment
    with bind_database(path):
        db.init_db()
        project = db.create_project('原项目')
        batch = db.create_batch(project_id=project, instrument='原仪器', reagent='原试剂',
            qc_material='原质控品', concentration='一个水平', lot_no='QC-KEEP', target_n=20)
        export = db.create_report_export_snapshot(report_type='lj', project_id=project, batch_id=batch,
            report_month='2026-09', generated_at='2026-09-30 12:00:00', input_value_type='raw',
            method_label='L-J', summary={'source': 'retained'}, file_name='original.pdf')
        with db.atomic_write() as connection:
            connection.execute('INSERT INTO report_export_files VALUES(?,?,?)',
                (export, PDF, hashlib.sha256(PDF).hexdigest()))
            connection.execute('''INSERT INTO qc_report_archive_files
                (export_id,request_key,request_digest,archive_name,archive_bytes,sha256,size_bytes,manifest_json)
                VALUES(?,?,?,?,?,?,?,?)''',
                (export, 'retained', 'digest', 'original.zip', ZIP, hashlib.sha256(ZIP).hexdigest(), len(ZIP), '{}'))
            connection.execute('''INSERT INTO qc_ooc_events
                (id,source_type,source_id,qc_method,original_classification,origin_snapshot_json,opened_by,opened_at)
                VALUES(1,'lj_result',1,'lj','reject','{}','原检验员','2026-09-01')''')
        stage_attachment(1, '原附件.csv', '检测值\n10\n'.encode(), uploaded_by='原检验员')
    files = path.with_name(path.stem + '_files')
    (files / 'retained-unregistered.txt').write_text('原位置额外资料', encoding='utf-8')
    return files


class StoragePathsAdoptionTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.target = self.root / 'fixed'
        self.environment = patch.dict(os.environ, {'LJQC_DATA_ROOT': str(self.target)})
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.temp.cleanup()

    def test_path_resolution_is_pure_and_source_cloud_desktop_rules_are_explicit(self):
        base = self.root / 'source'
        self.assertEqual(base/'data', get_storage_paths(base_dir=base, environ={}, frozen=False).data_dir)
        cloud = get_storage_paths(base_dir=base, environ={'LJQC_DEPLOYMENT_MODE':'cloud'}, frozen=True, platform='darwin')
        self.assertEqual(base/'data/storage_config.json', cloud.storage_config_path)
        self.assertFalse(cloud.uses_stable_root)
        windows = get_storage_paths(environ={'LOCALAPPDATA':str(self.root/'local')}, frozen=True, platform='win32')
        self.assertEqual(self.root/'local/LJQCApp/config/storage_config.json', windows.storage_config_path)
        mac = get_storage_paths(environ={}, frozen=True, platform='darwin', home=self.root/'home')
        self.assertEqual(self.root/'home/Library/Application Support/LJQCApp/data/qc_lj_app.db', mac.default_db_path)
        explicit = get_storage_paths(environ={'LJQC_DATA_ROOT':str(self.target),'LJQC_DEPLOYMENT_MODE':'cloud'}, frozen=False)
        self.assertEqual(self.target/'config/storage_config.json', explicit.storage_config_path)
        self.assertEqual(self.target/'data/qc_lj_app.db', explicit.default_db_path)
        self.assertFalse(self.target.exists())

    def test_bad_or_missing_saved_location_never_silently_falls_back(self):
        config = self.root/'storage_config.json'
        for contents in ('{', '[]', '{}', '{"database_path":"relative.db"}',
                         json.dumps({'database_path':str(self.root/'missing.db')})):
            with self.subTest(contents=contents):
                config.write_text(contents)
                with self.assertRaises(StoragePathError):
                    read_storage_config(config)
        self.assertFalse((self.root/'missing.db').exists())
        self.assertFalse(self.target.exists())

    def test_default_adoption_retains_wal_rows_original_reports_and_all_files(self):
        old = self.root/'old/data';old.mkdir(parents=True)
        source = old/'qc_lj_app.db';files=seed_source(source)
        with closing(sqlite3.connect(source)) as keeper:
            self.assertEqual('wal', keeper.execute('PRAGMA journal_mode=WAL').fetchone()[0])
            keeper.execute("INSERT INTO app_settings VALUES('wal-proof','最新提交','2026-11-06')")
            keeper.commit()
            before=storage._database_dump_digest(source)
            original_files=storage._external_file_inventory(files)
            self.assertTrue(Path(str(source)+'-wal').exists())
            result=storage.adopt_legacy_storage(old)
            self.assertTrue(result.adopted)
            self.assertFalse(result.kept_custom_path)
            self.assertEqual(self.target/'data/qc_lj_app.db', result.database_path)
            self.assertEqual(before, storage._database_dump_digest(source))
            self.assertEqual(before, storage._database_dump_digest(result.database_path))
            self.assertEqual(original_files, storage._external_file_inventory(result.database_path.with_name('qc_lj_app_files')))
            with closing(sqlite3.connect(result.database_path)) as connection:
                self.assertEqual('最新提交', connection.execute("SELECT value FROM app_settings WHERE key='wal-proof'").fetchone()[0])
                self.assertEqual(PDF, connection.execute('SELECT pdf_bytes FROM report_export_files').fetchone()[0])
                self.assertEqual(ZIP, connection.execute('SELECT archive_bytes FROM qc_report_archive_files').fetchone()[0])
            self.assertTrue(source.exists())
            self.assertTrue(Path(str(source)+'-wal').exists())

    def test_existing_custom_pointer_is_preserved_without_copying_or_initializing(self):
        old=self.root/'old/data';old.mkdir(parents=True)
        source=self.root/'selected elsewhere/custom.db';seed_source(source)
        old_config=old/'storage_config.json'
        old_config.write_text(json.dumps({'database_path':str(source)}))
        original=old_config.read_bytes();before=storage._database_dump_digest(source)
        with patch.object(db, 'init_db', side_effect=AssertionError('adoption must not initialize')):
            result=storage.adopt_legacy_storage(old)
        self.assertTrue(result.kept_custom_path)
        self.assertEqual(source,result.database_path)
        self.assertEqual(str(source),read_storage_config(result.config_path)['database_path'])
        self.assertEqual(before,storage._database_dump_digest(source))
        self.assertEqual(original,old_config.read_bytes())
        self.assertFalse((self.target/'data/qc_lj_app.db').exists())

    def test_second_adoption_uses_stable_choice_and_does_not_import_again(self):
        old=self.root/'old';old.mkdir();source=old/'qc_lj_app.db';seed_source(source)
        first=storage.adopt_legacy_storage(old)
        config=first.config_path.read_bytes();before=storage._database_dump_digest(first.database_path)
        second=storage.adopt_legacy_storage(self.root/'different-missing-old-folder')
        self.assertFalse(second.adopted)
        self.assertEqual(first.database_path,second.database_path)
        self.assertEqual(config,first.config_path.read_bytes())
        self.assertEqual(before,storage._database_dump_digest(first.database_path))

    def test_legacy_missing_or_corrupt_configuration_never_creates_a_target(self):
        old=self.root/'old';old.mkdir()
        with self.assertRaises(RuntimeError):storage.adopt_legacy_storage(old)
        (old/'storage_config.json').write_text('{')
        with self.assertRaises(RuntimeError):storage.adopt_legacy_storage(old)
        self.assertFalse(self.target.exists())

    def test_future_columns_format_and_migration_refuse_writes_without_changing_bytes(self):
        for name,statement in (
            ('projects', 'ALTER TABLE projects ADD COLUMN future_value TEXT'),
            ('results', 'ALTER TABLE results ADD COLUMN future_value TEXT'),
            ('format', 'PRAGMA user_version=99'),
            ('migration', "INSERT INTO schema_migrations(migration_key) VALUES('future_999')"),
        ):
            with self.subTest(name=name):
                path=self.root/name/'qc.db'
                with bind_database(path):
                    db.init_db()
                    with closing(sqlite3.connect(path)) as connection, connection:
                        connection.execute(statement)
                    before=path.read_bytes()
                    with self.assertRaises(SchemaCompatibilityError):db.init_db()
                    with self.assertRaises(SchemaCompatibilityError):db.get_connection()
                    self.assertEqual(before,path.read_bytes())

    def test_copy_and_configuration_failure_preserve_source_and_do_not_publish(self):
        old=self.root/'old';old.mkdir();source=old/'qc_lj_app.db';seed_source(source)
        before=storage._database_dump_digest(source)
        with patch.object(storage.shutil,'copytree',side_effect=OSError('injected attachment copy failure')):
            with self.assertRaises(OSError):storage.adopt_legacy_storage(old)
        self.assertFalse((self.target/'config/storage_config.json').exists())
        self.assertFalse((self.target/'data/qc_lj_app.db').exists())
        original_replace=Path.replace
        def reject_config(path,target):
            if path.name.startswith('.storage_config.'):
                raise PermissionError('injected config publish failure')
            return original_replace(path,target)
        with patch.object(Path,'replace',reject_config):
            with self.assertRaises(PermissionError):storage.adopt_legacy_storage(old)
        self.assertFalse((self.target/'config/storage_config.json').exists())
        self.assertFalse((self.target/'data/qc_lj_app.db').exists())
        self.assertEqual(before,storage._database_dump_digest(source))

    def test_incomplete_adoption_lock_blocks_empty_initialization(self):
        config=self.target/'config/storage_config.json';config.parent.mkdir(parents=True)
        (config.parent/'.legacy-adoption.lock').write_text('retained interrupted attempt')
        with bind_database(self.target/'data/qc_lj_app.db'):
            db.STORAGE_CONFIG_PATH=config
            with self.assertRaises(StoragePathError):db.init_db()
        self.assertFalse((self.target/'data/qc_lj_app.db').exists())

    def test_unknown_source_and_existing_target_are_not_overwritten(self):
        old=self.root/'old';old.mkdir();source=old/'qc_lj_app.db';seed_source(source)
        with closing(sqlite3.connect(source)) as connection, connection:
            connection.execute('ALTER TABLE results ADD COLUMN future_value TEXT')
        before=source.read_bytes()
        with self.assertRaises(SchemaCompatibilityError):storage.adopt_legacy_storage(old)
        self.assertEqual(before,source.read_bytes())
        self.assertFalse(self.target.exists())
        other=self.root/'other';other.mkdir();seed_source(other/'qc_lj_app.db')
        target=self.target/'data/qc_lj_app.db';target.parent.mkdir(parents=True);target.write_bytes(b'keep existing target')
        with self.assertRaises(StoragePathError):storage.adopt_legacy_storage(other)
        self.assertEqual(b'keep existing target',target.read_bytes())
        self.assertFalse((self.target/'config/storage_config.json').exists())

    def test_disappearing_saved_database_does_not_create_an_empty_replacement(self):
        path=self.root/'selected/qc.db';seed_source(path)
        with bind_database(path):
            db.save_db_path_config(path)
            kept=path.with_suffix('.kept');path.replace(kept)
            with self.assertRaises(StoragePathError):db.get_connection()
            with self.assertRaises(StoragePathError):db.init_db()
            self.assertFalse(path.exists())
            self.assertTrue(kept.exists())

    def test_busy_source_is_not_adopted(self):
        old=self.root/'old';old.mkdir();source=old/'qc_lj_app.db';seed_source(source)
        with closing(sqlite3.connect(source)) as writer:
            writer.execute('BEGIN IMMEDIATE')
            with self.assertRaisesRegex(RuntimeError,'原程序仍在写入'):
                storage.adopt_legacy_storage(old)
            writer.rollback()
        self.assertFalse((self.target/'config/storage_config.json').exists())
        self.assertFalse((self.target/'data/qc_lj_app.db').exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
