"""Dialog guards for existing QC lot operations; calculation and history stay unchanged."""
from __future__ import annotations

import hashlib
import json

from database import atomic_write, get_connection
from services.lot_lifecycle_service import (
    change_qc_lot, create_level_combination, effective_qc_state, qc_lots_for_source,
    record_lot_verification, set_qc_usage_state, target_profile,
)
from services.material_workflow_service import available_materials, config_material_summary


def _rows(c, sql, params=()):
    return [dict(row) for row in c.execute(sql, params)]


def _scope(c, config_id):
    config = c.execute('SELECT * FROM qc_lot_configs WHERE id=?', (config_id,)).fetchone()
    if config is None:
        raise ValueError('未找到批次，请重新选择。')
    template_id, material_id = config['template_id'], config['qc_material_id']
    data = {'config_id': int(config_id)}
    data['project'] = _rows(c, 'SELECT * FROM qc_project_templates WHERE id=?', (template_id,))
    data['project_items'] = _rows(c, 'SELECT * FROM qc_project_template_items WHERE template_id=? ORDER BY id', (template_id,))
    data['configs'] = _rows(c, 'SELECT * FROM qc_lot_configs WHERE template_id=? ORDER BY id', (template_id,))
    data['items'] = _rows(c, '''SELECT i.* FROM qc_lot_config_items i JOIN qc_lot_configs q ON q.id=i.lot_config_id
        WHERE q.template_id=? ORDER BY i.id''', (template_id,))
    data['assignments'] = _rows(c, '''SELECT a.* FROM qc_lot_config_item_levels a JOIN qc_lot_config_items i ON i.id=a.lot_config_item_id
        JOIN qc_lot_configs q ON q.id=i.lot_config_id WHERE q.template_id=? ORDER BY a.id''', (template_id,))
    data['material'] = _rows(c, 'SELECT * FROM md_qc_materials WHERE id=?', (material_id,))
    data['lots'] = _rows(c, 'SELECT * FROM md_qc_material_lots WHERE qc_material_id=? ORDER BY id', (material_id,))
    data['levels'] = _rows(c, '''SELECT l.* FROM md_qc_levels l JOIN md_qc_material_lots q ON q.id=l.qc_material_lot_id
        WHERE q.qc_material_id=? ORDER BY l.id''', (material_id,))
    data['bindings'] = _rows(c, '''SELECT b.* FROM qc_workbench_bindings b JOIN qc_lot_configs q ON q.id=b.lot_config_id
        WHERE q.template_id=? ORDER BY b.id''', (template_id,))
    # Existing synchronization touches this timestamp on every page run, even when
    # identity, source revision and status are unchanged.
    for binding in data['bindings']:
        binding.pop('updated_at', None)
    data['systems'] = _rows(c, '''SELECT s.* FROM qc_detection_systems s JOIN qc_project_template_items i ON i.id=s.template_item_id
        WHERE i.template_id=? ORDER BY s.id''', (template_id,))
    data['verifications'] = _rows(c, '''SELECT v.* FROM qc_lot_verifications v JOIN qc_project_template_items i ON i.id=v.template_item_id
        WHERE i.template_id=? AND v.qc_lot_id IS NOT NULL ORDER BY v.id''', (template_id,))
    data['events'] = _rows(c, '''SELECT e.* FROM qc_lot_change_events e JOIN qc_project_template_items i ON i.id=e.template_item_id
        WHERE i.template_id=? AND e.event_type IN ('qc','parallel','active','ended') ORDER BY e.id''', (template_id,))
    data['states'] = _rows(c, '''SELECT s.* FROM qc_config_item_lifecycle s JOIN qc_lot_config_items i ON i.id=s.lot_config_item_id
        JOIN qc_lot_configs q ON q.id=i.lot_config_id WHERE q.template_id=? ORDER BY i.id''', (template_id,))
    data['profiles'] = _rows(c, '''SELECT p.* FROM qc_target_profiles p JOIN qc_workbench_bindings b
        ON b.qc_method=p.qc_method AND b.runtime_batch_id=p.batch_id JOIN qc_lot_configs q ON q.id=b.lot_config_id
        WHERE q.template_id=? ORDER BY p.id''', (template_id,))
    return dict(config), data


