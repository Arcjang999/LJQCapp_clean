"""Fresh isolated regression for routine batch monthly report archives."""
import csv
import io
import json
import sqlite3
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import database
from database import get_connection, add_result, create_batch
from services import batch_monthly_report_service as svc
from services.report_service import build_lj_monthly_report_package, get_report_history_record
from services.monthly_report_source_service import monthly_source_version
from tests.lj_monthly_report_smoke_test import TemporaryDatabaseContext, seed_lj_batch_with_formal_monthly_data, seed_lj_batch_with_building_only_data
from tests.zscore_monthly_report_smoke_test import seed_zscore_batch_with_formal_monthly_data, seed_three_level_zscore_batch_without_abnormal
from tests.instant_v12_fixtures import seed_instant_configuration


def db_dump():
    with get_connection() as c:
        return '\n'.join(c.iterdump())


def rejects(call, text):
    try:
        call()
    except ValueError as exc:
        assert text in str(exc), str(exc)
    else:
        raise AssertionError('Expected rejection: ' + text)


def job_for(preview, request='first', keys=None):
    return svc.create_monthly_report_job(preview, keys or [r['unit_key'] for r in preview['items']], request)


def seed_mix():
    project, lj = seed_lj_batch_with_formal_monthly_data()
    _, z2 = seed_zscore_batch_with_formal_monthly_data()
    _, z3 = seed_three_level_zscore_batch_without_abnormal()
    seed_lj_batch_with_building_only_data()
    seed_instant_configuration(name='月报即时法', instrument='另一台仪器')
    # The same project can have another actual batch during the same month.
    other = create_batch(project_id=project, instrument='第二台仪器', reagent='CRP 试剂',
        qc_material='CRP 质控品', concentration='L1', lot_no='LOT-SECOND', target_n=5, cv_limit=5.0)
    for index, value in enumerate([100, 100.2, 99.8, 100.1, 99.9, 100]):
        add_result(batch_id=other, test_time=f'2026-04-0{index+1} 10:00:00', operator='工程验收', value=value, log_value=None)
    return lj, z2, z3, other


def test_readonly_scope_filter_empty_month_and_single_equivalence():
    with TemporaryDatabaseContext():
        ids = seed_mix()
        before = db_dump()
        choices = svc.list_monthly_report_choices()
        preview = svc.preview_monthly_reports('2026-04')
        assert db_dump() == before, 'preview must not create evaluations, events, histories or jobs'
        assert len(preview['items']) == 4, preview
        assert len(preview['exclusions']) == 2
        assert any('即时法' in r['reason'] for r in preview['exclusions'])
        assert len({r['instrument_name'] for r in preview['items']}) >= 3
        assert len([r for r in preview['items'] if r['project_name']=='LJ 月报项目']) == 2
        assert not svc.preview_monthly_reports('2027-01')['items']
        for instrument in choices['instruments']:
            filtered = svc.preview_monthly_reports('2026-04', lab_instrument_id=instrument)
            assert all(r['lab_instrument_id'] == instrument for r in filtered['items'] + filtered['exclusions'])
        row = next(r for r in preview['items'] if r['batch_id'] == ids[0])
        report = build_lj_monthly_report_package(ids[0], '2026-04').report
        assert row['formal_count'] == report.statistics.formal_count
        assert row['source_fingerprint'] == report.source_version['fingerprint']
        assert db_dump() == before


