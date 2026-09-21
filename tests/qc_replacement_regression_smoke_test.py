"""Replacement identity, pending confirmation and history remain intact on isolated data."""
from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from database import get_connection
from services.material_workflow_service import create_material_config, copy_material_config, register_control_material
from services.lot_lifecycle_service import create_target_profile
from services.project_config_service import (activate_project_template, create_project_template,
    list_lot_config_items, list_lot_item_levels, list_template_items, save_lot_item_levels,
    save_template_items, activate_lot_config, copy_lot_config)
from tests.project_management_v11_smoke_test import TemporaryDatabaseContext, _seed_v11_configuration_dependencies
from tests.quality_review_fixtures import confirm_fixture_project
from tests.quality_targets_smoke_test import new_lot, apply, activate
from tests.lot_lifecycle_smoke_test import IsolatedDatabase, rejected
from zscore_logic import create_zscore_run


def database_dump():
    with get_connection() as connection:
        return '\n'.join(connection.iterdump())


def latest_snapshot(config_id):
    with get_connection() as connection:
        return json.loads(connection.execute('SELECT snapshot_json FROM qc_config_snapshots WHERE lot_config_id=? ORDER BY id DESC',
            (config_id,)).fetchone()[0])


def historical_rows(config_id):
    """Capture old config and runtime records, excluding newly created draft rows."""
    with get_connection() as connection:
        result = {table: [tuple(row) for row in connection.execute(f'SELECT * FROM {table} ORDER BY id')]
            for table in ('results', 'zscore_runs', 'zscore_level_results', 'instant_results',
                          'qc_result_contexts', 'qc_result_context_levels', 'qc_result_evaluations',
                          'qc_target_profiles', 'qc_workbench_bindings')}
        result['source_config'] = tuple(connection.execute('SELECT * FROM qc_lot_configs WHERE id=?', (config_id,)).fetchone())
        result['source_items'] = [tuple(row) for row in connection.execute('SELECT * FROM qc_lot_config_items WHERE lot_config_id=? ORDER BY id', (config_id,))]
        result['source_levels'] = [tuple(row) for row in connection.execute('''SELECT l.* FROM qc_lot_config_item_levels l
            JOIN qc_lot_config_items i ON i.id=l.lot_config_item_id WHERE i.lot_config_id=? ORDER BY l.id''', (config_id,))]
        result['source_snapshots'] = [tuple(row) for row in connection.execute('SELECT * FROM qc_config_snapshots WHERE lot_config_id=? ORDER BY id', (config_id,))]
        return result


def test_one_selected_method_never_enables_another_method_for_same_analyte():
    with TemporaryDatabaseContext():
        data = _seed_v11_configuration_dependencies()
        template_id = create_project_template(template_name='同项目两种质控方式',
            lab_instrument_id=data['lab_instrument_id'], qc_material_id=data['qc_material_id'])
        save_template_items(template_id, [dict(test_item_id=data['lj_item_id'], qc_method=method,
            input_value_type='raw', unit_id=data['unit_id'], method_id=data['method_id'],
            reagent_id=data['reagent_id'], level_count=1, target_n=20, cv_limit=5)
            for method in ('lj', 'instant')])
        confirm_fixture_project(template_id)
        activate_project_template(template_id)
        items = list_template_items(template_id)
        source = create_material_config(template_id=template_id,
            selections={int(row.id): [data['source_levels'][0]] for row in items.itertuples()})
        source_items = list_lot_config_items(source)
        chosen = int(source_items[source_items.qc_method == 'lj'].iloc[0].id)
        before = historical_rows(source)
        copied = copy_material_config(source_config_id=source, selections={chosen: [data['target_levels'][0]]})
        new_items = list_lot_config_items(copied)
        assert new_items[new_items.is_enabled == 1].qc_method.tolist() == ['lj']
        assert set(new_items.qc_method) == {'lj', 'instant'}
        assert {row['qc_method']: row['is_enabled'] for row in latest_snapshot(copied)['items']} == {'lj': 1, 'instant': 0}
        assert historical_rows(source) == before


