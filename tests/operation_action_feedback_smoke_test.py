"""Action prerequisites stay visible and clear when users fix the missing step."""
from datetime import date
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import database as db
from streamlit.testing.v1 import AppTest
from tests.daily_entry_smoke_test import seed_twenty
from tests.daily_ui_smoke_test import APP as DAILY_APP


class OperationActionFeedbackTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = {key: getattr(db, key) for key in
                        ('DB_PATH', 'DEFAULT_DB_PATH', 'STORAGE_CONFIG_PATH', 'LEGACY_DB_CANDIDATES')}
        cls.base = TemporaryDirectory()
        db.DB_PATH = db.DEFAULT_DB_PATH = Path(cls.base.name)/'base.db'
        db.STORAGE_CONFIG_PATH = Path(cls.base.name)/'storage.json'
        db.LEGACY_DB_CANDIDATES = []
        db.init_db()
        cls.fixture = seed_twenty()
        cls.base_path = db.DB_PATH

    @classmethod
    def tearDownClass(cls):
        for key, value in cls.original.items():
            setattr(db, key, value)
        cls.base.cleanup()

    def setUp(self):
        self.temp = TemporaryDirectory()
        db.DB_PATH = db.DEFAULT_DB_PATH = Path(self.temp.name)/'test.db'
        db.STORAGE_CONFIG_PATH = Path(self.temp.name)/'storage.json'
        db.LEGACY_DB_CANDIDATES = []
        shutil.copyfile(self.base_path, db.DB_PATH)

    def tearDown(self):
        self.temp.cleanup()

    def healthy(self, app):
        self.assertFalse(list(app.exception), [error.message for error in app.exception])

    def has_info(self, app, text):
        return any(text in item.value for item in app.info)

    def click(self, app, label):
        next(button for button in app.button if button.label == label).click().run()
        self.healthy(app)

    def snapshot(self):
        with db.read_snapshot() as connection:
            return tuple(connection.iterdump())

    def daily(self):
        from services.daily_context_service import get_daily_context
        from services.daily_draft_service import new_draft
        f = self.fixture
        context = get_daily_context(f['data']['lab_instrument_id'],f['data']['qc_material_id'],
            f['main_lot'],'2026-09-28 08:00:00',lot_config_id=f['config_id'])
        app = AppTest.from_string(DAILY_APP, default_timeout=30)
        app.session_state['show_daily_entry_page'] = True
        app.session_state['daily_draft'] = new_draft(context)
        app.session_state['daily_restore_selection'] = True
        app.run()
        self.healthy(app)
        return app

    def test_changed_paste_and_empty_project_selection_have_visible_next_step(self):
        app = self.daily()
        before = self.snapshot()
        draft = app.session_state['daily_draft']
        key = 'entry_paste_'+draft['draft_id']
        app.text_area(key=key).set_value('\n'.join('101.25' for _ in draft['values'])).run()
        self.click(app, '预览粘贴内容')
        apply = lambda: next(button for button in app.button if button.label == '确认带入本组')
        self.assertFalse(apply().disabled)
        app.text_area(key=key).set_value('\n'.join('102.5' for _ in draft['values'])).run()
        self.assertTrue(apply().disabled)
        self.assertTrue(self.has_info(app, '请重新点击“预览粘贴内容”'))
        self.click(app, '预览粘贴内容')
        self.assertFalse(apply().disabled)
        self.assertFalse(self.has_info(app, '请重新点击“预览粘贴内容”'))
        app.multiselect(key='entry_selected_'+draft['draft_id']).set_value([]).run()
        self.healthy(app)
        self.assertTrue(app.button(key='entry_validate').disabled)
        self.assertTrue(self.has_info(app, '“本次录入项目”中至少选择一个'))
        self.assertEqual(before, self.snapshot())

    def test_replacing_result_file_explains_why_old_preview_cannot_be_applied(self):
        app = self.daily()
        before = self.snapshot()
        app.session_state['daily_draft']['file_preview'] = {'file_hash': 'previous-upload'}
        app.session_state['test_upload_bytes'] = b'new upload not previewed'
        app.run()
        self.healthy(app)
        self.assertTrue(self.has_info(app, '上传的文件已更换'))
        self.assertTrue(any(button.label == '预览结果文件' for button in app.button))
        self.assertFalse(any(button.label == '带入结果草稿' for button in app.button))
        self.assertEqual(before, self.snapshot())

    def test_instant_transfer_conditions_are_visible_without_opening_an_expander(self):
        script = '''
import streamlit as st
from pages.instant_page import _render_instant_transfer_section
eligible=st.session_state.get('test_eligible',False)
_render_instant_transfer_section({'summary':{'effective_count':20 if eligible else 3,'disabled_count':0},
 'transfer_state':{'eligible':eligible,'blockers':[] if eligible else ['有效建靶点不足20个']}})
'''
        app = AppTest.from_string(script).run()
        self.healthy(app)
        self.assertTrue(app.button(key='open_instant_transfer_dialog').disabled)
        self.assertTrue(self.has_info(app, '有效建靶点不足20个'))
        self.assertFalse(any(expander.label == '查看转入条件' for expander in app.expander))
        app.session_state['test_eligible'] = True
        app.run()
        self.assertFalse(app.button(key='open_instant_transfer_dialog').disabled)
        self.assertFalse(self.has_info(app, '有效建靶点不足20个'))
        app.session_state['qc_transfer_dialog'] = {'mode': 'import'}
        app.run()
        self.assertTrue(app.button(key='open_instant_transfer_dialog').disabled)
        self.assertTrue(self.has_info(app, '请先完成或取消当前打开'))

    def test_batch_report_missing_method_and_report_selection_explain_the_action(self):
        script = '''
from unittest.mock import patch
import streamlit as st
from ui.batch_monthly_reports import render_batch_monthly_reports_page
def preview(start,end,**kwargs):
 return {'scope':dict(report_month=kwargs.get('report_month'),period_start=start,period_end=end,
  lab_instrument_id=kwargs['lab_instrument_id'],template_id=kwargs['template_id'],methods=sorted(kwargs['methods'])),
 'items':[dict(unit_key='one',project_name='项目甲',instrument_name='仪器甲',lot_no='LOT-A',
  report_month=kwargs.get('report_month'),method_label='单水平（LJ）',formal_count=20)],'exclusions':[]}
with patch('ui.batch_monthly_reports.list_monthly_report_choices',return_value={'months':['2026-09'],'instruments':{},'templates':{}}), \
 patch('ui.batch_monthly_reports.list_monthly_report_jobs',return_value=[]), \
 patch('ui.batch_monthly_reports.preview_period_reports',side_effect=preview):
 render_batch_monthly_reports_page()
'''
        app = AppTest.from_string(script).run()
        self.healthy(app)
        app.multiselect(key='monthly_methods').set_value([]).run()
        self.assertTrue(app.button(key='monthly_preview').disabled)
        self.assertTrue(self.has_info(app, '“质控方法”中至少选择一种'))
        app.multiselect(key='monthly_methods').set_value(['lj']).run()
        self.assertFalse(app.button(key='monthly_preview').disabled)
        app.button(key='monthly_preview').click().run()
        self.healthy(app)
        key = 'monthly_selection_'+app.session_state['monthly_request_id']
        app.multiselect(key=key).set_value([]).run()
        self.assertTrue(app.button(key='monthly_generate').disabled)
        self.assertTrue(self.has_info(app, '“选择本次生成的报告”中至少选择一份'))
        app.multiselect(key=key).set_value(['one']).run()
        self.assertFalse(app.button(key='monthly_generate').disabled)
        self.assertFalse(self.has_info(app, '“选择本次生成的报告”中至少选择一份'))
        app.multiselect(key='monthly_methods').set_value(['zscore']).run()
        self.assertTrue(self.has_info(app, '日期、仪器、项目或质控方法已变更'))
        self.assertFalse(any(button.key == 'monthly_generate' for button in app.button))

    def test_cloud_restore_guides_file_then_confirmation_without_writing_records(self):
        from services.storage_service import create_database_backup
        backup = create_database_backup(Path(self.temp.name)/'backup').target_path.read_bytes()
        before = self.snapshot()
        script = '''
from types import SimpleNamespace
from unittest.mock import patch
import streamlit as st
from pages.settings_page import _render_cloud_storage_section
payload=st.session_state.get('test_payload')
file=None if payload is None else SimpleNamespace(name='backup.zip',getvalue=lambda:payload)
with patch('pages.settings_page.st.file_uploader',return_value=file):
 _render_cloud_storage_section()
'''
        app = AppTest.from_string(script).run()
        self.healthy(app)
        self.assertTrue(app.button(key='cloud_confirm_restore').disabled)
        self.assertTrue(self.has_info(app, '请先在“选择备份文件”中上传备份'))
        app.session_state['test_payload'] = backup
        app.run()
        self.healthy(app)
        self.assertTrue(app.button(key='cloud_confirm_restore').disabled)
        self.assertTrue(self.has_info(app, '备份文件已通过检查。请勾选'))
        app.checkbox(key='settings_cloud_restore_confirmed').check().run()
        self.assertFalse(app.button(key='cloud_confirm_restore').disabled)
        self.assertFalse(self.has_info(app, '备份文件已通过检查。请勾选'))
        self.assertEqual(before, self.snapshot())

    def test_project_import_guides_target_selection_before_preview(self):
        script = '''
from io import BytesIO
from unittest.mock import patch
from pages.project_management_page import _render_import_export_tab
with patch('pages.project_management_page.st.file_uploader',return_value=BytesIO(b'not previewed yet')):
 _render_import_export_tab()
'''
        before = self.snapshot()
        app = AppTest.from_string(script).run()
        self.healthy(app)
        self.assertTrue(app.button(key='v11_prepare_project_import').disabled)
        self.assertTrue(self.has_info(app, '“导入到项目”中选择一个项目'))
        target = next(option for option in app.selectbox(key='v11_import_target_template').options
                      if option != '请选择目标项目')
        app.selectbox(key='v11_import_target_template').set_value(target).run()
        self.healthy(app)
        self.assertFalse(app.button(key='v11_prepare_project_import').disabled)
        self.assertFalse(self.has_info(app, '“导入到项目”中选择一个项目'))
        self.assertEqual(before, self.snapshot())

    def test_data_import_guides_upload_then_review_before_enabling_confirmation(self):
        from services.export_utils import dataframe_to_csv_bytes
        from tests.data_transfer_smoke_test import batch_for, file_for, toolbar_page
        batch = batch_for('lj')
        before = self.snapshot()
        app = AppTest.from_function(toolbar_page, args=(batch,), default_timeout=30).run()
        self.healthy(app)
        app.button(key=f'qc_transfer_open_import_lj_{batch}').click().run()
        self.healthy(app)
        state = app.session_state['qc_transfer_dialog']
        nonce = state['nonce']
        self.assertTrue(app.button(key='qc_transfer_review_'+nonce).disabled)
        self.assertTrue(app.button(key='qc_transfer_confirm_'+nonce).disabled)
        self.assertTrue(self.has_info(app, '请先在“上传检测数据”中选择'))
        state.update(upload=dataframe_to_csv_bytes(file_for('lj', 2)), upload_name='本次检测.csv')
        app.run()
        self.healthy(app)
        self.assertFalse(app.button(key='qc_transfer_review_'+nonce).disabled)
        self.assertTrue(app.button(key='qc_transfer_confirm_'+nonce).disabled)
        self.assertTrue(self.has_info(app, '请先点击“检查文件并预览”'))
        app.button(key='qc_transfer_review_'+nonce).click().run()
        self.healthy(app)
        self.assertFalse(app.button(key='qc_transfer_confirm_'+nonce).disabled)
        self.assertFalse(self.has_info(app, '请先点击“检查文件并预览”'))
        self.assertEqual(before, self.snapshot())

    def test_invalid_export_dates_point_to_date_inputs_instead_of_missing_records(self):
        from tests.data_transfer_smoke_test import batch_for, toolbar_page
        batch = batch_for('lj')
        before = self.snapshot()
        app = AppTest.from_function(toolbar_page, args=(batch,), default_timeout=30).run()
        app.button(key=f'qc_transfer_open_export_lj_{batch}').click().run()
        self.healthy(app)
        nonce = app.session_state['qc_transfer_dialog']['nonce']
        app.radio(key='qc_transfer_dates_'+nonce).set_value('按月或自选日期').run()
        prefix = 'qc_transfer_data_period_'+nonce
        app.radio(key=prefix+'_period_mode').set_value('自选日期').run()
        app.date_input(key=prefix+'_start').set_value(date(2026, 10, 26))
        app.date_input(key=prefix+'_end').set_value(date(2026, 10, 1)).run()
        self.healthy(app)
        self.assertTrue(list(app.warning))
        self.assertTrue(self.has_info(app, '请先修正上方报告期间的开始日期和结束日期'))
        self.assertFalse(self.has_info(app, '所选范围内暂无检测记录'))
        self.assertEqual(before, self.snapshot())


if __name__ == '__main__':
    unittest.main(verbosity=2)
