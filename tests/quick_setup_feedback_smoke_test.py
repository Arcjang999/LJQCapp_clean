"""Wizard feedback points to the actual editable step, on isolated data."""
from copy import deepcopy
import csv
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from database import get_connection
from services.project_config_service import create_project_template
from tests.instant_v12_integration_smoke_test import IsolatedDatabase
from tests.project_workspace_smoke_test import assert_clean, draft_key
from tests.quick_project_setup_smoke_test import database_contents, metadata_values
from tests.quick_project_setup_ui_smoke_test import open_details, to_preview, fill_quality, quality_prefix


class QuickSetupFeedbackTest(unittest.TestCase):
    def test_missing_name_is_reported_in_detection_settings_before_quality(self):
        with IsolatedDatabase():
            values = metadata_values()
            app = open_details(values)
            app.text_input(key=draft_key(app, 'template_name')).set_value('').run()
            self.assertTrue(any('填写本页的“项目名称”' in row.value for row in app.warning))
            before = database_contents()
            app.button(key='quick_details_next').click().run()
            assert_clean(app)
            self.assertEqual('details', app.session_state['project_workspace_dialog'].get('quick_step', 'details'))
            self.assertTrue(any('请填写项目名称' in row.value for row in app.error))
            self.assertEqual(before, database_contents())
            app.text_input(key=draft_key(app, 'template_name')).set_value(values['template_name']).run()
            app.button(key='quick_details_next').click().run()
            assert_clean(app)
            self.assertEqual('quality', app.session_state['project_workspace_dialog']['quick_step'])
            self.assertEqual(before, database_contents())

    def test_duplicate_name_does_not_wait_until_the_quality_step(self):
        with IsolatedDatabase():
            values = metadata_values()
            create_project_template(template_name=values['template_name'],
                lab_instrument_id=values['lab_instrument_id'], qc_material_id=values['qc_material_id'])
            app = open_details(values)
            before = database_contents()
            app.button(key='quick_details_next').click().run()
            assert_clean(app)
            self.assertEqual('details', app.session_state['project_workspace_dialog'].get('quick_step', 'details'))
            self.assertTrue(any('同名项目' in row.value for row in app.error))
            self.assertEqual(before, database_contents())
            app.text_input(key=draft_key(app, 'template_name')).set_value('另一项目名称').run()
            app.button(key='quick_details_next').click().run()
            assert_clean(app)
            self.assertEqual('quality', app.session_state['project_workspace_dialog']['quick_step'])

    def test_missing_unit_is_visible_and_corrected_in_the_current_step(self):
        with IsolatedDatabase():
            values = metadata_values()
            app = open_details(values)
            app.selectbox(key=draft_key(app, 'unit_id')).set_value(None).run()
            self.assertTrue(any('在本页选择“单位”' in row.value for row in app.warning))
            before = database_contents()
            app.button(key='quick_details_next').click().run()
            assert_clean(app)
            self.assertTrue(any('请选择单位' in row.value for row in app.error))
            self.assertEqual('details', app.session_state['project_workspace_dialog'].get('quick_step', 'details'))
            self.assertEqual(before, database_contents())
            app.selectbox(key=draft_key(app, 'unit_id')).set_value(values['unit_id']).run()
            app.button(key='quick_details_next').click().run()
            self.assertEqual('quality', app.session_state['project_workspace_dialog']['quick_step'])

    def test_changed_reference_in_quality_points_back_to_detection_settings(self):
        with IsolatedDatabase():
            values = metadata_values()
            app = open_details(values)
            app.button(key='quick_details_next').click().run()
            with get_connection() as connection:
                connection.execute('UPDATE lab_instruments SET is_disabled=1 WHERE id=?', (values['lab_instrument_id'],))
            before = database_contents()
            app.run()
            assert_clean(app)
            self.assertTrue(any('仪器已停用' in row.value for row in app.error))
            self.assertTrue(any('返回检测设置' in row.value for row in app.info))
            self.assertTrue(any(row.key == 'quick_invalid_back' for row in app.button))
            self.assertEqual(before, database_contents())
            app.button(key='quick_invalid_back').click().run()
            assert_clean(app)
            self.assertEqual('details', app.session_state['project_workspace_dialog']['quick_step'])
            self.assertEqual(values['template_name'], app.text_input(key=draft_key(app, 'template_name')).value)

    def test_preview_can_go_directly_to_detection_settings_without_losing_draft(self):
        with IsolatedDatabase():
            values = metadata_values()
            app = open_details(values)
            to_preview(app, values)
            draft = deepcopy(app.session_state['project_workspace_dialog']['draft'])
            before = database_contents()
            app.button(key='quick_preview_details').click().run()
            assert_clean(app)
            actual = app.session_state['project_workspace_dialog']['draft']
            self.assertEqual('details', app.session_state['project_workspace_dialog']['quick_step'])
            for field in ('template_name', 'level_ids', 'quality', 'method_id', 'unit_id'):
                self.assertEqual(draft[field], actual[field])
            self.assertEqual(before, database_contents())

    def test_new_quality_input_survives_external_name_conflict_and_back(self):
        with IsolatedDatabase():
            values = metadata_values()
            app = open_details(values)
            app.button(key='quick_details_next').click().run()
            prefix = fill_quality(app, values)
            app.text_input(key=prefix+'_person').set_value('刚填尚未单独刷新的质量确认人')
            app.text_area(key=prefix+'_evidence').set_value('点击下一步同时提交的新说明')
            create_project_template(template_name=values['template_name'],
                lab_instrument_id=values['lab_instrument_id'],qc_material_id=values['qc_material_id'])
            before = database_contents()
            app.button(key='quick_quality_next').click().run()
            assert_clean(app)
            self.assertTrue(any('同名项目' in row.value for row in app.error))
            app.button(key='quick_invalid_back').click().run()
            app.text_input(key=draft_key(app,'template_name')).set_value('修改后的项目名称').run()
            app.button(key='quick_details_next').click().run()
            assert_clean(app)
            self.assertEqual(prefix,quality_prefix(app))
            self.assertEqual('刚填尚未单独刷新的质量确认人',app.text_input(key=prefix+'_person').value)
            self.assertEqual('点击下一步同时提交的新说明',app.text_area(key=prefix+'_evidence').value)
            self.assertEqual(before,database_contents())

    def test_quality_csv_confirmation_tracks_the_current_uploaded_file(self):
        from streamlit.testing.v1 import AppTest
        from services.quality_target_service import custom_template_csv, IMPORT_COLUMNS
        def content(name):
            row = next(csv.DictReader(io.StringIO(custom_template_csv().decode('utf-8-sig'))))
            row.update(检验项目=name, 确认人='隔离核对人')
            output = io.StringIO()
            writer = csv.DictWriter(output, fieldnames=IMPORT_COLUMNS)
            writer.writeheader(); writer.writerow(row)
            return output.getvalue().encode('utf-8-sig')
        app_source = '''
import io
from unittest.mock import patch
import streamlit as st
from pages.quality_targets_page import render_quality_targets_page
with patch('streamlit.file_uploader',return_value=io.BytesIO(st.session_state['test_upload'])):
    render_quality_targets_page()
'''
        with IsolatedDatabase():
            app = AppTest.from_string(app_source, default_timeout=30)
            app.session_state['test_upload'] = content('第一份质量要求')
            app.run(); assert_clean(app)
            before = database_contents()
            def confirm_button():
                return next(row for row in app.button if row.label == '确认导入质量要求')
            self.assertTrue(confirm_button().disabled)
            self.assertTrue(any('已检查预览中的要求' in row.value for row in app.info))
            app.checkbox(key='quality_import_confirm').check().run()
            self.assertFalse(confirm_button().disabled)
            app.session_state['test_upload'] = content('第二份质量要求')
            app.run(); assert_clean(app)
            self.assertFalse(app.checkbox(key='quality_import_confirm').value)
            self.assertTrue(confirm_button().disabled)
            self.assertTrue(any('文件已更换' in row.value for row in app.info))
            app.checkbox(key='quality_import_confirm').check().run()
            self.assertFalse(confirm_button().disabled)
            self.assertEqual(before, database_contents())


if __name__ == '__main__':
    unittest.main(verbosity=2)
