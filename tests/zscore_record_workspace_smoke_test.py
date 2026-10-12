"""Exercise stable run selection and reversible manufacturer-reference editing."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import database as db
from streamlit.testing.v1 import AppTest
from tests.zscore_monthly_report_smoke_test import (
    TemporaryDatabaseContext,
    ZSCORE_PAGE_APPTEST_SCRIPT,
    seed_zscore_batch_with_building_only_data,
    seed_zscore_batch_with_formal_monthly_data,
)
from zscore_logic import get_template_id_for_level_count, get_zscore_level_targets, get_zscore_runs
from services.project_config_service import set_lot_config_disabled
from tests.zscore_v12_fixtures import seed_zscore_configuration


APP = """
import streamlit as st
from pages.zscore_sections import build_zscore_workbench_context, render_zscore_rule_records_overview_section, render_zscore_vendor_reference_section
from ui.record_actions import render_pending_record_dialog
from ui.zscore_reference_dialog import render_pending_zscore_reference_dialog
batch = st.session_state['batch']
context = build_zscore_workbench_context(batch)
if st.session_state.get('show_workspace', True):
    render_zscore_rule_records_overview_section(context)
    render_zscore_vendor_reference_section(context, batch)
    if st.session_state.get('qc_record_dialog'):
        render_pending_record_dialog('zscore', batch)
    else:
        render_pending_zscore_reference_dialog()
else:
    st.write('异常处理页')
