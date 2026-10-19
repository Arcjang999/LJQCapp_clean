"""Page refreshes keep report previews valid; actual source edits still reject."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import database as db
from services.monthly_report_source_service import range_source_version
from services.report_service import (
    build_lj_period_report_package, build_lj_period_report_pdf, save_lj_period_report_snapshot,
    build_zscore_monthly_report_package, build_zscore_monthly_report_pdf, save_zscore_monthly_report_snapshot,
)
from services.storage_service import restore_database_from_backup_file
from services.workbench_config_service import sync_lj_workbench_bindings
from services.zscore_workbench_service import sync_zscore_workbench_bindings
from tests.quick_project_setup_smoke_test import database_contents
from tests.storage_smoke_test import TemporaryStorageContext
from tests.zscore_monthly_report_smoke_test import seed_zscore_batch_with_formal_monthly_data


def binding(method, batch):
    with db.get_connection() as connection:
        return dict(connection.execute(
            'SELECT * FROM qc_workbench_bindings WHERE qc_method=? AND runtime_batch_id=?',
            (method, batch)).fetchone())


class ReportSourceRefreshTests(unittest.TestCase):
    def test_restored_trial_lj_preview_saves_after_real_cross_second_page_refresh(self):
        with TemporaryStorageContext():
            db.init_db()
            restore_database_from_backup_file(ROOT / 'test_data/trial-2026-10-13/trial-data.zip')
            with db.get_connection() as connection:
                batch = connection.execute("SELECT id FROM batches WHERE lot_no='QC-202608-HIV1-A-L1'").fetchone()[0]
            sync_lj_workbench_bindings()
            package = build_lj_period_report_package(batch, '2026-09-01', '2026-09-30')
            before = binding('lj', batch)
            time.sleep(1.05)
            sync_lj_workbench_bindings()
            after = binding('lj', batch)
            self.assertNotEqual(before.pop('updated_at'), after.pop('updated_at'))
            self.assertEqual(before, after)
            self.assertEqual(package.report.source_version['fingerprint'],
                range_source_version('lj', batch, '2026-09-01', '2026-09-30')['fingerprint'])
            pdf = build_lj_period_report_pdf(package)
            time.sleep(1.05)
            sync_lj_workbench_bindings()
            export = save_lj_period_report_snapshot(package, pdf, request_key='cross-second-lj-refresh')
            self.assertGreater(export, 0)

    def test_zscore_preview_survives_unchanged_binding_refresh(self):
        with TemporaryStorageContext():
            db.init_db()
            _, batch = seed_zscore_batch_with_formal_monthly_data()
            with db.get_connection() as connection:
                connection.execute("UPDATE qc_workbench_bindings SET updated_at='2000-01-01 00:00:00' WHERE qc_method='zscore' AND runtime_batch_id=?", (batch,))
            package = build_zscore_monthly_report_package(batch, '2026-04')
            before = binding('zscore', batch)
            sync_zscore_workbench_bindings()
            after = binding('zscore', batch)
            self.assertNotEqual(before.pop('updated_at'), after.pop('updated_at'))
            self.assertEqual(before, after)
            self.assertEqual(package.report.source_version['fingerprint'],
                range_source_version('zscore', batch, '2026-04-01', '2026-04-30')['fingerprint'])
            pdf = build_zscore_monthly_report_pdf(package)
            self.assertGreater(save_zscore_monthly_report_snapshot(package, pdf), 0)

    def test_actual_binding_changes_still_reject_without_archive_writes(self):
        with TemporaryStorageContext():
            db.init_db()
            _, batch = seed_zscore_batch_with_formal_monthly_data()
            package = build_zscore_monthly_report_package(batch, '2026-04')
            pdf = build_zscore_monthly_report_pdf(package)
            original = binding('zscore', batch)
            snapshot = json.loads(original['source_snapshot_json'])
            changed = deepcopy(snapshot)
            changed['reagent_name'] = '已修改的实际试剂名称'
            for field, value in (
                ('binding_status', 'inactive'),
                ('source_revision_no', original['source_revision_no'] + 1),
                ('source_snapshot_json', json.dumps(changed, ensure_ascii=False)),
            ):
                with self.subTest(field=field):
                    with db.get_connection() as connection:
                        connection.execute(f'UPDATE qc_workbench_bindings SET {field}=? WHERE id=?', (value, original['id']))
                    self.assertNotEqual(package.report.source_version['fingerprint'],
                        range_source_version('zscore', batch, '2026-04-01', '2026-04-30')['fingerprint'])
                    before_save = database_contents()
                    with self.assertRaisesRegex(ValueError, '已变化'):
                        save_zscore_monthly_report_snapshot(package, pdf)
                    self.assertEqual(before_save, database_contents())
                    with db.get_connection() as connection:
                        connection.execute(f'UPDATE qc_workbench_bindings SET {field}=? WHERE id=?', (original[field], original['id']))


if __name__ == '__main__':
    unittest.main()
