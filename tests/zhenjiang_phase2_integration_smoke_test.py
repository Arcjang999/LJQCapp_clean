"""Single and batch period archives share frozen evidence and survive full restore."""
from copy import deepcopy
from datetime import datetime
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import sys
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import database as db
from services.batch_monthly_report_service import (
    preview_period_reports, create_monthly_report_job, get_monthly_report_job,
    run_monthly_report_item, build_monthly_report_zip,
)
from services.out_of_control_service import save_handling, get_event
from services.out_of_control_attachment_service import list_attachments, read_attachment
from services.out_of_control_report_service import generate_event_report, read_event_report
from services.report_archive_service import read_report_archive, get_report_archive_metadata
from services.report_service import (
    build_lj_period_report_package, build_zscore_period_report_package,
    build_lj_period_report_pdf, build_zscore_period_report_pdf,
    save_lj_period_report_snapshot, save_zscore_period_report_snapshot,
    build_lj_monthly_report_package, build_zscore_monthly_report_package,
    save_lj_monthly_report_snapshot, save_zscore_monthly_report_snapshot,
    get_report_history_record, read_report_history_pdf, regenerate_report_from_history,
)
from services.settings_service import get_report_settings, save_report_settings_form
from services.storage_service import create_database_backup, restore_database_from_backup_file
from tests.report_archive_smoke_test import seed
from tests.storage_smoke_test import TemporaryStorageContext
from tests.report_pdf_assertions import assert_uniform_a4_pages_without_watermark


def manifest(data):
    with zipfile.ZipFile(BytesIO(data)) as archive:
        return json.loads(archive.read('归档清单.json'))