"""


def _stored_runs():
    with db.read_snapshot() as connection:
        runs = [dict(row) for row in connection.execute('SELECT * FROM zscore_runs ORDER BY id')]
        levels = [dict(row) for row in connection.execute('SELECT * FROM zscore_level_results ORDER BY id')]
    return runs, levels


class ZscoreRecordWorkspaceTests(unittest.TestCase):
    def open(self, batch):
        app = AppTest.from_string(APP, default_timeout=25)
        app.session_state['batch'] = batch
        app.run()
        self.assert_clean(app)
        return app

    def assert_clean(self, app):
        self.assertFalse(list(app.exception), str(list(app.exception)))

    def select(self, app, batch, run_id):
        app.session_state[f'zscore_records_{batch}_selected'] = run_id
        app.run()
        self.assert_clean(app)

    def test_selected_context_has_only_work_and_report_tabs_and_no_inline_maintenance(self):
        with TemporaryDatabaseContext():
            project, batch = seed_zscore_batch_with_formal_monthly_data()
            app = AppTest.from_string(ZSCORE_PAGE_APPTEST_SCRIPT, default_timeout=25)
            app.session_state['zscore_selected_project_id'] = project
            app.session_state['zscore_selected_batch_id'] = batch
            app.run()
            self.assert_clean(app)
            self.assertEqual(['当前批次', '质控报告'], [tab.label for tab in app.tabs])
            self.assertFalse(any(widget.label in ('项目', '批次', '选择需要处理的参数建立期记录') for widget in app.selectbox))
            self.assertFalse(app.get('file_uploader'))
            self.assertEqual(1, sum(button.label == '导入数据' for button in app.button))
            self.assertEqual(1, sum(button.label == '导出' for button in app.button))
            app.button(key=f'zscore_reference_open_{batch}').click().run()
            self.assert_clean(app)
            self.assertTrue(app.button(key=f'qc_transfer_open_import_zscore_{batch}').disabled)
            self.assertTrue(app.button(key=f'qc_record_zscore_{batch}_view').disabled)

    def test_filtered_selection_opens_the_selected_run_and_formal_values_are_read_only(self):
        with TemporaryDatabaseContext():
            _, batch = seed_zscore_batch_with_formal_monthly_data()
            runs = get_zscore_runs(batch, get_template_id_for_level_count(2))
            selected = next(run for run in runs if run['phase'] == 'formal_qc' and run['run_status'] == 'reject')
            app = self.open(batch)
            app.selectbox(key=f'zscore_records_{batch}_filter_widget').set_value('警告 / 失控').run()
            self.select(app, batch, int(selected['run_id']))
            self.assertEqual(['查看', '异常处理'], [button.label for button in app.button if button.key.startswith('qc_record_')])
            app.button(key=f'qc_record_zscore_{batch}_view').click().run()
            self.assert_clean(app)
            self.assertEqual(selected['run_id'], app.session_state['qc_record_dialog']['record_id'])
            self.assertFalse(app.number_input)
            self.assertFalse(app.text_input)
            self.assertFalse(any(button.label == '保存修改' for button in app.button))
            app.button(key='record_view_close').click().run()
            self.assert_clean(app)
            self.assertEqual('警告 / 失控', app.selectbox(key=f'zscore_records_{batch}_filter_widget').value)
            app.selectbox(key=f'zscore_records_{batch}_filter_widget').set_value('参数建立期').run()
            self.assertIsNone(app.session_state[f'zscore_records_{batch}_selected'])
            self.assertTrue(app.button(key=f'qc_record_zscore_{batch}_view').disabled)

    def test_record_range_survives_leaving_the_workbench(self):
        with TemporaryDatabaseContext():
            _, batch = seed_zscore_batch_with_building_only_data()
            app = self.open(batch)
            app.selectbox(key=f'zscore_records_{batch}_filter_widget').set_value('参数建立期').run()
            app.session_state['show_workspace'] = False
            app.run()
            app.session_state['show_workspace'] = True
            app.run()
            self.assert_clean(app)
            self.assertEqual('参数建立期', app.selectbox(key=f'zscore_records_{batch}_filter_widget').value)

    def test_disable_and_restore_apply_to_whole_run_and_preserve_original_values(self):
        with TemporaryDatabaseContext():
            _, batch = seed_zscore_batch_with_building_only_data()
            run_id = int(get_zscore_runs(batch, get_template_id_for_level_count(2))[1]['run_id'])
            _, before = _stored_runs()
            app = self.open(batch)
            self.select(app, batch, run_id)
            app.button(key=f'qc_record_zscore_{batch}_disable').click().run()
            self.assert_clean(app)
            next(widget for widget in app.text_area if widget.label == '维护原因').set_value('本次检测操作有误，暂不参与建靶')
            app.button(key='record_save').click().run()
            self.assert_clean(app)
            self.assertFalse(list(app.error), str(list(app.error)))
            _, disabled = _stored_runs()
            for old, current in zip(before, disabled):
                self.assertEqual(old['raw_value'], current['raw_value'])
                self.assertEqual(0 if current['run_id'] == run_id else 1, current['is_building_included'])
            app.selectbox(key=f'zscore_records_{batch}_filter_widget').set_value('已禁用').run()
            self.select(app, batch, run_id)
            app.button(key=f'qc_record_zscore_{batch}_restore').click().run()
            next(widget for widget in app.text_area if widget.label == '维护原因').set_value('已核实操作和结果，恢复本次检测')
            app.button(key='record_save').click().run()
            self.assert_clean(app)
            self.assertFalse(list(app.error), str(list(app.error)))
            _, restored = _stored_runs()
            self.assertTrue(all(row['is_building_included'] for row in restored))
            with db.read_snapshot() as connection:
                events = connection.execute('SELECT action,reason,record_id FROM qc_record_maintenance_events ORDER BY id').fetchall()
            self.assertEqual(['disable', 'restore'], [row['action'] for row in events])
            self.assertTrue(all(row['reason'] and row['record_id'] == run_id for row in events))

    def test_canceling_manufacturer_reference_draft_does_not_write(self):
        with TemporaryDatabaseContext():
            _, batch = seed_zscore_batch_with_formal_monthly_data()
            template = get_template_id_for_level_count(2)
            before = get_zscore_level_targets(batch, template, required_n=5)
            app = self.open(batch)
            app.button(key=f'zscore_reference_open_{batch}').click().run()
            self.assert_clean(app)
            app.text_input[0].set_value('110.5').run()
            app.button(key='zscore_reference_cancel').click().run()
            self.assert_clean(app)
            self.assertTrue(app.session_state['zscore_reference_dialog']['discard'])
            self.assertEqual(before, get_zscore_level_targets(batch, template, required_n=5))
            app.button(key='zscore_reference_discard').click().run()
            self.assert_clean(app)
            self.assertNotIn('zscore_reference_dialog', app.session_state.filtered_state)
            self.assertEqual(before, get_zscore_level_targets(batch, template, required_n=5))

    def test_manufacturer_reference_save_keeps_confirmed_controls_and_records(self):
        with TemporaryDatabaseContext():
            _, batch = seed_zscore_batch_with_formal_monthly_data()
            template = get_template_id_for_level_count(2)
            before_profiles = get_zscore_level_targets(batch, template, required_n=5)
            before_records = _stored_runs()
            app = self.open(batch)
            app.button(key=f'zscore_reference_open_{batch}').click().run()
            app.text_input[0].set_value('100.25')
            app.text_input[1].set_value('0.2')
            app.button(key='zscore_reference_save').click().run()
            self.assert_clean(app)
            self.assertFalse(list(app.error), str(list(app.error)))
            current = get_zscore_level_targets(batch, template, required_n=5)
            self.assertEqual(100.25, current['Level 1']['vendor_reference_mean'])
            self.assertEqual(0.2, current['Level 1']['vendor_reference_sd'])
            reference_fields = {'vendor_reference_mean', 'vendor_reference_sd', 'vendor_reference_cv', 'vendor_reference_source_note'}
            for level in current:
                self.assertEqual({key: value for key, value in before_profiles[level].items() if key not in reference_fields},
                                 {key: value for key, value in current[level].items() if key not in reference_fields})
            self.assertEqual(before_records, _stored_runs())

    def test_external_disable_keeps_manufacturer_reference_draft_and_cancel_reachable(self):
        with TemporaryDatabaseContext():
            fixture = seed_zscore_configuration(name="外部停用厂家参考", target_n=5)
            batch = int(fixture['batch_id'])
            template = get_template_id_for_level_count(2)
            app = AppTest.from_string(ZSCORE_PAGE_APPTEST_SCRIPT, default_timeout=25)
            app.session_state['zscore_selected_project_id'] = fixture['project_id']
            app.session_state['zscore_selected_batch_id'] = batch
            app.run()
            app.button(key=f'zscore_reference_open_{batch}').click().run()
            self.assert_clean(app)
            state = app.session_state['zscore_reference_dialog']
            mean_key = f"zscore_reference_{state['token']}_Level 1_mean"
            app.text_input(key=mean_key).set_value('110.5').run()
            before_profiles = get_zscore_level_targets(batch, template, required_n=5)
            before_records = _stored_runs()

            set_lot_config_disabled(fixture['config_id'], is_disabled=True, reason='另一会话停止使用此批次')
            app.run()
            self.assert_clean(app)
            self.assertIsNone(app.session_state['zscore_selected_batch_id'])
            self.assertEqual(batch, app.session_state['zscore_reference_dialog']['batch_id'])
            self.assertEqual('110.5', app.text_input(key=mean_key).value)
            app.button(key='zscore_reference_save').click().run()
            self.assert_clean(app)
            self.assertTrue(any('停用' in item.value or '只读' in item.value for item in app.error))
            self.assertEqual(before_profiles, get_zscore_level_targets(batch, template, required_n=5))
            self.assertEqual(before_records, _stored_runs())

            app.button(key='zscore_reference_cancel').click().run()
            self.assert_clean(app)
            self.assertTrue(app.session_state['zscore_reference_dialog']['discard'])
            app.button(key='zscore_reference_continue').click().run()
            self.assert_clean(app)
            self.assertEqual('110.5', app.text_input(key=mean_key).value)
            app.button(key='zscore_reference_cancel').click().run()
            app.button(key='zscore_reference_discard').click().run()
            self.assert_clean(app)
            self.assertNotIn('zscore_reference_dialog', app.session_state.filtered_state)
            self.assertEqual(before_profiles, get_zscore_level_targets(batch, template, required_n=5))
            self.assertEqual(before_records, _stored_runs())


if __name__ == '__main__':
    unittest.main(verbosity=2)
