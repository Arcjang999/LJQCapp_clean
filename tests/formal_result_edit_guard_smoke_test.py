"""Formal originals stay immutable while building edits and notes remain usable."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import database as db
from services.lot_lifecycle_service import get_result_edit_availability, save_lj_result_manual_note
from tests.lj_monthly_report_smoke_test import TemporaryDatabaseContext, seed_lj_batch_with_formal_monthly_data
from tests.zscore_monthly_report_smoke_test import seed_zscore_batch_with_formal_monthly_data, seed_zscore_batch_with_building_only_data
from zscore_logic import update_saved_zscore_run, update_saved_zscore_run_manual_note


def dump():
    with db.read_snapshot() as connection:
        return '\n'.join(connection.iterdump())


class FormalResultEditGuardTests(unittest.TestCase):
    def test_lj_formal_and_prior_building_values_cannot_be_overwritten(self):
        with TemporaryDatabaseContext():
            _, batch = seed_lj_batch_with_formal_monthly_data()
            with db.read_snapshot() as connection:
                rows = [dict(r) for r in connection.execute('SELECT * FROM results WHERE batch_id=? ORDER BY id', (batch,))]
            before = dump()
            for row in (rows[0], rows[-1]):
                self.assertFalse(get_result_edit_availability('lj', row['id'])['allowed'])
                with self.assertRaisesRegex(ValueError, '不能覆盖原始记录'):
                    db.update_result(row['id'], row['test_time'], row['value'] + 0.1, operator=row['operator'])
                self.assertEqual(before, dump())
            selected = rows[-1]
            save_lj_result_manual_note(selected['id'], '人工补充调查说明')
            current = dict(db.get_result(selected['id']))
            self.assertEqual('人工补充调查说明', current.pop('manual_note'))
            selected.pop('manual_note')
            self.assertEqual(selected, current)

    def test_zscore_formal_and_prior_building_values_cannot_be_overwritten(self):
        with TemporaryDatabaseContext():
            _, batch = seed_zscore_batch_with_formal_monthly_data()
            with db.read_snapshot() as connection:
                rows = [dict(r) for r in connection.execute('SELECT * FROM zscore_runs WHERE batch_id=? ORDER BY id', (batch,))]
            before = dump()
            for row in (rows[0], rows[-1]):
                self.assertFalse(get_result_edit_availability('zscore', row['id'])['allowed'])
                values = db.get_zscore_run_with_levels(row['id'])['level_results']
                with self.assertRaisesRegex(ValueError, '不能覆盖原始记录'):
                    update_saved_zscore_run(row['id'], test_time=row['test_time'], operator=row['operator'],
                        level_results=[dict(level_id=v['level_id'], raw_value=v['raw_value'] + 0.1) for v in values])
                self.assertEqual(before, dump())
            selected = rows[-1]
            update_saved_zscore_run_manual_note(selected['id'], '人工补充复测说明')
            with db.read_snapshot() as connection:
                current = dict(connection.execute('SELECT * FROM zscore_runs WHERE id=?', (selected['id'],)).fetchone())
            self.assertEqual('人工补充复测说明', current.pop('manual_note'))
            selected.pop('manual_note')
            self.assertEqual(selected, current)

    def test_unconfirmed_building_batches_still_allow_value_edits(self):
        with TemporaryDatabaseContext():
            project = db.create_project('建靶维护', input_value_type='raw')
            batch = db.create_batch(project_id=project, instrument='仪器', reagent='试剂', qc_material='质控品',
                                    concentration='单水平', lot_no='BUILD-01', target_n=5)
            result = db.add_result(batch, '2026-09-28 08:00:00', 100.0, operator='建立人员')
            self.assertTrue(get_result_edit_availability('lj', result)['allowed'])
            db.update_result(result, '2026-09-28 08:00:00', 100.1, operator='建立人员')
            self.assertEqual(100.1, db.get_result(result)['value'])
            _, zbatch = seed_zscore_batch_with_building_only_data()
            with db.read_snapshot() as connection:
                zrun = connection.execute('SELECT id FROM zscore_runs WHERE batch_id=? ORDER BY id LIMIT 1', (zbatch,)).fetchone()[0]
            original = db.get_zscore_run_with_levels(zrun)
            self.assertTrue(get_result_edit_availability('zscore', zrun)['allowed'])
            update_saved_zscore_run(zrun, test_time=original['test_time'], operator=original['operator'],
                level_results=[dict(level_id=v['level_id'], raw_value=v['raw_value'] + 0.01) for v in original['level_results']])
            current = db.get_zscore_run_with_levels(zrun)
            self.assertAlmostEqual(original['level_results'][0]['raw_value'] + 0.01, current['level_results'][0]['raw_value'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