class ZhenjiangPhaseTwoIntegration(unittest.TestCase):
    def test_period_reports_freeze_handling_retests_files_batch_history_and_full_restore(self):
        with TemporaryStorageContext() as storage:
            db.init_db()
            save_report_settings_form(dict(lab_name='检验实验室', department_name='检验科',
                qc_owner_name='检验员甲', reviewer_name='复核员乙'))
            lj_fixture, z_fixture = seed('lj'), seed('zscore')
            lj_batch, z_batch = lj_fixture['batch'], z_fixture['batch']
            old_lj = save_lj_monthly_report_snapshot(build_lj_monthly_report_package(lj_batch, '2026-09'))
            old_z = save_zscore_monthly_report_snapshot(build_zscore_monthly_report_package(z_batch, '2026-09'))
            old_pdf = {identifier: read_report_history_pdf(identifier) for identifier in (old_lj, old_z)}
            retest_id = lj_fixture['retest']
            event = lj_fixture['events'][0]
            source_id = int(event['source_id'])
            event_id = event['event_id']
            attachments = list_attachments(event_id, event['revision_no'])
            originals = [(read_attachment(event_id, row['id'], event['revision_no']), row['description']) for row in attachments]
            content = deepcopy(event['content'])
            content.update(cause_category='仪器', cause_analysis='核查后登记维护原因',
                corrective_action='核对维护并完成复测', effect_description='由实验室人工核对处理效果',
                effect_evidence='维护和复测记录', handler_text='处理甲',
                patient_impact_assessment='无需评估', patient_impact_basis='本次仅为质控核对',
                attachment_ids=[row['id'] for row in attachments],
                retest_refs=[dict(source_type='lj_result', source_id=retest_id,
                    difference_reason='本次复测沿用原批次；资料及参数逐项核对')])
            event = save_handling(event_id, event['revision_no'], 'phase2-event-draft', 'save_draft', content, '处理甲')
            event = save_handling(event_id, event['revision_no'], 'phase2-event-submit', 'submit', event['content'], '处理甲')
            confirmed = deepcopy(event['content'])
            confirmed.update(confirmer_text='确认乙', confirmed_at=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                             confirmation_checked=True)
            event = save_handling(event_id, event['revision_no'], 'phase2-event-confirm', 'confirm', confirmed, '确认乙')
            selected_revision = event['revision_no']
            event_report = generate_event_report(event_id, selected_revision)
            original_event_pdf = read_event_report(event_report['report_id'])
            choices = {event_id: selected_revision}
            before = None
            with db.read_snapshot() as connection:
                before = '\n'.join(connection.iterdump())
            lj_package = build_lj_period_report_package(lj_batch, '2026-09-28', '2026-09-28', handling_versions=choices)
            lj_pdf = build_lj_period_report_pdf(lj_package)
            z_package = build_zscore_period_report_package(z_batch, '2026-09-28', '2026-09-28')
            z_pdf = build_zscore_period_report_pdf(z_package)
            with db.read_snapshot() as connection:
                self.assertEqual(before, '\n'.join(connection.iterdump()))
            for data in (lj_pdf, z_pdf):
                reader = assert_uniform_a4_pages_without_watermark(data)
                text = ''.join(page.extract_text() for page in reader.pages)
                self.assertIn('2026-09-28', text)
                self.assertIn('检验员甲', text)
                self.assertIn('复核员乙', text)
            lj_export = save_lj_period_report_snapshot(lj_package, lj_pdf, request_key='phase2-single-lj')
            z_export = save_zscore_period_report_snapshot(z_package, z_pdf, request_key='phase2-single-z')
            self.assertEqual(lj_export, save_lj_period_report_snapshot(lj_package, lj_pdf, request_key='phase2-single-lj'))
            zip_bytes = read_report_archive(lj_export)
            frozen = manifest(zip_bytes)
            self.assertEqual('2026-09-28', frozen['period']['start_date'])
            self.assertEqual([source_id], frozen['source_ids'])
            self.assertEqual(choices, {int(key): value for key, value in frozen['handling_versions'].items()})
            self.assertEqual('2026-10-05 08:00:00', frozen['events'][0]['retests'][0]['test_time'])
            self.assertEqual(2, len(frozen['attachments']))
            self.assertEqual(2, len({row['path'] for row in frozen['attachments']}))
            with zipfile.ZipFile(BytesIO(zip_bytes)) as archive:
                self.assertEqual(lj_pdf, archive.read('质控报告.pdf'))
                self.assertEqual(original_event_pdf, archive.read(f'失控处理报告/处理-{event_id}-V{selected_revision}.pdf'))
                expected_files = {row['id']: data for row, (data, _) in zip(attachments, originals)}
                for row in frozen['attachments']:
                    self.assertEqual(expected_files[row['file_id']], archive.read(row['path']))
            preview = preview_period_reports('2026-09-28', '2026-09-28', methods=['lj', 'zscore'])
            self.assertEqual(2, len(preview['items']), preview)
            job = create_monthly_report_job(preview, [row['unit_key'] for row in preview['items']], 'phase2-batch')
            job_id = job if isinstance(job, int) else job['id']
            for row in get_monthly_report_job(job_id)['items']:
                self.assertEqual('succeeded', run_monthly_report_item(job_id, row['id'])['status'])
            bulk = build_monthly_report_zip(job_id)
            bulk_bytes = bulk if isinstance(bulk, bytes) else bulk['zip_bytes']
            with zipfile.ZipFile(BytesIO(bulk_bytes)) as archive:
                nested = [name for name in archive.namelist() if name.endswith('.zip')]
                self.assertEqual(2, len(nested))
                for name in nested:
                    self.assertEqual('2026-09-28', manifest(archive.read(name))['period']['end_date'])
            record = get_report_history_record(lj_export)
            self.assertEqual(('2026-09-28', '2026-09-28'), (record.period_start, record.period_end))
            archive_hash = sha256(zip_bytes).hexdigest()
            event = get_event(event_id)
            changed = deepcopy(event['content'])
            changed['cause_analysis'] = '追加处理核对记录'
            save_handling(event_id, event['revision_no'], 'phase2-event-revise', 'revise', changed, '处理甲',
                          change_reason='追加核对依据')
            self.assertEqual(archive_hash, sha256(read_report_archive(lj_export)).hexdigest())
            self.assertEqual(lj_pdf, read_report_history_pdf(lj_export))
            regenerated = regenerate_report_from_history(lj_export, request_key='phase2-regenerate')
            new_id = regenerated['snapshot_id'] if isinstance(regenerated, dict) else regenerated.snapshot_id
            self.assertNotEqual(lj_export, new_id)
            self.assertEqual(('2026-09-28', '2026-09-28'),
                (get_report_history_record(new_id).period_start, get_report_history_record(new_id).period_end))
            for identifier, data in old_pdf.items():
                self.assertEqual(data, read_report_history_pdf(identifier))
            backup = create_database_backup(storage.root / 'complete-period-backups')
            with db.atomic_write() as connection:
                connection.execute('DELETE FROM qc_report_archive_files WHERE export_id IN (?,?)', (lj_export, z_export))
            save_report_settings_form(dict(lab_name='隔离变更后资料'))
            restore_database_from_backup_file(backup.target_path)
            self.assertEqual(archive_hash, sha256(read_report_archive(lj_export)).hexdigest())
            self.assertEqual(lj_pdf, read_report_history_pdf(lj_export))
            self.assertEqual('检验实验室', get_report_settings().lab_name)
            self.assertTrue(get_report_archive_metadata(z_export))
            for row, (data, _) in zip(attachments, originals):
                self.assertEqual(data, read_attachment(event_id, row['id'], selected_revision))


if __name__ == '__main__':
    unittest.main(verbosity=2)
