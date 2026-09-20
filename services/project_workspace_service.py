"""Project workspace queries and revision-checked edits; calculation services stay separate."""
from __future__ import annotations

import sqlite3
import pandas as pd

from database import atomic_write, get_connection
from services.project_config_service import (
    QC_METHOD_LABELS, create_project_template, get_project_template,
    list_template_items, save_template_items, set_project_template_disabled,
)


def matching_project_ids(*, qc_method: str = '', method_name: str = '') -> set[int]:
    """Both filters must match the same test item, not different items in a group."""
    clauses = ['i.is_disabled=0']
    params = []
    if qc_method:
        clauses.append('i.qc_method=?'); params.append(qc_method)
    if method_name:
        clauses.append('m.method_name=?'); params.append(method_name)
    with get_connection() as connection:
        return {int(r[0]) for r in connection.execute(
            'SELECT DISTINCT i.template_id FROM qc_project_template_items i '
            'LEFT JOIN md_methods m ON m.id=i.method_id WHERE ' + ' AND '.join(clauses), params)}


def project_identity_locked(template_id: int) -> bool:
    with get_connection() as connection:
        return bool(connection.execute(
            'SELECT 1 FROM qc_project_template_items WHERE template_id=? UNION ALL '
            'SELECT 1 FROM qc_lot_configs WHERE template_id=? LIMIT 1',
            (template_id, template_id)).fetchone())


def _check_revision(connection, template_id: int, expected_revision: int):
    row = connection.execute('SELECT * FROM qc_project_templates WHERE id=?', (template_id,)).fetchone()
    if row is None or int(row['revision_no']) != int(expected_revision):
        raise ValueError('项目已发生变化，请取消后重新打开，核对最新资料。')
    return row


def save_project_details(values: dict, *, template_id=None, expected_revision=None) -> int:
    fields = {key: values.get(key) for key in (
        'template_name', 'lab_instrument_id', 'qc_material_id', 'notes', 'default_reagent_id',
        'default_qc_method', 'default_method_id', 'default_level_count', 'project_group')}
    if not str(fields['template_name'] or '').strip():
        raise ValueError('请填写项目名称。')
    if fields['lab_instrument_id'] is None or fields['qc_material_id'] is None or (template_id is None and fields['default_reagent_id'] is None):
        raise ValueError('请选择仪器、默认试剂和质控品。')
    with atomic_write() as connection:
        if template_id is None:
            return create_project_template(**fields)
        current = _check_revision(connection, template_id, expected_revision)
        if current['is_disabled']:
            raise ValueError('请先恢复项目，再编辑资料。')
        if project_identity_locked(template_id) and any(current[k] != fields[k] for k in ('lab_instrument_id', 'qc_material_id')):
            raise ValueError('已添加检验项目或批次，不能更换仪器和质控品；如需更换，请新建项目。')
        for field, table in [('lab_instrument_id', 'lab_instruments'), ('qc_material_id', 'md_qc_materials'),
                             ('default_reagent_id', 'md_reagents'), ('default_method_id', 'md_methods')]:
            value = fields[field]
            if value is not None and value != current[field] and not connection.execute(
                f'SELECT 1 FROM {table} WHERE id=? AND is_disabled=0', (value,)).fetchone():
                raise ValueError('所选资料已停用，请重新选择。')
        way = fields['default_qc_method']
        if way not in QC_METHOD_LABELS:
            raise ValueError('请选择支持的质控方式。')
        fields['default_level_count'] = int(fields['default_level_count']) if way == 'zscore' else 1
        if way == 'zscore' and fields['default_level_count'] not in (2, 3):
            raise ValueError('多水平请选择 2 或 3 个水平。')
        for key in ('template_name', 'notes', 'project_group'):
            fields[key] = str(fields[key] or '').strip()
        try:
            connection.execute('UPDATE qc_project_templates SET ' + ','.join(f'{k}=?' for k in fields) +
                ', revision_no=revision_no+1, updated_at=CURRENT_TIMESTAMP WHERE id=?',
                [*fields.values(), template_id])
        except sqlite3.IntegrityError as exc:
            raise ValueError('存在同名项目或所选资料已失效，请核对。') from exc
        return int(template_id)


def change_project_status(template_id: int, *, expected_revision: int, disabled: bool, reason: str):
    if disabled and not str(reason or '').strip():
        raise ValueError('请填写停用原因。')
    with atomic_write() as connection:
        _check_revision(connection, template_id, expected_revision)
        set_project_template_disabled(template_id, is_disabled=disabled, reason=reason)


