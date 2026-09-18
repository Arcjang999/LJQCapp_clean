"""Isolated acceptance tests: lots, immutable history, versions and atomic writes."""
import sys,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from unittest.mock import patch
from tests.instant_v12_integration_smoke_test import IsolatedDatabase
from tests.instant_v12_fixtures import seed_instant_configuration
from tests.zscore_v12_fixtures import seed_zscore_configuration
from tests.lj_v12_integration_smoke_test import _seed_active_lj_configuration
from database import get_connection,add_result,get_results,get_batch,init_db
from services.lot_lifecycle_service import *
from services.workbench_config_service import sync_lj_workbench_bindings
from services.instant_service import save_instant_result,confirm_instant_transfer_to_lj
from zscore_logic import create_zscore_run,get_zscore_runs,get_zscore_level_targets,rebuild_zscore_batch_state
from qc_logic import calculate_qc_results


def rejected(fn,text):
    try:fn()
    except ValueError as e:assert text in str(e),str(e)
    else:raise AssertionError('Expected '+text)


def lj():
    _seed_active_lj_configuration();sync_lj_workbench_bindings()
    with get_connection() as c:
        b=dict(c.execute("SELECT * FROM qc_workbench_bindings WHERE qc_method='lj'").fetchone())
    return b['runtime_batch_id']


def verified(method,batch,label):
    with get_connection() as c:s,_,_=source_context(c,method,batch)
    lot=create_reagent_lot(reagent_id=s['identity'][7],lot_no=label,expiry_date='2028-12-31')
    v=record_lot_verification(template_item_id=s['project_template_item_id'],system_id=s['system_id'],reagent_lot_id=lot,
        conclusion='pass',evidence='对照批验证，依据实验室 SOP：接受',confirmed_by='测试确认人',confirmed_at='2026-09-01')
    return s,lot,v


def switch(s,lot,v,when='2026-09-02'):
    with get_connection() as c:rev=usage_revision(c,s['system_id'])
    return switch_reagent_lots(selections=[dict(system_id=s['system_id'],template_item_id=s['project_template_item_id'],reagent_lot_id=lot,verification_id=v,expected_revision=rev)],effective_at=when,operator='测试',reason='验证通过，原靶值适用')[0]


def test_lj_lot_changes_keep_identity_targets_and_rules():
    with IsolatedDatabase():
        batch=lj();original_project_id=get_batch(batch)['project_id'];s,r1,v1=verified('lj',batch,'R001');_,r2,v2=verified('lj',batch,'R002')
        switch(s,r1,v1)
        p=create_target_profile(method='lj',batch_id=batch,levels=[{'level_id':'Level 1','mean':100,'sd':2}],source='manual',evidence='已验证',confirmed_by='测试',effective_at='2026-09-02')
        for i in range(4):add_result(batch,f'2026-09-03 08:0{i}:00',103,operator='测试',lot_selection={'reagent_lot_id':r1 if i<2 else r2})
        before=get_results(batch);switch(s,r2,v2,'2026-09-03 08:02:00')
        after=get_results(batch);assert before.to_dict('records')==after.to_dict('records')
        qc,stats=calculate_qc_results(after,5)
        assert set(after.target_profile_id)=={p}
        assert '4_1s' in str(qc.iloc[-1].rule_hits),qc[['rule_hits','zi']]
        assert after.actual_reagent_lot.tolist()==['R001','R001','R002','R002']
        assert get_batch(batch)['project_id']==original_project_id
        with get_connection() as c:
            assert c.execute('SELECT COUNT(*) FROM qc_target_profiles').fetchone()[0]==1
            assert c.execute('PRAGMA foreign_key_check').fetchall()==[]


