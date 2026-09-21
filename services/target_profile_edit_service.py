"""Read-only parameter contexts and atomic saves through the existing version service."""
from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json

from database import atomic_write, get_connection
from services.lot_lifecycle_service import create_target_profile, effective_qc_state, timestamp


def _row(connection, table, identifier):
    row = connection.execute(f'SELECT * FROM {table} WHERE id=?', (identifier,)).fetchone()
    return dict(row) if row else {}


def _context(connection, binding_id):
    binding = _row(connection, 'qc_workbench_bindings', int(binding_id))
    if not binding or binding['qc_method'] not in ('lj', 'zscore'):
        raise ValueError('请选择单水平或多水平的质控批次。')
    method, batch_id = binding['qc_method'], binding['runtime_batch_id']
    config = _row(connection, 'qc_lot_configs', binding['lot_config_id'])
    item = _row(connection, 'qc_lot_config_items', binding['lot_config_item_id'])
    template = _row(connection, 'qc_project_templates', config['template_id'])
    batch = _row(connection, 'batches', batch_id)
    project = _row(connection, 'projects', binding['runtime_project_id'])
    snapshot = json.loads(binding['source_snapshot_json'] or '{}')
    actual = [dict(row) for row in connection.execute('''
        SELECT a.*,l.level_name,l.level_code,l.qc_material_lot_id,l.is_disabled AS level_disabled,
               q.lot_no,q.expiry_date,q.is_disabled AS lot_disabled
        FROM qc_lot_config_item_levels a JOIN md_qc_levels l ON l.id=a.qc_level_id
        JOIN md_qc_material_lots q ON q.id=l.qc_material_lot_id
        WHERE a.lot_config_item_id=? AND a.is_disabled=0 ORDER BY a.level_order,a.id
    ''', (item['id'],))]
    profiles = [dict(row) for row in connection.execute('''SELECT * FROM qc_target_profiles
        WHERE qc_method=? AND batch_id=? ORDER BY version_no,id''', (method, batch_id))]
    for profile in profiles:
        profile['levels'] = json.loads(profile['levels_json'])
    now = timestamp(datetime.now())
    effective = [profile for profile in profiles if profile['effective_at'] <= now]
    current = max(effective, key=lambda profile: (profile['effective_at'], profile['id'])) if effective else None
    retained = [dict(row) for row in connection.execute('''
        SELECT m.*,p.version_no,p.levels_json,p.qc_method AS source_method,p.batch_id AS source_batch_id
        FROM qc_level_combination_members m
        LEFT JOIN qc_target_profiles p ON p.id=m.source_profile_id WHERE m.lot_config_item_id=?
        ORDER BY m.qc_level_id''', (item['id'],))]
    for row in retained:
        origin = connection.execute('SELECT * FROM qc_workbench_bindings WHERE qc_method=? AND runtime_batch_id=?',
                                    (row['source_method'], row['source_batch_id'])).fetchone()
        row['source_binding'] = {key: origin[key] for key in origin.keys() if key != 'updated_at'} if origin else None
    events = [dict(row) for row in connection.execute('''SELECT * FROM qc_lot_change_events
        WHERE (next_id=? AND event_type IN ('active','parallel','ended')) OR
        (event_type='qc' AND template_item_id=? AND json_extract(details_json,'$.new_config_id')=?)
        ORDER BY id''', (item['id'], binding['project_template_item_id'], config['id']))]
    state = effective_qc_state(connection, item['id'])
    lifecycle = connection.execute('SELECT * FROM qc_config_item_lifecycle WHERE lot_config_item_id=?', (item['id'],)).fetchone()
    source_levels = snapshot.get('levels') or actual
    identity = snapshot.get('identity') or []
    tables = ('lab_instruments', 'md_qc_materials', 'md_qc_material_lots', 'md_test_items',
              None, 'md_units', 'md_methods', 'md_reagents')
    references = {index: _row(connection, table, identity[index]) for index, table in enumerate(tables)
                  if table and len(identity) > index and identity[index]
                  and not (config['material_selection_mode'] and index == 2)}
    if not batch or batch.get('is_disabled') or project.get('is_disabled'):
        reason = '此批次已停用，均值和标准差仅供查询。'
    elif binding['binding_status'] != 'active' or config['status'] != 'active' or config['is_disabled'] or template['status'] != 'active' or template['is_disabled'] or not item['is_enabled'] or item['is_disabled']:
        reason = '此批次的项目或批次设置已停用或调整，均值和标准差仅供查询。'
    elif state in ('ended', 'pending'):
        reason = '此批次已停止使用，均值和标准差仅供查询。' if state == 'ended' else '此批次尚未开始使用，请到使用状态中核对生效时间。'
    elif any(not row or row['is_disabled'] for row in references.values()) or any(row['level_disabled'] or row['lot_disabled'] for row in actual):
        reason = '此批次使用的资料或质控品已停用，均值和标准差仅供查询。'
    else:
        actual_identity = [config['lab_instrument_id'], config['qc_material_id'], config['qc_material_lot_id'],
                           item['test_item_id'], item['input_value_type'], item['unit_id'], item['method_id'], item['reagent_id']]
        indices = (0, 1, 3, 4, 5, 6, 7) if config['material_selection_mode'] else range(8)
        altered = identity and any(len(identity) <= i or identity[i] != actual_identity[i] for i in indices)
        altered = altered or ([row.get('qc_level_id') for row in source_levels] != [row['qc_level_id'] for row in actual])
        reason = '质控品或检验项目设置已修改，请在新批次中确认均值和标准差。' if altered else ''
    current_levels = {row['level_id']: row for row in current['levels']} if current else {}
    initial_levels = []
    for order, source in enumerate(source_levels, 1):
        level = dict(source)
        code = f'Level {order}'
        chosen = current_levels.get(code)
        retained_row = next((row for row in retained if row['qc_level_id'] == level.get('qc_level_id') and row['levels_json']), None)
        original_levels = json.loads(retained_row['source_binding']['source_snapshot_json'] or '{}').get('levels', []) if retained_row and retained_row['source_binding'] else []
        original_code = next((f'Level {position}' for position, row in enumerate(original_levels, 1)
                              if row.get('qc_level_id') == level.get('qc_level_id')), None)
        reference = next((row for row in json.loads(retained_row['levels_json']) if row['level_id'] == original_code), None) if original_code else None
        initial_levels.append(dict(level_id=code, qc_level_id=level.get('qc_level_id'),
            level_name=level.get('level_name') or f'水平 {order}', level_code=level.get('level_code') or '',
            lot_no=level.get('lot_no') or snapshot.get('lot_no') or '未记录', expiry_date=level.get('expiry_date') or '',
            mean=chosen['mean'] if chosen else reference['mean'] if reference else level.get('target_mean'),
            sd=chosen['sd'] if chosen else reference['sd'] if reference else level.get('target_sd'),
            retained_version=retained_row['version_no'] if reference and not chosen else None))
    table = 'results' if method == 'lj' else 'zscore_runs'
    latest = dict(connection.execute(f'SELECT COUNT(*) AS count,MAX(id) AS latest_id,MAX(test_time) AS latest_time FROM {table} WHERE batch_id=?', (batch_id,)).fetchone())
    initial_time = datetime.now().replace(microsecond=0)
    if profiles:
        initial_time = max(initial_time, datetime.fromisoformat(profiles[-1]['effective_at']) + timedelta(seconds=1))
    if latest['latest_time']:
        initial_time = max(initial_time, datetime.fromisoformat(timestamp(latest['latest_time'])))
    # Workbench synchronization refreshes this timestamp on every page rerun.
    # The binding's identities, status and source revision remain part of the check.
    stable_binding = {key: value for key, value in binding.items() if key != 'updated_at'}
    # init_db reseeds official test items on each app rerun. Their timestamp alone
    # is not a changed setting; names, units, notes, status and all other data are.
    stable_references = {index: {key: value for key, value in row.items()
        if not (index == 3 and row.get('origin_type') == 'official' and key == 'updated_at')}
        for index, row in references.items()}
    state_payload = dict(binding=stable_binding, config=config, item=item, template=template, batch=batch,
        project=project, levels=actual, profiles=profiles, retained=retained, events=events, state=state,
        current_profile_id=current['id'] if current else None, lifecycle=dict(lifecycle) if lifecycle else None,
        references=stable_references, latest=latest)
    fingerprint = hashlib.sha256(json.dumps(state_payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return dict(binding=binding, config=config, item=item, template=template, snapshot=snapshot,
        levels=initial_levels, profiles=profiles, current=current, state=state,
        editable=not bool(reason), read_only_reason=reason, fingerprint=fingerprint, initial_time=initial_time)


def get_target_profile_context(binding_id):
    with get_connection() as connection:
        return _context(connection, binding_id)


def list_target_profile_batches():
    with get_connection() as connection:
        bindings = connection.execute("SELECT id FROM qc_workbench_bindings WHERE qc_method IN ('lj','zscore') ORDER BY id DESC").fetchall()
        return [_context(connection, row['id']) for row in bindings]


def save_target_profile_settings(binding_id, levels, *, expected_fingerprint, source, evidence,
                                 confirmed_by, effective_at, confirmed=False):
    with atomic_write() as connection:
        context = _context(connection, binding_id)
        if not context['editable']:
            raise ValueError(context['read_only_reason'])
        if not expected_fingerprint or context['fingerprint'] != expected_fingerprint:
            raise ValueError('此批次的参数或设置已修改，请重新打开并核对后再保存。')
        if not confirmed:
            raise ValueError('请先确认全部水平的均值和标准差适用。')
        if source not in ('manual', 'manufacturer', 'revision'):
            raise ValueError('请选择本次均值和标准差的来源。')
        try:
            parameters = [dict(level_id=row['level_id'], mean=float(row['mean']), sd=float(row['sd'])) for row in levels]
        except (TypeError, ValueError, KeyError) as error:
            raise ValueError('请填写全部水平的均值和标准差。') from error
        return create_target_profile(method=context['binding']['qc_method'],
            batch_id=context['binding']['runtime_batch_id'], levels=parameters, source=source,
            evidence=evidence, confirmed_by=confirmed_by, effective_at=effective_at)
