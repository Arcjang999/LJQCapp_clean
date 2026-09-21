"""Current parameter references survive replacement by actual control identity."""
from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database import get_connection
from services.lot_lifecycle_service import create_target_profile
from services.material_workflow_service import register_control_material
from services.project_config_service import (
    activate_lot_config, list_lot_config_items, list_lot_item_levels, save_lot_item_levels,
)
from services.qc_replacement_edit_service import get_qc_replacement_context, save_qc_replacement
from services.target_profile_edit_service import get_target_profile_context
from services.zscore_workbench_service import sync_zscore_workbench_bindings
from tests.lot_lifecycle_smoke_test import IsolatedDatabase, rejected
from tests.qc_replacement_regression_smoke_test import database_dump, historical_rows, latest_snapshot
from tests.quality_targets_smoke_test import activate, apply, new_lot
from zscore_logic import create_zscore_run


def seed_source(*, current=True, future=True):
    item, config, _ = new_lot('zscore', 3, name='换批参数来源专项')
    ids = list_lot_item_levels(item).qc_level_id.astype(int).tolist()
    save_lot_item_levels(item, [dict(qc_level_id=identifier, target_source='manual',
        target_mean=10*order, target_sd=0.5*order, target_confirmed=True,
        notes=f'原第 {order} 水平备注') for order, identifier in enumerate(ids, 1)])
    goal = apply(item, count=3)
    batch = activate(item, config, 'zscore')
    profile = None
    if current:
        profile = create_target_profile(method='zscore', batch_id=batch,
            levels=[dict(level_id=f'Level {order}', mean=111*order, sd=3*order) for order in (1, 2, 3)],
            source='revision', evidence='当前已确认值与最初配置值不同', confirmed_by='当前确认人',
            effective_at='2026-09-01')
        create_zscore_run(batch_id=batch, test_time='2026-09-02', operator='换批来源回归',
            template_id='3_level_threes', required_n=5,
            level_results=[dict(level_id=f'Level {order}', raw_value=111*order) for order in (1, 2, 3)])
    future_profile = None
    if future:
        future_profile = create_target_profile(method='zscore', batch_id=batch,
            levels=[dict(level_id=f'Level {order}', mean=999*order, sd=9*order) for order in (1, 2, 3)],
            source='revision', evidence='尚未生效的未来计划', confirmed_by='未来确认人',
            effective_at='2099-01-01')
    context = get_qc_replacement_context(config)
    new_level = register_control_material(material_id=context['config']['qc_material_id'],
        level_name='中值', level_code='M', lot_no='PROFILE-REPLACE-MID', expiry_date='2029-02-28')
    return dict(item=item, config=config, batch=batch, ids=ids, new_level=new_level,
                profile=profile, future_profile=future_profile, goal=goal)


def raw_levels(item):
    with get_connection() as connection:
        return [dict(row) for row in connection.execute('''SELECT * FROM qc_lot_config_item_levels
            WHERE lot_config_item_id=? AND is_disabled=0 ORDER BY level_order,id''', (item,))]


def replace(fixture, context=None):
    context = context or get_qc_replacement_context(fixture['config'])
    chosen = [fixture['ids'][2], fixture['new_level'], fixture['ids'][0]]
    copied = save_qc_replacement(source_config_id=fixture['config'], expected_fingerprint=context['fingerprint'],
        mode='materials', selections={fixture['item']: chosen})
    item = int(list_lot_config_items(copied).iloc[0]['id'])
    return copied, item, chosen


