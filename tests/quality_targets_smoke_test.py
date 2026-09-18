"""Isolated regression for standard provenance, conditional limits, adoption and immutable history."""
from pathlib import Path
import sys,json,csv,io,sqlite3
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import database
from database import get_connection,init_db,add_result
from streamlit.testing.v1 import AppTest
from tests.lot_lifecycle_smoke_test import IsolatedDatabase,lj,rejected
from tests.zscore_v12_fixtures import seed_zscore_configuration
from tests.instant_v12_fixtures import seed_instant_configuration
from services.quality_target_service import *
from services.project_config_service import (copy_lot_config,activate_lot_config,activate_project_template,list_lot_config_items,create_lot_config_from_template,save_lot_item_cv_requirement)
from services.master_data_service import create_qc_lot,create_qc_level,list_units
from services.workbench_config_service import sync_lj_workbench_bindings
from services.zscore_workbench_service import sync_zscore_workbench_bindings
from services.instant_workbench_service import sync_instant_workbench_bindings
from services.report_service import build_lj_monthly_report_package,build_zscore_monthly_report_package
from zscore_logic import create_zscore_run
ROOT=Path(__file__).resolve().parents[1]


def new_lot(method='lj',count=1,name='质量目标验收'):
    old=lj() if method=='lj' else (seed_zscore_configuration(name=name,level_count=count,instrument=name+"仪器") if method=='zscore' else seed_instant_configuration(name=name,instrument=name+"仪器"))['batch_id']
    with get_connection() as c:
        binding=dict(c.execute('SELECT * FROM qc_workbench_bindings WHERE qc_method=? AND runtime_batch_id=?',(method,old)).fetchone())
        material=c.execute('SELECT qc_material_id FROM qc_lot_configs WHERE id=?',(binding['lot_config_id'],)).fetchone()[0]
    lot=create_qc_lot(qc_material_id=material,lot_no='QUALITY-NEW',expiry_date='2028-12-31')
    for i in range(count):create_qc_level(qc_material_lot_id=lot,level_order=i+1,level_name=f'水平{i+1}')
    config=copy_lot_config(source_lot_config_id=binding['lot_config_id'],target_qc_material_lot_id=lot)
    item=int(list_lot_config_items(config).iloc[0]['id'])
    return item,config,binding


def apply(item,requirement='wst403-2024-047',count=1,**kw):
    levels=kw.pop('levels',[dict(level_order=i+1,concentration=100*(i+1),category='') for i in range(count)])
    return adopt_requirement('lot',item,requirement,confirmed_by='测试确认人',evidence='已核对项目、单位、浓度与适用条件',levels=levels,**kw)


def activate(item,config,method):
    activate_lot_config(config)
    {'lj':sync_lj_workbench_bindings,'zscore':sync_zscore_workbench_bindings,'instant':sync_instant_workbench_bindings}[method]()
    with get_connection() as c:return c.execute('SELECT runtime_batch_id FROM qc_workbench_bindings WHERE lot_config_item_id=?',(item,)).fetchone()[0]


def test_catalog_source_values_and_conditional_boundaries():
    with IsolatedDatabase():
        records=list_catalog();assert len(records)==94
        assert len([r for r in records if '403' in r['standard']])==82
        assert all(r['source_url'].startswith('https://www.nhc.gov.cn/') and r['effective_date']=='2024-11-01' for r in records)
        assert get_requirement('wst403-2024-001')['imprecision'][0]['value']==2.5
        assert get_requirement('wst403-2024-047')['imprecision'][0]['value']==7.5
        assert get_requirement('wst403-2024-055')['tea_text'].startswith('0.1 U/L')
        device=get_requirement('wst403-2024-082')
        assert select_rule(device,5.499)['kind']=='sd'
        assert select_rule(device,5.5)['kind']=='cv'
        rejected(lambda:select_rule(device,None),'浓度值')
        assert select_rule(get_requirement('wst406-2024-wbc'),category='低浓度')['value']==6
        assert select_rule(get_requirement('wst406-2024-wbc'),category='中/高浓度')['value']==4.5
        assert get_requirement('wst406-2024-pt')['bias_text']==''
        assert get_requirement('wst406-2024-mch')['bias_text']==''
        assert [r['id'] for r in suggested_requirements('CRP')]==['wst403-2024-047']
        goal=dict(spec=device,levels=[dict(level_order=1,rule=select_rule(device,5.5))])
        assert evaluate_cv(goal,1,7.5,count=20)=='超出所选 CV 要求'
        assert evaluate_cv(goal,1,7.49,count=20)=='满足所选 CV 要求'


