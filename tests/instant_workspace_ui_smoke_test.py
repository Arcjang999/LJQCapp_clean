"""Instant workbench navigation and selection using a temporary configured database."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest
from tests.instant_smoke_test import TemporaryDatabaseContext, INSTANT_PAGE_APPTEST_SCRIPT, bootstrap_batch, seed_instant_results
from database import get_instant_results, read_snapshot
from services.instant_service import save_instant_result
from services.project_config_service import set_lot_config_disabled
from tests.instant_v12_fixtures import seed_instant_configuration


class InstantWorkspaceUI(unittest.TestCase):
    def test_missing_context_is_safe_and_selection_opens_one_workbench(self):
        with TemporaryDatabaseContext():
            project_id, batch_id = bootstrap_batch(project_name='安全选择', configured=True)
            app = AppTest.from_string(INSTANT_PAGE_APPTEST_SCRIPT, default_timeout=15)
            app.session_state['instant_selected_project_id'] = 999999
            app.session_state['instant_selected_batch_id'] = 999999
            app.run()
            self.assertFalse(list(app.exception))
            self.assertIsNone(app.session_state['instant_selected_project_id'])
            self.assertIsNone(app.session_state['instant_selected_batch_id'])
            project = app.selectbox(key='v12_instant_project_selector')
            project.set_value(project.options[1]).run()
            batch = app.selectbox(key='v12_instant_batch_selector')
            batch.set_value(batch.options[1]).run()
            self.assertFalse(list(app.exception))
            self.assertEqual(app.session_state['instant_selected_project_id'], project_id)
            self.assertEqual(app.session_state['instant_selected_batch_id'], batch_id)
            self.assertFalse(list(app.get('tab')))
            self.assertNotIn('v12_instant_project_selector', {item.key for item in app.selectbox})
            self.assertTrue(any(button.key == 'instant_entry_save_button' for button in app.button))

    def test_record_selection_keeps_identity_on_save_and_has_no_maintenance_dropdown(self):
        with TemporaryDatabaseContext():
            project_id, batch_id = bootstrap_batch(project_name='记录选择', configured=True)
            record_ids = [save_instant_result(batch_id=batch_id, test_time=f'2026-10-11 08:0{i}:00',
                operator='检验员甲', value=value, log_value=None) for i, value in enumerate([100.0, 101.0])]
            app = AppTest.from_string(INSTANT_PAGE_APPTEST_SCRIPT, default_timeout=15)
            app.session_state['instant_selected_project_id'] = project_id
            app.session_state['instant_selected_batch_id'] = batch_id
            selection_key = f'instant_{batch_id}_selected'
            app.session_state[selection_key] = record_ids[1]
            app.run()
            self.assertFalse(list(app.exception))
            self.assertEqual(app.session_state[selection_key], record_ids[1])
            app.text_input(key='instant_entry_value').set_value('102.5').run()
            app.selectbox(key='instant_entry_operator').set_value('检验员甲').run()
            app.button(key='instant_entry_save_button').click().run()
            self.assertFalse(list(app.exception))
            self.assertEqual(app.session_state[selection_key], record_ids[1])
            records = [table.value for table in app.dataframe if '记录序号' in table.value.columns]
            self.assertEqual(len(records), 1)
            self.assertEqual(len(records[0]), 3)
            self.assertFalse(any(str(item.key).startswith('instant_maintenance_selector_') for item in app.selectbox))

    def test_record_view_and_transfer_confirmation_are_mutually_exclusive(self):
        with TemporaryDatabaseContext():
            project_id, batch_id = bootstrap_batch(project_name='转入与查询', configured=True)
            seed_instant_results(batch_id, [100.0 + i * 0.2 for i in range(20)])
            before = get_instant_results(batch_id).copy()
            app = AppTest.from_string(INSTANT_PAGE_APPTEST_SCRIPT, default_timeout=15)
            app.session_state['instant_selected_project_id'] = project_id
            app.session_state['instant_selected_batch_id'] = batch_id
            app.session_state[f'instant_{batch_id}_selected'] = int(before.iloc[1]['id'])
            app.run()
            self.assertFalse(list(app.exception))
            self.assertFalse(app.button(key='open_instant_transfer_dialog').disabled)
            app.button(key=f'qc_record_instant_{batch_id}_view').click().run()
            self.assertFalse(list(app.exception))
            self.assertEqual(app.session_state['qc_record_dialog']['record_id'], int(before.iloc[1]['id']))
            self.assertTrue(app.button(key='open_instant_transfer_dialog').disabled)
            self.assertFalse(app.session_state.filtered_state.get('show_instant_transfer_dialog', False))
            app.button(key='record_view_close').click().run()
            app.button(key='open_instant_transfer_dialog').click().run()
            self.assertFalse(list(app.exception))
            self.assertNotIn('qc_record_dialog', app.session_state.filtered_state)
            self.assertTrue(app.button(key=f'qc_record_instant_{batch_id}_view').disabled)
            app.button(key=f'cancel_instant_transfer_dialog_{batch_id}').click().run()
            self.assertFalse(list(app.exception))
            self.assertFalse(app.session_state.filtered_state.get('show_instant_transfer_dialog', False))
            self.assertEqual(before[['id', 'value']].to_dict('records'),
                             get_instant_results(batch_id)[['id', 'value']].to_dict('records'))

    def test_external_batch_disable_preserves_cancel_path_and_releases_other_batch(self):
        with TemporaryDatabaseContext():
            fixtures = [seed_instant_configuration(name=f'外部停用{letter}', instrument=f'回归仪器{letter}',
                lot_no=f'EXTERNAL-{letter}') for letter in ('A', 'B')]
            old, other = fixtures
            ids = [save_instant_result(batch_id=item['batch_id'], test_time='2026-10-11 08:00:00',
                operator='检验员甲', value=100.0 + index, log_value=None)
                for index, item in enumerate(fixtures)]
            old_before = get_instant_results(old['batch_id']).to_dict('records')
            app = AppTest.from_string(INSTANT_PAGE_APPTEST_SCRIPT, default_timeout=15)
            app.session_state['instant_selected_project_id'] = old['project_id']
            app.session_state['instant_selected_batch_id'] = old['batch_id']
            app.session_state[f"instant_{old['batch_id']}_selected"] = ids[0]
            app.run()
            app.button(key=f"qc_record_instant_{old['batch_id']}_disable").click().run()
            state = app.session_state['qc_record_dialog']
            reason_key = 'record_' + state['token'] + '_reason'
            reason = '已核对原始记录，尚未确认处理'
            app.text_area(key=reason_key).set_value(reason).run()
            self.assertEqual(reason, app.session_state['qc_record_dialog']['draft']['reason'])

            # The shared configuration service represents a second local session's action.
            set_lot_config_disabled(old['config_id'], is_disabled=True, reason='停止使用此批次')
            app.run()
            self.assertFalse(list(app.exception), list(app.exception))
            self.assertIsNone(app.session_state['instant_selected_batch_id'])
            self.assertEqual(old['batch_id'], app.session_state['qc_record_dialog']['batch_id'])
            self.assertEqual(ids[0], app.session_state['qc_record_dialog']['record_id'])
            self.assertEqual(reason, app.text_area(key=reason_key).value)

            app.button(key='record_save').click().run()
            self.assertFalse(list(app.exception), list(app.exception))
            self.assertTrue(any('停用' in item.value or '只读' in item.value for item in app.error))
            self.assertEqual(reason, app.session_state['qc_record_dialog']['draft']['reason'])
            self.assertEqual(old_before, get_instant_results(old['batch_id']).to_dict('records'))
            with read_snapshot() as connection:
                self.assertEqual(0, connection.execute('SELECT COUNT(*) FROM qc_record_maintenance_events').fetchone()[0])

            app.button(key='record_cancel').click().run()
            self.assertFalse(list(app.exception), list(app.exception))
            self.assertTrue(app.session_state['qc_record_dialog']['discard'])
            app.button(key='record_continue').click().run()
            self.assertFalse(list(app.exception), list(app.exception))
            self.assertFalse(app.session_state['qc_record_dialog']['discard'])
            self.assertEqual(reason, app.text_area(key=reason_key).value)
            app.button(key='record_cancel').click().run()
            app.button(key='record_discard').click().run()
            self.assertFalse(list(app.exception), list(app.exception))
            self.assertNotIn('qc_record_dialog', app.session_state.filtered_state)

            project = app.selectbox(key='v12_instant_project_selector')
            project.set_value(project.options[1]).run()
            batch = app.selectbox(key='v12_instant_batch_selector')
            batch.set_value(batch.options[1]).run()
            self.assertEqual(other['batch_id'], app.session_state['instant_selected_batch_id'])
            app.session_state[f"instant_{other['batch_id']}_selected"] = ids[1]
            app.run()
            self.assertFalse(app.button(key=f"qc_record_instant_{other['batch_id']}_view").disabled)
            self.assertFalse(app.button(key=f"qc_record_instant_{other['batch_id']}_disable").disabled)
            app.button(key=f"qc_record_instant_{other['batch_id']}_disable").click().run()
            other_state = app.session_state['qc_record_dialog']
            self.assertEqual(other['batch_id'], other_state['batch_id'])
            app.text_area(key='record_' + other_state['token'] + '_reason').set_value('新批次记录独立核对').run()
            app.button(key='record_save').click().run()
            self.assertFalse(list(app.exception), list(app.exception))
            self.assertNotIn('qc_record_dialog', app.session_state.filtered_state)
            with read_snapshot() as connection:
                events = [dict(row) for row in connection.execute('SELECT * FROM qc_record_maintenance_events')]
            self.assertEqual(1, len(events))
            self.assertEqual(('instant', other['batch_id'], ids[1]),
                             (events[0]['qc_method'], events[0]['batch_id'], events[0]['record_id']))
            self.assertEqual(0, int(get_instant_results(other['batch_id']).iloc[0]['is_effective']))
            self.assertEqual(old_before, get_instant_results(old['batch_id']).to_dict('records'))


if __name__ == '__main__':
    unittest.main()
