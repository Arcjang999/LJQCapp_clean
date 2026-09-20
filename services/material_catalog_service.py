"""Safe catalogue editing for the material dialog, without changing QC calculations."""
from __future__ import annotations

import hashlib
import json
import sqlite3

import pandas as pd

from database import atomic_write, get_connection
from services.master_data_service import _date_text, create_qc_lot, set_master_entity_disabled
from services.material_workflow_service import create_material_spec


def _version(row: dict) -> str:
    return hashlib.sha256(json.dumps(row, sort_keys=True, default=str).encode()).hexdigest()


def _check_version(row: dict, expected_version: str) -> None:
    if not expected_version or row['edit_version'] != expected_version:
        raise ValueError('资料已修改，请关闭窗口后重新打开，核对最新内容。')


def _material_references(c, level_id: int, lot_id: int) -> dict:
    # Drafts, disabled configurations and verification records still retain identity.
    return {
        'config_count': c.execute('SELECT COUNT(DISTINCT lot_config_item_id) FROM qc_lot_config_item_levels WHERE qc_level_id=?', (level_id,)).fetchone()[0],
        'combination_count': c.execute('SELECT COUNT(*) FROM qc_level_combination_members WHERE qc_level_id=?', (level_id,)).fetchone()[0],
        'result_count': c.execute('SELECT COUNT(*) FROM qc_result_context_levels WHERE qc_level_id=?', (level_id,)).fetchone()[0],
        'lot_config_count': c.execute('SELECT COUNT(*) FROM qc_lot_configs WHERE qc_material_lot_id=?', (lot_id,)).fetchone()[0],
        'verification_count': c.execute('SELECT COUNT(*) FROM qc_lot_verifications WHERE qc_lot_id=?', (lot_id,)).fetchone()[0],
    }


def _get_control_material(c, level_id: int) -> dict:
    raw = c.execute('''SELECT l.*, q.lot_no, q.expiry_date, q.qc_material_id,
        q.is_disabled AS lot_disabled, q.updated_at AS lot_updated_at,
        m.generic_name AS product_name, m.is_disabled AS product_disabled,
        COALESCE(s.catalog_no,'') AS specification_catalog_no
        FROM md_qc_levels l JOIN md_qc_material_lots q ON q.id=l.qc_material_lot_id
        JOIN md_qc_materials m ON m.id=q.qc_material_id
        LEFT JOIN md_qc_material_specs s ON s.id=l.specification_id WHERE l.id=?''', (int(level_id),)).fetchone()
    if raw is None:
        raise ValueError('未找到所选质控品批号，请重新选择。')
    row = dict(raw)
    row.update(_material_references(c, row['id'], row['qc_material_lot_id']))
    row['identity_locked'] = any(row[k] for k in ('config_count', 'combination_count', 'result_count', 'lot_config_count', 'verification_count'))
    row['edit_version'] = _version(row)
    return row


def get_control_material(level_id: int) -> dict:
    with get_connection() as c:
        return _get_control_material(c, level_id)


def list_control_materials(material_id: int, include_disabled: bool = False) -> pd.DataFrame:
    with get_connection() as c:
        return pd.read_sql_query('''SELECT l.id, l.level_name, l.level_code, l.concentration_label,
            l.is_disabled, l.disabled_reason, q.lot_no, q.expiry_date,
            q.is_disabled AS lot_disabled, m.is_disabled AS product_disabled,
            COALESCE(s.catalog_no,'') AS specification_catalog_no
            FROM md_qc_levels l JOIN md_qc_material_lots q ON q.id=l.qc_material_lot_id
            JOIN md_qc_materials m ON m.id=q.qc_material_id
            LEFT JOIN md_qc_material_specs s ON s.id=l.specification_id
            WHERE q.qc_material_id=?''' + ('' if include_disabled else
            ' AND l.is_disabled=0 AND q.is_disabled=0 AND m.is_disabled=0') +
            ' ORDER BY l.is_disabled, q.expiry_date DESC, q.lot_no, l.level_order, l.id', c, params=(int(material_id),))


