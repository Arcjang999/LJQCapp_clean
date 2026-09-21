"""Read-only reagent dialog contexts and guarded calls to existing lot services."""
from __future__ import annotations

import hashlib
import json

import pandas as pd

from database import atomic_write, get_connection
from services.lot_lifecycle_service import (
    correct_reagent_event, create_reagent_lot, record_lot_verification,
    switch_reagent_lots, timestamp,
)
from services.project_config_service import QC_METHOD_LABELS


def _rows(c, sql, params=()):
    return [dict(row) for row in c.execute(sql, params)]


def _hash(scope):
    return hashlib.sha256(json.dumps(scope, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _system_identity(snapshot):
    identity = snapshot.get('identity') or []
    if len(identity) < 8:
        return None
    return [snapshot.get('project_template_item_id'), *[identity[i] for i in (0, 3, 4, 5, 6, 7)]]


def _read_context(c):
    products = _rows(c, '''SELECT r.*,m.display_name AS manufacturer_name FROM md_reagents r
        LEFT JOIN md_manufacturers m ON m.id=r.manufacturer_id ORDER BY r.id''')
    lots = _rows(c, '''SELECT l.*,r.generic_name AS reagent_name,r.trade_name,r.is_disabled AS product_disabled,
        m.display_name AS manufacturer_name FROM md_reagent_lots l JOIN md_reagents r ON r.id=l.reagent_id
        LEFT JOIN md_manufacturers m ON m.id=r.manufacturer_id ORDER BY l.id''')
    systems = _rows(c, 'SELECT * FROM qc_detection_systems ORDER BY id')
    templates = _rows(c, 'SELECT * FROM qc_project_templates ORDER BY id')
    items = _rows(c, 'SELECT * FROM qc_project_template_items ORDER BY id')
    bindings = _rows(c, 'SELECT * FROM qc_workbench_bindings ORDER BY id')
    verifications = _rows(c, 'SELECT * FROM qc_lot_verifications WHERE reagent_lot_id IS NOT NULL ORDER BY id')
    usages = _rows(c, 'SELECT * FROM qc_reagent_lot_usage ORDER BY id')
    history_events = _rows(c, 'SELECT * FROM qc_lot_change_events ORDER BY id')
    events = [event for event in history_events if event['event_type'] in ('reagent', 'correction')]
    master_tables = ('lab_instruments', 'md_test_items', 'md_units', 'md_methods', 'md_manufacturers')
    masters = {table: _rows(c, f'SELECT * FROM {table} ORDER BY id') for table in master_tables}
    master_lookup = {table: {row['id']: row for row in rows} for table, rows in masters.items()}
    master_lookup['md_reagents'] = {row['id']: row for row in products}
    by_template = {row['id']: row for row in templates}
    by_item = {row['id']: row for row in items}
    active_identities = set()
    for binding in bindings:
        if binding['binding_status'] != 'active':
            continue
        snapshot = json.loads(binding['source_snapshot_json'] or '{}')
        identity = _system_identity(snapshot)
        if identity:
            active_identities.add(json.dumps(identity))
    raw_systems = [dict(row) for row in systems]
    for system in systems:
        snapshot = json.loads(system['snapshot_json'] or '{}')
        item = by_item.get(system['template_item_id'])
        template = by_template.get(item['template_id']) if item else None
        identity = _system_identity(snapshot)
        available = bool(identity and item and template and not item['is_disabled'] and not template['is_disabled']
                         and template['status'] == 'active' and json.dumps(identity) in active_identities)
        if available:
            expected = [item['id'], template['lab_instrument_id'], item['test_item_id'], item['input_value_type'],
                        item['unit_id'], item['method_id'], item['reagent_id']]
            available = identity == expected
        source_identity = snapshot.get('identity') or []
        for table, index in [('lab_instruments', 0), ('md_test_items', 3), ('md_units', 5), ('md_methods', 6), ('md_reagents', 7)]:
            identifier = source_identity[index] if len(source_identity) > index else None
            if identifier is not None:
                related = master_lookup[table].get(identifier)
                available = available and related is not None and not related['is_disabled']
        qc_method = snapshot.get('qc_method') or (item['qc_method'] if item else '')
        parts = [template['template_name'] if template else '', snapshot.get('test_item_name', ''),
                 snapshot.get('instrument_name', ''), snapshot.get('method_name', ''),
                 snapshot.get('unit_symbol', ''), snapshot.get('reagent_name', ''), QC_METHOD_LABELS.get(qc_method, '')]
        system.update(snapshot=snapshot, label='｜'.join(str(value) for value in parts if value),
                      available=bool(available), reagent_id=source_identity[7] if len(source_identity) > 7 else None,
                      revision=max((usage['id'] for usage in usages if usage['system_id'] == system['id']), default=0))
    # Existing sync rewrites binding.updated_at every render without changing its meaning.
    semantic_bindings = [{key: value for key, value in row.items() if key != 'updated_at'} for row in bindings]
    # App startup refreshes official test-item seed timestamps on every rerun.
    # Keep all business fields (including notes and disabled state) in the guard,
    # but a timestamp-only refresh must not invalidate an open dialog.
    semantic_masters = {table: [{key: value for key, value in row.items() if key != 'updated_at'}
                               for row in rows] for table, rows in masters.items()}
    scope = {'products': products, 'lots': lots, 'systems': raw_systems, 'templates': templates, 'items': items,
             'bindings': semantic_bindings, 'verifications': verifications, 'usages': usages, 'events': events, 'masters': semantic_masters}
    return {'products': [row for row in products if not row['is_disabled']], 'all_products': products,
            'lots': [row for row in lots if not row['is_disabled'] and not row['product_disabled']], 'all_lots': lots,
            'systems': systems, 'verifications': verifications, 'events': events, 'usages': usages,
            'history_events': history_events, 'all_bindings': bindings, 'fingerprint': _hash(scope)}


def get_reagent_workspace_context():
    """Call after the page's existing synchronization; querying here performs no writes."""
    with get_connection() as c:
        return _read_context(c)


def _check(context, expected):
    if not expected or context['fingerprint'] != expected:
        raise ValueError('试剂批号、验证或使用记录已修改，请关闭窗口后重新打开，核对最新内容。')


def _system(context, system_id, *, require_available):
    system = next((row for row in context['systems'] if row['id'] == system_id), None)
    if system is None:
        raise ValueError('未找到所选检验项目，请重新选择。')
    if require_available and not system['available']:
        raise ValueError('所选检验项目或相关资料已停用或变更，请先核对项目和批次设置。')
    return system


def _lot(context, lot_id, reagent_id=None):
    lot = next((row for row in context['all_lots'] if row['id'] == lot_id), None)
    if lot is None:
        raise ValueError('请选择已登记的试剂批号。')
    if lot['is_disabled'] or lot['product_disabled']:
        raise ValueError('所选试剂或批号已停用，请重新选择。')
    if reagent_id is not None and lot['reagent_id'] != reagent_id:
        raise ValueError('试剂批号不属于所选检验项目采用的试剂产品。')
    return lot


def build_reagent_switch_preview(context, *, reagent_lot_id, system_ids, effective_at):
    lot = _lot(context, reagent_lot_id)
    when = timestamp(effective_at)
    if len(system_ids) != len(set(system_ids)):
        raise ValueError('请选择不重复的检验项目。')
    lot_names = {row['id']: row['lot_no'] for row in context['all_lots']}
    preview = []
    for system_id in system_ids:
        system = _system(context, system_id, require_available=True)
        if lot['reagent_id'] != system['reagent_id']:
            raise ValueError('所选检验项目未采用该试剂产品，请重新核对。')
        verifications = [row for row in context['verifications'] if row['system_id'] == system_id
                         and row['reagent_lot_id'] == reagent_lot_id and row['confirmed_at'] <= when]
        latest = max(verifications, key=lambda row: (row['confirmed_at'], row['id']), default=None)
        usages = [row for row in context['usages'] if row['system_id'] == system_id and row['effective_at'] <= when]
        previous = max(usages, key=lambda row: (row['effective_at'], row['id']), default=None)
        preview.append({'system_id': system_id, 'template_item_id': system['template_item_id'],
            'reagent_lot_id': reagent_lot_id, 'verification_id': latest['id'] if latest else None,
            'expected_revision': system['revision'], 'system_label': system['label'],
            'conclusion': latest['conclusion'] if latest else None, 'evidence': latest['evidence'] if latest else '',
            'confirmed_at': latest['confirmed_at'] if latest else None, 'expiry_date': lot['expiry_date'],
            'previous_lot_no': lot_names.get(previous['reagent_lot_id'], '未登记') if previous else '未登记',
            'next_lot_no': lot['lot_no']})
    return preview


def save_reagent_registration(values, *, expected_fingerprint):
    fields = dict(values)
    if fields.get('expiry_date') in (None, ''):
        raise ValueError('请填写试剂批号效期。')
    try:
        expiry = pd.Timestamp(fields['expiry_date'])
        if pd.isna(expiry):
            raise ValueError()
        fields['expiry_date'] = expiry.date().isoformat()
    except (ValueError, TypeError, OverflowError) as error:
        raise ValueError('请填写有效的试剂批号效期。') from error
    with atomic_write() as c:
        context = _read_context(c)
        _check(context, expected_fingerprint)
        if not any(row['id'] == fields.get('reagent_id') for row in context['products']):
            raise ValueError('请选择未停用的试剂产品。')
        return create_reagent_lot(**fields)


def save_reagent_verification(values, *, expected_fingerprint):
    fields = dict(values)
    with atomic_write() as c:
        context = _read_context(c)
        _check(context, expected_fingerprint)
        system = _system(context, fields.get('system_id'), require_available=True)
        if fields.get('template_item_id') != system['template_item_id']:
            raise ValueError('所选检验项目已变化，请重新选择。')
        _lot(context, fields.get('reagent_lot_id'), system['reagent_id'])
        if fields.get('qc_lot_id') is not None:
            raise ValueError('此处只登记试剂批号验证。')
        return record_lot_verification(**fields)


def save_reagent_switch(values, *, expected_fingerprint):
    fields = dict(values)
    if fields.pop('confirmed', False) is not True:
        raise ValueError('请先核对所选检验项目、验证记录及生效时间，并确认原均值和标准差继续适用。')
    with atomic_write() as c:
        context = _read_context(c)
        _check(context, expected_fingerprint)
        selections = fields.get('selections') or []
        checked = []
        for selection in selections:
            system = _system(context, selection.get('system_id'), require_available=True)
            if selection.get('template_item_id') != system['template_item_id']:
                raise ValueError('所选检验项目与换批预览不一致，请重新核对。')
            _lot(context, selection.get('reagent_lot_id'), system['reagent_id'])
            if selection.get('expected_revision') != system['revision']:
                raise ValueError('批号使用记录已修改，请重新打开换批预览后确认。')
            checked.append({key: selection.get(key) for key in ('system_id', 'template_item_id', 'reagent_lot_id', 'verification_id', 'expected_revision')})
        fields['selections'] = checked
        return switch_reagent_lots(**fields)


def _correction_context(c, event_id):
    context = _read_context(c)
    event = next((row for row in context['history_events'] if row['id'] == int(event_id)), None)
    if event is None:
        raise ValueError('未找到所选使用记录，请重新选择。')
    system = _system(context, event['system_id'], require_available=False)
    corrections = [row for row in context['events'] if row['corrects_event_id'] == event['id']]
    can_correct = event['event_type'] in ('reagent', 'correction')
    reason = '此处只更正试剂换批记录，其他记录保留查询。' if not can_correct else ''
    context.update(event=event, system=system, can_correct=can_correct, blocking_reason=reason,
                   corrections=corrections, has_corrections=bool(corrections),
                   fingerprint=_hash({'workspace': context['fingerprint'], 'event_id': int(event_id)}))
    return context


def get_reagent_correction_context(event_id):
    with get_connection() as c:
        return _correction_context(c, event_id)


def save_reagent_correction(event_id, values, *, expected_fingerprint):
    fields = dict(values)
    if fields.pop('confirmed', False) is not True:
        raise ValueError('请先核对原记录、新批号、验证记录和生效时间，再确认更正。')
    with atomic_write() as c:
        context = _correction_context(c, event_id)
        _check(context, expected_fingerprint)
        if not context['can_correct']:
            raise ValueError(context['blocking_reason'])
        system = context['system']
        _lot(context, fields.get('reagent_lot_id'), system['reagent_id'])
        expected_revision = fields.pop('expected_revision', system['revision'])
        if expected_revision != system['revision']:
            raise ValueError('使用记录已修改，请重新打开并核对。')
        return correct_reagent_event(event_id=int(event_id), expected_revision=expected_revision, **fields)
