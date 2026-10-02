"""B1 end-to-end service acceptance on fresh, real IgG configurations.

Uses application-created results and unchanged LJ/Z algorithms; output is an
isolated acceptance database, immutable PDFs, renders and a machine-readable log.
Never reuse an existing output directory containing acceptance.db.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run(output: Path):
    import database as db
    from pypdf import PdfReader
    from tests.b1_business_chain import seed
    from services import out_of_control_service as handling
    from services import out_of_control_attachment_service as attachments
    from services.out_of_control_report_service import generate_event_report, read_event_report, list_event_reports
    from services import report_service as reports
    from services import storage_service as storage

    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    original_globals = {key: getattr(db, key) for key in ('DB_PATH', 'DEFAULT_DB_PATH', 'STORAGE_CONFIG_PATH', 'LEGACY_DB_CANDIDATES')}
    fixture = seed(output)
    events, monthly, pdfs, checkpoints = [], [], [], []
    pdf_dir = output / 'pdf'
    pdf_dir.mkdir()
    summary_file = output / 'integrated-results.json'

    def record(name, **details):
        checkpoints.append({'case': name, **details})
        summary_file.write_text(json.dumps({'checkpoints': checkpoints, 'events': events, 'monthly': monthly,
                                          'pdfs': pdfs}, ensure_ascii=False, indent=2))
        print(name, flush=True)

    def source_data():
        with db.get_connection() as connection:
            return {table: [dict(row) for row in connection.execute(f'SELECT * FROM {table} ORDER BY id')]
                    for table in ('results', 'zscore_runs', 'zscore_level_results', 'qc_result_contexts',
                                  'qc_result_context_levels', 'qc_result_evaluations', 'qc_target_profiles')}

    baseline = source_data()
    baseline_hash = sha256(json.dumps(baseline, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

    def unchanged(stage):
        after = source_data()
        if after != baseline:
            different = [table for table in baseline if baseline[table] != after[table]]
            (output / 'source-difference.json').write_text(json.dumps(
                {'stage': stage, 'tables': different, 'before': baseline, 'after': after}, ensure_ascii=False, indent=2))
            raise AssertionError(f'Original source changed at {stage}: {different}')
        record(stage, source_sha256=baseline_hash, original_values_evaluations_and_contexts_unchanged=True)

    def save(event, action, request, content=None, change_reason=''):
        return handling.save_handling(event['event_id'], event['revision_no'], request, action,
                                      content if content is not None else event['content'], '隔离验收处理人', change_reason)

    def complete(event, prefix):
        pending = save(event, 'submit', prefix + '-submit')
        confirmation = {**pending['content'], 'confirmer_text': '隔离验收确认人',
                        'confirmed_at': handling._now(), 'confirmation_checked': True}
        completed = save(pending, 'confirm', prefix + '-confirm', confirmation)
        assert completed['status'] == 'completed'
        assert completed['original_classification'] == 'reject'
        return completed

    def store_pdf(name, data, expected):
        path = pdf_dir / name
        path.write_bytes(data)
        reader = PdfReader(BytesIO(data))
        text = '\n'.join(page.extract_text() for page in reader.pages)
        for label in expected:
            assert ''.join(label.split()) in ''.join(text.split()), (name, label)
        for page in reader.pages:
            assert abs(float(page.mediabox.width) - 595.28) < 2
            assert abs(float(page.mediabox.height) - 841.89) < 2
        pdfs.append({'path': str(path), 'pages': len(reader.pages), 'sha256': sha256(data).hexdigest()})
        return path

    try:
        unopened_before = handling.list_pending()['pending_count']
        for index, original in enumerate([row for row in fixture['sources'] if row['position'] == 0], 1):
            source_type, source_id = original['source_type'], original['source_id']
            event = handling.open_event(source_type, source_id, f'integrated-open-{index}', '隔离验收登记人')
            assert event['origin_snapshot']['test_item_name'] == '免疫球蛋白G（IgG）' or '免疫球蛋白' in event['origin_snapshot']['test_item_name']
            assert len(event['origin_snapshot']['levels']) == original['level_count']
            assert event['origin_snapshot']['quality_snapshot']
            snapshot = deepcopy(event['origin_snapshot'])
            content = {**event['content'], 'cause_category': '质控品',
                       'cause_analysis': '隔离验收：经核对本次异常及材料处理过程，记录调查依据。',
                       'corrective_action': '隔离验收：核对操作记录并执行两次后续复测。',
                       'handler_text': '隔离验收处理人', 'effect_description': '人工对照原异常、两次后续检测和调查记录。',
                       'effect_evidence': '保存的完整检测记录、材料批号及附件清单。',
                       'patient_impact_assessment': '无需评估', 'patient_impact_scope': '合成验收数据，无患者明细',
                       'patient_impact_start': '2026-09-28 08:00:00', 'patient_impact_end': '2026-09-28 10:00:00',
                       'patient_impact_actions': '本次仅核验软件记录流程。', 'patient_impact_basis': '全部检测值来自隔离工程样例。'}
            event = save(event, 'save_draft', f'integrated-draft-{index}', content)
            assert handling.get_event(event['event_id'])['content']['cause_analysis'] == content['cause_analysis']
            candidates = handling.list_retest_candidates(event['event_id'])
            expected_retests = [row for row in fixture['sources']
                                if row['method'] == original['method'] and row['level_count'] == original['level_count']
                                and row['position'] in (1, 2)]
            refs = []
            for retest in expected_retests:
                candidate = next(row for row in candidates if row['source_type'] == retest['source_type'] and row['source_id'] == retest['source_id'])
                assert len(candidate['snapshot']['levels']) == original['level_count']
                refs.append({'source_type': retest['source_type'], 'source_id': retest['source_id'],
                             'difference_reason': '材料、参数及记录差异已对照原资料核对。' if candidate['differences'] else ''})
            assert len(refs) == 2
            attachment_data = f'验收项目,原检测,资料说明\nIgG-{index},{source_id},原材料与复测核对\n'.encode()
            attached = attachments.stage_attachment(event['event_id'], f'IgG-{index}-调查记录.csv', attachment_data,
                                                     uploaded_by='隔离验收处理人', description='本事件的原材料和复测调查记录')
            content = {**event['content'], 'retest_refs': refs, 'attachment_ids': [attached['attachment_id']]}
            event = save(event, 'save_draft', f'integrated-link-{index}', content)
            assert event['status'] == 'in_progress' and len(event['retest_refs']) == 2
            event = complete(event, f'integrated-first-{index}')
            report = generate_event_report(event['event_id'], event['revision_no'])
            parameter_label = f"参数版本：{snapshot['target_profile']['version_no']}"
            store_pdf(f'IgG-{index}-completed.pdf', report['pdf_bytes'], ['免疫球蛋白', '复测1', '复测2', '已完成', attached['original_name'], parameter_label])
            assert generate_event_report(event['event_id'], event['revision_no'])['report_id'] == report['report_id']
            original_revision = event['revision_no']
            event = save(event, 'revise', f'integrated-revise-{index}', change_reason='补充调查结论的证据说明。')
            assert event['status'] == 'in_progress'
            assert handling.get_event(event['event_id'], original_revision)['status'] == 'completed'
            event = save(event, 'save_draft', f'integrated-supplement-{index}',
                         {**event['content'], 'supplementary_note': '补充记录：复测材料与原记录再次核对，原报告继续保留。'})
            event = complete(event, f'integrated-second-{index}')
            revised_report = generate_event_report(event['event_id'], event['revision_no'])
            store_pdf(f'IgG-{index}-revised.pdf', revised_report['pdf_bytes'], ['补充记录', '复测1', '复测2', '已完成', parameter_label])
            assert revised_report['report_id'] != report['report_id']
            assert read_event_report(report['report_id']) == report['pdf_bytes']
            assert event['origin_snapshot'] == snapshot
            assert attachments.read_attachment(event['event_id'], attached['attachment_id'], original_revision) == attachment_data
            events.append({'event_id': event['event_id'], 'source_type': source_type, 'source_id': source_id,
                           'levels': original['level_count'], 'original_revision': original_revision,
                           'current_revision': event['revision_no'], 'retest_ids': [ref['source_id'] for ref in refs],
                           'attachment_id': attached['attachment_id'], 'attachment_sha256': sha256(attachment_data).hexdigest(),
                           'report_id': report['report_id'], 'report_sha256': report['sha256'],
                           'revised_report_id': revised_report['report_id'], 'revised_report_sha256': revised_report['sha256']})
            unchanged(f'真实IgG {original["method"]} {original["level_count"]}水平完整处理、两次复测、附件及报告修订')
        assert handling.list_pending()['pending_count'] == unopened_before - 3
        assert len(list_event_reports()) == 6
        for index, binding in enumerate(fixture['bindings'], 1):
            is_lj = binding['qc_method'] == 'lj'
            build = reports.build_lj_monthly_report_package if is_lj else reports.build_zscore_monthly_report_package
            render = reports.build_lj_monthly_report_pdf if is_lj else reports.build_zscore_monthly_report_pdf
            archive = reports.save_lj_monthly_report_snapshot if is_lj else reports.save_zscore_monthly_report_snapshot
            package = build(binding['runtime_batch_id'], '2026-09')
            assert package.report.statistics.formal_count == 4
            assert len(package.report.handling_summaries) == 1
            assert package.report.handling_summaries[0]['status'] == 'completed'
            pdf_bytes = render(package)
            export_id = archive(package, pdf_bytes)
            path = store_pdf(f'IgG-{index}-monthly.pdf', pdf_bytes, ['免疫球蛋白', '处理', '已完成'])
            assert reports.read_report_history_pdf(export_id) == pdf_bytes
            monthly.append({'export_id': export_id, 'batch_id': binding['runtime_batch_id'], 'method': binding['qc_method'],
                            'formal_count': 4, 'handling_count': 1, 'path': str(path), 'sha256': sha256(pdf_bytes).hexdigest()})
        unchanged('三份月报引用当前完成版本，Z按完整检测计数，原检测及判读不变')

        def verify_archives(stage):
            for event in events:
                assert handling.get_event(event['event_id'])['revision_no'] == event['current_revision']
                assert handling.get_event(event['event_id'], event['original_revision'])['status'] == 'completed'
                assert sha256(read_event_report(event['report_id'])).hexdigest() == event['report_sha256']
                assert sha256(read_event_report(event['revised_report_id'])).hexdigest() == event['revised_report_sha256']
                assert sha256(attachments.read_attachment(event['event_id'], event['attachment_id'], event['original_revision'])).hexdigest() == event['attachment_sha256']
            for item in monthly:
                assert sha256(reports.read_report_history_pdf(item['export_id'])).hexdigest() == item['sha256']
            unchanged(stage)

        backup = storage.create_database_backup(output / 'backups')
        assert backup.target_path.suffix == '.zip'
        assert storage.validate_backup_file(backup.target_path)[0]
        with zipfile.ZipFile(backup.target_path) as archive:
            manifest = json.loads(archive.read('manifest.json'))
            assert len(manifest['files']) == 3
            assert 'data.db' in archive.namelist()
        record('完整ZIP已包含数据库和三份事件附件，数据库中含六份事件PDF和三份月报', backup=str(backup.target_path))
        # Repair a deliberately missing test file using the complete backup;
        # originals and QC results are never altered as part of this failure case.
        first_file = attachments.list_attachments(events[0]['event_id'])[0]
        attachments.resolve_managed_path(first_file['relative_path']).unlink()
        db.save_app_settings({'lab_name': '恢复前的隔离变更'})
        restored = storage.restore_database_from_backup_file(backup.target_path)
        assert restored.protection_backup_path.exists()
        verify_archives('完整ZIP恢复后原报告字节、历史版本、附件及原检测一致')
        moved_dir = output / 'migrated'
        moved_dir.mkdir()
        moved = storage.migrate_database_to_directory(moved_dir)
        db.refresh_db_path_from_config()
        assert db.get_db_path().resolve() == moved.target_path.resolve()
        db.init_db()
        verify_archives('位置迁移和重新初始化后全部历史与文件可读')

        renderer = shutil.which('pdftoppm')
        assert renderer, 'PDF rendering requires pdftoppm'
        render_dir = output / 'renders'
        render_dir.mkdir()
        rendered = 0
        for item in pdfs:
            path = Path(item['path'])
            prefix = render_dir / path.stem
            subprocess.run([renderer, '-r', '85', '-png', str(path), str(prefix)], check=True, capture_output=True)
            count = len(list(render_dir.glob(path.stem + '-*.png')))
            assert count == item['pages'], (path.name, count, item['pages'])
            rendered += count
        record('九份PDF全部页面已渲染供视觉核验', documents=len(pdfs), rendered_pages=rendered,
               renders=str(render_dir), visual_review='待独立查看渲染图，不以文本提取代替')
        record('B1同一真实配置服务链全部通过', final_database=str(db.get_db_path()),
               source_sha256=baseline_hash, event_count=len(events), event_report_count=6, monthly_report_count=3,
               full_zip_restore=True, complete_migration=True, original_source_unchanged=True)
        return json.loads(summary_file.read_text())
    finally:
        for key, value in original_globals.items():
            setattr(db, key, value)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    run(parser.parse_args().output)