def test_stale_form_invalid_lot_and_batch_rollback():
    with IsolatedDatabase():
        f=seed_instant_configuration();b=f['batch_id'];s,r1,v1=verified('instant',b,'R1')
        revision=result_lot_options('instant',b,'2026-09-03')['revision'];switch(s,r1,v1)
        fn=lambda **sel:save_instant_result(batch_id=b,test_time='2026-09-03',value=100,log_value=None,operator='测试',lot_selection=sel)
        rejected(lambda:fn(reagent_lot_id=r1,expected_revision=revision),'重新确认')
        rejected(lambda:fn(reagent_lot_id=r1+100),'不适用')
        rejected(lambda:fn(),'明确选择')
        with get_connection() as c:
            assert c.execute('SELECT COUNT(*) FROM instant_results').fetchone()[0]==0
            assert c.execute('SELECT COUNT(*) FROM qc_result_contexts').fetchone()[0]==0
        fn(reagent_lot_id=r1)
        old=context_dataframe('instant',b).to_dict('records')
        _,r2,v2=verified('instant',b,'R2');event=switch(s,r2,v2,'2026-09-04')
        with get_connection() as c:rev=usage_revision(c,s['system_id'])
        correct_reagent_event(event_id=event,reagent_lot_id=r1,verification_id=v1,effective_at='2026-09-04',operator='更正人',reason='实际继续使用旧批',expected_revision=rev)
        assert context_dataframe('instant',b).to_dict('records')==old
        assert result_lot_options('instant',b,'2026-09-03')['suggested_lot_id']==r1
        assert len(list_lot_events(s['system_id']))==3


def test_qc_new_batch_same_project_parallel_ended_readonly():
    from services.master_data_service import create_qc_lot,create_qc_level
    from services.instant_workbench_service import sync_instant_workbench_bindings
    with IsolatedDatabase():
        f=seed_instant_configuration();s,r,v=verified('instant',f['batch_id'],'R1')
        lot=create_qc_lot(qc_material_id=f['material_id'],lot_no='QC-NEW',expiry_date='2028-12-31')
        create_qc_level(qc_material_lot_id=lot,level_order=1,level_name='常规水平')
        new=change_qc_lot(source_config_id=f['config_id'],target_qc_lot_id=lot,template_item_ids=[s['project_template_item_id']],operator='测试',reason='新批平行',effective_at='2026-09-03')
        sync_instant_workbench_bindings()
        with get_connection() as c:
            b=c.execute('SELECT * FROM qc_workbench_bindings WHERE lot_config_id=?',(new,)).fetchone()
            assert b['runtime_project_id']==f['project_id'] and b['runtime_batch_id']!=f['batch_id']
            assert c.execute('SELECT COUNT(*) FROM instant_results WHERE batch_id=?',(b['runtime_batch_id'],)).fetchone()[0]==0
        set_qc_usage_state(lot_config_item_id=f['item_id'],state='ended',effective_at='2099-09-03',operator='测试',reason='未来结束计划')
        with get_connection() as c:
            assert effective_qc_state(c,f['item_id'],'2026-09-04')=='active'
            assert effective_qc_state(c,f['item_id'],'2099-09-04')=='ended'
            require_writable(c,'instant',f['batch_id'])
        set_qc_usage_state(lot_config_item_id=f['item_id'],state='ended',effective_at='2026-09-03',operator='测试',reason='结束旧批')
        rejected(lambda:save_instant_result(batch_id=f['batch_id'],test_time='2026-09-04',value=100,log_value=None,operator='测试'),'停止使用')


def test_instant_transfer_preserves_each_actual_lot():
    with IsolatedDatabase():
        f=seed_instant_configuration();b=f['batch_id'];s,r1,v1=verified('instant',b,'R1');_,r2,v2=verified('instant',b,'R2')
        for i in range(21):save_instant_result(batch_id=b,test_time=f'2026-09-03 08:{i:02d}:00',value=100+(i%3-1)*.1,log_value=None,operator='测试',lot_selection={'reagent_lot_id':r1 if i<10 else r2})
        transfer=confirm_instant_transfer_to_lj(b)
        with get_connection() as c:
            rows=c.execute('SELECT * FROM qc_result_contexts WHERE lj_result_id IS NOT NULL ORDER BY lj_result_id').fetchall()
            assert len(rows)==21
            for i,r in enumerate(rows):
                assert r['reagent_lot_id']==(r1 if i<10 else r2) and r['source_context_id'] is not None
            assert c.execute('PRAGMA foreign_key_check').fetchall()==[]


