"""Reviewable QC replacement drafts using the established copying services."""
import hashlib
import json

import pandas as pd

from database import atomic_write, get_connection
from services.master_data_service import list_qc_lots
from services.material_workflow_service import available_materials, copy_material_config
from services.lot_lifecycle_service import target_profile
from services.project_config_service import (
    copy_lot_config, get_lot_config, list_lot_config_items, list_lot_item_levels, save_lot_item_levels,
)


def _rows(connection, sql, values):
    return [dict(row) for row in connection.execute(sql, values)]


def get_qc_replacement_context(config_id):
    with get_connection() as connection:
        config = dict(get_lot_config(int(config_id)))
        project = dict(connection.execute('SELECT * FROM qc_project_templates WHERE id=?', (config['template_id'],)).fetchone())
        product = dict(connection.execute('SELECT * FROM md_qc_materials WHERE id=?', (config['qc_material_id'],)).fetchone())
        raw_items = _rows(connection, 'SELECT * FROM qc_lot_config_items WHERE lot_config_id=? ORDER BY id', (config_id,))
        raw_levels = _rows(connection, '''SELECT l.* FROM qc_lot_config_item_levels l
            JOIN qc_lot_config_items i ON i.id=l.lot_config_item_id WHERE i.lot_config_id=? ORDER BY l.id''', (config_id,))
        items = list_lot_config_items(config_id).to_dict('records')
        levels = {int(item['id']): list_lot_item_levels(int(item['id'])).to_dict('records') for item in items}
        materials = available_materials(config['qc_material_id'], include_disabled=True).to_dict('records')
        enabled_materials = available_materials(config['qc_material_id']).to_dict('records')
        lots = list_qc_lots(config['qc_material_id']).to_dict('records')
        existing = _rows(connection, '''SELECT * FROM qc_lot_configs WHERE template_id=?
            AND is_disabled=0 ORDER BY id''', (config['template_id'],))
        bindings = _rows(connection, 'SELECT * FROM qc_workbench_bindings WHERE lot_config_id=? ORDER BY id', (config_id,))
        references = {}
        for binding in bindings:
            # Routine synchronization refreshes only this timestamp on page runs.
            binding.pop('updated_at', None)
            if binding['qc_method'] not in ('lj', 'zscore'):
                continue
            profile = target_profile(binding['qc_method'], binding['runtime_batch_id'])
            if profile:
                frozen = json.loads(binding['source_snapshot_json'] or '{}').get('levels', [])
                values = {level['level_id']: level for level in profile['levels']}
                references[binding['lot_config_item_id']] = {
                    'profile': profile,
                    'levels': {int(level['qc_level_id']): values[f'Level {index+1}']
                               for index, level in enumerate(frozen)
                               if level.get('qc_level_id') and f'Level {index+1}' in values},
                }
        # Capture all identities/parameters that the old copying functions read.
        # The same context is rechecked inside the write transaction.
        state = [config, project, product, raw_items, raw_levels, materials, existing, bindings, references]
        fingerprint = hashlib.sha256(json.dumps(state, sort_keys=True, default=str).encode()).hexdigest()
        reason = ''
        if config['is_disabled'] or project['is_disabled']:
            reason = '此项目或批次已停用，只能查看原有资料。'
        elif product['is_disabled']:
            reason = '此质控品已停用，请先核对基础资料。'
        elif not items:
            reason = '原批次没有可沿用的检验项目，请先补充批次设置。'
        return dict(config=config, items=items, levels=levels, materials=enabled_materials,
                    all_materials=materials, lots=lots, existing=existing,
                    references=references,
                    fingerprint=fingerprint, editable=not reason, read_only_reason=reason)


def save_qc_replacement(*, source_config_id, expected_fingerprint, mode, selections=None,
                        target_lot_id=None, config_name=''):
    with atomic_write() as connection:
        context = get_qc_replacement_context(source_config_id)
        if context['fingerprint'] != expected_fingerprint:
            raise ValueError('原批次或质控品资料已修改，请关闭后重新打开，核对最新资料。')
        if not context['editable']:
            raise ValueError(context['read_only_reason'])
        if mode == 'materials':
            new_id = copy_material_config(source_config_id=int(source_config_id),
                                          selections=selections or {}, config_name=config_name)
            source_items = {item['id']: item for item in connection.execute(
                'SELECT * FROM qc_lot_config_items WHERE lot_config_id=?', (source_config_id,))}
            for item_id in (selections or {}):
                reference = context['references'].get(item_id)
                if not reference:
                    continue
                new_item = connection.execute('''SELECT id FROM qc_lot_config_items
                    WHERE lot_config_id=? AND source_template_item_id=?''',
                    (new_id, source_items[item_id]['source_template_item_id'])).fetchone()
                assignments = list_lot_item_levels(new_item['id']).to_dict('records')
                retained = False
                prior_ids = {row['qc_level_id'] for row in context['levels'][item_id]}
                for level in assignments:
                    material_id = level['qc_level_id']
                    parameters = reference['levels'].get(material_id) if material_id in prior_ids else None
                    if parameters:
                        level.update(target_mean=parameters['mean'], target_sd=parameters['sd'],
                                     target_source='copied_pending', target_confirmed=False)
                        connection.execute('''INSERT INTO qc_level_combination_members
                            (lot_config_item_id,qc_level_id,source_profile_id) VALUES(?,?,?)''',
                            (new_item['id'], material_id, reference['profile']['id']))
                        retained = True
                if retained:
                    save_lot_item_levels(new_item['id'], [dict(qc_level_id=row['qc_level_id'],
                        target_source=row['target_source'],
                        target_mean=None if pd.isna(row['target_mean']) else row['target_mean'],
                        target_sd=None if pd.isna(row['target_sd']) else row['target_sd'],
                        target_confirmed=bool(row['target_confirmed']), notes=row['notes']) for row in assignments])
            return new_id
        if mode == 'lot':
            if context['config']['material_selection_mode']:
                raise ValueError('此批次按各水平登记批号，请分别选择要更换的水平。')
            choices = {int(lot['id']) for lot in context['lots']}
            if target_lot_id not in choices:
                raise ValueError('请选择新质控品批号。')
            return copy_lot_config(source_lot_config_id=int(source_config_id),
                                   target_qc_material_lot_id=int(target_lot_id), config_name=config_name)
        raise ValueError('请选择更换质控品批次的方式。')
