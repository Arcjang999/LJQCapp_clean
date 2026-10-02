"""Fresh 20-item daily workflow through handling, original reports and full restore."""
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


def run(output):
    import database as db
    from pypdf import PdfReader
    from tests.b2_business_chain import seed
    from services import daily_context_service as contexts, daily_entry_service as entry
    from services import daily_draft_service as drafts, daily_result_io_service as files
    from services.daily_overview_service import get_daily_overview
    from services import out_of_control_service as handling, out_of_control_attachment_service as attachments
    from services.out_of_control_report_service import generate_event_report, read_event_report
    from services import report_service as reports, storage_service as storage

    output = Path(output).resolve()
    original_globals = {key:getattr(db, key) for key in ('DB_PATH','DEFAULT_DB_PATH','STORAGE_CONFIG_PATH','LEGACY_DB_CANDIDATES')}
    checkpoints, saved, event_reports, monthly, documents = [], [], [], [], []
    path = output / 'integrated-results.json'

    def record(case, **details):
        checkpoints.append(dict(case=case, **details))
        path.write_text(json.dumps(dict(checkpoints=checkpoints, submissions=saved, events=event_reports,
            monthly=monthly, documents=documents), ensure_ascii=False, indent=2))
        print(case, flush=True)

    def source_data():
        with db.read_snapshot() as c:
            return {table:[dict(row) for row in c.execute(f'SELECT * FROM {table} ORDER BY {order}')]
                for table,order in [('results','id'),('zscore_runs','id'),('zscore_level_results','id'),
                    ('instant_results','id'),('qc_result_contexts','id'),('qc_result_context_levels','id'),
                    ('qc_result_evaluations','id'),('qc_target_profiles','id'),
                    ('qc_daily_submissions','submission_id'),('qc_daily_submission_items','submission_id,row_key')]}

    def document(name, data, required=()):
        target = output / 'pdf' / name
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(data)
        reader = PdfReader(BytesIO(data))
        extracted = ''.join(''.join(page.extract_text().split()) for page in reader.pages)
        for term in required:
            assert ''.join(term.split()) in extracted, (name, term)
        assert reader.pages
        documents.append(dict(path=str(target), pages=len(reader.pages), sha256=sha256(data).hexdigest()))

    try:
        sample = seed(output)
        f = sample['fixture']
        initial = get_daily_overview('2026-09-28', template_id=f['template_id'])
        assert len(initial['items']) == 20
        by_batch = {row['runtime_batch_id']:row for row in initial['items'] if row['qc_method'] == 'instant'}
        for case in sample['instant_stage_samples']:
            item = by_batch[case['batch_id']]
            assert item['count'] == case['count']
            assert item['instant_summary']['effective_count'] == case['count']
        assert initial['missing_rate'] is None and initial['expected_count'] is None
        record('20项工程配置及即时法2、3、20点阶段按原规则读取', initial_count=initial['count'],
               instant_samples=sample['instant_stage_samples'])

        def context(when):
            return contexts.get_daily_context(f['data']['lab_instrument_id'], f['data']['qc_material_id'],
                f['main_lot'], when, lot_config_id=f['config_id'])

        receipts = []
        for index,when in enumerate(('2026-09-28 08:00:00','2026-09-28 09:00:00','2026-09-28 10:00:00')):
            ctx = context(when)
            assert len(ctx['items']) == 20 and all(row['writable'] for row in ctx['items'])
            draft = drafts.new_draft(ctx, operator='B2独立整链验收')
            draft['submission_id'] = f'integrated-group-{index+1}'
            for row in drafts.draft_rows(draft):
                item,level = row['item'],row['level']
                offset = 15 if index == 0 and item['target_profile'] and item['qc_method'] != 'instant' else 0
                draft['values'][row['key']] = str(100+level['level_order']*10+offset)
                draft['notes'][item['row_key']] = f'第{index+1}轮完整检测记录'
            original_draft = deepcopy(draft)
            workbook = files.export_daily_workbook(draft)
            (output / f'round-{index+1}-routine.xlsx').write_bytes(workbook)
            before = source_data()
            blank = drafts.new_draft(ctx)
            preview = files.preview_daily_workbook(workbook, blank)
            assert preview['valid'], preview['errors']
            assert len(preview['rows']) == len(drafts.draft_rows(draft))
            files.apply_daily_workbook(blank, preview)
            assert source_data() == before
            assert blank['values'] == draft['values']
            assert blank['notes'] == draft['notes'] and blank['reagents'] == draft['reagents']
            assert blank['operator'] == draft['operator'] and blank['test_time'] == when
            blank['submission_id'] = draft['submission_id']
            precheck = entry.validate_submission(drafts.build_request(blank))
            assert precheck['valid'], precheck['errors']
            assert draft == original_draft
            receipt = entry.submit_daily(precheck['frozen_request'])
            assert receipt['count'] == 20
            assert source_data() != before
            stable = source_data()
            assert receipt == entry.submit_daily(precheck['frozen_request'])
            assert stable == source_data()
            assert receipt == entry.get_submission(draft['submission_id'])
            for row in receipt['items']:
                assert len(row['levels']) == row['level_count']
                if row['qc_method'] == 'zscore':
                    assert len({level['qc_material_lot_id'] for level in row['levels']}) == row['level_count']
                assert not row['conclusion'].isascii()
            receipts.append(receipt)
            saved.append(dict(submission_id=receipt['submission_id'],count=20,
                result_keys=[(r['source_type'],r['source_id']) for r in receipt['items']],
                frozen_receipt_sha256=sha256(json.dumps(receipt,sort_keys=True,ensure_ascii=False).encode()).hexdigest()))
            record(f'第{index+1}轮20项完整保存、表格交换和同请求防重通过',
                   workbook_rows=len(preview['rows']), saved_count=20, test_time=when)

        baseline = source_data()
        baseline_hash = sha256(json.dumps(baseline,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        overview = get_daily_overview('2026-09-28', template_id=f['template_id'])
        assert overview['count'] == initial['count']+60
        assert baseline == source_data()
        first = receipts[0]
        rejects = [r for r in first['items'] if r['classification'] == 'reject']
        assert {(r['qc_method'],r['level_count']) for r in rejects} == {('lj',1),('zscore',2),('zscore',3)}
        by_key = {r['lot_config_item_id']:r for r in overview['items']}
        for row in rejects:
            item = by_key[row['lot_config_item_id']]
            assert item['ever_reject'] and item['latest']['classification'] == 'accept'
        record('总览20项102次检测按完整run计数；曾失控与最新在控并存，查询无写入',
               count=overview['count'], source_sha256=baseline_hash)

        for index,row in enumerate(rejects,1):
            event = handling.open_event(row['source_type'],row['source_id'],f'b2-open-{index}','独立验收')
            origin = deepcopy(event['origin_snapshot'])
            candidates = handling.list_retest_candidates(event['event_id'])
            refs = []
            for receipt in receipts[1:]:
                retest = next(r for r in receipt['items'] if r['row_key'] == row['row_key'])
                candidate = next(c for c in candidates if c['source_type'] == retest['source_type'] and c['source_id'] == retest['source_id'])
                refs.append(dict(source_type=retest['source_type'],source_id=retest['source_id'],
                    difference_reason='逐项核对本次材料与参数。' if candidate['differences'] else ''))
            data = f'检测项,轮次,资料说明\n{row["test_item_name"]},1,真实软件链工程输入\n'.encode()
            attachment = attachments.stage_attachment(event['event_id'],f'调查-{index}.csv',data,uploaded_by='独立验收',description='本次检测调查资料')
            content = {**event['content'],'cause_category':'质控品','cause_analysis':'核对本次工程检测和材料处理记录。',
                'corrective_action':'记录处理后两次完整复测。','handler_text':'独立验收',
                'effect_description':'两次后续完整检测已核对，原异常仍保留。','effect_evidence':'检测记录与调查附件。',
                'patient_impact_assessment':'无需评估','patient_impact_basis':'全部数据为隔离工程样例。',
                'retest_refs':refs,'attachment_ids':[attachment['attachment_id']]}
            def save(event,action,label,content=None):
                return handling.save_handling(event['event_id'],event['revision_no'],f'b2-{index}-{label}',action,
                    content if content is not None else event['content'],'独立验收')
            event = save(event,'save_draft','draft',content)
            event = save(event,'submit','submit')
            event = save(event,'confirm','confirm',{**event['content'],'confirmer_text':'独立确认',
                'confirmed_at':handling._now(),'confirmation_checked':True})
            assert event['status'] == 'completed' and event['origin_snapshot'] == origin
            report = generate_event_report(event['event_id'],event['revision_no'])
            document(f'handling-{index}.pdf',report['pdf_bytes'],['已完成','复测1','复测2',row['test_item_name']])
            event_reports.append(dict(event_id=event['event_id'],revision_no=event['revision_no'],
                report_id=report['report_id'],sha256=report['sha256'],attachment_id=attachment['attachment_id'],
                attachment_sha256=sha256(data).hexdigest()))
            lj = row['qc_method'] == 'lj'
            package = (reports.build_lj_monthly_report_package if lj else reports.build_zscore_monthly_report_package)(row['runtime_batch_id'],'2026-09')
            assert package.report.statistics.formal_count == 4
            assert any(r['event_id'] == event['event_id'] for r in package.report.handling_summaries)
            pdf = (reports.build_lj_monthly_report_pdf if lj else reports.build_zscore_monthly_report_pdf)(package)
            export = (reports.save_lj_monthly_report_snapshot if lj else reports.save_zscore_monthly_report_snapshot)(package,pdf)
            assert reports.read_report_history_pdf(export) == pdf
            document(f'monthly-{index}.pdf',pdf,[row['test_item_name'],'已完成'])
            monthly.append(dict(export_id=export,method=row['qc_method'],level_count=row['level_count'],sha256=sha256(pdf).hexdigest()))
            assert baseline == source_data()
        record('日常录入正式LJ/Z2/Z3失控接两次复测、附件、完成及六份原报告；原检测与判读未改',
               source_sha256=baseline_hash, event_count=3)

        expected_context = context('2026-09-28 12:00:00')
        def verify(stage):
            assert baseline == source_data()
            assert expected_context == context('2026-09-28 12:00:00')
            for receipt in receipts:
                assert entry.get_submission(receipt['submission_id']) == receipt
            for event in event_reports:
                assert handling.get_event(event['event_id'])['status'] == 'completed'
                assert sha256(read_event_report(event['report_id'])).hexdigest() == event['sha256']
                assert sha256(attachments.read_attachment(event['event_id'],event['attachment_id'],event['revision_no'])).hexdigest() == event['attachment_sha256']
            for report in monthly:
                assert sha256(reports.read_report_history_pdf(report['export_id'])).hexdigest() == report['sha256']
            record(stage, source_sha256=baseline_hash, original_records_and_frozen_receipts_unchanged=True)

        backup = storage.create_database_backup(output/'backups')
        assert storage.validate_backup_file(backup.target_path)[0]
        with zipfile.ZipFile(backup.target_path) as archive:
            manifest = json.loads(archive.read('manifest.json'))
            assert len(manifest['files']) == 3
        attachment_row = attachments.list_attachments(event_reports[0]['event_id'])[0]
        attachments.resolve_managed_path(attachment_row['relative_path']).unlink()
        restored = storage.restore_database_from_backup_file(backup.target_path)
        assert restored.protection_backup_path.exists()
        verify('完整ZIP恢复后三份附件、六份报告、三轮回执及20项真实材料关系一致')
        target = output/'migrated'; target.mkdir()
        moved = storage.migrate_database_to_directory(target)
        db.refresh_db_path_from_config(); db.init_db()
        assert db.get_db_path().resolve() == moved.target_path.resolve()
        verify('迁移位置并重新初始化后完整记录、参数、材料与原报告一致')

        renderer = shutil.which('pdftoppm')
        assert renderer
        renders = output/'renders'; renders.mkdir()
        for item in documents:
            pdf = Path(item['path'])
            subprocess.run([renderer,'-r','85','-png',str(pdf),str(renders/pdf.stem)],check=True,capture_output=True)
            assert len(list(renders.glob(pdf.stem+'-*.png'))) == item['pages']
        record('B2完整服务链通过，报告全部页面已渲染待视觉检查', final_database=str(db.get_db_path()),
               backup=str(backup.target_path), documents=len(documents), pages=sum(r['pages'] for r in documents),
               full_zip_restore=True, complete_migration=True, visual_review='pending')
        return json.loads(path.read_text())
    finally:
        for key,value in original_globals.items():setattr(db,key,value)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--output',type=Path,required=True)
    run(parser.parse_args().output)