def test_zscore_target_versions_and_atomic_incomplete_run():
    with IsolatedDatabase():
        f=seed_zscore_configuration();b=f['batch_id']
        p=create_target_profile(method='zscore',batch_id=b,levels=[{'level_id':f'Level {i}','mean':100*i,'sd':2} for i in (1,2)],source='manual',evidence='两水平已确认',confirmed_by='测试',effective_at='2026-09-02')
        def run(when,a=105,bv=201):return create_zscore_run(batch_id=b,test_time=when,operator='测试',level_results=[{'level_id':'Level 1','raw_value':a},{'level_id':'Level 2','raw_value':bv}],template_id='2_level_classic')
        one=run('2026-09-03 08:00');two=run('2026-09-03 09:00')
        assert two['phase']=='formal_qc' and '2_2s' in str(two['rule_hits_run']),two
        p2=create_target_profile(method='zscore',batch_id=b,levels=[{'level_id':f'Level {i}','mean':100*i+5,'sd':2} for i in (1,2)],source='revision',evidence='独立修订',confirmed_by='测试',effective_at='2026-09-03 10:00')
        run('2026-09-03 10:00',105,205)
        rebuilt=rebuild_zscore_batch_state(b)['runs']
        assert [r['target_profile_id'] for r in rebuilt]==[p,p,p2]
        assert '2_2s' in str(rebuilt[1]['rule_hits_run'])
        assert not rebuilt[-1]['rule_hits_run']
        with get_connection() as c:
            before=[c.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in ('zscore_runs','zscore_level_results','qc_result_contexts','qc_result_evaluations')]
        with patch('zscore_logic.db_add_zscore_level_results',side_effect=ValueError('模拟保存失败')):
            rejected(lambda:run('2026-09-03 11:00'),'模拟')
        with get_connection() as c:
            assert [c.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in ('zscore_runs','zscore_level_results','qc_result_contexts','qc_result_evaluations')]==before


def test_lj_snapshot_and_repeat_init():
    with IsolatedDatabase():
        b=lj();add_result(b,'2026-09-03',100,operator='测试');old=dict(get_batch(b))
        with get_connection() as c:c.execute("UPDATE md_reagents SET generic_name='新名称'")
        sync_lj_workbench_bindings()
        assert get_batch(b)['reagent']==old['reagent']
        before=context_dataframe('lj',b).to_dict('records');init_db();init_db()
        assert context_dataframe('lj',b).to_dict('records')==before
        with get_connection() as c:assert c.execute('PRAGMA foreign_key_check').fetchall()==[]


