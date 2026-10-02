"""Whole-backup and relocation evidence for immutable monthly jobs and ZIP files."""
from pathlib import Path
from hashlib import sha256
import argparse
import json
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def run(output):
    import database as db
    from tests.b1_business_chain import seed
    from services import batch_monthly_report_service as svc, storage_service as storage
    original={name:getattr(db,name) for name in ('DB_PATH','DEFAULT_DB_PATH','STORAGE_CONFIG_PATH','LEGACY_DB_CANDIDATES')}
    output=Path(output).resolve()
    try:
        seed(output)
        preview=svc.preview_monthly_reports('2026-09')
        assert len(preview['items']) == 3,preview
        job_id=svc.create_monthly_report_job(preview,[r['unit_key'] for r in preview['items']],'whole-backup-job')
        for item in svc.get_monthly_report_job(job_id)['items']:
            assert svc.run_monthly_report_item(job_id,item['id'])['status'] == 'succeeded'
        job=svc.get_monthly_report_job(job_id)
        summary=svc.monthly_job_summary(job_id)
        pdfs={item['id']:svc.read_monthly_report_item(job_id,item['id'])['pdf_bytes'] for item in job['items']}
        archive=svc.build_monthly_report_zip(job_id)
        (output/'reports-before.zip').write_bytes(archive)
        def source_rows():
            with db.read_snapshot() as connection:
                return {table:[dict(r) for r in connection.execute(f'SELECT * FROM {table} ORDER BY id')]
                    for table in ('results','zscore_runs','zscore_level_results','qc_result_contexts',
                        'qc_result_context_levels','qc_result_evaluations','qc_target_profiles')}
        original_sources=source_rows()
        def verify():
            assert job == svc.get_monthly_report_job(job_id)
            assert summary == svc.monthly_job_summary(job_id)
            assert archive == svc.build_monthly_report_zip(job_id)
            for item_id,pdf in pdfs.items():
                assert pdf == svc.read_monthly_report_item(job_id,item_id)['pdf_bytes']
            assert original_sources == source_rows()
            with db.read_snapshot() as connection:
                assert connection.execute('SELECT COUNT(*) FROM report_exports').fetchone()[0] == 3
                assert connection.execute('SELECT COUNT(*) FROM report_export_files').fetchone()[0] == 3
                assert connection.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
        backup=storage.create_database_backup(output/'backups')
        assert storage.validate_backup_file(backup.target_path)[0]
        # A complete restore replaces a deliberately different test setting,
        # while result and report originals are never edited for this test.
        db.save_app_settings({'lab_name':'恢复前独立测试标记'})
        restored=storage.restore_database_from_backup_file(backup.target_path)
        assert restored.protection_backup_path.exists()
        verify()
        target=output/'migrated';target.mkdir()
        moved=storage.migrate_database_to_directory(target)
        db.refresh_db_path_from_config();db.init_db()
        assert db.get_db_path().resolve() == moved.target_path.resolve()
        verify()
        # A successful item or same request retried after restart is still the
        # same immutable report; it adds no archive and changes no job counter.
        assert svc.create_monthly_report_job(preview,[r['unit_key'] for r in preview['items']],'whole-backup-job') == job_id
        for item in job['items']:
            assert svc.run_monthly_report_item(job_id,item['id'])['status'] == 'succeeded'
        verify()
        (output/'reports-after.zip').write_bytes(svc.build_monthly_report_zip(job_id))
        result=dict(passed=True,job_id=job_id,report_count=3,job_exact=True,summary_exact=True,
            pdf_exact=True,zip_exact=True,zip_sha256=sha256(archive).hexdigest(),
            pdf_sha256={key:sha256(value).hexdigest() for key,value in pdfs.items()},
            source_records_unchanged=True,retry_no_duplicate=True,backup=str(backup.target_path),
            final_database=str(db.get_db_path()))
        (output/'verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return result
    finally:
        for name,value in original.items():setattr(db,name,value)


if __name__ == '__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    run(parser.parse_args().output)