def test_partial_failure_retry_idempotency_zip_original_and_restart():
    with TemporaryDatabaseContext():
        seed_lj_batch_with_formal_monthly_data()
        seed_zscore_batch_with_formal_monthly_data()
        seed_three_level_zscore_batch_without_abnormal()
        preview = svc.preview_monthly_reports('2026-04')
        job = job_for(preview)
        assert job_for(preview) == job
        rejects(lambda: job_for(preview, keys=[preview['items'][0]['unit_key']]), '选择已变化')
        items = svc.get_monthly_report_job(job)['items']
        zitems = [r for r in items if r['qc_method']=='zscore']
        original = svc.build_zscore_monthly_report_pdf
        fail_id = zitems[0]['batch_id']
        def fail_one(package):
            if package.report.batch_id == fail_id:
                raise RuntimeError('Injected renderer failure')
            return original(package)
        with patch.object(svc, 'build_zscore_monthly_report_pdf', side_effect=fail_one):
            for item in items:
                svc.run_monthly_report_item(job, item['id'])
        partial = svc.get_monthly_report_job(job)
        assert partial['counts']['succeeded']==2 and partial['counts']['failed']==1
        saved = {r['id']: svc.read_monthly_report_item(job,r['id'])['pdf_bytes'] for r in partial['items'] if r['status']=='succeeded'}
        zip_partial = ZipFile(io.BytesIO(svc.build_monthly_report_zip(job)))
        assert len([n for n in zip_partial.namelist() if n.endswith('.pdf')]) == 2
        assert '未生成' in zip_partial.read('月报清单.csv').decode('utf-8-sig')
        failed = next(r for r in partial['items'] if r['status']=='failed')
        # Simulate a process interruption after the visible generating marker was committed.
        with get_connection() as c:
            c.execute("UPDATE qc_monthly_report_job_items SET status='generating' WHERE id=?", (failed['id'],))
        database.init_db()
        assert svc.run_monthly_report_item(job, failed['id'])['status']=='succeeded'
        for item in svc.get_monthly_report_job(job)['items']:
            prior = item['export_id']
            assert svc.run_monthly_report_item(job,item['id'])['export_id']==prior
            assert item['attempts'] == (2 if item['id']==failed['id'] else 1)
            archived = get_report_history_record(prior).summary_json
            single = svc._build(item['qc_method'],item['batch_id'],item['report_month']).report.to_snapshot_summary()
            for key in ('statistics','level_statistics','lot_trace','quality_summary','handling_summaries','processing_candidates','source_version'):
                assert archived.get(key)==single.get(key), key
        before = db_dump()
        archive = ZipFile(io.BytesIO(svc.build_monthly_report_zip(job)))
        names = [n for n in archive.namelist() if n.endswith('.pdf')]
        assert len(names)==len(set(names))==3
        manifest=list(csv.DictReader(io.StringIO(archive.read('月报清单.csv').decode('utf-8-sig'))))
        for row in manifest:
            if row['状态']=='已生成':
                from services.report_service import read_report_history_pdf
                assert archive.read(row['文件']) == read_report_history_pdf(get_report_history_record(int(row['报告编号'])))
        for item_id,pdf in saved.items():
            assert svc.read_monthly_report_item(job,item_id)['pdf_bytes']==pdf
        svc.monthly_job_summary(job)
        assert db_dump()==before
        with get_connection() as c:
            assert c.execute('SELECT count(*) FROM report_exports').fetchone()[0]==3


def test_stale_data_and_failed_archive_rollback_and_lock_message():
    with TemporaryDatabaseContext():
        _, batch = seed_lj_batch_with_formal_monthly_data()
        preview=svc.preview_monthly_reports('2026-04')
        job=job_for(preview)
        item=svc.get_monthly_report_job(job)['items'][0]
        original_saver = svc.save_lj_monthly_report_snapshot
        def fail_after_insert(package, pdf):
            original_saver(package, pdf)
            raise RuntimeError('Injected failure after archive insertion')
        with patch.object(svc,'save_lj_monthly_report_snapshot',side_effect=fail_after_insert):
            assert svc.run_monthly_report_item(job,item['id'])['status']=='failed'
        with get_connection() as c:
            assert c.execute('SELECT count(*) FROM report_exports').fetchone()[0]==0
            assert c.execute('SELECT count(*) FROM report_export_files').fetchone()[0]==0
        add_result(batch_id=batch,test_time='2026-04-04 10:00:00',operator='补充检测',value=100,log_value=None)
        rejects(lambda: job_for(preview,'stale'), '已变化')
        assert '变化' in svc.run_monthly_report_item(job,item['id'])['error_message']
        with patch.object(svc,'atomic_write',side_effect=sqlite3.OperationalError('database is locked')):
            rejects(lambda: svc.run_monthly_report_item(job,item['id']), '稍后重试')
            rejects(lambda: job_for(preview,'busy'), '稍后重试')


def test_usage_event_change_invalidates_preview():
    with TemporaryDatabaseContext():
        _, batch = seed_zscore_batch_with_formal_monthly_data()
        preview=svc.preview_monthly_reports('2026-04')
        row=next(r for r in preview['items'] if r['batch_id']==batch)
        from services.lot_lifecycle_service import source_context
        with get_connection() as c:
            source,_,_=source_context(c,'zscore',batch,read_only=True)
            assert source['system_id']
            c.execute("""INSERT INTO qc_lot_change_events(system_id,template_item_id,event_type,effective_at,reason,operator)
                VALUES(?,?,'active','2026-04-03 12:00:00','使用事件补充说明','工程验收')""",
                (source['system_id'],source['project_template_item_id']))
        assert monthly_source_version('zscore',batch,'2026-04')['fingerprint']!=row['source_fingerprint']
        rejects(lambda: job_for(preview), '已变化')


