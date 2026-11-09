"""Preparation files survive cancel and failed saves without partial registration."""
from pathlib import Path
import os
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest
import database as db
from services.export_utils import dataframes_to_xlsx_bytes
from services.settings_service import get_report_settings, save_report_settings_form
from tests.lab_preparation_smoke_test import dump, initialize_receipts, tables
from tests.settings_smoke_test import TemporaryDatabaseContext


APP = 'from pages.settings_page import render_settings_page\nrender_settings_page()'


class LabPreparationUI(unittest.TestCase):
    def assert_preparation_closed(self, app):
        self.assertFalse([item for item in app.get('file_uploader')
                          if (item.key or '').startswith('lab_prep_upload_')])
        self.assertFalse([item for item in app.get('download_button')
                          if item.key in ('lab_prep_template', 'lab_prep_reference')])
        self.assert_backup_entry(app)

    def assert_backup_entry(self, app):
        uploads = [item for item in app.get('file_uploader') if item.key == 'cloud_restore_upload']
        if os.environ.get('LJQC_DEPLOYMENT_MODE', '').strip().lower() == 'cloud':
            self.assertEqual(1, len(uploads))
            self.assertEqual('选择备份文件', uploads[0].label)
            self.assertEqual('生成完整备份', app.button(key='cloud_create_backup').label)
            self.assertTrue(app.button(key='cloud_confirm_restore').disabled)
        else:
            self.assertFalse(uploads)

    def open(self):
        app = AppTest.from_string(APP, default_timeout=15).run()
        self.assertFalse(list(app.exception), list(app.exception))
        self.assert_preparation_closed(app)
        self.assertTrue(any('单位资料准备' in item.proto.body for item in app.get('html')))
        self.assertFalse(any('Excel' in item.label for item in app.expander))
        self.assertEqual('下载填写表或导入Excel', app.button(key='lab_prep_open').label)
        app.button(key='lab_prep_open').click().run()
        self.assertFalse(list(app.exception), list(app.exception))
        dialog = app.get('dialog')[0]
        self.assertEqual('单位资料准备', dialog.proto.dialog.title)
        self.assertEqual({'lab_prep_template', 'lab_prep_reference'},
                         {item.key for item in dialog.get('download_button')})
        uploads = dialog.get('file_uploader')
        self.assertEqual(1, len(uploads))
        self.assertEqual('lab_prep_upload_' + app.session_state['lab_preparation_dialog']['token'], uploads[0].key)
        self.assertEqual('上传填写后的单位资料清单', uploads[0].label)
        self.assert_backup_entry(app)
        data = dataframes_to_xlsx_bytes(tables())
        app.session_state['lab_preparation_dialog'].update(data=data, filename='单位资料.xlsx')
        app.run()
        return app, data

    def review(self, app):
        app.button(key='lab_prep_review').click().run()
        self.assertFalse(list(app.exception), list(app.exception))
        self.assertFalse(app.button(key='lab_prep_save').disabled)

    def test_excel_import_is_optional_and_settings_can_be_saved_without_a_file(self):
        with TemporaryDatabaseContext():
            app = AppTest.from_string(APP, default_timeout=15).run()
            self.assertFalse(list(app.exception), list(app.exception))
            self.assert_preparation_closed(app)
            self.assertNotIn('lab_preparation_dialog', app.session_state.filtered_state)
            self.assertTrue(any('单位资料准备' in item.proto.body for item in app.get('html')))
            self.assertFalse(any('Excel' in item.label for item in app.expander))
            self.assertEqual('下载填写表或导入Excel', app.button(key='lab_prep_open').label)
            app.text_input(key='settings_lab_name').set_value('直接填写实验室')
            app.button(key='save_system_settings').click().run()
            self.assertFalse(list(app.exception), list(app.exception))
            self.assertEqual('直接填写实验室', get_report_settings().lab_name)
            self.assertNotIn('lab_preparation_dialog', app.session_state.filtered_state)

    def test_review_and_cancel_continue_preserve_file_without_writes(self):
        with TemporaryDatabaseContext():
            initialize_receipts()
            before = dump()
            app, data = self.open()
            self.review(app)
            self.assertEqual(before, dump())
            app.button(key='lab_prep_cancel').click().run()
            self.assertTrue(app.session_state['lab_preparation_dialog']['discard'])
            app.button(key='lab_prep_keep').click().run()
            self.assertFalse(list(app.exception), list(app.exception))
            self.assertEqual(data, app.session_state['lab_preparation_dialog']['data'])
            self.assertFalse(app.button(key='lab_prep_save').disabled)
            app.button(key='lab_prep_cancel').click().run()
            app.button(key='lab_prep_discard').click().run()
            self.assertFalse(list(app.exception), list(app.exception))
            self.assertNotIn('lab_preparation_dialog', app.session_state.filtered_state)
            self.assertEqual(before, dump())

    def test_failed_save_retains_file_and_retry_saves_once_and_refreshes_settings(self):
        with TemporaryDatabaseContext():
            initialize_receipts()
            with db.atomic_write() as connection:
                connection.execute("""CREATE TRIGGER fail_unit_receipt BEFORE INSERT ON qc_lab_preparation_receipts
                    BEGIN SELECT RAISE(ABORT, 'injected failure'); END""")
            before = dump()
            app, data = self.open()
            self.review(app)
            app.button(key='lab_prep_save').click().run()
            self.assertFalse(list(app.exception), list(app.exception))
            self.assertTrue(any('未保存' in item.value for item in app.error))
            self.assertEqual(data, app.session_state['lab_preparation_dialog']['data'])
            self.assertIsNone(app.session_state['lab_preparation_dialog']['review'])
            self.assertTrue(app.button(key='lab_prep_save').disabled)
            self.assertEqual(before, dump())
            with db.atomic_write() as connection:
                connection.execute('DROP TRIGGER fail_unit_receipt')
            self.review(app)
            app.button(key='lab_prep_save').click().run()
            self.assertFalse(list(app.exception), list(app.exception))
            self.assertNotIn('lab_preparation_dialog', app.session_state.filtered_state)
            self.assertEqual('准备实验室', get_report_settings().lab_name)
            self.assertEqual('准备实验室', app.text_input(key='settings_lab_name').value)
            self.assertEqual('准备科室', app.text_input(key='settings_department_name').value)
            self.assertTrue(any('单位资料已保存' in item.value for item in app.success))
            with db.read_snapshot() as connection:
                self.assertEqual(1, connection.execute('SELECT COUNT(*) FROM qc_lab_preparation_receipts').fetchone()[0])
                self.assertEqual(1, connection.execute('SELECT COUNT(*) FROM lab_instruments').fetchone()[0])

    def test_stale_preview_is_rejected_and_file_is_still_available_for_review(self):
        with TemporaryDatabaseContext():
            initialize_receipts()
            app, data = self.open()
            self.review(app)
            save_report_settings_form({'lab_name': '另一会话的单位设置'})
            before = dump()
            app.button(key='lab_prep_save').click().run()
            self.assertFalse(list(app.exception), list(app.exception))
            self.assertTrue(any('已变化' in item.value for item in app.error))
            self.assertEqual(before, dump())
            self.assertEqual(data, app.session_state['lab_preparation_dialog']['data'])
            self.assertIsNone(app.session_state['lab_preparation_dialog']['review'])
            self.review(app)
            changes = next(table.value for table in app.dataframe if '原内容' in table.value)
            self.assertEqual('另一会话的单位设置', changes.loc[changes['项目'] == '实验室名称', '原内容'].iloc[0])


if __name__ == '__main__':
    unittest.main(verbosity=2)
