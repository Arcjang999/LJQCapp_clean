"""Exercise the complete LJ workbench with a temporary monthly-report fixture."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database import atomic_write, get_results, read_snapshot
from tests.lj_monthly_report_smoke_test import (
    TemporaryDatabaseContext,
    seed_lj_batch_with_building_only_data,
    seed_lj_batch_with_formal_monthly_data,
)
from tests.project_workspace_smoke_test import select_table_row


LJ_PAGE_SCRIPT = f"""
import sys
import pandas as pd
import streamlit as st
sys.path.insert(0, {str(ROOT)!r})
from pages import lj_page

# The established monthly-report fixture predates configured project selection.
# Supply its already-selected IDs; all workbench sections remain the real page.
project_id = st.session_state['selected_project_id']
batch_id = st.session_state['selected_batch_id']
original_prepare = lj_page.prepare_lj_v12_project_batch_context
lj_page.prepare_lj_v12_project_batch_context = lambda: (
    pd.DataFrame([{{'id': project_id}}]), project_id,
    pd.DataFrame([{{'id': batch_id}}]), batch_id)
try:
    lj_page.render_lj_page()
finally:
    lj_page.prepare_lj_v12_project_batch_context = original_prepare
"""


def full_page(project_id, batch_id, **initial_state):
    app = AppTest.from_string(LJ_PAGE_SCRIPT, default_timeout=20)
    app.session_state['selected_project_id'] = project_id
    app.session_state['selected_batch_id'] = batch_id
    for key, value in initial_state.items():
        app.session_state[key] = value
    app.run()
    assert not list(app.exception), [str(exc.value) for exc in app.exception]
    return app


def records_table_index(app, batch_id):
    return next(index for index, table in enumerate(app.dataframe)
                if f'lj_records_{batch_id}_table_' in table.proto.id)


def choose_record(app, batch_id, row=0):
    select_table_row(app, row, index=records_table_index(app, batch_id))
    return app.session_state[f'lj_records_{batch_id}_selected']


def original_inputs(batch_id):
    frame = get_results(batch_id, include_manual_note=True)
    columns = ['id', 'test_time', 'operator', 'value', 'manual_note',
               'reagent_lot_changed', 'is_building_included', 'manual_status']
    return frame[[column for column in columns if column in frame]].to_dict('records')


def maintenance_count():
    with read_snapshot() as connection:
        return connection.execute('SELECT COUNT(*) FROM qc_record_maintenance_events').fetchone()[0]


class LjRecordWorkspaceTests(unittest.TestCase):
    def assert_clean(self, app):
        self.assertFalse(list(app.exception), [str(exc.value) for exc in app.exception])

    def test_complete_page_has_two_workspaces_and_compact_transfer_entries(self):
        with TemporaryDatabaseContext():
            project, batch = seed_lj_batch_with_formal_monthly_data()
            app = full_page(project, batch)
            self.assertEqual(['当前批次', '质控报告'], [tab.label for tab in app.tabs])
            labels = [button.label for button in app.button]
            self.assertEqual(1, labels.count('导入数据'))
            self.assertEqual(1, labels.count('导出'))
            self.assertEqual([], list(app.get('file_uploader')))
            self.assertEqual([], list(app.get('download_button')))
            self.assertFalse(any('导入' in box.label or '维护记录' in box.label for box in app.selectbox))
            self.assertTrue(any(box.label == '记录范围' for box in app.selectbox))
            self.assertFalse(any(button.label in ('编辑', '保留', '禁用', '恢复', '删除记录') for button in app.button))

    def test_formal_detail_contains_no_disabled_edit_fields_and_preserves_selection(self):
        with TemporaryDatabaseContext():
            project, batch = seed_lj_batch_with_formal_monthly_data()
            app = full_page(project, batch)
            selected = choose_record(app, batch)
            self.assertEqual(int(get_results(batch).iloc[-1]['id']), selected)
            self.assertFalse(any(button.label == '编辑' for button in app.button))
            app.button(key=f'qc_record_lj_{batch}_view').click().run()
            self.assert_clean(app)
            self.assertEqual('view', app.session_state['qc_record_dialog']['action'])
            token_prefix = 'record_' + app.session_state['qc_record_dialog']['token'] + '_'
            for kind in ('number_input', 'text_input', 'text_area', 'datetime_input', 'checkbox'):
                self.assertFalse(any(str(widget.key or '').startswith(token_prefix) for widget in app.get(kind)))
            self.assertTrue(any('仅供查询' in item.value for item in app.info))
            self.assertTrue(app.button(key=f'qc_transfer_open_import_lj_{batch}').disabled)
            self.assertTrue(app.button(key=f'qc_transfer_open_export_lj_{batch}').disabled)
            app.button(key='record_view_close').click().run()
            self.assert_clean(app)
            self.assertNotIn('qc_record_dialog', app.session_state.filtered_state)
            self.assertEqual(selected, app.session_state[f'lj_records_{batch}_selected'])
            self.assertEqual('全部', app.selectbox(key=f'lj_records_{batch}_filter_widget').value)

    def test_edit_cancel_continue_and_discard_preserve_draft_without_writes(self):
        with TemporaryDatabaseContext():
            project, batch = seed_lj_batch_with_building_only_data()
            app = full_page(project, batch)
            before = original_inputs(batch)
            selected = choose_record(app, batch)
            app.button(key=f'qc_record_lj_{batch}_edit').click().run()
            self.assert_clean(app)
            token = app.session_state['qc_record_dialog']['token']
            prefix = 'record_' + token + '_'
            app.text_input(key=prefix + 'operator').set_value('尚未保存的检验员').run()
            app.number_input(key=prefix + 'value').set_value(25.8).run()
            app.text_area(key=prefix + 'reason').set_value('核对原始记录').run()
            self.assert_clean(app)
            app.button(key='record_cancel').click().run()
            self.assert_clean(app)
            self.assertTrue(app.session_state['qc_record_dialog']['discard'])
            self.assertEqual(before, original_inputs(batch))
            self.assertEqual(0, maintenance_count())
            app.button(key='record_continue').click().run()
            self.assert_clean(app)
            self.assertEqual('尚未保存的检验员', app.text_input(key=prefix + 'operator').value)
            self.assertEqual(25.8, app.number_input(key=prefix + 'value').value)
            self.assertEqual('核对原始记录', app.text_area(key=prefix + 'reason').value)
            app.button(key='record_cancel').click().run()
            app.button(key='record_discard').click().run()
            self.assert_clean(app)
            self.assertNotIn('qc_record_dialog', app.session_state.filtered_state)
            self.assertFalse(any(key.startswith(prefix) for key in app.session_state.filtered_state))
            self.assertEqual(selected, app.session_state[f'lj_records_{batch}_selected'])
            self.assertEqual(before, original_inputs(batch))
            self.assertEqual(0, maintenance_count())

    def test_filter_clears_hidden_id_and_preserves_visible_identity_across_sorting(self):
        with TemporaryDatabaseContext():
            project, batch = seed_lj_batch_with_formal_monthly_data()
            app = full_page(project, batch)
            latest = choose_record(app, batch)
            app.selectbox(key=f'lj_records_{batch}_filter_widget').set_value('参数建立期').run()
            self.assert_clean(app)
            self.assertIsNone(app.session_state[f'lj_records_{batch}_selected'])
            self.assertTrue(app.button(key=f'qc_record_lj_{batch}_view').disabled)
            table = app.dataframe[records_table_index(app, batch)]
            self.assertEqual([], json.loads(table.proto.selection_default)['selection']['rows'])
            building_id = choose_record(app, batch, row=1)
            self.assertNotEqual(latest, building_id)
            app.selectbox(key=f'lj_records_{batch}_filter_widget').set_value('全部').run()
            self.assert_clean(app)
            self.assertEqual(building_id, app.session_state[f'lj_records_{batch}_selected'])
            table = app.dataframe[records_table_index(app, batch)]
            selected_rows = json.loads(table.proto.selection_default)['selection']['rows']
            self.assertEqual([4], selected_rows)
            self.assertEqual('builder-3', table.value.iloc[selected_rows[0]]['检测人'])
            app.button(key=f'qc_record_lj_{batch}_view').click().run()
            self.assert_clean(app)
            self.assertEqual(building_id, app.session_state['qc_record_dialog']['record_id'])
            self.assertEqual('builder-3', app.session_state['qc_record_dialog']['context']['record']['operator'])

    def test_import_and_record_dialogs_are_mutually_exclusive(self):
        with TemporaryDatabaseContext():
            project, batch = seed_lj_batch_with_building_only_data()
            app = full_page(project, batch)
            choose_record(app, batch)
            app.button(key=f'qc_transfer_open_import_lj_{batch}').click().run()
            self.assert_clean(app)
            self.assertIn('qc_transfer_dialog', app.session_state.filtered_state)
            self.assertNotIn('qc_record_dialog', app.session_state.filtered_state)
            self.assertTrue(app.button(key=f'qc_record_lj_{batch}_view').disabled)
            self.assertTrue(app.button(key=f'qc_record_lj_{batch}_edit').disabled)
            nonce = app.session_state['qc_transfer_dialog']['nonce']
            app.button(key=f'qc_transfer_cancel_{nonce}').click().run()
            self.assert_clean(app)
            self.assertNotIn('qc_transfer_dialog', app.session_state.filtered_state)
            app = full_page(project, batch, zscore_reference_dialog={'batch_id': 999})
            self.assert_clean(app)
            self.assertTrue(app.button(key=f'qc_transfer_open_import_lj_{batch}').disabled)
            self.assertTrue(app.button(key=f'qc_transfer_open_export_lj_{batch}').disabled)
            self.assertTrue(app.button(key=f'qc_record_lj_{batch}_view').disabled)

    def test_failed_audit_save_keeps_reason_and_original_inputs(self):
        with TemporaryDatabaseContext():
            project, batch = seed_lj_batch_with_building_only_data()
            before = original_inputs(batch)
            app = full_page(project, batch)
            choose_record(app, batch)
            app.button(key=f'qc_record_lj_{batch}_disable').click().run()
            next(widget for widget in app.text_area if widget.label == '维护原因').set_value('复核原始记录')
            with atomic_write() as connection:
                connection.execute("CREATE TRIGGER reject_maintenance BEFORE INSERT ON qc_record_maintenance_events BEGIN SELECT RAISE(ABORT,'storage unavailable'); END")
            app.button(key='record_save').click().run()
            self.assert_clean(app)
            self.assertTrue(any('尚未保存' in str(item.value) for item in app.error))
            self.assertEqual('复核原始记录', app.session_state['qc_record_dialog']['draft']['reason'])
            self.assertEqual(before, original_inputs(batch))
            self.assertEqual(0, maintenance_count())


if __name__ == '__main__':
    unittest.main()
