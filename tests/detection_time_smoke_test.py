"""Completed detections cannot be saved in the future through any writable route."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import pandas as pd
import streamlit as st
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import database as db
from services import time_service as clock
from services import daily_entry_service as daily
from services.daily_draft_service import new_draft
from services.daily_result_io_service import export_daily_workbook, preview_daily_workbook, apply_daily_workbook
from services.export_utils import dataframe_to_csv_bytes, dataframe_to_xlsx_bytes
from services.lot_lifecycle_service import import_reviewed_results, timestamp
from services.record_maintenance_service import get_record_context, save_record_action
from tests.instant_v12_integration_smoke_test import IsolatedDatabase
from tests.instant_v12_fixtures import seed_instant_configuration
from tests.data_transfer_smoke_test import batch_for, file_for
from tests.record_maintenance_transaction_test import seed_batch, edit_changes
from tests.xlsx_datetime_import_smoke_test import draft as workbook_draft
from ui.data_transfer import _fresh_import_context, _import_source_signature, review_data_import, commit_data_import
from zscore_logic import create_zscore_run

NOW = datetime(2026, 10, 27, 9, 0, 0)
FUTURE = '2026-10-27 09:00:01'


def dump():
    with db.read_snapshot() as connection:
        return '\n'.join(connection.iterdump())


class DetectionTimeTests(unittest.TestCase):
    def setUp(self):
        datetime_input = st.datetime_input

        def laboratory_datetime_input(*args, **kwargs):
            # The real widget's default clock must follow the frozen test clock.
            if len(args) < 2 and 'value' not in kwargs:
                kwargs['value'] = clock.local_now()
            return datetime_input(*args, **kwargs)

        replacement = patch.object(st, 'datetime_input', new=laboratory_datetime_input)
        replacement.start()
        self.addCleanup(replacement.stop)

    def test_current_past_future_and_timezone_boundaries(self):
        self.assertEqual('2026-10-27 09:00:00', clock.validate_detection_time(NOW, now=NOW))
        self.assertEqual('2026-09-01 08:00:00', clock.validate_detection_time('2026-09-01 08:00:00', now=NOW))
        self.assertEqual('2026-10-27 09:00:00', clock.validate_detection_time('2026-10-27T01:00:00Z', now=NOW))
        for value in (FUTURE, '2026-10-28 00:00:00', '2099-01-01', NOW + timedelta(microseconds=1)):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, '不能晚于当前时间'):
                clock.validate_detection_time(value, now=NOW)
        midnight = datetime(2026, 11, 1)
        self.assertEqual('2026-10-31 23:59:59', clock.validate_detection_time('2026-10-31T15:59:59Z', now=midnight))
        with self.assertRaisesRegex(ValueError, '不能晚于当前时间'):
            clock.validate_detection_time('2026-10-31T16:00:01Z', now=midnight)
        for value in (None, pd.NaT, '', 'wrong-date'):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, '有效的检测时间'):
                clock.validate_detection_time(value, now=NOW)

    def test_valid_microseconds_are_retained_when_saving(self):
        value = '2026-09-30 23:59:59.999999'
        self.assertEqual(value, clock.validate_detection_time(value, now=NOW))
        with IsolatedDatabase(), patch.object(clock, 'local_now', return_value=NOW):
            batch = batch_for('lj')
            identifier = db.add_result(batch, value, 100, operator='检验员')
            with db.read_snapshot() as connection:
                self.assertEqual(value, connection.execute('SELECT test_time FROM results WHERE id=?', (identifier,)).fetchone()[0])

    def test_shanghai_clock_is_explicit_and_future_plans_still_parse(self):
        with patch.object(clock, 'datetime', wraps=datetime) as source:
            source.now.return_value = datetime(2026, 11, 1, 0, 1, tzinfo=clock.LAB_TIMEZONE)
            self.assertEqual(datetime(2026, 11, 1, 0, 1), clock.local_now())
            self.assertEqual(datetime(2026, 11, 1).date(), clock.local_today())
            source.now.assert_called_with(clock.LAB_TIMEZONE)
        self.assertEqual('2099-01-01 08:00:00', timestamp('2099-01-01T00:00:00Z'))

    def test_three_methods_direct_saves_reject_future_without_any_writes(self):
        from services.instant_service import save_instant_result
        for method in ('lj', 'zscore', 'instant'):
            with self.subTest(method=method), IsolatedDatabase(), patch.object(clock, 'local_now', return_value=NOW):
                batch = (seed_instant_configuration()['batch_id'] if method == 'instant' else batch_for(method))
                if method == 'lj':
                    save = lambda when: db.add_result(batch, when, 100, operator='检验员')
                elif method == 'instant':
                    save = lambda when: save_instant_result(batch_id=batch, test_time=when, value=100, log_value=None, operator='检验员')
                else:
                    save = lambda when: create_zscore_run(batch_id=batch, test_time=when, operator='检验员', template_id='2_level_classic',
                        level_results=[{'level_id':f'Level {level}', 'raw_value':100*level} for level in (1, 2)])
                before = dump()
                with self.assertRaisesRegex(ValueError, '不能晚于当前时间'):
                    save(FUTURE)
                self.assertEqual(before, dump())
                save('2026-10-27T00:59:59Z')
                table = {'lj':'results', 'zscore':'zscore_runs', 'instant':'instant_results'}[method]
                with db.read_snapshot() as connection:
                    row = connection.execute(f'SELECT test_time FROM {table} WHERE batch_id=?', (batch,)).fetchone()
                self.assertEqual('2026-10-27 08:59:59', row[0])

    def test_csv_and_xlsx_preview_show_future_row_and_block_entire_file(self):
        for method in ('lj', 'zscore'):
            for encode in (dataframe_to_csv_bytes, dataframe_to_xlsx_bytes):
                with self.subTest(method=method, format=encode.__name__), IsolatedDatabase(), patch.object(clock, 'local_now', return_value=NOW):
                    batch = batch_for(method)
                    context = _fresh_import_context(method, batch)
                    frame = file_for(method, count=2)
                    frame.loc[1, '检测时间'] = FUTURE
                    before = dump()
                    review = review_data_import(method, context, batch, 'building', encode(frame))
                    self.assertTrue(review['summary']['has_blocking_errors'])
                    errors = [issue for issue in review['issues'] if '不能晚于当前时间' in issue['message']]
                    self.assertEqual(1, len(errors))
                    self.assertEqual('3', str(errors[0]['row_label']))
                    with self.assertRaises(ValueError):
                        commit_data_import(method, batch, 'building', review, _import_source_signature(method, context))
                    self.assertEqual(before, dump())

    def test_direct_batch_import_rolls_back_earlier_valid_row(self):
        with IsolatedDatabase(), patch.object(clock, 'local_now', return_value=NOW):
            batch = batch_for('lj')
            rows = [dict(test_time=when, value=100, operator='检验员', lot_selection={'allow_unknown':True})
                    for when in ('2026-10-27 08:00:00', FUTURE)]
            before = dump()
            with self.assertRaisesRegex(ValueError, '不能晚于当前时间'):
                import_reviewed_results('lj', batch, rows)
            self.assertEqual(before, dump())

    def test_allowed_building_edits_reject_future_and_keep_original_audit(self):
        for method in ('lj', 'zscore'):
            with self.subTest(method=method), IsolatedDatabase(), patch.object(clock, 'local_now', return_value=NOW):
                batch, identifiers = seed_batch(method)
                context = get_record_context(method, identifiers[1], batch)
                changes = edit_changes(method, context['record'])
                changes['test_time'] = FUTURE
                before = dump()
                with self.assertRaisesRegex(ValueError, '不能晚于当前时间'):
                    save_record_action(method, batch, identifiers[1], 'edit', reason='核对检测时间',
                        expected_fingerprint=context['fingerprint'], request_key='future-edit', changes=changes)
                self.assertEqual(before, dump())

    def test_direct_zscore_time_update_is_guarded_and_normalizes_timezone(self):
        with IsolatedDatabase(), patch.object(clock, 'local_now', return_value=NOW):
            batch, identifiers = seed_batch('zscore')
            before = dump()
            with self.assertRaisesRegex(ValueError, '不能晚于当前时间'):
                db.update_zscore_run(identifiers[0], test_time=FUTURE)
            self.assertEqual(before, dump())
            db.update_zscore_run(identifiers[0], test_time='2026-09-28T23:00:00Z')
            with db.read_snapshot() as connection:
                value = connection.execute('SELECT test_time FROM zscore_runs WHERE id=?', (identifiers[0],)).fetchone()[0]
            self.assertEqual('2026-09-29 07:00:00', value)

    def test_transfer_rejects_existing_future_effective_result_before_creating_target(self):
        from services.instant_service import confirm_instant_transfer_to_lj
        from tests.instant_v12_integration_smoke_test import entry
        with IsolatedDatabase(), patch.object(clock, 'local_now', return_value=NOW):
            fixture = seed_instant_configuration()
            identifiers = [entry(fixture, index) for index in range(20)]
            with db.get_connection() as connection:
                # Represent an already stored trial record; all contexts already exist.
                connection.execute('UPDATE instant_results SET test_time=? WHERE id=?', (FUTURE, identifiers[-1]))
            before = dump()
            with self.assertRaisesRegex(ValueError, '不能晚于当前时间'):
                confirm_instant_transfer_to_lj(fixture['batch_id'])
            self.assertEqual(before, dump())

    def test_daily_group_precheck_and_submit_reject_future_without_mutating_draft(self):
        from tests.daily_entry_smoke_test import seed_twenty
        from services.daily_context_service import get_daily_context
        from services.daily_draft_service import build_request
        with IsolatedDatabase(), patch.object(clock, 'local_now', return_value=NOW):
            fixture = seed_twenty()
            context = get_daily_context(lab_instrument_id=fixture['data']['lab_instrument_id'],
                qc_material_id=fixture['data']['qc_material_id'], qc_material_lot_id=fixture['main_lot'],
                lot_config_id=fixture['config_id'], test_time='2026-09-28 08:00:00')
            draft = new_draft(context, operator='检验员', test_time=FUTURE)
            draft['values'] = {key:'100' for key in draft['values']}
            original = deepcopy(draft)
            request = build_request(draft)
            before = dump()
            review = daily.validate_submission(request)
            self.assertFalse(review['valid'])
            self.assertEqual('test_time', review['errors'][0]['field'])
            self.assertIn('不能晚于当前时间', review['errors'][0]['message'])
            with self.assertRaisesRegex(daily.DailyEntryError, '不能晚于当前时间'):
                daily.submit_daily(request)
            self.assertEqual(before, dump())
            self.assertEqual(original, draft)

    def test_daily_workbook_future_preview_and_tampered_apply_preserve_draft(self):
        with patch.object(clock, 'local_now', return_value=NOW):
            draft = workbook_draft()
            draft['test_time'] = FUTURE
            original = deepcopy(draft)
            preview = preview_daily_workbook(export_daily_workbook(draft), draft)
            self.assertFalse(preview['valid'])
            self.assertTrue(any('第2行' in error and '不能晚于当前时间' in error for error in preview['errors']))
            with self.assertRaises(ValueError):
                apply_daily_workbook(draft, preview)
            self.assertEqual(original, draft)
            draft['test_time'] = '2026-09-28 08:00:00'
            preview = preview_daily_workbook(export_daily_workbook(draft), draft)
            self.assertTrue(preview['valid'], preview['errors'])
            preview['test_time'] = FUTURE
            original = deepcopy(draft)
            with self.assertRaisesRegex(ValueError, '不能晚于当前时间'):
                apply_daily_workbook(draft, preview)
            self.assertEqual(original, draft)

    def test_three_real_entry_sections_keep_input_after_future_time_rejection(self):
        script = '''
import streamlit as st
method, batch = st.session_state['method'], st.session_state['batch']
if method == 'lj':
    from pages.lj_sections import build_lj_workbench_context, render_lj_entry_and_stats_section
    render_lj_entry_and_stats_section(build_lj_workbench_context(batch), batch)
elif method == 'zscore':
    from pages.zscore_sections import build_zscore_workbench_context, render_zscore_entry_section
    render_zscore_entry_section(build_zscore_workbench_context(batch), batch)
else:
    from services.instant_service import build_instant_workbench_context
    from pages.instant_page import _render_instant_entry_and_summary_section
    _render_instant_entry_and_summary_section(build_instant_workbench_context(batch), batch)
'''
        for method in ('lj', 'zscore', 'instant'):
            with self.subTest(method=method), IsolatedDatabase(), patch.object(clock, 'local_now', return_value=NOW):
                batch = (seed_instant_configuration()['batch_id'] if method == 'instant' else batch_for(method))
                if method == 'lj':
                    db.add_result(batch, '2026-09-01 08:00:00', 100, operator='检验员')
                elif method == 'instant':
                    from services.instant_service import save_instant_result
                    save_instant_result(batch_id=batch, test_time='2026-09-01 08:00:00', value=100, log_value=None, operator='检验员')
                else:
                    create_zscore_run(batch_id=batch, test_time='2026-09-01 08:00:00', operator='检验员', template_id='2_level_classic',
                        level_results=[{'level_id':f'Level {level}', 'raw_value':100*level} for level in (1, 2)])
                app = AppTest.from_string(script, default_timeout=25)
                app.session_state['method'], app.session_state['batch'] = method, batch
                prefix = {'lj':'', 'zscore':'zscore_', 'instant':'instant_'}[method]
                app.session_state[prefix+'entry_batch_id'] = batch
                app.session_state[prefix+'entry_test_time'] = NOW
                app.run()
                self.assertFalse(app.exception, [error.message for error in app.exception])
                time_key, operator_key = prefix+'entry_test_time', prefix+'entry_operator'
                app.selectbox(key=operator_key).set_value('检验员')
                value_keys = ['zscore_level1_value', 'zscore_level2_value'] if method == 'zscore' else [prefix+'entry_value']
                for index, key in enumerate(value_keys, 1):
                    app.text_input(key=key).set_value(str(index*100))
                app.get('date_time_input')[0].set_value(NOW + timedelta(minutes=1))
                save = next(button for button in app.button if button.label in ('保存检测结果', '保存本次检测'))
                save.click().run()
                self.assertFalse(app.exception, [error.message for error in app.exception])
                self.assertTrue(any('不能晚于当前时间' in error.value for error in app.error))
                self.assertEqual(NOW + timedelta(minutes=1), app.session_state[time_key])
                self.assertEqual('检验员', app.session_state[operator_key])
                for index, key in enumerate(value_keys, 1):
                    self.assertEqual(str(index*100), app.session_state[key])
                table = {'lj':'results', 'zscore':'zscore_runs', 'instant':'instant_results'}[method]
                with db.read_snapshot() as connection:
                    self.assertEqual(1, connection.execute(f'SELECT COUNT(*) FROM {table} WHERE batch_id=?', (batch,)).fetchone()[0])
                app.get('date_time_input')[0].set_value(NOW - timedelta(minutes=1))
                save = next(button for button in app.button if button.label in ('保存检测结果', '保存本次检测'))
                save.click().run()
                self.assertFalse(app.exception, [error.message for error in app.exception])
                with db.read_snapshot() as connection:
                    self.assertEqual(2, connection.execute(f'SELECT COUNT(*) FROM {table} WHERE batch_id=?', (batch,)).fetchone()[0])

    def test_existing_future_entry_drafts_open_and_can_be_corrected_in_place(self):
        script = '''
import streamlit as st
method, batch = st.session_state['method'], st.session_state['batch']
if method == 'lj':
    from pages.lj_sections import build_lj_workbench_context, render_lj_entry_and_stats_section
    render_lj_entry_and_stats_section(build_lj_workbench_context(batch), batch)
elif method == 'zscore':
    from pages.zscore_sections import build_zscore_workbench_context, render_zscore_entry_section
    render_zscore_entry_section(build_zscore_workbench_context(batch), batch)
else:
    from services.instant_service import build_instant_workbench_context
    from pages.instant_page import _render_instant_entry_and_summary_section
    _render_instant_entry_and_summary_section(build_instant_workbench_context(batch), batch)
'''
        prior = datetime(2026, 10, 29, 9, 15)
        for method in ('lj', 'zscore', 'instant'):
            with self.subTest(method=method), IsolatedDatabase(), patch.object(clock, 'local_now', return_value=NOW):
                batch, _ = seed_batch(method, count=1)
                app = AppTest.from_string(script, default_timeout=25)
                app.session_state['method'], app.session_state['batch'] = method, batch
                prefix = {'lj':'', 'zscore':'zscore_', 'instant':'instant_'}[method]
                app.session_state[prefix+'entry_batch_id'] = batch
                app.session_state[prefix+'entry_test_time'] = prior
                app.session_state[prefix+'entry_operator'] = '检测人1'
                value_keys = ['zscore_level1_value', 'zscore_level2_value', 'zscore_level3_value'] if method == 'zscore' else [prefix+'entry_value']
                for index, key in enumerate(value_keys, 1):
                    app.session_state[key] = str(index*100)
                app.run()
                self.assertFalse(app.exception, [error.message for error in app.exception])
                self.assertEqual(prior, app.get('date_time_input')[0].value)
                next(button for button in app.button if button.label in ('保存检测结果', '保存本次检测')).click().run()
                self.assertFalse(app.exception, [error.message for error in app.exception])
                self.assertTrue(any('不能晚于当前时间' in error.value for error in app.error))
                self.assertEqual(prior, app.session_state[prefix+'entry_test_time'])
                app.get('date_time_input')[0].set_value(NOW - timedelta(minutes=1))
                next(button for button in app.button if button.label in ('保存检测结果', '保存本次检测')).click().run()
                self.assertFalse(app.exception, [error.message for error in app.exception])
                table = {'lj':'results', 'zscore':'zscore_runs', 'instant':'instant_results'}[method]
                with db.read_snapshot() as connection:
                    rows = connection.execute(f'SELECT test_time FROM {table} WHERE batch_id=? ORDER BY id', (batch,)).fetchall()
                self.assertEqual(2, len(rows))
                self.assertEqual('2026-10-27 08:59:00', rows[-1][0])

    def test_existing_future_record_editor_opens_and_retains_failed_changes(self):
        script = '''
import streamlit as st
from ui.record_actions import open_record_dialog, render_pending_record_dialog
method, batch, record = st.session_state['method'], st.session_state['batch'], st.session_state['record']
if not st.session_state.get('qc_record_dialog'):
    open_record_dialog(method, batch, record, 'edit')
render_pending_record_dialog(method, batch)
'''
        prior = datetime(2026, 10, 29, 9, 15)
        for method in ('lj', 'zscore'):
            with self.subTest(method=method), IsolatedDatabase(), patch.object(clock, 'local_now', return_value=NOW):
                batch, identifiers = seed_batch(method)
                table = 'results' if method == 'lj' else 'zscore_runs'
                with db.get_connection() as connection:
                    connection.execute(f'UPDATE {table} SET test_time=? WHERE id=?', (str(prior), identifiers[-1]))
                app = AppTest.from_string(script, default_timeout=25)
                app.session_state['method'], app.session_state['batch'], app.session_state['record'] = method, batch, identifiers[-1]
                app.run()
                self.assertFalse(app.exception, [error.message for error in app.exception])
                self.assertEqual(prior, app.get('date_time_input')[0].value)
                reason = next(area for area in app.text_area if area.label == '维护原因')
                reason.set_value('核对检测时间').run()
                before = dump()
                app.button(key='record_save').click().run()
                self.assertFalse(app.exception, [error.message for error in app.exception])
                self.assertTrue(any('不能晚于当前时间' in error.value for error in app.error))
                self.assertEqual(prior, app.session_state['qc_record_dialog']['draft']['test_time'])
                self.assertEqual(before, dump())


if __name__ == '__main__':
    unittest.main(verbosity=2)