def test_single_level_replacement_freezes_old_combination():
    from services.master_data_service import create_qc_lot,create_qc_level
    with IsolatedDatabase():
        f=seed_zscore_configuration(level_count=3);b=f['batch_id']
        with get_connection() as c:source,_,_=source_context(c,'zscore',b)
        lot=create_qc_lot(qc_material_id=source['identity'][1],lot_no='NEW-HIGH',expiry_date='2028-12-31')
        new_level=create_qc_level(qc_material_lot_id=lot,level_name='新高值',level_order=3)
        vid=record_lot_verification(template_item_id=source['project_template_item_id'],system_id=source['system_id'],qc_lot_id=lot,
            conclusion='pass',evidence='高值批间平行验证',confirmed_by='测试',confirmed_at='2026-09-02')
        old_profile=create_target_profile(method='zscore',batch_id=b,levels=[{'level_id':f'Level {i}','mean':100*i,'sd':2} for i in (1,2,3)],
            source='manual',evidence='已确认',confirmed_by='测试',effective_at='2026-09-02')
        create_zscore_run(batch_id=b,test_time='2026-09-03',operator='测试',level_results=[{'level_id':f'Level {i}','raw_value':100*i} for i in (1,2,3)],template_id='3_level_threes')
        new=create_level_combination(source_batch_id=b,level_ids=f['level_ids'][:2]+[new_level],verification_ids={new_level:vid},operator='测试',reason='只换高值',effective_at='2026-09-04')
        with get_connection() as c:
            binding=c.execute('SELECT * FROM qc_workbench_bindings WHERE lot_config_id=?',(new,)).fetchone()
            assert binding and binding['runtime_project_id']==f['project_id']
            snap=json.loads(binding['source_snapshot_json'])
            assert [level['qc_level_id'] for level in snap['levels']]==f['level_ids'][:2]+[new_level]
            assert snap['levels'][-1]['lot_no']=='NEW-HIGH'
            assert c.execute('SELECT COUNT(*) FROM qc_level_combination_members WHERE source_profile_id=?',(old_profile,)).fetchone()[0]==2
            assert c.execute('SELECT COUNT(*) FROM zscore_runs WHERE batch_id=?',(binding['runtime_batch_id'],)).fetchone()[0]==0
        assert get_zscore_runs(b,'3_level_threes')[0]['target_profile_id']==old_profile
        run=create_zscore_run(batch_id=binding['runtime_batch_id'],test_time='2026-09-04',operator='测试',level_results=[{'level_id':f'Level {i}','raw_value':100*i} for i in (1,2,3)],template_id='3_level_threes')
        assert run['phase']=='target_building'
        with get_connection() as c:
            context=c.execute('SELECT id FROM qc_result_contexts WHERE zscore_run_id=?',(run['id'],)).fetchone()[0]
            assert [r[0] for r in c.execute('SELECT lot_no FROM qc_result_context_levels WHERE context_id=? ORDER BY level_order',(context,))]==[source['lot_no'],source['lot_no'],'NEW-HIGH']
            assert c.execute('PRAGMA foreign_key_check').fetchall()==[]


def test_import_preview_preserves_unknown_and_atomicity():
    from import_review import review_lj_building_import_csv
    with IsolatedDatabase():
        b=lj();source,r,v=verified('lj',b,'R-IMPORT');switch(source,r,v)
        review=review_lj_building_import_csv('检测时间,检测人,真实检测值,实际试剂批号\n2026-09-03 08:00,测试,100,R-IMPORT\n2026-09-03 09:00,测试,101,\n'.encode(),get_results(b),5)
        review=review_import_lots(review,'lj',b)
        assert not any(i['is_blocking'] for i in review['issues']),review
        assert review['normalized_rows'][1]['lot_selection']['allow_unknown']
        bad=[dict(row) for row in review['normalized_rows']]
        bad[1]['lot_selection']={'reagent_lot_id':999,'expected_revision':1}
        rejected(lambda:import_reviewed_results('lj',b,bad),'不适用')
        assert get_results(b).empty
        assert import_reviewed_results('lj',b,review['normalized_rows'])==2
        assert get_results(b).actual_reagent_lot.tolist()==['R-IMPORT','未记录']


