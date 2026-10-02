"""T1-10/11 isolated archive, pagination, version and monthly evidence checks."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json
import re
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

import pypdf
import database
from report_history_smoke_test import _seed_lj_report_snapshot, _seed_zscore_report_snapshot
from services.out_of_control_attachment_service import stage_attachment, link_revision_attachments
from services.out_of_control_report_service import generate_event_report, list_event_reports, read_event_report
from services.report_service import (
    build_lj_monthly_report_package, build_lj_monthly_report_pdf, save_lj_monthly_report_snapshot,
    build_zscore_monthly_report_package, list_report_history_records, filter_report_history_records,
    read_report_history_pdf, REPORT_TYPE_EVENT,
)
from report_pdf_assertions import assert_uniform_a4_pages_without_watermark

OUT = ROOT / 'output/execution/B1/reports'


def _snapshot(source_type, source_id, project_id=1, batch_id=1, *, level_count=3):
    return dict(source_type=source_type, source_id=source_id, project_id=project_id, batch_id=batch_id,
        qc_method='lj' if source_type == 'lj_result' else 'zscore', record_type='routine',
        project_name='报告验收项目', test_item_name='免疫球蛋白', instrument_name='验收仪器甲',
        test_time='2026-04-02 08:00:00', input_value_type='raw', unit_symbol='g/L',
        method_name='免疫比浊', classification='reject', rule_names=['1_3s'], manual_note='旧手动备注',
        context={'reagent_lot_no': '试剂原批号'}, target_profile={'version_no': 2, 'evidence': '原参数依据'},
        quality_snapshot={'quality_goal_json': {'spec': {'name': '原采用项目', 'standard': '原标准',
            'version': '2024', 'source_clause': '第5条', 'source_page': '7'},
            'unit': 'g/L', 'evidence': '原质量依据', 'confirmed_by': '质量确认人', 'adopted_at': '2026-03-01',
            'levels': [{'level_order': 1, 'concentration': 10, 'category': '正常',
                        'rule': {'kind':'cv','operator':'<=','value':5,'unit':'%'}}]}},
        levels=[{'level_id':f'Level {i}', 'level_order':i, 'level_name':f'验收水平{i}',
            'value':10*i, 'log_value':None, 'classification':'reject' if i == level_count else 'accept',
            'rule_names':['1_3s'] if i == level_count else [], 'lot_no':f'原质控批号{i}',
            'target_mean':8*i, 'target_sd':1.1*i} for i in range(1,level_count+1)], missing_fields=[])


def _insert_event(source_type, source_id, *, snapshot=None, status='completed', long=False):
    snapshot = snapshot or _snapshot(source_type, source_id)
    content = dict(cause_category='仪器', cause_analysis=('长原因逐页验收。' * 200 if long else '短原因分析'),
        corrective_action='完成纠正措施', effect_description='人工核对处理效果', effect_evidence='复测及维护记录',
        handler_text='处理甲', handled_at='2026-04-02 10:00:00', confirmer_text='确认乙',
        confirmed_at='2026-04-02 11:00:00', patient_impact_assessment='需要评估',
        patient_impact_start='2026-04-02 07:00:00', patient_impact_end='2026-04-02 08:30:00',
        patient_impact_scope='当日范围', patient_impact_actions='由实验室复核', patient_impact_basis='质控调查依据')
    with database.atomic_write() as c:
        event_id = c.execute('''INSERT INTO qc_ooc_events(source_type,source_id,qc_method,original_classification,
            origin_snapshot_json,current_revision_no,opened_by,opened_at) VALUES(?,?,?,?,?,3,?,?)''',
            (source_type,source_id,snapshot['qc_method'],'reject',json.dumps(snapshot,ensure_ascii=False),'登记人','2026-04-02 08:01:00')).lastrowid
        for number, state in ((1,'pending'),(2,'in_progress'),(3,status)):
            c.execute('''INSERT INTO qc_ooc_revisions(event_id,revision_no,previous_revision_no,status,action,
                content_json,saved_by,saved_at) VALUES(?,?,?,?,?,?,?,?)''',
                (event_id,number,number-1 or None,state,'fixture',json.dumps(content,ensure_ascii=False),'处理甲','2026-04-02 11:00:00'))
        if long:
            for number in range(10):
                followup=deepcopy(snapshot)
                followup.update(test_time=f'2026-04-{number+3:02d} 08:00:00',classification='accept')
                c.execute('''INSERT INTO qc_ooc_retests(event_id,revision_no,source_type,source_id,difference_reason,snapshot_json,differences_json)
                    VALUES(?,3,?,?,?,?,?)''',(event_id,source_type,900+number,'批号差异经核对',json.dumps(followup,ensure_ascii=False),'[]'))
    return int(event_id)


def run():
    OUT.mkdir(parents=True, exist_ok=True)
    original=database.DB_PATH
    legacy=database.LEGACY_DB_CANDIDATES
    with TemporaryDirectory(prefix='qc-report-test-') as temporary:
        database.DB_PATH=Path(temporary)/'reports.db'
        database.LEGACY_DB_CANDIDATES=[]
        try:
            database.init_db()
            event_id=_insert_event('zscore_run',777,long=True)
            ids=[]
            for i in range(15):
                attachment=stage_attachment(event_id, f'附件{i+1:02d}_'+('长中文附件名称'*8)+'.csv',
                    f'项目,结果\n验收,{i}\n'.encode(), uploaded_by='上传人', description=f'附件说明{i+1:02d}')
                ids.append(attachment['attachment_id'])
            with database.atomic_write() as c:
                link_revision_attachments(c,event_id,3,ids)
            result=generate_event_report(event_id,3)
            reader=assert_uniform_a4_pages_without_watermark(result['pdf_bytes'])
            (OUT/'event-long-v3.pdf').write_bytes(result['pdf_bytes'])
            text='\n'.join(page.extract_text() for page in reader.pages)
            assert len(reader.pages)>=5
            assert '参数版本：2' in re.sub(r'\s+', '', text)
            for needle in ['验收水平1','验收水平2','验收水平3','原质控批号3','原标准','2024','CV',
                           '附件说明15','复测10','原手动备注','患者结果影响评估','确认乙','长原因逐页验收']:
                assert needle in text,needle
            body=''.join(page.extract_text().split('失控处理报告')[0].replace('原因分析与纠正措施（续）','') for page in reader.pages)
            assert re.sub(r'\s+', '', body).count('长原因逐页验收') == 200
            for i,page in enumerate(reader.pages,1):
                assert f'第 {i}/{len(reader.pages)} 页' in page.extract_text()
            (OUT/'event-long-v3.pdf').write_bytes(result['pdf_bytes'])
            assert generate_event_report(event_id,3)['report_id']==result['report_id']
            assert len(list_event_reports(event_id))==1
            assert read_event_report(result['report_id'])==result['pdf_bytes']
            before=sha256(result['pdf_bytes']).hexdigest()
            with database.atomic_write() as c:
                content=json.loads(c.execute('SELECT content_json FROM qc_ooc_revisions WHERE event_id=? AND revision_no=3',(event_id,)).fetchone()[0])
                content['cause_analysis']='修订后的原因'
                c.execute('''INSERT INTO qc_ooc_revisions(event_id,revision_no,previous_revision_no,status,action,content_json,saved_by,saved_at)
                    VALUES(?,4,3,'in_progress','revise',?,'处理甲','2026-04-12 12:00:00')''',(event_id,json.dumps(content,ensure_ascii=False)))
                c.execute('UPDATE qc_ooc_events SET current_revision_no=4 WHERE id=?',(event_id,))
            revised=generate_event_report(event_id)
            assert revised['revision_no']==4 and revised['report_id']!=result['report_id']
            assert sha256(read_event_report(result['report_id'])).hexdigest()==before
            assert '尚未完成处理' in ''.join(p.extract_text() for p in pypdf.PdfReader(BytesIO(revised['pdf_bytes'])).pages)
            (OUT/'event-draft-v4.pdf').write_bytes(revised['pdf_bytes'])
            count=len(list_event_reports())
            failure_event=_insert_event('lj_result',888,snapshot=_snapshot('lj_result',888,level_count=1))
            with patch('services.out_of_control_report_service.render_event_report_pdf',side_effect=OSError('injected render failure')):
                try: generate_event_report(failure_event)
                except OSError: pass
                else: raise AssertionError('render failure must propagate')
            assert len(list_event_reports())==count
            with database.atomic_write() as c:
                c.execute("CREATE TRIGGER fail_report BEFORE INSERT ON qc_event_reports BEGIN SELECT RAISE(ABORT,'injected write failure'); END")
            try: generate_event_report(failure_event)
            except sqlite3.DatabaseError: pass
            else: raise AssertionError('write failure must propagate')
            assert len(list_event_reports())==count
            with database.atomic_write() as c: c.execute('DROP TRIGGER fail_report')
            # Monthly result sets are produced by the pre-existing algorithms.
            _,pid,bid=_seed_lj_report_snapshot()
            original_month=build_lj_monthly_report_package(bid,'2026-04')
            formal_ids=original_month.formal_df['id'].tolist()
            first=_insert_event('lj_result',int(formal_ids[1]),snapshot=_snapshot('lj_result',int(formal_ids[1]),pid,bid,level_count=1))
            second=_insert_event('lj_result',int(formal_ids[2]),snapshot=_snapshot('lj_result',int(formal_ids[2]),pid,bid,level_count=1),status='in_progress')
            generate_event_report(first)
            monthly=build_lj_monthly_report_package(bid,'2026-04')
            assert monthly.report.statistics == original_month.report.statistics
            assert len(monthly.report.handling_summaries)==2
            assert {row['status'] for row in monthly.report.handling_summaries}=={'completed','in_progress'}
            monthly_pdf=build_lj_monthly_report_pdf(monthly)
            archived=save_lj_monthly_report_snapshot(monthly,monthly_pdf)
            (OUT/'monthly-with-handling.pdf').write_bytes(monthly_pdf)
            assert read_report_history_pdf(archived)==monthly_pdf
            # A later lab edit cannot rewrite the original PDF or summary snapshot.
            database.save_app_settings({'lab_name':'后来实验室'})
            assert read_report_history_pdf(archived)==monthly_pdf
            _,zpid,zbid=_seed_zscore_report_snapshot()
            with database.get_connection() as c: rid=c.execute('SELECT id FROM zscore_runs WHERE batch_id=? ORDER BY id DESC',(zbid,)).fetchone()[0]
            _insert_event('zscore_run',rid,snapshot=_snapshot('zscore_run',rid,zpid,zbid,level_count=2))
            zmonthly=build_zscore_monthly_report_package(zbid,'2026-04')
            assert len(zmonthly.report.handling_summaries)==1
            records=list_report_history_records()
            filtered=filter_report_history_records(records,report_type=REPORT_TYPE_EVENT,instrument_query='验收仪器甲')
            assert len(filtered)==3
            assert all(record.report_month=='' for record in filtered)
            # A complete database copy restores original PDF bytes.
            backup=Path(temporary)/'backup.db'
            with database.get_connection() as c, sqlite3.connect(backup) as dest: c.backup(dest)
            database.DB_PATH=backup
            assert read_event_report(result['report_id'])==result['pdf_bytes']
            assert read_report_history_pdf(archived)==monthly_pdf
            with database.atomic_write() as c: c.execute("UPDATE qc_event_reports SET pdf_bytes=x'31' WHERE id=?",(result['report_id'],))
            try: read_event_report(result['report_id'])
            except ValueError: pass
            else: raise AssertionError('corrupt PDF must not be regenerated')
            with database.atomic_write() as c: c.execute('DELETE FROM report_export_files WHERE export_id=?',(archived,))
            try: read_report_history_pdf(archived)
            except ValueError: pass
            else: raise AssertionError('missing PDF must not be regenerated')
            summary={'long_pages':len(reader.pages),'idempotency':True,'old_pdf_preserved':True,
                'failure_without_history':True,'monthly_counts_unchanged':True,'z_event_once':True,
                'backup_pdf_identical':True,'missing_corrupt_rejected':True}
            (OUT/'verification.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
            print(json.dumps(summary,ensure_ascii=False))
        finally:
            database.DB_PATH=original
            database.LEGACY_DB_CANDIDATES=legacy


if __name__=='__main__':run()