def test_unopened_rejections_are_frozen_and_do_not_change_denominator():
    with TemporaryDatabaseContext():
        _,batch=seed_lj_batch_with_formal_monthly_data()
        package=build_lj_monthly_report_package(batch,'2026-04')
        pending=[r for r in package.report.processing_candidates if r['status']!='completed']
        assert pending and all(r['event_id'] is None for r in pending)
        job=job_for(svc.preview_monthly_reports('2026-04'))
        item=svc.get_monthly_report_job(job)['items'][0]
        assert svc.run_monthly_report_item(job,item['id'])['status']=='succeeded'
        summary=svc.monthly_job_summary(job)['reports'][0]
        assert summary['unopened_count']==len(pending)==summary['pending_event_count']
        assert summary['out_of_control_count']==package.report.statistics.out_of_control_count
        from services.out_of_control_service import open_event
        event=open_event(pending[0]['source_type'],pending[0]['source_id'],'monthly-open','工程验收')
        assert event['event_id']
        assert svc.monthly_job_summary(job)['reports'][0]==summary, 'archive summary must not silently change'


def seed_versioned_quality():
    from tests.quality_targets_smoke_test import new_lot, apply, activate
    from services.lot_lifecycle_service import create_target_profile
    item,config,_=new_lot();apply(item);batch=activate(item,config,'lj')
    for version,(day,mean,values) in enumerate([(2,100,[100,101]),(10,200,[200,202]),(20,300,[300])],1):
        create_target_profile(method='lj',batch_id=batch,levels=[dict(level_id='Level 1',mean=mean,sd=10)],
            source='manual' if version==1 else 'revision',evidence='独立工程验收参数依据',confirmed_by='测试确认人',effective_at=f'2026-09-{day:02d} 08:00:00')
        for index,value in enumerate(values):
            add_result(batch_id=batch,test_time=f'2026-09-{day+index+1:02d} 10:00:00',operator='工程验收',value=value,
                       log_value=None,lot_selection={'allow_unknown':True})
    return batch


def test_cv_parameter_versions_no_pooled_evaluation():
    with TemporaryDatabaseContext():
        batch=seed_versioned_quality()
        report=build_lj_monthly_report_package(batch,'2026-09').report
        rows=report.quality_summary['rows']
        assert [r['parameter_version'] for r in rows]==['1','2','3']
        assert [r['count'] for r in rows]==[2,2,1]
        assert [r['days'] for r in rows]==[2,2,1]
        assert 0.70 < rows[0]['cv'] < 0.71 and abs(rows[0]['cv']-rows[1]['cv'])<1e-10
        assert rows[2]['cv'] is None and '不足' in rows[2]['decision']
        assert report.statistics.monthly_cv is None
        assert report.statistics.formal_count==5


def test_page_preview_and_archive_ui():
    from streamlit.testing.v1 import AppTest
    with TemporaryDatabaseContext():
        seed_lj_batch_with_formal_monthly_data()
        app=AppTest.from_string("from ui.batch_monthly_reports import render_batch_monthly_reports_page\nrender_batch_monthly_reports_page()",default_timeout=120).run()
        assert not app.exception
        app.date_input(key='monthly_month').set_value(date(2026,4,1))
        before=db_dump()
        app.button(key='monthly_preview').click().run()
        assert not app.exception and app.multiselect(key='monthly_selection_'+app.session_state['monthly_request_id']).value
        assert before==db_dump()
        app.button(key='monthly_generate').click().run()
        assert not app.exception
        assert len(svc.list_monthly_report_jobs())==1
        assert any('月度回顾' in s.value for s in app.subheader)