def test_monthly_report_versions_and_browser_management():
    from services.report_service import build_lj_monthly_report_package,build_lj_monthly_report_pdf,build_zscore_monthly_report_package,build_zscore_monthly_report_pdf
    from streamlit.testing.v1 import AppTest
    root=Path(__file__).resolve().parents[1]
    with IsolatedDatabase():
        b=lj();source,r,v=verified('lj',b,'R-REPORT');switch(source,r,v)
        for hour,mean in [(8,100),(10,110)]:
            create_target_profile(method='lj',batch_id=b,levels=[{'level_id':'Level 1','mean':mean,'sd':2}],source='manual' if hour==8 else 'revision',evidence='月内参数修订测试，已确认适用',confirmed_by='测试',effective_at=f'2026-09-03 {hour:02d}:00')
            for minute in range(3):add_result(b,f'2026-09-03 {hour:02d}:{minute:02d}',mean+minute*.1,operator='测试',lot_selection={'reagent_lot_id':r})
        package=build_lj_monthly_report_package(b,'2026-09')
        assert len(package.report.lot_trace['statistics_by_target_version'])==2
        assert package.report.statistics.monthly_mean is None
        assert {g['target_mean'] for g in package.report.lot_trace['statistics_by_target_version']}=={100,110}
        out=root/'output/lot-lifecycle-2026-09-11';out.mkdir(parents=True,exist_ok=True)
        (out/'lj-versions.pdf').write_bytes(build_lj_monthly_report_pdf(package))
        f=seed_zscore_configuration()
        create_target_profile(method='zscore',batch_id=f['batch_id'],levels=[{'level_id':f'Level {i}','mean':100*i,'sd':2} for i in (1,2)],source='manual',evidence='全部水平已确认',confirmed_by='测试',effective_at='2026-09-02')
        for h in (8,10,9):create_zscore_run(batch_id=f['batch_id'],test_time=f'2026-09-03 {h:02d}:00',operator='测试',level_results=[{'level_id':f'Level {i}','raw_value':100*i+1} for i in (1,2)],template_id='2_level_classic')
        zs=build_zscore_monthly_report_package(f['batch_id'],'2026-09')
        assert len(zs.report.lot_trace['statistics_by_target_version'])==2
        assert set(zs.monthly_plot_df.formal_reference_mean)=={100,200}
        assert set(zs.monthly_plot_df.formal_reference_sd)=={2}
        (out/'zscore-versions.pdf').write_bytes(build_zscore_monthly_report_pdf(zs))
        at=AppTest.from_file(str(root/'app.py'),default_timeout=20).run()
        at.button(key='open_project_management_page').click().run()
        assert not list(at.exception)
        assert '批号使用与追溯' in [tab.label for tab in at.tabs]


def test_legacy_backfill_keeps_unknown_lots_and_continuous_window():
    with IsolatedDatabase():
        batch=lj()
        with get_connection() as c:
            for i,value in enumerate([99,100,101,100,100,101,101,101]):
                c.execute('INSERT INTO results(batch_id,test_time,value,operator) VALUES(?,?,?,?)',(batch,f'2026-09-03 08:{i:02d}',value,'旧系统'))
        assert backfill_result_contexts()==8
        before=context_dataframe('lj',batch)
        assert set(before.reagent_lot_no)=={'未记录'}
        add_result(batch,'2026-09-03 08:08',101,operator='新录入')
        qc,stats=calculate_qc_results(get_results(batch),5)
        assert '4_1s' in qc.iloc[-1].rule_hits
        assert context_dataframe('lj',batch).iloc[:8].target_profile_id.isna().all()
        assert backfill_result_contexts()==0


def test_xlsx_matching_and_latest_failed_verification():
    from services.export_utils import dataframe_to_xlsx_bytes
    from import_review import review_lj_building_import_csv
    with IsolatedDatabase():
        batch=lj();s,lot,vid=verified('lj',batch,'R-XLSX')
        frame=pd.DataFrame([{'检测时间':'2026-09-03 08:00','检测人':'测试','真实检测值':100,'实际试剂批号':'R-XLSX'}])
        review=review_import_lots(review_lj_building_import_csv(dataframe_to_xlsx_bytes(frame),get_results(batch),5),'lj',batch)
        assert not any(i['is_blocking'] for i in review['issues']),review
        assert import_reviewed_results('lj',batch,review['normalized_rows'])==1
        record_lot_verification(template_item_id=s['project_template_item_id'],system_id=s['system_id'],reagent_lot_id=lot,conclusion='fail',evidence='后续验证不符合标准',confirmed_by='测试',confirmed_at='2026-09-04')
        assert result_lot_options('lj',batch,'2026-09-03')['options']
        assert not result_lot_options('lj',batch,'2026-09-05')['options']
        rejected(lambda:add_result(batch,'2026-09-05',100,operator='测试',lot_selection={'reagent_lot_id':lot}),'不适用')
        assert len(get_results(batch))==1

if __name__=='__main__':
    tests=[v for k,v in list(globals().items()) if k.startswith('test_')]
    for test in tests:test();print('PASS',test.__name__,flush=True)
    print('All',len(tests),'lot lifecycle tests passed.')
