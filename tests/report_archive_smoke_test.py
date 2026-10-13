"""Frozen complete archives use real saved QC results and immutable handling evidence."""
from copy import deepcopy
from io import BytesIO
from pathlib import Path
import json
import sqlite3
import sys
import unittest
from unittest.mock import patch
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pypdf import PdfReader
import database as db
from services import report_archive_service as archives
from services.lot_lifecycle_service import create_target_profile
from services.out_of_control_attachment_service import stage_attachment, resolve_managed_path
from services.out_of_control_report_service import generate_event_report, read_event_report
from services.out_of_control_service import open_event, save_handling
from services.report_service import (
    build_lj_period_report_package, build_lj_period_report_pdf, save_lj_period_report_snapshot,
    build_zscore_period_report_package, build_zscore_period_report_pdf, save_zscore_period_report_snapshot,
    read_report_history_pdf, regenerate_report_from_history,
)
from services.settings_service import save_report_settings_form
from services.storage_service import create_database_backup, restore_database_from_backup_file
from tests.lot_lifecycle_smoke_test import lj, verified, switch
from tests.quick_project_setup_smoke_test import database_contents
from tests.storage_smoke_test import TemporaryStorageContext
from tests.zscore_v12_fixtures import seed_zscore_configuration
from zscore_logic import create_zscore_run, get_template_id_for_level_count


START, END = '2026-09-28', '2026-09-30'


def seed(method='lj', count=2):
    batch = lj() if method == 'lj' else seed_zscore_configuration(level_count=count)['batch_id']
    system, reagent, verification = verified(method, batch, '000123')
    switch(system, reagent, verification)
    create_target_profile(method=method, batch_id=batch,
        levels=[dict(level_id=f'Level {index}', mean=100 * index, sd=2) for index in range(1, 2 if method == 'lj' else count + 1)],
        source='manual', evidence='合成归档验收参数', confirmed_by='隔离参数确认人', effective_at='2026-09-02')
    def record(when, rejected=True):
        if method == 'lj':
            return db.add_result(batch, when, 109 if rejected else 100, operator='隔离检测人',
                                 lot_selection={'reagent_lot_id': reagent})
        return create_zscore_run(batch_id=batch, test_time=when, operator='隔离检测人',
            level_results=[dict(level_id=f'Level {index}', raw_value=100 * index + (9 if rejected else 0))
                           for index in range(1, count + 1)], template_id=get_template_id_for_level_count(count),
            lot_selection={'reagent_lot_id': reagent})['id']
    source_type = 'lj_result' if method == 'lj' else 'zscore_run'
    first, second, pending = [record(f'2026-09-{day} 08:00:00') for day in (28, 29, 30)]
    retest = record('2026-10-05 08:00:00', False)
    events, files = [], []
    for index, source in enumerate((first, second), 1):
        event = open_event(source_type, source, f'archive-open-{method}-{index}', '隔离登记人')
        attachment = stage_attachment(event['event_id'], '同名复测.csv',
            f'项目,结果\n隔离材料,{index}\n'.encode(), uploaded_by='隔离上传人', description=f'原件{index}')
        extra = stage_attachment(event['event_id'], '同名复测.csv', '项目,结果\n第二份材料,3\n'.encode(),
                                 uploaded_by='隔离上传人') if index == 1 else None
        files.extend([attachment, *([extra] if extra else [])])
        content = dict(event['content'], cause_category='仪器', cause_analysis=f'所选版本原因{index}',
            corrective_action='按测试方案核对并纠正', effect_description='人工核对效果',
            effect_evidence='保留本测试的检查依据', handler_text='实际处理人',
            attachment_ids=[attachment['file_id'], *([extra['file_id']] if extra else [])],
            retest_refs=[dict(source_type=source_type, source_id=retest, difference_reason='已核对日期及材料')]
                if index == 1 else [], patient_impact_assessment='' if index == 1 else '无需评估')
        events.append(save_handling(event['event_id'], event['revision_no'], f'archive-draft-{method}-{index}',
                                   'save_draft', content, '实际处理人'))
    return dict(method=method, batch=batch, events=events, files=files, pending=pending, retest=retest)


def package(fixture, versions=None):
    function = build_lj_period_report_package if fixture['method'] == 'lj' else build_zscore_period_report_package
    return function(fixture['batch'], START, END, handling_versions=versions)


def pdf(fixture, current):
    return (build_lj_period_report_pdf if fixture['method'] == 'lj' else build_zscore_period_report_pdf)(current)


def save(fixture, current, request='archive-save'):
    return (save_lj_period_report_snapshot if fixture['method'] == 'lj' else save_zscore_period_report_snapshot)(
        current, pdf(fixture, current), request_key=request)


def text(data):
    return '\n'.join(page.extract_text() for page in PdfReader(BytesIO(data)).pages)


