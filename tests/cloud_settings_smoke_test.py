"""Browser storage actions reuse the complete backup and protected restore services."""
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import database as db
from services.storage_service import create_database_backup, validate_backup_file
from tests.storage_smoke_test import TemporaryStorageContext


SCRIPT = """
from types import SimpleNamespace
from unittest.mock import patch
import streamlit as st
from pages.settings_page import _render_storage_section
payload = st.session_state.get('test_upload_payload')
upload = None if payload is None else SimpleNamespace(
    name=st.session_state.get('test_upload_name', 'backup.db'), getvalue=lambda: payload)
with patch('pages.settings_page.st.file_uploader', return_value=upload):
    _render_storage_section()
"""


class CloudSettingsTests(unittest.TestCase):
    def setUp(self):
        self.context = TemporaryStorageContext()
        self.context.__enter__()
        db.init_db()
        self.mode = patch.dict('os.environ', {'LJQC_DEPLOYMENT_MODE': 'cloud'})
        self.mode.start()

    def tearDown(self):
        self.mode.stop()
        self.context.__exit__(None, None, None)

    def app(self):
        app = AppTest.from_string(SCRIPT, default_timeout=20).run()
        self.clean(app)
        return app

    def clean(self, app):
        self.assertFalse(list(app.exception), [item.message for item in app.exception])

    def names(self, path=None):
        with closing(sqlite3.connect(path or db.get_db_path())) as connection:
            return [row[0] for row in connection.execute('SELECT name FROM projects ORDER BY id')]

    def upload(self, app, data, name='backup.db'):
        app.session_state['test_upload_payload'] = data
        app.session_state['test_upload_name'] = name
        app.run()
        self.clean(app)

    def test_cloud_has_no_server_folder_actions_and_generates_downloadable_backup(self):
        db.create_project('备份项目')
        with patch('pages.settings_page.choose_directory_via_dialog', side_effect=AssertionError('native dialog')):
            app = self.app()
            keys = {button.key for button in app.button}
            self.assertFalse(keys & {'open_db_folder', 'open_default_backup_dir', 'pick_storage_migration_dir',
                                     'backup_custom_dir', 'pick_restore_backup_file'})
            self.assertTrue(app.button(key='cloud_confirm_restore').disabled)
            app.button(key='cloud_create_backup').click().run()
            self.clean(app)
        backup = Path(app.session_state['settings_cloud_backup_path'])
        self.assertTrue(validate_backup_file(backup)[0])
        self.assertEqual(['备份项目'], self.names(backup))
        self.assertEqual(1, len(app.get('download_button')))

    def test_invalid_upload_cannot_be_restored_or_change_data(self):
        db.create_project('保留项目')
        app = self.app()
        self.upload(app, b'not a database')
        self.assertTrue(app.checkbox(key='settings_cloud_restore_confirmed').disabled)
        self.assertTrue(app.button(key='cloud_confirm_restore').disabled)
        self.assertTrue(list(app.warning))
        self.assertEqual(['保留项目'], self.names())

    def test_restore_requires_confirmation_and_keeps_downloadable_protection_backup(self):
        db.create_project('备份内项目')
        backup = create_database_backup(self.context.root / 'source').target_path
        db.create_project('恢复前新增项目')
        app = self.app()
        self.upload(app, backup.read_bytes())
        self.assertFalse(app.checkbox(key='settings_cloud_restore_confirmed').disabled)
        self.assertTrue(app.button(key='cloud_confirm_restore').disabled)
        self.assertEqual(['备份内项目', '恢复前新增项目'], self.names())
        app.checkbox(key='settings_cloud_restore_confirmed').check().run()
        app.button(key='cloud_confirm_restore').click().run()
        self.clean(app)
        self.assertEqual(['备份内项目'], self.names())
        protection = Path(app.session_state['settings_cloud_protection_path'])
        self.assertEqual(['备份内项目', '恢复前新增项目'], self.names(protection))
        self.assertTrue(any('数据已恢复' in item.value for item in app.success))
        self.assertEqual(1, len(app.get('download_button')))

    def test_changing_upload_clears_previous_restore_confirmation(self):
        db.create_project('第一份项目')
        first = create_database_backup(self.context.root / 'first').target_path.read_bytes()
        db.create_project('第二份项目')
        second = create_database_backup(self.context.root / 'second').target_path.read_bytes()
        app = self.app()
        self.upload(app, first)
        app.checkbox(key='settings_cloud_restore_confirmed').check().run()
        self.assertFalse(app.button(key='cloud_confirm_restore').disabled)
        self.upload(app, second)
        self.assertFalse(app.checkbox(key='settings_cloud_restore_confirmed').value)
        self.assertTrue(app.button(key='cloud_confirm_restore').disabled)

    def test_desktop_storage_buttons_remain_available_without_cloud_flag(self):
        with patch.dict('os.environ', {'LJQC_DEPLOYMENT_MODE': ''}):
            app = self.app()
        keys = {button.key for button in app.button}
        self.assertTrue({'open_db_folder', 'pick_storage_migration_dir', 'backup_custom_dir',
                         'pick_restore_backup_file'}.issubset(keys))
        self.assertNotIn('cloud_create_backup', keys)


if __name__ == '__main__':
    unittest.main()