def _fingerprint(scope):
    return hashlib.sha256(json.dumps(scope, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def list_qc_lifecycle_configs():
    with get_connection() as c:
        rows = _rows(c, '''SELECT q.*,t.template_name,m.generic_name AS material_name FROM qc_lot_configs q
            JOIN qc_project_templates t ON t.id=q.template_id JOIN md_qc_materials m ON m.id=q.qc_material_id
            WHERE q.is_disabled=0 ORDER BY q.id DESC''')
    for row in rows:
        row['material_summary'] = config_material_summary(row['id'])
    return rows


def get_qc_config_context(config_id):
    with get_connection() as c:
        config, scope = _scope(c, int(config_id))
        items = _rows(c, '''SELECT i.*,t.chinese_name AS test_item_name FROM qc_lot_config_items i
            JOIN md_test_items t ON t.id=i.test_item_id WHERE i.lot_config_id=? AND i.is_disabled=0 ORDER BY i.sort_order,i.id''', (config_id,))
        return {'config': config, 'items': items, 'lots': scope['lots'], 'levels': scope['levels'],
                'configs': scope['configs'], 'all_items': scope['items'], 'bindings': scope['bindings'],
                'fingerprint': _fingerprint(scope)}


def _binding_context(c, binding_id):
    raw = c.execute('SELECT * FROM qc_workbench_bindings WHERE id=?', (int(binding_id),)).fetchone()
    if raw is None:
        raise ValueError('未找到检测批次，请重新选择。')
    binding = dict(raw)
    config, scope = _scope(c, binding['lot_config_id'])
    source = json.loads(binding['source_snapshot_json'] or '{}')
    if not source.get('identity') or not source.get('project_template_item_id'):
        raise ValueError('此批次缺少完整资料，请先核对批次设置。')
    identity = source['identity']
    system_identity = json.dumps([source['project_template_item_id'], *[identity[i] for i in (0, 3, 4, 5, 6, 7)]])
    system = c.execute('SELECT id FROM qc_detection_systems WHERE identity_json=?', (system_identity,)).fetchone()
    if system is None:
        raise ValueError('检测项目资料尚未准备好，请返回批次设置核对后再打开。')
    source['system_id'] = system['id']
    source['lot_config_item_id'] = binding['lot_config_item_id']
    source['runtime_method'], source['runtime_batch_id'] = binding['qc_method'], binding['runtime_batch_id']
    actual_lots = qc_lots_for_source(c, source)
    verifications = [v for v in scope['verifications'] if v['system_id'] == system['id']]
    state = next((s for s in scope['states'] if s['lot_config_item_id'] == binding['lot_config_item_id']), None)
    return {'binding': binding, 'config': config, 'source': source, 'actual_lots': actual_lots,
            'lots': scope['lots'], 'levels': scope['levels'], 'verifications': verifications,
            'state': effective_qc_state(c, binding['lot_config_item_id']), 'state_plan': state,
            'profile': target_profile(binding['qc_method'], binding['runtime_batch_id']) if binding['qc_method'] != 'instant' else None,
            'fingerprint': _fingerprint({**scope, 'binding_id': int(binding_id)})}


def get_qc_binding_context(binding_id):
    with get_connection() as c:
        return _binding_context(c, binding_id)


def replacement_materials(context, position):
    """Compare against the original material, never against its position in the run."""
    old_id = context['source']['levels'][position]['qc_level_id']
    original = next(row for row in context['levels'] if row['id'] == old_id)
    choices = []
    for row in available_materials(context['config']['qc_material_id']).to_dict('records'):
        if original.get('specification_id'):
            same = row.get('specification_id') == original['specification_id']
        elif row.get('level_code') or original.get('level_code'):
            same = (row['level_code'], row['level_name']) == (original['level_code'], original['level_name'])
        else:
            same = row['level_order'] == original['level_order']
        if same:
            choices.append(row)
    return choices


def parallel_target(context, lot_id):
    """Return the existing ordinary target and selectable source items for that target."""
    existing = next((q for q in context['configs'] if not q['is_disabled'] and not q['combination_key']
                     and q['qc_material_lot_id'] == lot_id), None)
    bound = {b['project_template_item_id'] for b in context['bindings'] if b['lot_config_id'] == context['config']['id']}
    eligible = [item for item in context['items'] if item['source_template_item_id'] in bound]
    if existing:
        fixed = existing['status'] != 'draft' or existing['activated_at'] or any(b['lot_config_id'] == existing['id'] for b in context['bindings'])
        remaining = {item['source_template_item_id'] for item in context['all_items']
                     if item['lot_config_id'] == existing['id'] and not item['is_disabled'] and not item['is_enabled']}
        eligible = [] if fixed else [item for item in eligible if item['source_template_item_id'] in remaining]
    return existing, eligible


def save_qc_lifecycle_action(kind, context_id, values, *, expected_fingerprint):
    with atomic_write() as c:
        context = get_qc_config_context(context_id) if kind == 'prepare' else _binding_context(c, context_id)
        if not expected_fingerprint or context['fingerprint'] != expected_fingerprint:
            raise ValueError('批次资料或验证记录已修改，请关闭窗口后重新打开，核对最新内容。')
        if context['config']['is_disabled']:
            raise ValueError('此批次已停用，请先核对批次设置。')
        if kind == 'prepare':
            existing, _ = parallel_target(context, values.get('target_qc_lot_id'))
            if context['config']['material_selection_mode'] and existing is None:
                raise ValueError('此批次按各水平登记质控品，请使用“选择各水平的新批号”建立新批次。')
            return change_qc_lot(source_config_id=int(context_id), **values)
        binding, source = context['binding'], context['source']
        if kind == 'verify':
            return record_lot_verification(template_item_id=source['project_template_item_id'], system_id=source['system_id'], **values)
        if kind == 'state':
            return set_qc_usage_state(lot_config_item_id=binding['lot_config_item_id'], **values)
        if kind == 'combination':
            if binding['qc_method'] != 'zscore' or binding['binding_status'] != 'active':
                raise ValueError('请先确认多水平批次设置，再更换水平的质控品。')
            return create_level_combination(source_batch_id=binding['runtime_batch_id'], **values)
        raise ValueError('请选择要进行的操作。')
