"""LJ conversion commits saved interpretations with the copied original values."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import database as db
from services.instant_service import confirm_instant_transfer_to_lj
from tests.instant_v12_fixtures import seed_instant_configuration
from tests.instant_v12_integration_smoke_test import IsolatedDatabase, entry


def dump():
    with db.read_snapshot() as connection:
        return '\n'.join(connection.iterdump())


class InstantTransferEvidenceTests(unittest.TestCase):
    def test_twenty_and_twenty_one_points_have_saved_lj_interpretations_immediately(self):
        for count in (20, 21):
            with self.subTest(count=count), IsolatedDatabase():
                fixture = seed_instant_configuration()
                for index in range(count):
                    entry(fixture, index)
                result = confirm_instant_transfer_to_lj(fixture['batch_id'])
                with db.read_snapshot() as connection:
                    records = [dict(row) for row in connection.execute('''SELECT r.*,x.id context_id,x.provenance,
                        x.source_context_id,e.evaluation_json FROM results r
                        JOIN qc_result_contexts x ON x.lj_result_id=r.id
                        LEFT JOIN qc_result_evaluations e ON e.id=(SELECT MAX(id) FROM qc_result_evaluations WHERE context_id=x.id)
                        WHERE r.batch_id=? ORDER BY r.id''', (result['target_batch_id'],))]
                    self.assertEqual(count, len(records))
                    self.assertTrue(all(row['evaluation_json'] for row in records))
                    self.assertTrue(all(row['provenance'] == 'instant_transfer' and row['source_context_id'] for row in records))
                    phases = [__import__('json').loads(row['evaluation_json'])['result']['phase'] for row in records]
                    self.assertEqual(20, phases.count('建靶数据'))
                    self.assertEqual(count - 20, phases.count('正式数据'))
                    self.assertEqual([100 + .1 * index for index in range(count)], [row['value'] for row in records])
                    self.assertFalse(connection.execute('PRAGMA foreign_key_check').fetchall())

    def test_interpretation_failure_rolls_back_results_context_and_transfer_state(self):
        with IsolatedDatabase():
            fixture = seed_instant_configuration()
            for index in range(20):
                entry(fixture, index)
            before = dump()
            with patch('qc_logic.persist_lj_batch_outlier_snapshot', side_effect=RuntimeError('判读保存失败')):
                with self.assertRaisesRegex(RuntimeError, '判读保存失败'):
                    confirm_instant_transfer_to_lj(fixture['batch_id'])
            self.assertEqual(before, dump())
            converted = confirm_instant_transfer_to_lj(fixture['batch_id'])
            self.assertEqual(20, converted['transferred_effective_count'])

    def test_new_lj_results_after_transfer_appear_in_daily_overview_without_counting_copies_twice(self):
        from services.daily_overview_service import get_daily_overview
        with IsolatedDatabase():
            fixture = seed_instant_configuration()
            for index in range(20):
                entry(fixture, index)
            converted = confirm_instant_transfer_to_lj(fixture['batch_id'])
            saved = db.add_result(converted['target_batch_id'], '2026-09-12 08:00:00', 101.0, operator='转入后检测人')
            before = dump()
            previous = get_daily_overview('2026-09-11')
            self.assertEqual(20, previous['count'])
            following = get_daily_overview('2026-09-12')
            self.assertEqual(1, following['count'])
            row = next(item for item in following['items'] if item['qc_method'] == 'lj' and item['runtime_batch_id'] == converted['target_batch_id'])
            self.assertEqual(saved, row['latest']['source_id'])
            self.assertEqual(1, len(get_daily_overview('2026-09-12', qc_method='lj')['items']))
            self.assertEqual(before, dump())

    def test_overview_can_select_both_methods_and_open_the_converted_lj_chart(self):
        from datetime import date
        from streamlit.testing.v1 import AppTest
        from services.daily_overview_service import get_daily_overview
        with IsolatedDatabase():
            fixture = seed_instant_configuration()
            for index in range(20):
                entry(fixture, index)
            converted = confirm_instant_transfer_to_lj(fixture['batch_id'])
            db.add_result(converted['target_batch_id'], '2026-09-12 08:00:00', 101.0, operator='转入后检测人')
            overview = get_daily_overview('2026-09-12')
            self.assertEqual(2, len({item['overview_key'] for item in overview['items']}))
            converted_item = next(item for item in overview['items'] if item['qc_method'] == 'lj')
            app = AppTest.from_string('''
import streamlit as st
from pages.main_page import LJ_ENTRY_LABEL
if st.session_state.get('pending_top_level_method') == LJ_ENTRY_LABEL:
    from pages.lj_page import render_lj_page
    render_lj_page()
else:
    from ui.daily_overview import render_daily_overview
    render_daily_overview()
''', default_timeout=20)
            app.session_state['daily_day'] = date(2026, 9, 12)
            app.session_state['daily_selected'] = converted_item['overview_key']
            app.run()
            self.assertFalse(list(app.exception), str(list(app.exception)))
            self.assertEqual(3, len(app.selectbox(key='daily_selected').options))
            next(button for button in app.button if button.label == '查看质控图与单份月报').click().run()
            self.assertFalse(list(app.exception), str(list(app.exception)))
            self.assertEqual(converted['target_batch_id'], app.session_state['selected_batch_id'])
            self.assertTrue(app.get('image'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