def test_partial_replacement_tracks_actual_levels_and_clears_quality_confirmation():
    with IsolatedDatabase():
        item_id, source, _ = new_lot('zscore', 3, name='逐水平换批回归')
        original = list_lot_item_levels(item_id)
        old_ids = original.qc_level_id.astype(int).tolist()
        save_lot_item_levels(item_id, [dict(qc_level_id=level_id, target_source='manual',
            target_mean=100 * order, target_sd=2 * order, target_confirmed=True,
            notes=f'原第 {order} 水平说明') for order, level_id in enumerate(old_ids, 1)])
        goal = apply(item_id, count=3)
        batch = activate(item_id, source, 'zscore')
        create_target_profile(method='zscore', batch_id=batch,
            levels=[dict(level_id=f'Level {order}', mean=100 * order, sd=2 * order) for order in range(1, 4)],
            source='manual', evidence='旧批参数已经核对', confirmed_by='换批回归', effective_at='2026-09-01')
        create_zscore_run(batch_id=batch, test_time='2026-09-03', operator='换批回归',
            template_id='3_level_threes', required_n=5,
            level_results=[dict(level_id=f'Level {order}', raw_value=100 * order) for order in range(1, 4)])
        with get_connection() as connection:
            material_id = connection.execute('SELECT qc_material_id FROM qc_lot_configs WHERE id=?', (source,)).fetchone()[0]
            source_review = json.loads(connection.execute('SELECT quality_review_json FROM qc_lot_config_items WHERE id=?', (item_id,)).fetchone()[0])
        changed_id = register_control_material(material_id=material_id, level_name='中值', level_code='M',
            lot_no='REPLACE-MIDDLE', expiry_date='2029-02-28')
        before = historical_rows(source)
        # Reversing the retained controls distinguishes actual material identity from position.
        copied = copy_material_config(source_config_id=source, selections={item_id: [old_ids[2], changed_id, old_ids[0]]})
        copied_item = list_lot_config_items(copied).iloc[0]
        levels = list_lot_item_levels(int(copied_item.id))
        assert levels.qc_level_id.astype(int).tolist() == [old_ids[2], changed_id, old_ids[0]]
        assert levels.target_source.tolist() == ['copied_pending', 'building', 'copied_pending']
        assert levels.iloc[0].target_mean == 300 and levels.iloc[0].target_sd == 6
        assert levels.iloc[2].target_mean == 100 and levels.iloc[2].target_sd == 2
        assert levels.iloc[[0, 2]].target_confirmed.astype(int).tolist() == [0, 0]
        assert levels.iloc[0].notes == '原第 3 水平说明'
        assert levels.iloc[2].notes == '原第 1 水平说明'
        copied_goal = json.loads(copied_item.quality_goal_json)
        assert copied_goal['spec'] == goal['spec']
        assert copied_goal['pending'] and copied_goal['levels'] == []
        assert copied_goal['confirmed_by'] == copied_goal['evidence'] == ''
        copied_review = json.loads(copied_item.quality_review_json)
        assert copied_review['status'] == 'pending' and copied_review['levels'] == []
        assert copied_review['selected_source_id'] == source_review['selected_source_id']
        assert copied_review['candidates'] == source_review['candidates']
        assert historical_rows(source) == before
        rejected(lambda: activate_lot_config(copied), '待确认')
        assert historical_rows(source) == before
        frozen = latest_snapshot(copied)['items'][0]['levels']
        assert [(level['qc_level_id'], level['target_mean']) for level in frozen] == [(old_ids[2], 300), (changed_id, None), (old_ids[0], 100)]
        assert frozen[1]['lot_no'] == 'REPLACE-MIDDLE' and frozen[1]['expiry_date'] == '2029-02-28'


def test_rejected_replacement_keeps_every_table_unchanged():
    with IsolatedDatabase():
        item_id, source, _ = new_lot('zscore', 2)
        old_ids = list_lot_item_levels(item_id).qc_level_id.astype(int).tolist()
        before = database_dump()
        rejected(lambda: copy_material_config(source_config_id=source, selections={item_id: old_ids}), '相同')
        assert database_dump() == before
        rejected(lambda: copy_material_config(source_config_id=source, selections={item_id: [old_ids[0], old_ids[0]]}), '不要重复')
        assert database_dump() == before
        # A pre-existing target never changes just because the caller retries a copy.
        with get_connection() as connection:
            original = connection.execute('SELECT copied_from_config_id,qc_material_lot_id FROM qc_lot_configs WHERE id=?', (source,)).fetchone()
        rejected(lambda: copy_lot_config(source_lot_config_id=original[0], target_qc_material_lot_id=original[1]), '已存在')
        assert database_dump() == before


if __name__ == '__main__':
    for name, function in list(globals().items()):
        if name.startswith('test_'):
            function()
            print('PASS', name, flush=True)
