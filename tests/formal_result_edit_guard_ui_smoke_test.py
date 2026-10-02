"""Visible maintenance fields follow the same formal-result protection as services."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import database as db
from streamlit.testing.v1 import AppTest
from tests.lj_monthly_report_smoke_test import TemporaryDatabaseContext, seed_lj_batch_with_formal_monthly_data
from tests.zscore_monthly_report_smoke_test import seed_zscore_batch_with_formal_monthly_data, seed_zscore_batch_with_building_only_data

APP = '''
import streamlit as st
from pages.lj_sections import build_lj_workbench_context, render_lj_abnormal_note_quick_entry
from pages.zscore_sections import build_zscore_workbench_context
from ui.dialogs import render_record_maintenance_dialog, render_zscore_record_maintenance_dialog
mode = st.session_state['mode']
batch = st.session_state['batch']
if mode == 'zscore':
    context = build_zscore_workbench_context(batch)
    render_zscore_record_maintenance_dialog(context['history_runs'], context['batch_context'])
else:
    context = build_lj_workbench_context(batch)
    if mode == 'note':
        rows = context['qc_df'].loc[lambda frame: frame.status.isin(['失控', '警告'])]
        render_lj_abnormal_note_quick_entry(rows.iloc[-1])
    else:
        render_record_maintenance_dialog(context['qc_df'], context['input_value_type'])
'''


class FormalResultEditUITests(unittest.TestCase):
    def open(self, method, batch):
        app = AppTest.from_string(APP, default_timeout=20)
        app.session_state['mode'] = method
        app.session_state['batch'] = batch
        app.run()
        self.assertFalse(list(app.exception), str(list(app.exception)))
        return app

    def select(self, app, method):
        key = 'zscore_run_selector' if method == 'zscore' else 'result_selector'
        widget = app.selectbox(key=key)
        widget.set_value(widget.options[1]).run()
        self.assertFalse(list(app.exception), str(list(app.exception)))

    def test_formal_lj_and_zscore_fields_are_read_only(self):
        with TemporaryDatabaseContext():
            for method, seed in [('lj', seed_lj_batch_with_formal_monthly_data), ('zscore', seed_zscore_batch_with_formal_monthly_data)]:
                with self.subTest(method=method):
                    _, batch = seed()
                    app = self.open(method, batch)
                    self.select(app, method)
                    self.assertTrue(app.number_input)
                    self.assertTrue(all(widget.disabled for widget in app.number_input))
                    self.assertTrue(all(widget.disabled for widget in app.text_input))
                    self.assertFalse(any(b.label == '保存记录修改' and not b.disabled for b in app.button))
                    self.assertTrue(any('不能覆盖原始记录' in item.value for item in app.info))

    def test_building_lj_and_zscore_keep_editable_fields(self):
        with TemporaryDatabaseContext():
            project = db.create_project('建靶页面维护', input_value_type='raw')
            batch = db.create_batch(project_id=project, instrument='仪器', reagent='试剂', qc_material='质控品',
                                    concentration='单水平', lot_no='BUILD-UI', target_n=5)
            db.add_result(batch, '2026-09-28 08:00:00', 100, operator='建立人员')
            _, zbatch = seed_zscore_batch_with_building_only_data()
            for method, selected in [('lj', batch), ('zscore', zbatch)]:
                with self.subTest(method=method):
                    app = self.open(method, selected)
                    self.select(app, method)
                    self.assertTrue(app.number_input)
                    self.assertTrue(all(not widget.disabled for widget in app.number_input))
                    self.assertTrue(any(b.label == '保存记录修改' and not b.disabled for b in app.button))

    def test_formal_lj_abnormal_note_saves_through_its_own_page(self):
        with TemporaryDatabaseContext():
            _, batch = seed_lj_batch_with_formal_monthly_data()
            with db.read_snapshot() as connection:
                before = [dict(row) for row in connection.execute('SELECT * FROM results ORDER BY id')]
            app = self.open('note', batch)
            app.text_area[0].set_value('演示时核对复测过程并补充说明')
            next(b for b in app.button if b.label == '保存当前异常备注').click().run()
            self.assertFalse(list(app.exception), str(list(app.exception)))
            self.assertFalse(list(app.error))
            with db.read_snapshot() as connection:
                after = [dict(row) for row in connection.execute('SELECT * FROM results ORDER BY id')]
            self.assertEqual(1, sum(row['manual_note'] == '演示时核对复测过程并补充说明' for row in after))
            for old, new in zip(before, after):
                old.pop('manual_note'); new.pop('manual_note')
                self.assertEqual(old, new)


if __name__ == '__main__':
    unittest.main(verbosity=2)