def test_concurrent_same_item_saves_once_and_long_names_unpack():
    from concurrent.futures import ThreadPoolExecutor
    from tempfile import TemporaryDirectory
    with TemporaryDatabaseContext():
        seed_lj_batch_with_formal_monthly_data()
        preview=svc.preview_monthly_reports('2026-04')
        job=job_for(preview)
        item=svc.get_monthly_report_job(job)['items'][0]
        pdf=svc.build_lj_monthly_report_pdf(svc._build('lj',item['batch_id'],'2026-04'))
        with patch.object(svc,'build_lj_monthly_report_pdf',return_value=pdf) as renderer:
            with ThreadPoolExecutor(2) as pool:
                results=list(pool.map(lambda _:svc.run_monthly_report_item(job,item['id']),range(2)))
            assert all(r['status']=='succeeded' for r in results)
            assert len({r['export_id'] for r in results})==1
            assert renderer.call_count==1
        long_item={'id':999,'selection':{'project_name':'很长的中文检验项目'*40,'instrument_name':'仪器/非法:*名称',
            'lot_no':'A/B','report_month':'2026-04'}}
        name=svc._zip_name(long_item)
        assert len(name.encode())<255 and name.endswith('_0999.pdf') and '/' not in name
        with TemporaryDirectory() as temporary:
            data=io.BytesIO()
            with ZipFile(data,'w') as z:z.writestr(name,pdf)
            with ZipFile(data) as z:z.extractall(temporary)
            assert (Path(temporary)/name).read_bytes()==pdf


def test_renderer_source_change_rolls_back_before_archive():
    with TemporaryDatabaseContext():
        _,batch=seed_lj_batch_with_formal_monthly_data()
        job=job_for(svc.preview_monthly_reports('2026-04'))
        item=svc.get_monthly_report_job(job)['items'][0]
        original=svc.build_lj_monthly_report_pdf
        with get_connection() as c:before=c.execute('SELECT operator FROM results WHERE batch_id=? ORDER BY id',(batch,)).fetchall()
        def change_during_render(package):
            pdf=original(package)
            with get_connection() as c:c.execute("UPDATE results SET operator='unexpected change' WHERE batch_id=?",(batch,))
            return pdf
        with patch.object(svc,'build_lj_monthly_report_pdf',side_effect=change_during_render):
            result=svc.run_monthly_report_item(job,item['id'])
        assert result['status']=='failed' and '生成期间' in result['error_message']
        with get_connection() as c:
            assert [tuple(r) for r in c.execute('SELECT operator FROM results WHERE batch_id=? ORDER BY id',(batch,))]==[tuple(r) for r in before]
            assert c.execute('SELECT count(*) FROM report_exports').fetchone()[0]==0


def test_zscore_whole_run_count_and_quality_evaluation_boundaries():
    from tests.zscore_v12_fixtures import seed_zscore_configuration
    from services.lot_lifecycle_service import create_target_profile
    from zscore_logic import create_zscore_run, get_template_id_for_level_count
    from services.monthly_report_source_service import versioned_quality_summary
    from services.quality_target_service import get_requirement,select_rule
    with TemporaryDatabaseContext():
        fixture=seed_zscore_configuration(name='月报整次判读',level_count=3)
        batch=fixture['batch_id']
        create_target_profile(method='zscore',batch_id=batch,
            levels=[dict(level_id=f'Level {i}',mean=100*i,sd=10) for i in (1,2,3)],source='manual',
            evidence='三个水平已核对的工程测试参数',confirmed_by='工程验收',effective_at='2026-09-02')
        create_zscore_run(batch_id=batch,test_time='2026-09-03 10:00:00',operator='工程验收',
            level_results=[dict(level_id=f'Level {i}',raw_value=value) for i,value in enumerate([140,240,300],1)],
            template_id=get_template_id_for_level_count(3),lot_selection={'allow_unknown':True})
        job=job_for(svc.preview_monthly_reports('2026-09'))
        item=svc.get_monthly_report_job(job)['items'][0]
        assert svc.run_monthly_report_item(job,item['id'])['status']=='succeeded'
        result=svc.monthly_job_summary(job)['reports'][0]
        assert result['formal_count']==result['out_of_control_count']==result['unopened_count']==1
        # Inputs are grouped once by run; a normal level in a rejected run is excluded too.
        goal={'spec':{'scope':'日间精密度'},'levels':[{'level_order':i,'concentration':100*i,'category':'',
            'rule':{'kind':'cv','operator':'<=','value':7.5,'unit':'%'}} for i in (1,2,3)]}
        records=[{'target_profile_id':None,'test_time':'2026-09-03 10:00:00','run_status':'reject',
            'level_results':[{'level_id':f'Level {i}','raw_value':100*i,'is_in_control_for_realtime_stats':i==3} for i in (1,2,3)]}]
        rows=versioned_quality_summary('zscore',batch,'2026-09',records,existing={'goal':goal})['rows']
        assert all(r['count']==0 and '不足' in r['decision'] for r in rows)
        lj_records=[dict(target_profile_id=None,test_time='2026-09-03 10:00:00',value=v,status='符合质控') for v in (100,101)]
        rows=versioned_quality_summary('lj',batch,'2026-09',lj_records,existing={'goal':goal})['rows']
        assert rows[0]['days']==1 and '不足两个检测日' in rows[0]['decision']
        goal['levels'][0]['rule']=select_rule(get_requirement('wst403-2024-082'),5)
        rows=versioned_quality_summary('lj',batch,'2026-09',lj_records,existing={'goal':goal})['rows']
        assert 'SD 要求仅展示' in rows[0]['decision']