def save_single_test_item(template_id: int, values: dict, *, expected_revision: int, item_id=None) -> int:
    with atomic_write() as connection:
        template = _check_revision(connection, template_id, expected_revision)
        if template['is_disabled']:
            raise ValueError('项目已停用。')
        frame = list_template_items(template_id)
        rows = frame.astype(object).where(pd.notna(frame), None).to_dict('records')
        if item_id is not None and not any(int(r['id']) == item_id for r in rows):
            raise ValueError('该检验项目已移除，请重新打开。')
        remaining = [r for r in rows if r['id'] != item_id]
        identity = tuple(values.get(k) for k in ('test_item_id', 'qc_method', 'input_value_type'))
        if any(tuple(r[k] for k in ('test_item_id', 'qc_method', 'input_value_type')) == identity for r in remaining):
            raise ValueError('已有相同检测项、质控方式和输入值类型，请编辑现有记录。')
        for field, table in [('test_item_id', 'md_test_items'), ('unit_id', 'md_units'),
                             ('method_id', 'md_methods'), ('reagent_id', 'md_reagents')]:
            if values.get(field) is None or not connection.execute(
                f'SELECT 1 FROM {table} WHERE id=? AND is_disabled=0', (values[field],)).fetchone():
                raise ValueError('请选择启用中的检测项、单位、方法学和试剂。')
        updated = [values if r['id'] == item_id else r for r in rows] if item_id is not None else rows + [values]
        save_template_items(template_id, updated)
        return int(connection.execute(
            'SELECT id FROM qc_project_template_items WHERE template_id=? AND test_item_id=? AND qc_method=? AND input_value_type=?',
            (template_id, *identity)).fetchone()[0])


def remove_test_item(template_id: int, item_id: int, *, expected_revision: int, reason: str):
    if not str(reason or '').strip():
        raise ValueError('请填写移除原因。')
    with atomic_write() as connection:
        _check_revision(connection, template_id, expected_revision)
        frame = list_template_items(template_id)
        rows = frame.astype(object).where(pd.notna(frame), None).to_dict('records')
        if not any(r['id'] == item_id for r in rows):
            raise ValueError('检验项目已移除，请刷新列表。')
        save_template_items(template_id, [r for r in rows if r['id'] != item_id])
        connection.execute('UPDATE qc_project_template_items SET disabled_reason=? WHERE id=?', (reason.strip(), item_id))


def list_item_batches(item_id: int) -> pd.DataFrame:
    """Read only. Opening a chosen batch explicitly synchronizes its method."""
    with get_connection() as connection:
        return pd.read_sql_query('''SELECT i.id AS config_item_id,i.qc_method,c.id AS lot_config_id,
            c.config_name,c.status,c.is_disabled,c.created_at,b.id AS binding_id,
            b.binding_status,b.runtime_project_id,b.runtime_batch_id
            FROM qc_lot_config_items i JOIN qc_lot_configs c ON c.id=i.lot_config_id
            LEFT JOIN qc_workbench_bindings b ON b.lot_config_item_id=i.id AND b.qc_method=i.qc_method
            WHERE i.source_template_item_id=? AND i.is_disabled=0 AND i.is_enabled=1 AND c.is_disabled=0
            ORDER BY c.id DESC''', connection, params=(item_id,))


def resolve_batch_binding(config_item_id: int) -> dict:
    with get_connection() as connection:
        item = connection.execute('SELECT qc_method FROM qc_lot_config_items WHERE id=?', (config_item_id,)).fetchone()
    if not item:
        raise ValueError('批次已不存在。')
    method = item['qc_method']
    from services.workbench_config_service import sync_lj_workbench_bindings
    from services.zscore_workbench_service import sync_zscore_workbench_bindings
    from services.instant_workbench_service import sync_instant_workbench_bindings
    issues = {'lj': sync_lj_workbench_bindings, 'zscore': sync_zscore_workbench_bindings,
              'instant': sync_instant_workbench_bindings}[method]()
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM qc_workbench_bindings WHERE lot_config_item_id=? AND qc_method=? AND binding_status='active'",
                                 (config_item_id, method)).fetchone()
        if not row:
            issue_rows = issues if isinstance(issues, list) else []
            detail = next((x.get('issue', '') for x in issue_rows if isinstance(x, dict) and x.get('lot_config_item_id') == config_item_id), '')
            raise ValueError(detail or '此批次尚不能进入，请先核对并确认批次设置。')
        binding = dict(row)
        if method == 'instant':
            batch = connection.execute('SELECT transfer_status,transferred_to_lj_project_id,transferred_to_lj_batch_id FROM instant_batches WHERE id=?',
                                       (binding['runtime_batch_id'],)).fetchone()
            if batch and batch['transfer_status'] == 'transferred' and batch['transferred_to_lj_batch_id']:
                binding.update(qc_method='lj', runtime_project_id=batch['transferred_to_lj_project_id'],
                               runtime_batch_id=batch['transferred_to_lj_batch_id'])
        return binding
