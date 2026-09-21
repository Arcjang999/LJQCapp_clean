"""Edit local catalogue records while preserving identities used by QC records."""
from __future__ import annotations

import hashlib
import json
import sqlite3

from database import atomic_write, get_connection
from services import master_data_service as master


EDIT_FIELDS = {
    'manufacturer': frozenset(('display_name', 'legal_name', 'country_or_region', 'registration_holder_name', 'notes')),
    'test_item': frozenset(('chinese_name', 'standard_code', 'english_name', 'abbreviation', 'category_name', 'specimen_type', 'default_unit_id', 'notes')),
    'instrument_model': frozenset(('manufacturer_id', 'generic_name', 'brand_name', 'model', 'registration_no', 'device_category_code', 'catalog_no', 'notes')),
    'lab_instrument': frozenset(('instrument_model_id', 'display_name', 'asset_code', 'serial_number', 'department_name', 'instrument_group', 'location', 'notes')),
    'reagent': frozenset(('manufacturer_id', 'generic_name', 'trade_name', 'specification', 'registration_no', 'catalog_no', 'applicable_instrument_text', 'notes')),
    'method': frozenset(('method_name', 'method_code', 'method_category', 'principle', 'notes')),
    'unit': frozenset(('symbol', 'unit_name', 'ucum_code', 'quantity_kind', 'notes')),
    'alias': frozenset(('entity_type', 'entity_id', 'alias_text', 'alias_type')),
}

_CREATORS = {name: getattr(master, f'create_{name}') for name in EDIT_FIELDS}
_LABELS = {'manufacturer': '厂家', 'test_item': '检验项目', 'instrument_model': '仪器型号',
           'lab_instrument': '仪器', 'reagent': '试剂', 'method': '方法学', 'unit': '单位', 'alias': '别名'}
_REQUIRED = {
    'manufacturer': {'display_name': '厂家名称'},
    'test_item': {'chinese_name': '检验项目名称'},
    'instrument_model': {'generic_name': '仪器名称', 'model': '仪器型号'},
    'lab_instrument': {'display_name': '仪器名称'},
    'reagent': {'generic_name': '试剂名称'},
    'method': {'method_name': '方法学名称'},
    'unit': {'symbol': '单位符号'},
    'alias': {'alias_text': '别名'},
}
_FOREIGN_FIELDS = {
    'test_item': {'default_unit_id': ('unit', False)},
    'instrument_model': {'manufacturer_id': ('manufacturer', False)},
    'lab_instrument': {'instrument_model_id': ('instrument_model', True)},
    'reagent': {'manufacturer_id': ('manufacturer', False)},
}
_ALIAS_TARGETS = {'manufacturer', 'test_item', 'instrument_model', 'reagent', 'qc_material', 'method', 'unit'}
_ALIAS_TYPES = {'short_name', 'english', 'brand', 'lis_code', 'historical', 'vendor_text', 'custom'}
_SAFE_REFERENCED_FIELDS = {'notes'}


def _entity_table(entity_type: str) -> str:
    if entity_type not in EDIT_FIELDS:
        raise ValueError('不支持的基础资料类型。')
    return master.MASTER_ENTITY_TABLES[entity_type]