def test_single_archive_rejects_changed_source_for_both_methods():
    from services import report_service as report
    from services.settings_service import save_report_settings_form
    for method,seed in [('lj',seed_lj_batch_with_formal_monthly_data),('zscore',seed_zscore_batch_with_formal_monthly_data)]:
        with TemporaryDatabaseContext():
            _,batch=seed()
            package=svc._build(method,batch,'2026-04')
            renderer=getattr(report,f'build_{method}_monthly_report_pdf')
            saver=getattr(report,f'save_{method}_monthly_report_snapshot')
            pdf=renderer(package)
            save_report_settings_form({'lab_name':'生成后修改的实验室'})
            rejects(lambda:saver(package,pdf),'资料或设置已变化')
            with get_connection() as c:
                assert c.execute('SELECT count(*) FROM report_exports').fetchone()[0]==0
                assert c.execute('SELECT count(*) FROM report_export_files').fetchone()[0]==0
            # A fresh package is accepted and the archive still reads its original bytes.
            fresh=svc._build(method,batch,'2026-04')
            new_pdf=renderer(fresh)
            export=saver(fresh,new_pdf)
            assert report.read_report_history_pdf(export)==new_pdf


def test_history_regeneration_change_preserves_original_for_both_methods():
    from services import report_service as report
    from services.settings_service import save_report_settings_form
    for method,seed in [('lj',seed_lj_batch_with_formal_monthly_data),('zscore',seed_zscore_batch_with_formal_monthly_data)]:
        with TemporaryDatabaseContext():
            _,batch=seed()
            package=svc._build(method,batch,'2026-04')
            renderer=getattr(report,f'build_{method}_monthly_report_pdf')
            pdf=renderer(package)
            export=getattr(report,f'save_{method}_monthly_report_snapshot')(package,pdf)
            def change_after_pdf(current):
                data=renderer(current)
                save_report_settings_form({'lab_name':'重新生成期间修改的实验室'})
                return data
            with patch.object(report,f'build_{method}_monthly_report_pdf',side_effect=change_after_pdf):
                rejects(lambda:report.regenerate_report_from_history(export),'资料或设置已变化')
            assert report.read_report_history_pdf(export)==pdf
            with get_connection() as c:
                assert c.execute('SELECT count(*) FROM report_exports').fetchone()[0]==1
                assert c.execute('SELECT count(*) FROM report_export_files').fetchone()[0]==1


def test_single_report_ui_shows_changed_source_without_exception():
    import importlib
    from streamlit.testing.v1 import AppTest
    from services.settings_service import save_report_settings_form
    for method,seed in [('lj',seed_lj_batch_with_formal_monthly_data),('zscore',seed_zscore_batch_with_formal_monthly_data)]:
        with TemporaryDatabaseContext():
            _,batch=seed()
            section=importlib.import_module(f'pages.{method}_report_section')
            original=getattr(section,f'build_{method}_monthly_report_pdf')
            def change_after_pdf(package):
                data=original(package)
                save_report_settings_form({'lab_name':'界面生成期间修改的实验室'})
                return data
            app=AppTest.from_string(f'from pages.{method}_report_section import render_{method}_monthly_report_section\nrender_{method}_monthly_report_section({batch})',default_timeout=120).run()
            with patch.object(section,f'build_{method}_monthly_report_pdf',side_effect=change_after_pdf):
                app.button(key=f'{method}_monthly_report_{batch}_generate').click().run()
            assert not app.exception
            assert any('资料或设置已变化' in warning.value for warning in app.warning)
            assert not app.get('download_button')
            with get_connection() as c:assert c.execute('SELECT count(*) FROM report_exports').fetchone()[0]==0


if __name__=='__main__':
    for name,function in list(globals().items()):
        if name.startswith('test_') and callable(function):
            function();print('PASS',name,flush=True)