def update_control_material(level_id: int, *, level_name: str, level_code: str, catalog_no: str,
                            lot_no: str, expiry_date, concentration_note: str,
                            expected_version: str) -> None:
    values = [str(value or '').strip() for value in (level_name, level_code, catalog_no, lot_no)]
    name, code, catalog, lot_number = values
    expiry = _date_text(expiry_date)
    if not name or not lot_number or not expiry:
        raise ValueError('请填写浓度水平、批号和效期。')
    if any(len(value) > 100 for value in values):
        raise ValueError('浓度水平、编号、货号和批号各不能超过 100 字。')
    with atomic_write() as c:
        row = _get_control_material(c, level_id)
        _check_version(row, expected_version)
        if row['is_disabled'] or row['lot_disabled'] or row['product_disabled']:
            raise ValueError('所选质控品、批号或浓度水平已停用，请先核对并恢复。')
        identity_changed = (name, code, catalog, lot_number, expiry) != (
            row['level_name'], row['level_code'], row['specification_catalog_no'], row['lot_no'], row['expiry_date'])
        if identity_changed and row['identity_locked']:
            raise ValueError('此批号已用于批次设置、质控验证或检测，不能直接修改浓度水平、浓度编号、批号和效期。更换时请新增质控品批号。')
        if identity_changed:
            existing_spec = c.execute('''SELECT * FROM md_qc_material_specs WHERE
                qc_material_id=? AND level_name=? AND level_code=?''', (row['qc_material_id'], name, code)).fetchone()
            if existing_spec is not None and existing_spec['catalog_no'] != catalog:
                other_materials = c.execute('SELECT COUNT(*) FROM md_qc_levels WHERE specification_id=? AND id<>?', (existing_spec['id'], level_id)).fetchone()[0]
                if other_materials:
                    raise ValueError('其它批号也使用了此浓度水平，不能直接修改货号，请核对所选浓度水平和浓度编号。')
                c.execute('UPDATE md_qc_material_specs SET catalog_no=? WHERE id=?', (catalog, existing_spec['id']))
            spec_id = create_material_spec(row['qc_material_id'], name, code, catalog)
            target = c.execute('''SELECT * FROM md_qc_material_lots
                WHERE qc_material_id=? AND LOWER(TRIM(lot_no))=LOWER(TRIM(?))
                ORDER BY is_disabled,id DESC''', (row['qc_material_id'], lot_number)).fetchone()
            if target is not None and target['is_disabled']:
                raise ValueError('所填批号已停用，请先核对并恢复该批号。')
            if target is not None and target['expiry_date'] != expiry:
                siblings = c.execute('SELECT COUNT(*) FROM md_qc_levels WHERE qc_material_lot_id=? AND id<>?', (target['id'], level_id)).fetchone()[0]
                if target['id'] != row['qc_material_lot_id'] or siblings:
                    raise ValueError('同一批号的其它浓度水平已填写不同效期，请核对批号和效期。')
                c.execute('UPDATE md_qc_material_lots SET expiry_date=?,updated_at=CURRENT_TIMESTAMP WHERE id=?', (expiry, target['id']))
            lot_id = target['id'] if target is not None else create_qc_lot(
                qc_material_id=row['qc_material_id'], lot_no=lot_number, expiry_date=expiry)
            duplicate = c.execute('''SELECT id FROM md_qc_levels WHERE qc_material_lot_id=?
                AND level_name=? AND level_code=? AND id<>?''', (lot_id, name, code, level_id)).fetchone()
            if duplicate:
                raise ValueError('此批号下已登记相同的浓度水平和浓度编号，请选择已有记录。')
            order = row['level_order']
            if lot_id != row['qc_material_lot_id']:
                occupied = {r[0] for r in c.execute('SELECT level_order FROM md_qc_levels WHERE qc_material_lot_id=? AND is_disabled=0', (lot_id,))}
                order = next((n for n in range(1, 10) if n not in occupied), None)
                if order is None:
                    raise ValueError('所填批号已有 9 个浓度水平，请核对批号。')
            c.execute('''UPDATE md_qc_levels SET specification_id=?,level_name=?,level_code=?,
                qc_material_lot_id=?,level_order=? WHERE id=?''', (spec_id, name, code, lot_id, order, level_id))
        c.execute('UPDATE md_qc_levels SET concentration_label=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',
                  (str(concentration_note or '').strip(), int(level_id)))