def _fingerprint(entity_type: str, record: dict) -> str:
    # The WST catalogue seed refreshes official test-item updated_at at every init.
    # Compare all business fields, including notes/status, even within the same second.
    values = {key: value for key, value in record.items()
              if not (entity_type == 'test_item' and record.get('origin_type') == 'official' and key == 'updated_at')}
    return hashlib.sha256(json.dumps(values, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _quoted(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _is_referenced(connection, table: str, entity_id: int) -> bool:
    # Keep disabled rows and draft configurations in this check: their history still matters.
    # Runtime results refer to the configuration rows, which retain these foreign keys.
    tables = connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
    for item in tables:
        child = item['name']
        for foreign_key in connection.execute(f'PRAGMA foreign_key_list({_quoted(child)})'):
            if foreign_key['table'] != table or foreign_key['to'] not in ('id', None):
                continue
            if connection.execute(
                f'SELECT 1 FROM {_quoted(child)} WHERE {_quoted(foreign_key["from"])}=? LIMIT 1',
                (entity_id,),
            ).fetchone():
                return True
    return False


def _context(connection, entity_type: str, entity_id: int) -> dict:
    table = _entity_table(entity_type)
    raw = connection.execute(f'SELECT * FROM {table} WHERE id=?', (int(entity_id),)).fetchone()
    if raw is None:
        raise ValueError('未找到所选资料，请重新选择。')
    record = dict(raw)
    referenced = _is_referenced(connection, table, int(entity_id)) or bool(connection.execute(
        'SELECT 1 FROM md_source_records WHERE entity_type=? AND entity_id=? LIMIT 1',
        (entity_type, int(entity_id)),
    ).fetchone())
    locked = set()
    reason = ''
    if record['origin_type'] == 'official':
        locked = EDIT_FIELDS[entity_type] - {'notes'}
        reason = '此条资料为系统收录内容，名称、代码等不能直接修改；可以补充备注。'
    elif referenced:
        safe = _SAFE_REFERENCED_FIELDS
        if entity_type == 'lab_instrument':
            safe = safe | {'department_name', 'instrument_group', 'location'}
        locked = EDIT_FIELDS[entity_type] - safe
        reason = '此条资料已用于项目或其他资料，名称、代码等不能直接修改；更换时请新增资料。'
    if entity_type == 'alias':
        locked = set(locked) | {'entity_type', 'entity_id'}
        if not reason:
            reason = '别名所属的资料不能更换；需要更换时请新增别名。'
    return {'record': record, 'fingerprint': _fingerprint(entity_type, record),
            'locked_fields': sorted(locked), 'lock_reason': reason, 'referenced': referenced}


def get_master_record_context(entity_type: str, entity_id: int) -> dict:
    with get_connection() as connection:
        return _context(connection, entity_type, entity_id)


def _check_fingerprint(context: dict, expected: str | None) -> None:
    if not expected or context['fingerprint'] != expected:
        raise ValueError('资料已修改，请关闭窗口后重新打开，核对最新内容。')


def _id(value, label: str, *, required: bool = False) -> int | None:
    if value in (None, ''):
        if required:
            raise ValueError(f'请选择{label}。')
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f'请选择有效的{label}。') from exc
    if isinstance(value, bool) or parsed <= 0 or str(value).strip() != str(parsed):
        raise ValueError(f'请选择有效的{label}。')
    return parsed


def _clean_values(entity_type: str, supplied: dict, record: dict | None) -> dict:
    if not isinstance(supplied, dict) or set(supplied) - EDIT_FIELDS[entity_type]:
        raise ValueError('提交的资料内容不完整或包含不能编辑的内容，请重新打开窗口。')
    values = {field: record[field] for field in EDIT_FIELDS[entity_type]} if record else {}
    foreign = _FOREIGN_FIELDS.get(entity_type, {})
    for field, value in supplied.items():
        if field in foreign:
            target, required = foreign[field]
            values[field] = _id(value, _LABELS[target], required=required)
        elif entity_type == 'alias' and field == 'entity_id':
            values[field] = _id(value, '别名所属的资料', required=True)
        elif field == 'notes':
            values[field] = str(value or '').strip()
        else:
            values[field] = master._clean_optional(value)
    for field, label in _REQUIRED[entity_type].items():
        values[field] = master._clean_required(values.get(field), label)
    if entity_type == 'manufacturer':
        values['legal_name'] = values.get('legal_name') or values['display_name']
    if entity_type == 'alias':
        if values.get('entity_type') not in _ALIAS_TARGETS:
            raise ValueError('请选择别名所属的资料。')
        values['entity_id'] = _id(values.get('entity_id'), '别名所属的资料', required=True)
        values.setdefault('alias_type', 'custom')
        if values['alias_type'] not in _ALIAS_TYPES:
            raise ValueError('请选择有效的别名类型。')
    return values


def _check_foreign(connection, entity_type: str, values: dict, original: dict | None) -> None:
    links = dict(_FOREIGN_FIELDS.get(entity_type, {}))
    if entity_type == 'alias':
        links['entity_id'] = (values['entity_type'], True)
    for field, (target, required) in links.items():
        label = _LABELS.get(target, '质控品')
        identifier = _id(values.get(field), label, required=required)
        if identifier is None:
            continue
        row = connection.execute(f'SELECT is_disabled FROM {master.MASTER_ENTITY_TABLES[target]} WHERE id=?', (identifier,)).fetchone()
        if row is None:
            raise ValueError(f'所选{label}不存在，请重新选择。')
        unchanged = original is not None and original.get(field) == identifier
        if entity_type == 'alias':
            unchanged = unchanged and original.get('entity_type') == target
        if row['is_disabled'] and not unchanged:
            raise ValueError(f'所选{label}已停用，请选择其他资料，或先恢复后再使用。')


def save_master_record(entity_type: str, values: dict, *, entity_id: int | None = None,
                       expected_fingerprint: str | None = None) -> int:
    table = _entity_table(entity_type)
    with atomic_write() as connection:
        context = _context(connection, entity_type, entity_id) if entity_id is not None else None
        original = context['record'] if context else None
        if context:
            _check_fingerprint(context, expected_fingerprint)
            if original['is_disabled']:
                raise ValueError('此条资料已停用，请先恢复，再编辑。')
        normalized = _clean_values(entity_type, values, original)
        if context and any(normalized.get(field) != original[field] for field in context['locked_fields']):
            raise ValueError(context['lock_reason'])
        _check_foreign(connection, entity_type, normalized, original)
        if original is None:
            identifier = _CREATORS[entity_type](**normalized)
            if 'notes' in normalized:
                # Old creators collapse whitespace. Preserve the dialog's multiline notes
                # within the same transaction without changing their older callers.
                connection.execute(f'UPDATE {table} SET notes=? WHERE id=?', (normalized['notes'], identifier))
            return identifier
        changes = {field: value for field, value in normalized.items() if value != original[field]}
        if not changes:
            return int(entity_id)
        if entity_type == 'alias' and 'alias_text' in changes:
            changes['normalized_alias'] = master._normalize_alias(normalized['alias_text'])
        try:
            columns = ','.join(f'{field}=?' for field in changes)
            connection.execute(f'UPDATE {table} SET {columns},updated_at=CURRENT_TIMESTAMP WHERE id=?',
                               (*changes.values(), int(entity_id)))
        except sqlite3.IntegrityError as exc:
            raise ValueError(f'已有同名或同编码的{_LABELS[entity_type]}，请核对填写内容。') from exc
        return int(entity_id)


def change_master_status(entity_type: str, entity_id: int, *, disabled: bool,
                         expected_fingerprint: str, reason: str = '') -> None:
    _entity_table(entity_type)
    with atomic_write() as connection:
        context = _context(connection, entity_type, entity_id)
        _check_fingerprint(context, expected_fingerprint)
        if disabled and not master._clean_optional(reason):
            raise ValueError('请填写停用原因。')
        if not disabled:
            _check_foreign(connection, entity_type, context['record'], context['record'])
        master.set_master_entity_disabled(entity_type, entity_id, is_disabled=disabled, reason=reason)
