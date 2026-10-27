"""Demo refresh respects archived reports, job dependencies and unrelated data."""
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import json
import sqlite3
import sys
import unittest
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import database as db
from services.demo_data_service import delete_demo_data
from tests.storage_smoke_test import TemporaryStorageContext


TABLES = ('projects', 'instant_projects', 'batches', 'results', 'qc_result_contexts',
          'qc_result_context_levels', 'qc_result_evaluations', 'report_exports',
          'report_export_files', 'qc_report_archive_files', 'qc_monthly_report_jobs',
          'qc_monthly_report_job_items')


def rows():
    with db.read_snapshot() as connection:
        return {table: [tuple(row) for row in connection.execute('SELECT * FROM ' + table + ' ORDER BY rowid')]
                for table in TABLES}


def seed():
    db.init_db()
    records = {}
    for kind, name in (('demo', '【演示】独立清理夹具'), ('real', '正式项目【演示】文字保留')):
        project = db.create_project(name)
        batch = db.create_batch(project_id=project, instrument='隔离仪器', reagent='隔离试剂',
            qc_material='隔离质控品', concentration='S3', lot_no=kind, target_n=5, cv_limit=5)
        result = db.add_result(batch, '2026-04-01 08:00:00', 100, operator='隔离检测人')
        records[kind] = dict(project=project, batch=batch, result=result, exports=[])
        for number in range(3 if kind == 'demo' else 2):
            export = db.create_report_export_snapshot(report_type='lj_monthly', project_id=project,
                batch_id=batch, report_month='2026-04', generated_at='2026-05-01 08:00:00',
                input_value_type='raw', method_label='单水平（L-J法）', summary={}, file_name=f'{kind}-{number}.pdf')
            pdf = b'%PDF-1.4\n' + f'{kind}-{number}'.encode()
            buffer = BytesIO()
            with ZipFile(buffer, 'w') as archive:
                archive.writestr('质控报告.pdf', pdf)
            content = buffer.getvalue()
            with db.get_connection() as connection:
                connection.execute('INSERT INTO report_export_files VALUES (?,?,?)',
                    (export, pdf, sha256(pdf).hexdigest()))
                connection.execute('''INSERT INTO qc_report_archive_files
                    (export_id,request_key,request_digest,archive_name,archive_bytes,sha256,size_bytes,manifest_json)
                    VALUES (?,?,?,?,?,?,?,?)''', (export, f'archive-{kind}-{number}', 'digest',
                        f'{kind}-{number}.zip', content, sha256(content).hexdigest(), len(content), '{}'))
            records[kind]['exports'].append(export)
    db.create_instant_project('【演示】即时法独立清理夹具')
    jobs = {}
    with db.get_connection() as connection:
        for kind, members in (('demo', [('demo', 0)]),
                              ('mixed', [('demo', 1), ('real', 0)]),
                              ('real', [('real', 1)]), ('unrelated_empty', [])):
            job = connection.execute('''INSERT INTO qc_monthly_report_jobs
                (request_id,request_digest,report_month,period_start,period_end,scope_json,exclusions_json)
                VALUES (?,?,'2026-04','2026-04-01','2026-04-30',?,'[]')''',
                ('job-' + kind, 'digest', json.dumps(dict(kind=kind)))).lastrowid
            jobs[kind] = job
            for owner, number in members:
                record = records[owner]
                connection.execute('''INSERT INTO qc_monthly_report_job_items
                    (job_id,unit_key,qc_method,batch_id,report_month,period_start,period_end,source_fingerprint,selection_json,status,export_id)
                    VALUES (?,?,'lj',?,'2026-04','2026-04-01','2026-04-30','fingerprint','{}','succeeded',?)''',
                    (job, f'{owner}-{number}', record['batch'], record['exports'][number]))
    return records, jobs


class DemoReportCleanupTests(unittest.TestCase):
    def test_dry_run_keeps_demo_and_unrelated_report_bytes(self):
        with TemporaryStorageContext():
            seed()
            before = rows()
            result = delete_demo_data(dry_run=True)
            self.assertEqual(2, result.total_projects)
            self.assertTrue(result.dry_run)
            self.assertEqual(before, rows())

    def test_remove_demo_reports_and_job_items_keeps_all_unrelated_rows(self):
        with TemporaryStorageContext():
            records, jobs = seed()
            before = rows()
            result = delete_demo_data()
            self.assertEqual(2, result.total_projects)
            after = rows()
            demo, real = records['demo'], records['real']
            with db.read_snapshot() as connection:
                self.assertEqual([], connection.execute('PRAGMA foreign_key_check').fetchall())
                self.assertIsNone(connection.execute('SELECT id FROM projects WHERE id=?', (demo['project'],)).fetchone())
                self.assertIsNone(connection.execute('SELECT id FROM qc_monthly_report_jobs WHERE id=?', (jobs['demo'],)).fetchone())
                self.assertEqual(set(real['exports']), {row[0] for row in connection.execute('SELECT export_id FROM qc_report_archive_files')})
            keep = {
                'projects': lambda row: row[0] == real['project'],
                'instant_projects': lambda row: False,
                'batches': lambda row: row[0] == real['batch'],
                'results': lambda row: row[0] == real['result'],
                'report_exports': lambda row: row[0] in real['exports'],
                'report_export_files': lambda row: row[0] in real['exports'],
                'qc_report_archive_files': lambda row: row[0] in real['exports'],
                'qc_monthly_report_jobs': lambda row: row[0] != jobs['demo'],
                'qc_monthly_report_job_items': lambda row: row[4] == real['batch'],
            }
            with db.read_snapshot() as connection:
                context_ids = {row[0] for row in connection.execute('SELECT id FROM qc_result_contexts WHERE lj_result_id=?', (real['result'],))}
            keep['qc_result_contexts'] = lambda row: row[0] in context_ids
            for table in ('qc_result_context_levels', 'qc_result_evaluations'):
                keep[table] = lambda row: row[1] in context_ids
            for table in TABLES:
                self.assertEqual([row for row in before[table] if keep[table](row)], after[table], table)

    def test_late_delete_failure_rolls_back_report_job_and_provenance_deletions(self):
        with TemporaryStorageContext():
            seed()
            with db.get_connection() as connection:
                connection.execute('''CREATE TRIGGER reject_demo_cleanup BEFORE DELETE ON projects
                    WHEN OLD.name LIKE '【演示】%' BEGIN SELECT RAISE(ABORT,'injected cleanup failure'); END''')
            before = rows()
            with self.assertRaisesRegex(sqlite3.IntegrityError, 'injected cleanup failure'):
                delete_demo_data()
            self.assertEqual(before, rows())
            with db.read_snapshot() as connection:
                self.assertEqual([], connection.execute('PRAGMA foreign_key_check').fetchall())


if __name__ == '__main__':
    unittest.main(verbosity=2)
