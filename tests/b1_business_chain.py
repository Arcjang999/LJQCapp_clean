"""Actual saved IgG configurations and unchanged QC algorithms, on a fresh test DB."""
from pathlib import Path
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def seed(output):
    import database as db
    from services.master_data_service import list_test_items, list_units, create_method
    from services.project_config_service import (create_project_template, save_template_items,
        list_template_items, activate_project_template, activate_lot_config,
        list_lot_config_items, save_lot_item_levels)
    from services.material_workflow_service import register_control_material, create_material_config
    from services.project_workspace_service import resolve_batch_binding
    from services.quality_target_service import adopt_requirement
    from services.lot_lifecycle_service import workbench_systems, create_target_profile
    from services.reagent_lifecycle_edit_service import get_reagent_workspace_context
    from tests.project_management_v11_smoke_test import _seed_v11_configuration_dependencies
    from tests.reagent_lifecycle_edit_smoke_test import register, verification, switch
    from pages.lj_sections import build_lj_workbench_context
    from zscore_logic import create_zscore_run, get_template_id_for_level_count
    from services.out_of_control_service import list_pending, read_source

    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    path = output / 'acceptance.db'
    assert not path.exists(), 'Fresh output directory required'
    db.DB_PATH = db.DEFAULT_DB_PATH = path
    db.STORAGE_CONFIG_PATH = output / 'storage_config.json'
    db.LEGACY_DB_CANDIDATES = []
    db.init_db()
    d = _seed_v11_configuration_dependencies()
    item = list_test_items().loc[lambda df: df.standard_code == '1101804A'].iloc[0]
    unit = int(list_units().loc[lambda df: df.symbol == 'g/L'].iloc[0]['id'])
    method_id = create_method(method_name='化学发光免疫法')
    conditions = dict(technique='immunoassay', specimen='血清', result_kind='quantitative',
        result_scale='concentration', purpose='clinical')
    levels = [register_control_material(material_id=d['qc_material_id'], level_name=name,
        level_code=str(i), lot_no=f'B1-MATERIAL-{i}', expiry_date='2100-12-31')
        for i, name in enumerate(['低值', '中值', '高值'], 1)]
    bindings = []
    for method, count, label in [('lj', 1, 'IgG 单水平'), ('zscore', 2, 'IgG 两水平'), ('zscore', 3, 'IgG 三水平')]:
        tid = create_project_template(template_name=label, lab_instrument_id=d['lab_instrument_id'],
            qc_material_id=d['qc_material_id'], default_reagent_id=d['reagent_id'])
        save_template_items(tid, [dict(test_item_id=int(item['id']), qc_method=method,
            input_value_type='raw', unit_id=unit, method_id=method_id, reagent_id=d['reagent_id'],
            level_count=count, target_n=20, cv_limit=None)])
        ti = list_template_items(tid).iloc[0]
        adopt_requirement('project', int(ti['id']), 'wst403-2024-042', confirmed_by='隔离验收人员',
            evidence='内置IgG、血清、g/L和免疫测定条件核对，仅使用合成检测值验收软件。', context=conditions)
        activate_project_template(tid)
        config = create_material_config(template_id=tid, selections={int(ti['id']): levels[:count]}, config_name=label+'首批')
        li = list_lot_config_items(config).iloc[0]
        save_lot_item_levels(int(li['id']), [dict(qc_level_id=v, target_source='manual',
            target_mean=10.0 * (i+1), target_sd=0.5, target_confirmed=True) for i, v in enumerate(levels[:count])])
        adopt_requirement('lot', int(li['id']), 'wst403-2024-042', confirmed_by='隔离验收人员',
            evidence='合成质控品各水平及原标准范围核对。', context=conditions,
            levels=[dict(level_order=i+1, concentration=10.0*(i+1)) for i in range(count)])
        activate_lot_config(config)
        b = resolve_batch_binding(int(li['id']))
        create_target_profile(method=method, batch_id=b['runtime_batch_id'],
            levels=[dict(level_id=f'Level {i+1}', mean=10.0*(i+1), sd=0.5) for i in range(count)],
            source='manual', evidence='隔离合成数据已确认均值和标准差，仅作软件验收。',
            confirmed_by='隔离验收人员', effective_at='2026-09-01')
        bindings.append(dict(b, fixture_label=label, fixture_level_count=count))
    workbench_systems()
    reagent = register({'data': d}, 'B1-REAGENT-01', expiry='2100-12-31')
    systems = get_reagent_workspace_context()['systems']
    for system in systems: verification(system, reagent)
    switch(reagent, [s['id'] for s in systems], '2026-09-01')
    sources = []
    for b in bindings:
        method, batch, count = b['qc_method'], b['runtime_batch_id'], b['fixture_level_count']
        for n, delta in enumerate([2.5, 0.0, -0.1, 1.1]):
            at = f'2026-09-28 {8+n:02d}:00:00'
            if method == 'lj':
                rid = db.add_result(batch, at, 10 + delta, operator='隔离验收人员',
                    lot_selection={'reagent_lot_id': reagent})
                build_lj_workbench_context(batch)
                source_type = 'lj_result'
            else:
                run = create_zscore_run(batch_id=batch, test_time=at, operator='隔离验收人员',
                    level_results=[dict(level_id=f'Level {i+1}', raw_value=10*(i+1)+(delta if i == count-1 else 0)) for i in range(count)],
                    template_id=get_template_id_for_level_count(count), required_n=20,
                    lot_selection={'reagent_lot_id': reagent})
                rid = run['id']; source_type = 'zscore_run'
            snapshot = read_source(source_type, rid)
            assert snapshot['phase'] == 'formal', snapshot
            assert len(snapshot['levels']) == count
            if n == 0: assert snapshot['classification'] == 'reject', snapshot
            sources.append(dict(source_type=source_type, source_id=rid, position=n,
                method=method, level_count=count, classification=snapshot['classification']))
    pending = list_pending()
    assert len([r for r in pending['items'] if r['event_id'] is None]) >= 3
    result = dict(bindings=bindings, sources=sources, pending_count=pending['count'])
    (output / 'fixture.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    seed(parser.parse_args().output)
