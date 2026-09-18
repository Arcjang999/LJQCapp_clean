"""Follow-up acceptance for verification scope, mixed lots and effective dates."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database import get_connection, get_results, add_result
from services.master_data_service import create_qc_lot, create_qc_level
from services.lot_lifecycle_service import (
    source_context, record_lot_verification, set_qc_usage_state,
    create_target_profile, create_level_combination, change_qc_lot,
    result_lot_options, context_dataframe,
    import_reviewed_results, review_import_lots,
)
from services.instant_service import save_instant_result
from services.instant_workbench_service import sync_instant_workbench_bindings
from tests.lot_lifecycle_smoke_test import IsolatedDatabase, lj, verified, switch, rejected
from tests.zscore_v12_fixtures import seed_zscore_configuration
from tests.instant_v12_fixtures import seed_instant_configuration


def snapshot(method, batch):
    with get_connection() as c:
        return source_context(c, method, batch)[0]


def verification(s, lot, conclusion='pass', when='2026-09-01'):
    return record_lot_verification(
        template_item_id=s['project_template_item_id'], system_id=s['system_id'],
        qc_lot_id=lot, conclusion=conclusion, evidence='隔离边界验收',
        confirmed_by='验收人员', confirmed_at=when,
    )


def profile(method, batch, count=1, when='2026-09-02'):
    return create_target_profile(method=method, batch_id=batch,
        levels=[{'level_id':f'Level {i}', 'mean':100*i, 'sd':2} for i in range(1,count+1)],
        source='manual', evidence='全部水平已复核', confirmed_by='验收人员', effective_at=when)


def state(item, vid=None, when='2026-09-04', target='active', **kwargs):
    return set_qc_usage_state(lot_config_item_id=item, state=target, effective_at=when,
        operator='验收人员', reason='边界验收', verification_id=vid, **kwargs)


def counts():
    with get_connection() as c:
        return {t:c.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in (
            'qc_lot_change_events','qc_lot_configs','qc_config_snapshots','qc_target_profiles',
            'results','instant_results','zscore_runs','zscore_level_results',
            'qc_result_contexts','qc_result_evaluations','qc_level_combination_members')}


def test_qc_activation_rejects_superseded_pass_and_accepts_historical_pass():
    for method in ('lj','zscore','instant'):
        with IsolatedDatabase():
            batch = lj() if method=='lj' else (seed_zscore_configuration() if method=='zscore' else seed_instant_configuration())['batch_id']
            s=snapshot(method,batch)
            if method!='instant': profile(method,batch,2 if method=='zscore' else 1)
            passed=verification(s,s['identity'][2])
            verification(s,s['identity'][2],'fail','2026-09-03')
            before=counts()
            rejected(lambda:state(s['lot_config_item_id'],passed),'后续结论')
            assert counts()==before
            state(s['lot_config_item_id'],passed,when='2026-09-02')
            renewed=verification(s,s['identity'][2],when='2026-09-04')
            state(s['lot_config_item_id'],renewed,when='2026-09-04')


def test_qc_activation_rejects_expiry_and_disabled_actual_lot():
    with IsolatedDatabase():
        batch=lj();s=snapshot('lj',batch);profile('lj',batch)
        vid=verification(s,s['identity'][2])
        with get_connection() as c:
            c.execute('UPDATE md_qc_material_lots SET expiry_date=? WHERE id=?',('2026-09-03',s['identity'][2]))
        state(s['lot_config_item_id'],vid,when='2026-09-03 23:59:59')
        before=counts()
        rejected(lambda:state(s['lot_config_item_id'],vid),'效期')
        assert counts()==before
        with get_connection() as c:
            c.execute('UPDATE md_qc_material_lots SET is_disabled=1 WHERE id=?',(s['identity'][2],))
        rejected(lambda:state(s['lot_config_item_id'],vid,when='2026-09-02'),'停用')


def combination_fixture():
    f=seed_zscore_configuration(level_count=3);s=snapshot('zscore',f['batch_id'])
    profile('zscore',f['batch_id'],3)
    lot=create_qc_lot(qc_material_id=s['identity'][1],lot_no='NEW-HIGH',expiry_date='2028-12-31')
    level=create_qc_level(qc_material_lot_id=lot,level_name='新高值',level_order=3)
    vid=verification(s,lot)
    return f,s,lot,level,vid


def combination(f,level,vid):
    return create_level_combination(source_batch_id=f['batch_id'],level_ids=f['level_ids'][:2]+[level],
        verification_ids={level:vid},operator='验收人员',reason='高水平换批',effective_at='2026-09-04')


def test_partial_replacement_rejects_superseded_pass_atomically():
    with IsolatedDatabase():
        f,s,lot,level,vid=combination_fixture()
        verification(s,lot,'fail','2026-09-03')
        before=counts()
        rejected(lambda:combination(f,level,vid),'后续结论')
        assert counts()==before


def test_mixed_combination_activation_requires_all_actual_lots():
    with IsolatedDatabase():
        f,s,lot,level,vid=combination_fixture();new=combination(f,level,vid)
        with get_connection() as c:
            b=dict(c.execute('SELECT * FROM qc_workbench_bindings WHERE lot_config_id=?',(new,)).fetchone())
        profile('zscore',b['runtime_batch_id'],3,when='2026-09-04')
        old_vid=verification(s,s['identity'][2])
        verification(s,lot,'fail','2026-09-04')
        before=counts()
        rejected(lambda:state(b['lot_config_item_id'],old_vid),'全部实际质控批号')
        assert counts()==before
        rejected(lambda:state(b['lot_config_item_id'],verification_ids={s['identity'][2]:old_vid,lot:vid}),'后续结论')
        new_vid=verification(s,lot,when='2026-09-04')
        event=state(b['lot_config_item_id'],verification_ids={s['identity'][2]:old_vid,lot:new_vid})
        with get_connection() as c:
            import json
            details=json.loads(c.execute('SELECT details_json FROM qc_lot_change_events WHERE id=?',(event,)).fetchone()[0])
            assert details['qc_verification_ids']=={str(s['identity'][2]):old_vid,str(lot):new_vid}
            assert c.execute('PRAGMA foreign_key_check').fetchall()==[]


def test_result_timestamp_obeys_qc_state_without_unlocking_ended_batch():
    with IsolatedDatabase():
        f=seed_instant_configuration();s=snapshot('instant',f['batch_id'])
        lot=create_qc_lot(qc_material_id=s['identity'][1],lot_no='NEW',expiry_date='2028-12-31')
        create_qc_level(qc_material_lot_id=lot,level_name='常规水平',level_order=1)
        new=change_qc_lot(source_config_id=f['config_id'],target_qc_lot_id=lot,
            template_item_ids=[s['project_template_item_id']],operator='验收人员',reason='平行计划',effective_at='2026-09-03')
        sync_instant_workbench_bindings()
        with get_connection() as c:
            b=dict(c.execute('SELECT * FROM qc_workbench_bindings WHERE lot_config_id=?',(new,)).fetchone())
        def save(when):
            return save_instant_result(batch_id=b['runtime_batch_id'],test_time=when,value=100,log_value=None,operator='验收人员')
        before=counts()
        rejected(lambda:save('2026-09-02'),'检测时间')
        assert counts()==before
        save('2026-09-03')
        state(b['lot_config_item_id'],target='ended',when='2099-01-01')
        before=counts()
        rejected(lambda:save('2099-01-01'),'检测时间')
        assert counts()==before
        state(b['lot_config_item_id'],target='ended',when='2026-09-04')
        rejected(lambda:save('2026-09-03 12:00'),'停止使用')


def test_verification_cannot_cross_system_lot_or_confirmation_time():
    with IsolatedDatabase():
        a=seed_zscore_configuration(name='检测项 A',instrument='仪器 A');b=seed_zscore_configuration(name='检测项 B',instrument='仪器 B')
        sa=snapshot('zscore',a['batch_id']);sb=snapshot('zscore',b['batch_id'])
        profile('zscore',a['batch_id'],2)
        other=verification(sb,sb['identity'][2])
        before=counts()
        rejected(lambda:state(a['item_id'],other),'本检测项')
        assert counts()==before
        other_lot=create_qc_lot(qc_material_id=sa['identity'][1],lot_no='OTHER',expiry_date='2028-12-31')
        wrong=verification(sa,other_lot)
        rejected(lambda:state(a['item_id'],wrong),'本实际质控批号')
        later=verification(sa,sa['identity'][2],when='2026-09-05')
        rejected(lambda:state(a['item_id'],later),'不得早于验证')


def test_reagent_changes_preserve_zscore_and_instant_sequences():
    from zscore_logic import create_zscore_run, get_zscore_runs
    from services.instant_service import build_instant_workbench_context
    for count in (2,3):
        with IsolatedDatabase():
            f=seed_zscore_configuration(level_count=count);batch=f['batch_id'];pid=profile('zscore',batch,count)
            s,r1,v1=verified('zscore',batch,'R1');_,r2,v2=verified('zscore',batch,'R2')
            template='2_level_classic' if count==2 else '3_level_threes'
            def run(when,lot):
                return create_zscore_run(batch_id=batch,test_time=when,operator='验收人员',template_id=template,
                    level_results=[{'level_id':f'Level {i}','raw_value':100*i+5} for i in range(1,count+1)],
                    lot_selection={'reagent_lot_id':lot})
            switch(s,r1,v1);run('2026-09-03 08:00',r1)
            before=context_dataframe('zscore',batch).to_dict('records')
            switch(s,r2,v2,'2026-09-03 09:00');run('2026-09-03 09:00',r2)
            runs=get_zscore_runs(batch,template)
            assert [r['target_profile_id'] for r in runs]==[pid,pid]
            assert runs[-1]['rule_hits_run'],runs[-1]
            assert context_dataframe('zscore',batch).iloc[:1].to_dict('records')==before
            assert context_dataframe('zscore',batch).reagent_lot_no.tolist()==['R1','R2']
            with get_connection() as c:
                assert c.execute('SELECT runtime_project_id FROM qc_workbench_bindings WHERE lot_config_item_id=?',(f['item_id'],)).fetchone()[0]==f['project_id']
    with IsolatedDatabase():
        f=seed_instant_configuration();batch=f['batch_id']
        s,r1,v1=verified('instant',batch,'R1');_,r2,v2=verified('instant',batch,'R2');switch(s,r1,v1)
        for i in range(3):
            if i==2:switch(s,r2,v2,'2026-09-03 08:02')
            save_instant_result(batch_id=batch,test_time=f'2026-09-03 08:0{i}',value=100+i*.1,
                log_value=None,operator='验收人员',lot_selection={'reagent_lot_id':r1 if i<2 else r2})
        assert build_instant_workbench_context(batch)['summary']['si_ready']
        assert context_dataframe('instant',batch).reagent_lot_no.tolist()==['R1','R1','R2']


def test_reagent_latest_verification_and_expiry_are_as_of_detection_time():
    with IsolatedDatabase():
        b=lj();s,r,v=verified('lj',b,'R1');switch(s,r,v)
        record_lot_verification(template_item_id=s['project_template_item_id'],system_id=s['system_id'],
            reagent_lot_id=r,conclusion='fail',evidence='后续复核失败',confirmed_by='验收人员',confirmed_at='2026-09-04')
        assert r in [x['id'] for x in result_lot_options('lj',b,'2026-09-03')['options']]
        assert not result_lot_options('lj',b,'2026-09-04')['options']
        before=counts()
        rejected(lambda:switch(s,r,v,'2026-09-05'),'后续结论')
        rejected(lambda:add_result(b,'2026-09-04',100,lot_selection={'reagent_lot_id':r}),'不适用')
        assert counts()==before
        add_result(b,'2026-09-03',100,lot_selection={'reagent_lot_id':r})
        with get_connection() as c:c.execute('UPDATE md_reagent_lots SET expiry_date=? WHERE id=?',('2026-09-02',r))
        assert not result_lot_options('lj',b,'2026-09-03')['options']


def test_historical_parameters_and_stale_import_confirmation_are_atomic():
    from import_review import review_lj_building_import_csv
    from zscore_logic import create_zscore_run
    for method in ('lj','zscore'):
        with IsolatedDatabase():
            b=lj() if method=='lj' else seed_zscore_configuration()['batch_id']
            p1=profile(method,b,1 if method=='lj' else 2)
            p2=profile(method,b,1 if method=='lj' else 2,when='2026-09-05')
            for when in ('2026-09-06','2026-09-03'):
                if method=='lj':add_result(b,when,101,operator='验收人员')
                else:create_zscore_run(batch_id=b,test_time=when,operator='验收人员',template_id='2_level_classic',
                    level_results=[{'level_id':'Level 1','raw_value':101},{'level_id':'Level 2','raw_value':201}])
            assert context_dataframe(method,b).target_profile_id.tolist()==[p1,p2]
    with IsolatedDatabase():
        b=lj();s,r1,v1=verified('lj',b,'R1');_,r2,v2=verified('lj',b,'R2');switch(s,r1,v1)
        review=review_lj_building_import_csv('检测时间,检测人,真实检测值,实际试剂批号\n2026-09-03 08:00,验收人员,100,R1\n2026-09-03 09:00,验收人员,101,R1\n'.encode(),get_results(b),5)
        preview=review_import_lots(review,'lj',b)
        switch(s,r2,v2,'2026-09-03')
        before=counts()
        rejected(lambda:import_reviewed_results('lj',b,preview['normalized_rows']),'重新确认')
        assert counts()==before
        refreshed=review_import_lots(review,'lj',b)
        assert import_reviewed_results('lj',b,refreshed['normalized_rows'])==2


if __name__=='__main__':
    tests=[(name,fn) for name,fn in list(globals().items()) if name.startswith('test_') and callable(fn)]
    failures=[]
    for name,fn in tests:
        try:
            fn();print('PASS',name)
        except Exception as exc:
            import traceback
            traceback.print_exc();failures.append(name)
    print(f'{len(tests)-len(failures)}/{len(tests)} boundary tests passed')
    if failures:sys.exit(1)