def set_control_material_disabled(level_id: int, *, is_disabled: bool, reason: str = '',
                                  expected_version: str) -> None:
    with atomic_write() as c:
        row = _get_control_material(c, level_id)
        _check_version(row, expected_version)
        if is_disabled and not str(reason or '').strip():
            raise ValueError('请填写停用原因。')
        if not is_disabled and (row['lot_disabled'] or row['product_disabled']):
            raise ValueError('此质控品或批号仍为停用状态，请先恢复质控品或批号。')
        if not is_disabled:
            duplicate = c.execute('''SELECT id FROM md_qc_levels WHERE qc_material_lot_id=?
                AND level_name=? AND level_code=? AND id<>? AND is_disabled=0''',
                (row['qc_material_lot_id'], row['level_name'], row['level_code'], int(level_id))).fetchone()
            if duplicate:
                raise ValueError('此批号下已有相同浓度水平和浓度编号的记录可供使用，请选择已有记录。')
            occupied = {r[0] for r in c.execute('SELECT level_order FROM md_qc_levels WHERE qc_material_lot_id=? AND is_disabled=0 AND id<>?', (row['qc_material_lot_id'], level_id))}
            if row['level_order'] in occupied:
                if row['identity_locked']:
                    raise ValueError('此批号下有另一条记录使用了相同的水平顺序。当前记录已用于批次设置或检测，请先核对并停用重复记录，再恢复。')
                order = next((n for n in range(1, 10) if n not in occupied), None)
                if order is None:
                    raise ValueError('此批号已有 9 个启用浓度水平，不能恢复。')
                c.execute('UPDATE md_qc_levels SET level_order=? WHERE id=?', (order, int(level_id)))
        set_master_entity_disabled('qc_level', level_id, is_disabled=is_disabled, reason=reason)


def restore_control_material_lot(level_id: int, *, expected_version: str) -> None:
    """Keep the recovery path for lot records disabled through earlier catalogue screens."""
    with atomic_write():
        row = get_control_material(level_id)
        _check_version(row, expected_version)
        if row['product_disabled']:
            raise ValueError('此质控品仍为停用状态，请先恢复质控品。')
        set_master_entity_disabled('qc_lot', row['qc_material_lot_id'], is_disabled=False)


def get_material_product(product_id: int) -> dict:
    with get_connection() as c:
        raw = c.execute('SELECT * FROM md_qc_materials WHERE id=?', (int(product_id),)).fetchone()
        if raw is None:
            raise ValueError('未找到质控品。')
        row = dict(raw)
        row['lot_count'] = c.execute('SELECT COUNT(*) FROM md_qc_material_lots WHERE qc_material_id=?', (product_id,)).fetchone()[0]
        row['project_count'] = c.execute('SELECT COUNT(*) FROM qc_project_templates WHERE qc_material_id=?', (product_id,)).fetchone()[0]
        row['identity_locked'] = bool(row['lot_count'] or row['project_count'])
        row['edit_version'] = _version(row)
        return row


def update_material_product(product_id: int, *, expected_version: str, **fields) -> None:
    keys = ('generic_name', 'trade_name', 'matrix', 'physical_form', 'catalog_no', 'registration_no')
    with atomic_write() as c:
        row = get_material_product(product_id)
        _check_version(row, expected_version)
        if row['is_disabled']:
            raise ValueError('请先恢复质控品，再编辑资料。')
        changed = any(str(fields.get(k, row[k]) or '').strip() != str(row[k] or '') for k in keys)
        if row['identity_locked'] and changed:
            raise ValueError('此质控品已登记批号或用于项目，不能直接修改名称、货号等资料；更换时请新增质控品。')
        values = {k: str(fields.get(k, row[k]) or '').strip() for k in keys}
        if not values['generic_name']:
            raise ValueError('请填写质控品名称。')
        if any(len(v) > 200 for v in values.values()):
            raise ValueError('每项质控品资料不能超过 200 字，请缩短填写内容。')
        try:
            c.execute('''UPDATE md_qc_materials SET generic_name=?,trade_name=?,matrix=?,physical_form=?,
                catalog_no=?,registration_no=?,notes=?,updated_at=CURRENT_TIMESTAMP WHERE id=?''',
                (*[values[k] for k in keys], str(fields.get('notes', row['notes']) or '').strip(), int(product_id)))
        except sqlite3.IntegrityError as exc:
            raise ValueError('此厂家下已登记同名质控品，请选择已有质控品。') from exc


def set_material_product_disabled(product_id: int, *, is_disabled: bool, reason: str = '',
                                  expected_version: str) -> None:
    with atomic_write():
        row = get_material_product(product_id)
        _check_version(row, expected_version)
        if is_disabled and not str(reason or '').strip():
            raise ValueError('请填写停用原因。')
        set_master_entity_disabled('qc_material', product_id, is_disabled=is_disabled, reason=reason)
