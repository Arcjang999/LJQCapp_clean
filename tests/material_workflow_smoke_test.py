"""Isolated regressions for physical material identity across all three workbenches."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from database import add_result, get_batch, get_connection, get_instant_batch, get_results, init_db
from qc_logic import calculate_qc_results
from services.instant_service import (
    build_instant_workbench_context, confirm_instant_transfer_to_lj, save_instant_result,
)
from services.instant_workbench_service import sync_instant_workbench_bindings
from services.lot_lifecycle_service import create_target_profile
from services.master_data_service import create_test_item
from services.material_workflow_service import create_material_config, copy_material_config, register_control_material
from services.project_config_service import (
    activate_lot_config, activate_project_template, create_project_template,
    list_template_items, save_template_items,
)
from services.workbench_config_service import sync_lj_workbench_bindings
from services.zscore_workbench_service import sync_zscore_workbench_bindings
from tests.lot_lifecycle_smoke_test import rejected
from tests.project_management_v11_smoke_test import TemporaryDatabaseContext, _seed_v11_configuration_dependencies
from tests.quality_review_fixtures import confirm_fixture_lot, confirm_fixture_project
from zscore_logic import create_zscore_run, rebuild_zscore_batch_state


def mixed_fixture():
    data = _seed_v11_configuration_dependencies()
    material = data['qc_material_id']
    expected = [
        ('低值', '001', 'ACTUAL-LOW', '2028-01-31'),
        ('中值', '002', 'ACTUAL-MID', '2028-02-29'),
        ('高值', '003', 'ACTUAL-HIGH', '2028-03-31'),
    ]
    levels = [register_control_material(material_id=material, level_name=name, level_code=code,
        lot_no=lot, expiry_date=expiry) for name, code, lot, expiry in expected]
    tid = create_project_template(template_name='混合检测项目', lab_instrument_id=data['lab_instrument_id'],
        qc_material_id=material, default_reagent_id=data['reagent_id'], project_group='血筛')
    instant_test = create_test_item(chinese_name='材料链即时项目', default_unit_id=data['unit_id'])
    items = [
        ('zscore', data['zscore_item_id'], 3, 5),
        ('lj', data['lj_item_id'], 1, 5),
        ('instant', instant_test, 1, 20),
    ]
    save_template_items(tid, [dict(test_item_id=test, qc_method=method, input_value_type='raw',
        unit_id=data['unit_id'], method_id=data['method_id'], reagent_id=data['reagent_id'],
        level_count=count, target_n=n, cv_limit=5.0, sort_order=index)
        for index, (method, test, count, n) in enumerate(items, 1)])
    confirm_fixture_project(tid)
    activate_project_template(tid)
    by_method = {row['qc_method']: row for row in list_template_items(tid).to_dict('records')}
    selections = {by_method['zscore']['id']: levels, by_method['lj']['id']: [levels[2]],
        by_method['instant']['id']: [levels[1]]}
    config = create_material_config(template_id=tid, selections=selections, config_name='首次三个实际批号')
    confirm_fixture_lot(config)
    activate_lot_config(config)
    sync_lj_workbench_bindings()
    assert sync_zscore_workbench_bindings() == []
    assert sync_instant_workbench_bindings() == []
    with get_connection() as c:
        bindings = {row['qc_method']: dict(row) for row in c.execute(
            'SELECT * FROM qc_workbench_bindings WHERE lot_config_id=?', (config,))}
    return dict(data=data, levels=levels, expected=expected, template_id=tid, config_id=config,
        bindings=bindings, selections=selections)


def context_levels(method, result_id):
    column = {'lj': 'lj_result_id', 'zscore': 'zscore_run_id', 'instant': 'instant_result_id'}[method]
    with get_connection() as c:
        return [dict(row) for row in c.execute(f'''SELECT l.* FROM qc_result_context_levels l
            JOIN qc_result_contexts c ON c.id=l.context_id WHERE c.{column}=? ORDER BY l.level_order''', (result_id,))]


def assert_materials(rows, expected):
    assert [(row['level_name'], row['level_code'], row['lot_no'], row['expiry_date']) for row in rows] == expected


def instant_entry(batch, minute, value=100.0):
    return save_instant_result(batch_id=batch, test_time=f'2026-09-03 08:{minute:02d}:00',
        value=value, log_value=None, operator='材料链回归')


def test_mixed_methods_record_actual_materials_and_preserve_order():
    with TemporaryDatabaseContext():
        f = mixed_fixture()
        for method, order in [('lj', [2]), ('zscore', [0, 1, 2]), ('instant', [1])]:
            source = json.loads(f['bindings'][method]['source_snapshot_json'])
            assert_materials(source['levels'], [f['expected'][i] for i in order])
            assert json.loads(source['quality_review_json'])['status'] == 'confirmed'
        lj_batch = f['bindings']['lj']['runtime_batch_id']
        assert get_batch(lj_batch)['lot_no'] == 'ACTUAL-HIGH'
        add_result(lj_batch, '2026-09-03 08:00', 100, operator='材料链回归')
        result_id = int(get_results(lj_batch).iloc[0]['id'])
        assert_materials(context_levels('lj', result_id), [f['expected'][2]])
        instant_batch = f['bindings']['instant']['runtime_batch_id']
        assert get_instant_batch(instant_batch)['lot_no'] == 'ACTUAL-MID'
        instant_entry(instant_batch, 0)
        with get_connection() as c:
            result_id = c.execute('SELECT id FROM instant_results WHERE batch_id=?', (instant_batch,)).fetchone()[0]
        assert_materials(context_levels('instant', result_id), [f['expected'][1]])
        run = create_zscore_run(batch_id=f['bindings']['zscore']['runtime_batch_id'], test_time='2026-09-03',
            operator='材料链回归', template_id='3_level_threes',
            level_results=[dict(level_id=f'Level {i+1}', raw_value=100*(i+1)) for i in range(3)])
        assert_materials(context_levels('zscore', run['id']), f['expected'])


def test_stale_material_mapping_rejects_atomically_and_replacement_is_separate():
    with TemporaryDatabaseContext():
        f = mixed_fixture()
        binding = f['bindings']['lj']
        batch = binding['runtime_batch_id']
        add_result(batch, '2026-09-03 08:00', 100, operator='材料链回归')
        before = get_results(batch).to_dict('records')
        with get_connection() as c:
            c.execute('UPDATE qc_lot_config_item_levels SET qc_level_id=? WHERE lot_config_item_id=?',
                (f['levels'][0], binding['lot_config_item_id']))
            context_count = c.execute('SELECT COUNT(*) FROM qc_result_contexts').fetchone()[0]
        rejected(lambda: add_result(batch, '2026-09-03 08:01', 101, operator='材料链回归'), '实际质控材料')
        assert get_results(batch).to_dict('records') == before
        with get_connection() as c:
            assert c.execute('SELECT COUNT(*) FROM qc_result_contexts').fetchone()[0] == context_count
            c.execute('UPDATE qc_lot_config_item_levels SET qc_level_id=? WHERE lot_config_item_id=?',
                (f['levels'][2], binding['lot_config_item_id']))
        new_level = register_control_material(material_id=f['data']['qc_material_id'],
            level_name='高值', level_code='003', lot_no='NEXT-HIGH', expiry_date='2029-03-31')
        config = copy_material_config(source_config_id=f['config_id'],
            selections={binding['lot_config_item_id']: [new_level]})
        assert get_results(batch).to_dict('records') == before
        rejected(lambda: activate_lot_config(config), '质量目标')
        confirm_fixture_lot(config)
        activate_lot_config(config)
        sync_lj_workbench_bindings()
        with get_connection() as c:
            new_binding = dict(c.execute('SELECT * FROM qc_workbench_bindings WHERE lot_config_id=?', (config,)).fetchone())
            assert c.execute('PRAGMA foreign_key_check').fetchall() == []
        assert new_binding['runtime_project_id'] == binding['runtime_project_id']
        assert new_binding['runtime_batch_id'] != batch
        assert get_results(new_binding['runtime_batch_id']).empty
        source = json.loads(new_binding['source_snapshot_json'])
        assert_materials(source['levels'], [('高值', '003', 'NEXT-HIGH', '2029-03-31')])
        assert get_results(batch).to_dict('records') == before


def test_unrelated_anchor_lot_does_not_block_other_test_items():
    with TemporaryDatabaseContext():
        f = mixed_fixture()
        with get_connection() as c:
            c.execute('UPDATE md_qc_material_lots SET is_disabled=1 WHERE lot_no=?', ('ACTUAL-LOW',))
        # The first item's low control is only an internal config anchor for the others.
        add_result(f['bindings']['lj']['runtime_batch_id'], '2026-09-03', 100, operator='材料链回归')
        instant_entry(f['bindings']['instant']['runtime_batch_id'], 0)
        rejected(lambda: create_zscore_run(batch_id=f['bindings']['zscore']['runtime_batch_id'],
            test_time='2026-09-03', operator='材料链回归', template_id='3_level_threes',
            level_results=[dict(level_id=f'Level {i+1}', raw_value=100*(i+1)) for i in range(3)]), '停用')
        sync_lj_workbench_bindings()
        sync_instant_workbench_bindings()
        with get_connection() as c:
            statuses = {r['qc_method']: r['binding_status'] for r in c.execute(
                'SELECT * FROM qc_workbench_bindings WHERE lot_config_id=?', (f['config_id'],))}
        assert statuses['lj'] == statuses['instant'] == 'active'


def test_three_point_twenty_point_transfer_retains_material_and_values():
    with TemporaryDatabaseContext():
        f = mixed_fixture()
        batch = f['bindings']['instant']['runtime_batch_id']
        values = [100 + (i % 3 - 1) * 0.1 for i in range(21)]
        for i, value in enumerate(values):
            instant_entry(batch, i, value)
            context = build_instant_workbench_context(batch)
            if i < 20:
                assert bool(context['summary']['si_ready']) == (i >= 2)
            if i == 18:
                rejected(lambda: confirm_instant_transfer_to_lj(batch), '有效点不足')
        assert get_instant_batch(batch)['transfer_status'] == 'not_transferred'
        transfer = confirm_instant_transfer_to_lj(batch)
        assert transfer['building_count'] == 20 and transfer['formal_count'] == 1
        result_rows = get_results(transfer['target_batch_id'])
        assert result_rows['value'].tolist() == values
        assert get_batch(transfer['target_batch_id'])['lot_no'] == 'ACTUAL-MID'
        from services.quality_review_service import runtime_review
        assert runtime_review('lj', transfer['target_batch_id']) == runtime_review('instant', batch)
        from services.project_workspace_service import resolve_batch_binding
        destination = resolve_batch_binding(f['bindings']['instant']['lot_config_item_id'])
        assert destination['qc_method'] == 'lj'
        assert destination['runtime_batch_id'] == transfer['target_batch_id']
        assert destination['runtime_project_id'] == transfer['target_project_id']
        for result in result_rows.to_dict('records'):
            assert_materials(context_levels('lj', result['id']), [f['expected'][1]])
        rejected(lambda: instant_entry(batch, 22), '只读')
        init_db()
        with get_connection() as c:
            assert c.execute('PRAGMA foreign_key_check').fetchall() == []


def test_statistics_and_run_conclusions_use_each_level_parameters():
    with TemporaryDatabaseContext():
        f = mixed_fixture()
        batch = f['bindings']['lj']['runtime_batch_id']
        create_target_profile(method='lj', batch_id=batch, levels=[dict(level_id='Level 1', mean=100, sd=2)],
            source='manual', evidence='独立固定参数回归', confirmed_by='回归', effective_at='2026-09-02')
        for i in range(4):
            add_result(batch, f'2026-09-03 08:0{i}', 103, operator='材料链回归')
        frame, stats = calculate_qc_results(get_results(batch), 5)
        assert stats['mean'] == 100 and stats['sd'] == 2 and stats['cv'] == 2
        assert frame['z'].tolist() == [1.5] * 4
        assert '4_1s' in frame.iloc[-1]['rule_hits']
        batch = f['bindings']['zscore']['runtime_batch_id']
        create_target_profile(method='zscore', batch_id=batch,
            levels=[dict(level_id=f'Level {i+1}', mean=100*(i+1), sd=2*(i+1)) for i in range(3)],
            source='manual', evidence='独立固定参数回归', confirmed_by='回归', effective_at='2026-09-02')
        run = create_zscore_run(batch_id=batch, test_time='2026-09-03', operator='材料链回归',
            template_id='3_level_threes', level_results=[dict(level_id=f'Level {i+1}', raw_value=value)
                for i, value in enumerate([100, 216, 300])])
        assert run['phase'] == 'formal_qc'
        assert '1_3s' in str(run['rule_hits_run'])
        state = rebuild_zscore_batch_state(batch)
        assert '1_3s' in str(state['runs'][0]['rule_hits_run'])
        assert_materials(context_levels('zscore', run['id']), f['expected'])


def test_fixed_batch_cannot_enable_an_unreviewed_extra_item():
    from tests.project_management_v11_smoke_test import _build_active_source_config
    from services.project_config_service import copy_lot_config, list_lot_config_items, save_lot_item_levels
    from services.lot_lifecycle_service import change_qc_lot
    with TemporaryDatabaseContext():
        data = _seed_v11_configuration_dependencies()
        _, source_id = _build_active_source_config(data)
        sync_lj_workbench_bindings(); sync_zscore_workbench_bindings()
        target_id = copy_lot_config(source_lot_config_id=source_id, target_qc_material_lot_id=data['target_lot_id'])
        target_items = list_lot_config_items(target_id)
        lj_item = target_items[target_items.qc_method == 'lj'].iloc[0]
        z_item = target_items[target_items.qc_method == 'zscore'].iloc[0]
        save_lot_item_levels(int(lj_item.id), [dict(qc_level_id=data['target_levels'][0], target_source='building')])
        with get_connection() as c:
            c.execute('UPDATE qc_lot_config_items SET is_enabled=0 WHERE id=?', (int(z_item.id),))
        confirm_fixture_lot(target_id); activate_lot_config(target_id)
        with get_connection() as c:
            before_events = c.execute('SELECT COUNT(*) FROM qc_lot_change_events').fetchone()[0]
            source_item_id = c.execute('SELECT source_template_item_id FROM qc_lot_config_items WHERE id=?',
                (int(z_item.id),)).fetchone()[0]
        rejected(lambda: change_qc_lot(source_config_id=source_id, target_qc_lot_id=data['target_lot_id'],
            template_item_ids=[source_item_id], operator='回归', reason='补加检测项',
            effective_at='2026-09-03'), '目标批次已经固定')
        with get_connection() as c:
            assert c.execute('SELECT is_enabled FROM qc_lot_config_items WHERE id=?', (int(z_item.id),)).fetchone()[0] == 0
            assert c.execute('SELECT COUNT(*) FROM qc_lot_change_events').fetchone()[0] == before_events


def test_unavailable_lj_navigation_reports_configuration_issue():
    from services.project_workspace_service import resolve_batch_binding
    from services.project_config_service import set_lot_config_disabled
    from tests.lj_v12_integration_smoke_test import _seed_active_lj_configuration
    with TemporaryDatabaseContext():
        f = mixed_fixture()
        _seed_active_lj_configuration()
        set_lot_config_disabled(f['config_id'], is_disabled=True, reason='导航边界回归')
        rejected(lambda: resolve_batch_binding(f['bindings']['lj']['lot_config_item_id']), '尚不能进入')


if __name__ == '__main__':
    tests = [value for name, value in list(globals().items()) if name.startswith('test_')]
    for test in tests:
        test()
        print(f'PASS {test.__name__}')
    print(f'All {len(tests)} material workflow smoke tests passed.')