def test_legacy_migration_idempotent_and_no_cv_backfill():
    with IsolatedDatabase():
        old=lj()
        with get_connection() as c:
            before=[tuple(r) for r in c.execute('SELECT id,cv_limit,quality_target_source_text FROM qc_lot_config_items')]
        init_db();init_db()
        with get_connection() as c:
            assert before==[tuple(r) for r in c.execute('SELECT id,cv_limit,quality_target_source_text FROM qc_lot_config_items')]
            assert c.execute('SELECT count(*) FROM qc_quality_catalog').fetchone()[0]==94
        assert runtime_goal('lj',old)=={}


def test_adoption_freezes_batch_and_copied_goal_requires_confirmation():
    with IsolatedDatabase():
        item,config,old=new_lot();goal=apply(item);batch=activate(item,config,'lj')
        assert runtime_goal('lj',batch)==goal
        rejected(lambda:apply(item),'已有批次')
        rejected(lambda:clear_requirement('lot',item),'已有批次')
        project_id=item_context('lot',item)['source_template_item_id']
        adopt_requirement('project',project_id,'wst403-2024-010',confirmed_by='更新人',evidence='后续项目要求调整')
        template_id=item_context('project',project_id)['template_id']
        activate_project_template(template_id)
        assert runtime_goal('lj',batch)==goal
        with get_connection() as c:
            material=c.execute('SELECT qc_material_id FROM qc_lot_configs WHERE id=?',(config,)).fetchone()[0]
        lot=create_qc_lot(qc_material_id=material,lot_no='NEXT',expiry_date='2028-12-31');create_qc_level(qc_material_lot_id=lot,level_order=1,level_name='L1')
        copied=copy_lot_config(source_lot_config_id=config,target_qc_material_lot_id=lot)
        copied_item=int(list_lot_config_items(copied).iloc[0]['id'])
        assert decode(item_context('lot',copied_item)['quality_goal_json'])['pending']
        rejected(lambda:activate_lot_config(copied),'质量目标待核对')
        rejected(lambda:save_lot_item_cv_requirement(copied_item,99),'已选择质量目标')
        clear_requirement('lot',copied_item);save_lot_item_cv_requirement(copied_item,3,'自定义原路径')
        assert item_context('lot',copied_item)['cv_limit']==3
        default_lot=create_qc_lot(qc_material_id=material,lot_no='PROJECT-DEFAULT',expiry_date='2028-12-31')
        create_qc_level(qc_material_lot_id=default_lot,level_order=1,level_name='L1')
        default_config=create_lot_config_from_template(template_id=template_id,qc_material_lot_id=default_lot)
        inherited=decode(list_lot_config_items(default_config).iloc[0]['quality_goal_json'])
        assert inherited['pending'] and inherited['spec']['id']=='wst403-2024-010'
        assert runtime_goal('lj',batch)==goal


def test_units_categories_and_legacy_changes_do_not_bypass_confirmation():
    with IsolatedDatabase():
        item,config,_=new_lot('zscore',2)
        rejected(lambda:apply(item,'wst403-2024-006',2),'单位不匹配')
        with get_connection() as c:
            unit=int(list_units().loc[lambda d:d.symbol=='g/L','id'].iloc[0])
            c.execute('UPDATE qc_lot_config_items SET unit_id=? WHERE id=?',(unit,item))
        goal=apply(item,'wst406-2024-hb',2,levels=[dict(level_order=1,category='低浓度'),dict(level_order=2,category='中/高浓度')])
        assert item_context('lot',item)['cv_limit'] is None
        assert [x['rule']['value'] for x in goal['levels']]==[2.5,2]
        assert evaluate_cv(goal,1,2.3,count=10,days=1).startswith('不足两个')
        assert evaluate_cv(goal,1,2.3,count=10,days=2).startswith('满足')
        assert evaluate_cv(goal,2,2.3,count=10,days=2).startswith('超出')
        with get_connection() as c:c.execute("UPDATE qc_lot_config_items SET input_value_type='ct' WHERE id=?",(item,))
        rejected(lambda:activate_lot_config(config),'Ct')


def test_three_method_snapshots_and_report_do_not_modify_records():
    for method,count in [('lj',1),('zscore',2),('zscore',3),('instant',1)]:
        with IsolatedDatabase():
            item,config,_=new_lot(method,count);apply(item,count=count);batch=activate(item,config,method)
            assert runtime_goal(method,batch)['spec']['id']=='wst403-2024-047'
            if method=='instant':
                assert batch_quality_summary(method,batch)['rows'][0]['decision'].startswith('即时法');continue
            for i,value in enumerate([100,101,99,100.5,99.5,100.1,100.2,100.3]):
                if method=='lj':add_result(batch,f'2026-09-{i+1:02d} 08:00',value,operator='验收')
                else:create_zscore_run(batch_id=batch,test_time=f'2026-09-{i+1:02d} 08:00',operator='验收',level_results=[dict(level_id=f'Level {j+1}',raw_value=value*(j+1)) for j in range(count)],template_id='2_level_classic' if count==2 else '3_level_threes',required_n=5)
            with get_connection() as c:before=[tuple(r) for r in c.execute('SELECT * FROM qc_result_evaluations ORDER BY id')]
            summary=batch_quality_summary(method,batch,'2026-09')
            assert all(r['count']==3 for r in summary['rows']),summary
            assert all(r['decision'].startswith('满足') for r in summary['rows'])
            report=(build_lj_monthly_report_package if method=='lj' else build_zscore_monthly_report_package)(batch,'2026-09').report
            assert report.quality_summary==summary
            assert report.to_snapshot_summary()['quality_summary']==summary
            with get_connection() as c:assert before==[tuple(r) for r in c.execute('SELECT * FROM qc_result_evaluations ORDER BY id')]