class ReportArchiveTests(unittest.TestCase):
    def test_scope_selected_versions_outside_retests_original_files_and_read_only_prepare(self):
        with TemporaryStorageContext():
            db.init_db()
            save_report_settings_form(dict(lab_name='归档验收实验室', department_name='归档科室',
                qc_owner_name='报告默认检验员', reviewer_name='报告默认复核员'))
            fixture = seed()
            current = package(fixture)
            before = database_contents()
            data = pdf(fixture, current)
            self.assertEqual(current.report.title, PdfReader(BytesIO(data)).metadata.title)
            self.assertEqual(before, database_contents())
            archive = current.complete_archive
            manifest = archive['manifest']
            self.assertEqual([START, END], [manifest['period']['start_date'], manifest['period']['end_date']])
            self.assertEqual({str(value['event_id']): 2 for value in fixture['events']}, manifest['handling_versions'])
            self.assertEqual('2026-10-05 08:00:00', manifest['events'][0]['retests'][0]['test_time'])
            self.assertEqual([fixture['pending']], [row['source_id'] for row in manifest['unregistered_sources']])
            body = text(data)
            for value in ('所选版本原因1', '2026-10-05', '尚未登记失控处理', '患者影响评估：未记录',
                          '报告默认检验员', '报告默认复核员', '实际处理人'):
                self.assertIn(value, body)
            with ZipFile(BytesIO(archive['zip_bytes'])) as bundle:
                self.assertEqual(data, bundle.read('质控报告.pdf'))
                self.assertEqual(manifest, json.loads(bundle.read('归档清单.json')))
                self.assertEqual(len(bundle.namelist()), len(set(bundle.namelist())))
                self.assertEqual(3, len(manifest['attachments']))
                self.assertEqual(1, len({row['original_name'] for row in manifest['attachments']}))
                for record in manifest['attachments']:
                    original = next(row for row in fixture['files'] if row['file_id'] == record['file_id'])
                    self.assertEqual(resolve_managed_path(original['relative_path']).read_bytes(), bundle.read(record['path']))
                self.assertIn('归档位置', bundle.read('附件清单.csv').decode('utf-8-sig'))
            with db.read_snapshot() as connection:
                self.assertEqual(0, connection.execute('SELECT COUNT(*) FROM qc_event_reports').fetchone()[0])
                self.assertEqual(0, connection.execute('SELECT COUNT(*) FROM report_exports').fetchone()[0])

    def test_two_and_three_level_archives_keep_complete_runs_and_all_retest_levels(self):
        for count in (2, 3):
            with self.subTest(levels=count), TemporaryStorageContext():
                db.init_db()
                fixture = seed('zscore', count)
                current = package(fixture)
                export_id = save(fixture, current)
                manifest = archives.get_report_archive_metadata(export_id)['manifest']
                self.assertEqual(3, len(manifest['source_ids']))
                self.assertEqual(count, len(manifest['events'][0]['retests'][0]['snapshot']['levels']))
                self.assertEqual(current.complete_archive['zip_bytes'], archives.read_report_archive(export_id))
                self.assertEqual(current.complete_archive['pdf_bytes'], read_report_history_pdf(export_id))

    def test_saved_event_pdf_is_reused_and_original_default_people_are_not_overwritten(self):
        with TemporaryStorageContext():
            db.init_db()
            fixture = seed()
            event = fixture['events'][0]
            original = generate_event_report(event['event_id'], event['revision_no'])
            save_report_settings_form(dict(lab_name='后来修改的实验室', qc_owner_name='新默认人员'))
            current = package(fixture)
            save(fixture, current)
            with ZipFile(BytesIO(current.complete_archive['zip_bytes'])) as bundle:
                self.assertEqual(original['pdf_bytes'], bundle.read(f"失控处理报告/处理-{event['event_id']}-V2.pdf"))
            self.assertEqual(original['pdf_bytes'], read_event_report(original['report_id']))
            with db.read_snapshot() as connection:
                self.assertEqual(1, connection.execute('SELECT COUNT(*) FROM qc_event_reports').fetchone()[0])

    def test_selected_old_revision_is_consistent_and_saved_archive_survives_later_revision_and_regeneration(self):
        with TemporaryStorageContext():
            db.init_db()
            fixture = seed()
            old = fixture['events'][0]
            newer = save_handling(old['event_id'], 2, 'handling-new-3', 'save_draft',
                dict(old['content'], cause_analysis='以后修订的原因'), '修订处理人')
            versions = {old['event_id']: 2, fixture['events'][1]['event_id']: 2}
            current = package(fixture, versions)
            choices = archives.list_report_handling_versions(current, method='lj')
            self.assertEqual(3, choices[0]['current_revision_no'])
            self.assertEqual(2, choices[0]['selected_revision_no'])
            self.assertEqual('所选版本原因1', current.report.handling_summaries[0]['cause_analysis'])
            export_id = save(fixture, current)
            original_zip, original_pdf = archives.read_report_archive(export_id), read_report_history_pdf(export_id)
            save_handling(newer['event_id'], 3, 'handling-new-4', 'save_draft',
                dict(newer['content'], cause_analysis='再次修订的原因'), '修订处理人')
            regenerated = regenerate_report_from_history(export_id)
            self.assertNotEqual(export_id, regenerated.snapshot_id)
            self.assertEqual(original_zip, archives.read_report_archive(export_id))
            self.assertEqual(original_pdf, read_report_history_pdf(export_id))
            adopted = archives.get_report_archive_metadata(regenerated.snapshot_id)['handling_versions']
            self.assertEqual({str(key): value for key, value in versions.items()}, adopted)
            self.assertIn('所选版本原因1', text(regenerated.pdf_bytes))
            self.assertNotIn('再次修订的原因', text(regenerated.pdf_bytes))

    def test_logical_request_retry_and_late_archive_insert_failure_are_atomic(self):
        with TemporaryStorageContext():
            db.init_db()
            fixture = seed()
            current = package(fixture)
            pdf(fixture, current)
            damaged = deepcopy(current.complete_archive)
            damaged['zip_bytes'] = b'changed archive payload'
            with db.atomic_write() as connection:
                with self.assertRaisesRegex(ValueError, '归档文件已变化'):
                    archives.persist_complete_report_archive(1, damaged, request_key='bad-payload', connection=connection)
            with db.atomic_write() as connection:
                connection.execute("CREATE TRIGGER fail_archive BEFORE INSERT ON qc_report_archive_files BEGIN SELECT RAISE(ABORT,'injected archive failure'); END")
            before = database_contents()
            with self.assertRaises(sqlite3.DatabaseError):
                save(fixture, current)
            self.assertEqual(before, database_contents())
            with db.atomic_write() as connection:
                connection.execute('DROP TRIGGER fail_archive')
            export_id = save(fixture, current)
            after = database_contents()
            rebuilt = package(fixture)
            pdf(fixture, rebuilt)
            self.assertEqual(current.complete_archive['request_digest'], rebuilt.complete_archive['request_digest'])
            self.assertEqual(export_id, save(fixture, rebuilt))
            self.assertEqual(after, database_contents())
            chosen = package(fixture, {row['event_id']: 1 for row in fixture['events']})
            with self.assertRaisesRegex(ValueError, '另一份报告'):
                save(fixture, chosen)
            self.assertEqual(after, database_contents())

    def test_missing_or_changed_selected_attachment_refuses_prepare_and_rolls_back_save(self):
        for damage in ('missing', 'corrupt'):
            with self.subTest(damage=damage), TemporaryStorageContext():
                db.init_db()
                fixture = seed()
                current = package(fixture)
                pdf(fixture, current)
                path = resolve_managed_path(fixture['files'][0]['relative_path'])
                path.unlink() if damage == 'missing' else path.write_bytes(b'changed original bytes')
                before = database_contents()
                with self.assertRaisesRegex(ValueError, '附件'):
                    pdf(fixture, package(fixture))
                with self.assertRaisesRegex(ValueError, '附件'):
                    save(fixture, current)
                self.assertEqual(before, database_contents())

    def test_merge_zip_and_changed_handling_failures_do_not_create_success_history(self):
        with TemporaryStorageContext():
            db.init_db()
            fixture = seed()
            for function in ('_merge_pdf', '_write_zip'):
                before = database_contents()
                with patch.object(archives, function, side_effect=ValueError('injected render failure')):
                    with self.assertRaisesRegex(ValueError, 'injected'):
                        pdf(fixture, package(fixture))
                self.assertEqual(before, database_contents())
            current = package(fixture)
            pdf(fixture, current)
            event = fixture['events'][0]
            save_handling(event['event_id'], event['revision_no'], 'stale-handling', 'save_draft',
                dict(event['content'], supplementary_note='生成后补充资料'), '处理人')
            before = database_contents()
            with self.assertRaisesRegex(ValueError, '变化'):
                save(fixture, current)
            self.assertEqual(before, database_contents())

    def test_complete_backup_restores_original_zip_pdf_files_and_hash_checked_read(self):
        with TemporaryStorageContext() as storage:
            db.init_db()
            fixture = seed()
            current = package(fixture)
            export_id = save(fixture, current)
            original = archives.read_report_archive(export_id)
            original_pdf = read_report_history_pdf(export_id)
            backup = create_database_backup(storage.root / 'backups')
            self.assertEqual('.zip', backup.target_path.suffix)
            snapshot = database_contents()
            attachment = fixture['files'][0]
            path = resolve_managed_path(attachment['relative_path'])
            original_attachment = path.read_bytes()
            path.write_bytes(b'damaged after backup')
            with db.atomic_write() as connection:
                connection.execute("UPDATE qc_report_archive_files SET archive_bytes=x'31' WHERE export_id=?", (export_id,))
            with self.assertRaisesRegex(ValueError, '校验不一致'):
                archives.read_report_archive(export_id)
            restore_database_from_backup_file(backup.target_path)
            self.assertEqual(snapshot, database_contents())
            self.assertEqual(original, archives.read_report_archive(export_id))
            self.assertEqual(original_pdf, read_report_history_pdf(export_id))
            self.assertEqual(original_attachment, path.read_bytes())
            with db.read_snapshot() as connection:
                self.assertEqual([], connection.execute('PRAGMA foreign_key_check').fetchall())


if __name__ == '__main__':
    unittest.main(verbosity=2)
