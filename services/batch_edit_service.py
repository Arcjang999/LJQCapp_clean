"""Read and save batch-item settings through the existing configuration services."""
from __future__ import annotations

import pandas as pd

from database import atomic_write, get_connection
from services.master_data_service import list_qc_levels
from services.material_workflow_service import available_materials as list_available_materials
from services.project_config_service import (
    get_lot_config, list_lot_config_items, list_lot_item_levels,
    save_lot_item_levels, save_lot_item_cv_requirement,
)


def _records(frame):
    return frame.astype(object).where(pd.notna(frame), None).to_dict('records')


def get_batch_item_context(lot_config_item_id: int) -> dict:
    """Include disabled history for reading, while keeping its settings read-only."""
    with get_connection() as connection:
        raw_item = connection.execute('''SELECT i.*, t.chinese_name AS test_item_name,
            u.symbol AS unit_symbol,m.method_name,r.generic_name AS reagent_name
            FROM qc_lot_config_items i JOIN md_test_items t ON t.id=i.test_item_id
            LEFT JOIN md_units u ON u.id=i.unit_id LEFT JOIN md_methods m ON m.id=i.method_id
            LEFT JOIN md_reagents r ON r.id=i.reagent_id WHERE i.id=?''',
            (int(lot_config_item_id),)).fetchone()
        if raw_item is None:
            raise ValueError('未找到该批次的检验项目，请重新选择。')
        raw_item = dict(raw_item)
        config = dict(get_lot_config(raw_item['lot_config_id']))
        listed_item = next((row for row in _records(list_lot_config_items(config['id']))
                            if row['id'] == int(lot_config_item_id)), {})
        item = {**raw_item, **listed_item}
        levels = _records(list_lot_item_levels(lot_config_item_id))
        item.setdefault('assigned_level_count', len(levels))
        binding_row = connection.execute('''SELECT * FROM qc_workbench_bindings
            WHERE lot_config_item_id=? ORDER BY id LIMIT 1''', (int(lot_config_item_id),)).fetchone()
        binding = dict(binding_row) if binding_row else None
        batch_used = connection.execute('SELECT 1 FROM qc_workbench_bindings WHERE lot_config_id=? LIMIT 1',
                                        (config['id'],)).fetchone() is not None
        if config['is_disabled'] or item['is_disabled']:
            reason = '本批次或检验项目已停用，不能修改设置。'
        elif config.get('activated_at') or batch_used:
            reason = '本批次已确认使用，原设置不能修改。请通过更换批次或均值和标准差管理调整。'
        elif config['status'] != 'draft':
            reason = '本批次设置已确认，不能修改。请在新批次中设置。'
        else:
            reason = ''
        if config.get('material_selection_mode'):
            available = _records(list_available_materials(config['qc_material_id']))
        else:
            available = _records(list_qc_levels(qc_material_lot_id=config['qc_material_lot_id']))
        return dict(config=config, item=item, levels=levels, available_levels=available,
                    binding=binding, editable=not bool(reason), read_only_reason=reason,
                    revision=int(config['revision_no']))


def save_batch_item_settings(item_id: int, assignments: list[dict], *,
                             expected_revision: int, cv_limit=None, source_text=None,
                             update_cv: bool = False) -> dict:
    """Save parameters and optional legacy CV together, with optimistic locking."""
    with atomic_write():
        context = get_batch_item_context(item_id)
        if not context['editable']:
            raise ValueError(context['read_only_reason'])
        if expected_revision != context['revision']:
            raise ValueError('本批次设置已修改，请重新打开后再保存。')
        save_lot_item_levels(item_id, assignments)
        if update_cv:
            save_lot_item_cv_requirement(item_id, cv_limit, source_text or '')
        return get_batch_item_context(item_id)