def test_current_values_follow_actual_ids_after_reordering_and_preserve_old_history():
    with IsolatedDatabase():
        fixture = seed_source()
        context = get_qc_replacement_context(fixture['config'])
        reference = context['references'][fixture['item']]
        assert reference['profile']['id'] == fixture['profile'] != fixture['future_profile']
        assert reference['levels'][fixture['ids'][0]]['mean'] == 111
        before = historical_rows(fixture['config'])
        copied, item, chosen = replace(fixture, context)
        levels = raw_levels(item)
        assert [row['qc_level_id'] for row in levels] == chosen
        assert [row['target_mean'] for row in levels] == [333, None, 111]
        assert [row['target_sd'] for row in levels] == [9, None, 3]
        assert [row['target_source'] for row in levels] == ['copied_pending', 'building', 'copied_pending']
        assert [row['target_confirmed'] for row in levels] == [0, 1, 0]
        assert [row['notes'] for row in levels] == ['原第 3 水平备注', '', '原第 1 水平备注']
        with get_connection() as connection:
            members = {row['qc_level_id']: row['source_profile_id'] for row in connection.execute(
                'SELECT * FROM qc_level_combination_members WHERE lot_config_item_id=?', (item,))}
            assert connection.execute('SELECT status FROM qc_lot_configs WHERE id=?', (copied,)).fetchone()[0] == 'draft'
        assert members == {fixture['ids'][2]: fixture['profile'], fixture['ids'][0]: fixture['profile']}
        snapshot = latest_snapshot(copied)
        saved = next(row for row in snapshot['items'] if row['id'] == item)
        assert [(row['qc_level_id'], row['target_mean'], row['target_sd'], row['target_source'], row['target_confirmed'], row['notes'])
                for row in saved['levels']] == [(row['qc_level_id'], row['target_mean'], row['target_sd'],
                row['target_source'], row['target_confirmed'], row['notes']) for row in levels]
        assert json.loads(saved['quality_goal_json'])['pending']
        assert json.loads(saved['quality_review_json'])['status'] == 'pending'
        assert historical_rows(fixture['config']) == before
        rejected(lambda: activate_lot_config(copied), '待确认')
        assert historical_rows(fixture['config']) == before
        # Explicitly accept retained parameter references; the new middle control still builds its own values.
        save_lot_item_levels(item, [dict(qc_level_id=row['qc_level_id'],
            target_source='manual' if row['target_mean'] is not None else 'building',
            target_mean=row['target_mean'], target_sd=row['target_sd'], target_confirmed=True,
            notes=row['notes']) for row in levels])
        apply(item, count=3)
        activate_lot_config(copied)
        sync_zscore_workbench_bindings()
        with get_connection() as connection:
            new_binding = connection.execute('SELECT id FROM qc_workbench_bindings WHERE lot_config_id=?', (copied,)).fetchone()[0]
        parameters = get_target_profile_context(new_binding)
        assert parameters['current'] is None and parameters['profiles'] == []
        assert [row['qc_level_id'] for row in parameters['levels']] == chosen
        assert [row['mean'] for row in parameters['levels']] == [333, None, 111]
        assert [row['sd'] for row in parameters['levels']] == [9, None, 3]
        assert [row['retained_version'] for row in parameters['levels']] == [1, None, 1]
        after = historical_rows(fixture['config'])
        # Synchronization adds a new binding and refreshes its cache timestamp; original records are untouched.
        before.pop('qc_workbench_bindings')
        after.pop('qc_workbench_bindings')
        assert after == before


def test_changed_current_profile_rejects_old_replacement_fingerprint_without_partial_copy():
    with IsolatedDatabase():
        fixture = seed_source(future=False)
        context = get_qc_replacement_context(fixture['config'])
        new_profile = create_target_profile(method='zscore', batch_id=fixture['batch'],
            levels=[dict(level_id=f'Level {order}', mean=123*order, sd=4*order) for order in (1, 2, 3)],
            source='revision', evidence='其他窗口确认了新参数', confirmed_by='新确认人', effective_at='2026-09-03')
        latest = get_qc_replacement_context(fixture['config'])
        assert latest['references'][fixture['item']]['profile']['id'] == new_profile
        assert latest['fingerprint'] != context['fingerprint']
        before = database_dump()
        rejected(lambda: replace(fixture, context), '资料已修改')
        assert database_dump() == before
        copied, item, _ = replace(fixture, latest)
        assert [row['target_mean'] for row in raw_levels(item)] == [369, None, 123]
        assert latest_snapshot(copied)['config']['status'] == 'draft'


def test_future_only_profile_does_not_replace_original_configuration_references():
    with IsolatedDatabase():
        fixture = seed_source(current=False, future=True)
        context = get_qc_replacement_context(fixture['config'])
        assert fixture['item'] not in context['references']
        before = historical_rows(fixture['config'])
        _, item, _ = replace(fixture, context)
        rows = raw_levels(item)
        assert [row['target_mean'] for row in rows] == [30, None, 10]
        assert [row['target_sd'] for row in rows] == [1.5, None, 0.5]
        assert [row['target_source'] for row in rows] == ['copied_pending', 'building', 'copied_pending']
        with get_connection() as connection:
            assert not connection.execute('SELECT * FROM qc_level_combination_members WHERE lot_config_item_id=?', (item,)).fetchall()
        assert historical_rows(fixture['config']) == before


if __name__ == '__main__':
    for name, function in list(globals().items()):
        if name.startswith('test_'):
            function()
            print('PASS', name, flush=True)