def test_custom_import_validates_entire_file_before_writing():
    with IsolatedDatabase():
        data=custom_template_csv().decode('utf-8-sig');rows=list(csv.DictReader(io.StringIO(data)));rows[0]['确认人']='验收'
        def payload():
            out=io.StringIO();w=csv.DictWriter(out,fieldnames=IMPORT_COLUMNS);w.writeheader();w.writerows(rows);return out.getvalue().encode('utf-8')
        assert import_custom_csv(payload())==1
        assert import_custom_csv(payload())==0
        rows.append(dict(rows[0],上限='nan'))
        rejected(lambda:import_custom_csv(payload()),'有限数值')
        assert len(list_catalog())==95
        rows.pop();rows[0]['水平类别']='低值';rows.append(dict(rows[0],水平类别='高值',上限='3'))
        specs=preview_custom_csv(payload());assert len(specs)==1 and len(specs[0]['imprecision'])==2
        assert select_rule(specs[0],category='高值')['value']==3
        rows[1]['水平类别']='低值'
        rejected(lambda:import_custom_csv(payload()),'范围重叠')
        assert len(list_catalog())==95


def test_instant_transfer_preserves_adopted_quality_goal():
    from tests.instant_v12_integration_smoke_test import entry
    from services.instant_service import confirm_instant_transfer_to_lj
    with IsolatedDatabase():
        item,config,_=new_lot('instant');goal=apply(item);batch=activate(item,config,'instant')
        for i in range(21):entry({'batch_id':batch},i)
        result=confirm_instant_transfer_to_lj(batch)
        assert runtime_goal('lj',result['target_batch_id'])==goal


def test_mixed_level_replacement_leaves_goal_pending_then_accepts_confirmation():
    from services.lot_lifecycle_service import source_context,create_level_combination
    from tests.lot_lifecycle_boundary_smoke_test import verification
    with IsolatedDatabase():
        item,config,_=new_lot('zscore',3);goal=apply(item,count=3);batch=activate(item,config,'zscore')
        with get_connection() as c:source,_,_=source_context(c,'zscore',batch)
        lot=create_qc_lot(qc_material_id=source['identity'][1],lot_no='NEW-HIGH',expiry_date='2028-12-31')
        high=create_qc_level(qc_material_lot_id=lot,level_name='新高值',level_order=3)
        vid=verification(source,lot)
        ids=[v['qc_level_id'] for v in source['levels']][:2]+[high]
        new=create_level_combination(source_batch_id=batch,level_ids=ids,verification_ids={high:vid},operator='验收',reason='更换高值水平',effective_at='2026-09-04')
        new_item=int(list_lot_config_items(new).iloc[0]['id'])
        assert decode(item_context('lot',new_item)['quality_goal_json'])['pending']
        rejected(lambda:activate_lot_config(new),'质量目标待核对')
        apply(new_item,count=3);new_batch=activate(new_item,new,'zscore')
        assert runtime_goal('zscore',batch)==goal
        assert [r['qc_level_id'] for r in runtime_goal('zscore',new_batch)['levels']]==ids


def test_page_catalog_and_adoption_existing_batch_readonly():
    with IsolatedDatabase():
        item,config,old=new_lot()
        at=AppTest.from_file(str(ROOT/'app.py'),default_timeout=30);at.session_state['show_quality_targets_page']=True;at.run()
        assert not at.exception
        at.selectbox(key='quality_lot_selector').set_value(item).run()
        at.selectbox(key=f'quality_lot_{item}_requirement').set_value('wst403-2024-047').run()
        at.text_input(key=f'quality_lot_{item}_person').set_value('页面验收')
        at.text_area(key=f'quality_lot_{item}_evidence').set_value('已核对项目、单位、浓度和来源')
        at.checkbox(key=f'quality_lot_{item}_confirmed').check().run()
        at.button(key=f'quality_lot_{item}_adopt').click().run();assert not at.exception
        assert decode(item_context('lot',item)['quality_goal_json'])['confirmed_by']=='页面验收'
        activate(item,config,'lj');at.run();assert not at.exception
        assert not any(b.key==f'quality_lot_{item}_adopt' for b in at.button)


if __name__=='__main__':
    for name,fn in list(globals().items()):
        if name.startswith('test_'):
            fn();print('PASS',name,flush=True)
