"""R0-01 reproducible acceptance on a new, explicitly isolated output directory.

Run with --output PATH. Refuses to reuse a database; never opens the user DB.
The retained database can then be used for real browser acceptance.
"""
from pathlib import Path
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run(output):
    import database as db
    from tests.project_management_v11_smoke_test import _seed_v11_configuration_dependencies
    from tests.quality_review_fixtures import confirm_fixture_project, confirm_fixture_lot
    from services.master_data_service import create_test_item, create_method
    from services.project_config_service import (create_project_template, save_template_items,
        list_template_items, activate_project_template, activate_lot_config, list_lot_config_items)
    from services.material_workflow_service import register_control_material, create_material_config, copy_material_config
    from services.project_workspace_service import resolve_batch_binding
    from services.lot_lifecycle_service import import_reviewed_results, result_lot_options, workbench_systems
    from services.instant_service import save_instant_result, build_instant_workbench_context, confirm_instant_transfer_to_lj
    from services.report_service import (build_lj_monthly_report_package, build_lj_monthly_report_pdf,
        save_lj_monthly_report_snapshot, build_zscore_monthly_report_package, build_zscore_monthly_report_pdf,
        save_zscore_monthly_report_snapshot, list_report_history_records)
    from services.storage_service import create_database_backup, restore_database_from_backup_file
    from zscore_logic import create_zscore_run, get_template_id_for_level_count
    from tests.material_workflow_smoke_test import context_levels
    from tests.reagent_lifecycle_edit_smoke_test import register, verification, switch, frozen_rows
    from services.reagent_lifecycle_edit_service import get_reagent_workspace_context, get_reagent_correction_context, save_reagent_correction

    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    path = output / 'acceptance.db'
    assert not path.exists(), 'Use a new directory to preserve previous evidence'
    db.DB_PATH = db.DEFAULT_DB_PATH = path
    db.STORAGE_CONFIG_PATH = output / 'storage_config.json'
    db.LEGACY_DB_CANDIDATES = []
    db.init_db()
    events = []

    def record(name, **details):
        events.append(dict(case=name, **details))
        (output / 'chain-results.json').write_text(json.dumps(events, ensure_ascii=False, indent=2))
        print(name, flush=True)

    def dump():
        with db.get_connection() as c:
            return '\n'.join(c.iterdump())

    def denied(action):
        before = dump()
        try:
            action()
        except ValueError as exc:
            assert dump() == before, 'Rejected operation partly wrote data'
            return str(exc)
        raise AssertionError('Expected rejection')

    d = _seed_v11_configuration_dependencies()
    levels = [register_control_material(material_id=d['qc_material_id'], level_name=name,
        level_code=str(i), lot_no=f'R0-MATERIAL-{i}', expiry_date='2100-12-31')
        for i, name in enumerate(['低值', '中值', '高值'], 1)]
    tid = create_project_template(template_name='接续验收混合项目', lab_instrument_id=d['lab_instrument_id'],
        qc_material_id=d['qc_material_id'], default_reagent_id=d['reagent_id'], project_group='综合')
    specs = [('单水平验收', 'lj', 1, 'raw'), ('两水平验收', 'zscore', 2, 'raw'),
             ('三水平验收', 'zscore', 3, 'log'), ('即时法验收', 'instant', 1, 'ct')]
    save_template_items(tid, [dict(test_item_id=create_test_item(chinese_name=name, default_unit_id=d['unit_id']),
        qc_method=method, level_count=count, input_value_type=scale, unit_id=d['unit_id'],
        method_id=create_method(method_name='验收方法'+str(i)), reagent_id=d['reagent_id'],
        target_n=20 if method=='instant' else 5, cv_limit=5, sort_order=i)
        for i,(name,method,count,scale) in enumerate(specs,1)])
    confirm_fixture_project(tid)
    activate_project_template(tid)
    items = list_template_items(tid).to_dict('records')
    selections = {r['id']: levels[:r['level_count']] for r in items}
    config = create_material_config(template_id=tid, selections=selections, config_name='接续验收首批')
    message = denied(lambda: activate_lot_config(config))
    assert '质量' in message
    for r in list_lot_config_items(config).to_dict('records'):
        denied(lambda: resolve_batch_binding(r['id']))
    confirm_fixture_lot(config)
    activate_lot_config(config)
    bindings = [resolve_batch_binding(r['id']) for r in list_lot_config_items(config).to_dict('records')]
    record('A2 新批缺少质量核对禁止启用和进入，核对后可进入', rejection=message)

    # Simulate a stale open entry/import window after the batch became a draft.
    with db.get_connection() as c:
        c.execute("UPDATE qc_lot_configs SET status='draft' WHERE id=?", (config,))
    lj = next(b for b in bindings if b['qc_method']=='lj')
    bid = lj['runtime_batch_id']
    direct = denied(lambda: db.add_result(bid,'2026-09-02',100))
    imported = denied(lambda: import_reviewed_results('lj',bid,[dict(test_time='2026-09-02',value=100,operator='验收',log_value=None,lot_selection={})]))
    activate_lot_config(config)
    record('A2 陈旧录入和导入统一拒绝且全库无半写入', direct=direct, imported=imported)

    workbench_systems()
    systems = get_reagent_workspace_context()['systems']
    reagent_data = {'data': d}
    old = register(reagent_data, 'R0-REAGENT-OLD', expiry='2100-12-31')
    future = register(reagent_data, 'R0-REAGENT-FUTURE', expiry='2100-12-31')
    for s in systems:
        verification(s,old); verification(s,future)
    original_events = switch(old,[s['id'] for s in systems], '2026-09-01')
    for binding in bindings:
        method,batch = binding['qc_method'],binding['runtime_batch_id']
        source=json.loads(binding['source_snapshot_json'])
        count=len(source['levels'])
        for i in range(6 if method!='instant' else 20):
            at=f'2026-09-{i+1:02d} 08:00:00'
            delta=[0,.1,-.1,.05,-.05,0][i%6]
            if method=='lj':
                rid=db.add_result(batch,at,100+delta,operator='验收员',log_value=None,lot_selection={'reagent_lot_id':old})
            elif method=='instant':
                save_instant_result(batch_id=batch,test_time=at,value=25+delta,log_value=None,operator='验收员',lot_selection={'reagent_lot_id':old})
                if i in (1,2,19):
                    summary=build_instant_workbench_context(batch)['summary']
                    assert bool(summary['si_ready']) == (i>=2)
                    record(f'即时法{i+1}点', si_ready=bool(summary['si_ready']))
            else:
                vals=[dict(level_id=f'Level {j+1}',raw_value=(100*(j+1) if count==2 else 3+j)+delta) for j in range(count)]
                result=create_zscore_run(batch_id=batch,test_time=at,operator='验收员',
                    level_results=vals,template_id=get_template_id_for_level_count(count),required_n=5,lot_selection={'reagent_lot_id':old})
                assert result['phase']==('target_building' if i<5 else 'formal_qc')
                if i==5: assert result['run_status']=='accept', result
                rid=result['id']
        if method=='zscore':
            denied(lambda: create_zscore_run(batch_id=batch,test_time='2026-09-22',operator='验收员',
                level_results=[dict(level_id='Level 1',raw_value=100)],
                template_id=get_template_id_for_level_count(count),required_n=5))
        if method!='instant':
            assert [r['lot_no'] for r in context_levels(method,rid)]==[f'R0-MATERIAL-{j+1}' for j in range(count)]
        record('A1 连续录入及实际材料',method=method,batch=batch,levels=count,scale=source['input_value_type'])

    before=frozen_rows()
    switch(future,[s['id'] for s in systems], '2099-01-01')
    assert frozen_rows()==before
    for b in bindings:
        assert result_lot_options(b['qc_method'],b['runtime_batch_id'],'2026-09-22')['suggested_lot_id']==old
        assert result_lot_options(b['qc_method'],b['runtime_batch_id'],'2099-01-01')['suggested_lot_id']==future
    context=get_reagent_correction_context(original_events[0])
    verification_id=verification(systems[0],future)
    context=get_reagent_correction_context(original_events[0])
    save_reagent_correction(original_events[0],dict(reagent_lot_id=future,verification_id=verification_id,
        effective_at='2026-09-02',operator='验收员',reason='使用事件日期与批号核对',confirmed=True),expected_fingerprint=context['fingerprint'])
    assert frozen_rows()==before
    record('A3 未来试剂安排与事件更正不回写原检测')

    replacement=register_control_material(material_id=d['qc_material_id'],level_name='中值',level_code='2',
        lot_no='R0-MATERIAL-NEW',expiry_date='2100-12-31')
    source_items=list_lot_config_items(config).to_dict('records')
    z3=next(r for r in source_items if r['level_count']==3)
    before=frozen_rows()
    partial=copy_material_config(source_config_id=config,selections={z3['id']:[levels[0],replacement,levels[2]]})
    after=frozen_rows()
    assert all(after[k][:len(v)]==v for k,v in before.items())
    assert all(len(after[k])==len(before[k]) for k in before if k!='qc_config_snapshots')
    denied(lambda: activate_lot_config(partial))
    full_levels=[register_control_material(material_id=d['qc_material_id'],level_name=n,level_code=str(i),
        lot_no=f'R0-NEXT-{i}',expiry_date='2100-12-31') for i,n in enumerate(['低值','中值','高值'],1)]
    full=copy_material_config(source_config_id=config,selections={r['id']:full_levels[:r['level_count']] for r in source_items})
    after=frozen_rows()
    assert all(after[k][:len(v)]==v for k,v in before.items())
    assert all(len(after[k])==len(before[k]) for k in before if k!='qc_config_snapshots')
    confirm_fixture_lot(full); activate_lot_config(full)
    for r in list_lot_config_items(full).to_dict('records'):
        b=resolve_batch_binding(r['id'])
        table='instant_results' if b['qc_method']=='instant' else ('zscore_runs' if b['qc_method']=='zscore' else 'results')
        with db.get_connection() as c:
            assert c.execute(f'SELECT count(*) FROM {table} WHERE batch_id=?',(b['runtime_batch_id'],)).fetchone()[0]==0
    record('A3 部分换批待确认、完整换批空结果，旧记录无复制回写',partial=partial,full=full)

    for b in bindings:
        method,batch=b['qc_method'],b['runtime_batch_id']
        if method=='instant': continue
        if method=='lj':
            p=build_lj_monthly_report_package(batch,'2026-09'); pdf=build_lj_monthly_report_pdf(p); hid=save_lj_monthly_report_snapshot(p)
        else:
            p=build_zscore_monthly_report_package(batch,'2026-09'); pdf=build_zscore_monthly_report_pdf(p); hid=save_zscore_monthly_report_snapshot(p)
        assert p.report.basic_info.config_snapshot_id
        (output/f'{method}-{batch}.pdf').write_bytes(pdf)
        record('同链月报与历史',method=method,batch=batch,history_id=hid,formal_count=p.report.statistics.formal_count)
    instant=next(b for b in bindings if b['qc_method']=='instant')['runtime_batch_id']
    assert db.get_instant_batch(instant)['transfer_status']=='not_transferred'
    # Keep browser copy at the 20-point confirmation step.
    preview=create_database_backup(output/'browser-source')
    import shutil
    shutil.copy2(preview.target_path,output/'preview.db')
    transfer=confirm_instant_transfer_to_lj(instant)
    assert transfer['building_count']==20 and transfer['formal_count']==0
    assert len(db.get_results(transfer['target_batch_id']))==20
    record('即时法20点后人工转LJ',transfer=transfer)
    backup=create_database_backup(output/'backups')
    before=dump()
    db.create_project('恢复后应消失的验收标记')
    restored=restore_database_from_backup_file(backup.target_path)
    assert dump()==before and restored.protection_backup_path.exists()
    assert len(list_report_history_records())==3
    with db.get_connection() as c:
        assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert not c.execute('PRAGMA foreign_key_check').fetchall()
    record('同链备份恢复：全库内容一致、3份报告历史保留、保护备份有效')
    (output/'fixture.json').write_text(json.dumps(dict(template_id=tid,config_id=config,bindings=bindings),ensure_ascii=False,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    run(parser.parse_args().output)
