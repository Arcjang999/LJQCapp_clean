"""One explicit, revision-checked setup using the existing configuration gates."""
from __future__ import annotations

import hashlib
import json

from database import atomic_write, read_snapshot
from services.material_workflow_service import validate_material_selection, material_label
from services.project_config_service import _normalize_template_item


_FIELDS = ('test_item_id', 'qc_method', 'input_value_type', 'unit_id', 'method_id',
           'reagent_id', 'level_count', 'target_n', 'cv_limit', 'quality_target_source_text')
_QUALITY_ARGS = ('confirmed_by', 'evidence', 'context', 'search_record',
                 'adopted_standard_ids', 'registered_standards', 'process_requirements')


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                      separators=(',', ':'), default=str)


def _hash(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _active(connection, table, value, label):
    if value is None:
        raise ValueError('请选择' + label + '。')
    try:
        identifier = int(value)
    except (ValueError, TypeError):
        raise ValueError('请选择有效的' + label + '。') from None
    row = connection.execute(f'SELECT * FROM {table} WHERE id=? AND is_disabled=0', (identifier,)).fetchone()
    if row is None:
        raise ValueError(label + '已停用或不存在，请重新选择。')
    return dict(row)


def get_quick_setup_context(values):
    """Resolve draft names and physical controls without creating anything."""
    with read_snapshot() as connection:
        return _context(connection, values)


def _context(connection, values):
    references = {}
    for field, table, label in (
        ('lab_instrument_id', 'lab_instruments', '仪器'),
        ('qc_material_id', 'md_qc_materials', '质控品'),
        ('test_item_id', 'md_test_items', '检验项目'),
        ('unit_id', 'md_units', '单位'),
        ('method_id', 'md_methods', '方法学'),
        ('reagent_id', 'md_reagents', '试剂'),
    ):
        references[field] = _active(connection, table, values.get(field), label)
    item = _normalize_template_item({key: values.get(key) for key in _FIELDS}, 1)
    test, unit, method = (references[key] for key in ('test_item_id', 'unit_id', 'method_id'))
    item.update(test_item_name=test['chinese_name'], abbreviation=test['abbreviation'],
                standard_code=test['standard_code'], specimen_type=test['specimen_type'],
                result_type=test['result_type'], unit_symbol=unit['symbol'],
                method_name=method['method_name'], method_code=method['method_code'],
                method_principle=method['principle'], quality_goal_json='{}')
    aliases = [row[0] for row in connection.execute(
        "SELECT alias_text FROM md_aliases WHERE entity_type='test_item' AND entity_id=? AND is_disabled=0 ORDER BY id",
        (item['test_item_id'],))]
    item['test_aliases'] = aliases
    try:
        level_ids = [int(value) for value in values.get('level_ids', [])]
    except (ValueError, TypeError):
        raise ValueError('请为全部水平选择有效的实际质控品。') from None
    levels = [dict(row) for row in validate_material_selection(connection,
        int(values['qc_material_id']), level_ids, item['level_count'])]
    # Selected lots/specifications and their parent status are part of the review.
    for level in levels:
        references['lot_' + str(level['qc_material_lot_id'])] = _active(
            connection, 'md_qc_material_lots', level['qc_material_lot_id'], '质控品批号')
    references['aliases'] = aliases
    return dict(item=item, references=references, levels=levels)


def _product_review(values):
    from services.qc_product_recommendation_service import validate_choice_review
    prior = values.get('product_choice_review') or {}
    if not prior:
        raise ValueError('请先选择质控品，并确认它可用于所选仪器和试剂。')
    return validate_choice_review(prior, qc_material_id=int(values['qc_material_id']),
        reagent_id=int(values['reagent_id']), lab_instrument_id=int(values['lab_instrument_id']),
        test_item_id=int(values['test_item_id']), method_id=int(values['method_id']))


def prepare_quick_project_setup(values):
    """Preflight and preview the exact setup; all source and quality checks are shared."""
    with read_snapshot() as connection:
        return _prepare(connection, values)


def _prepare(connection, values):
    from services.quality_review_service import build_review
    from services.quality_target_service import get_requirement, validate_spec_for_item, select_rule, finite_number
    name = str(values.get('template_name') or '').strip()
    if not name:
        raise ValueError('请填写项目名称。')
    if connection.execute('SELECT 1 FROM qc_project_templates WHERE template_name=? AND is_disabled=0', (name,)).fetchone():
        raise ValueError('已存在同名项目，请打开原项目或修改名称。')
    context = _context(connection, values)
    choice = _product_review(values)
    quality = dict(values.get('quality') or {})
    if not quality.get('checked'):
        raise ValueError('请核对并确认质量要求与全部浓度水平。')
    args = {key: quality.get(key) for key in _QUALITY_ARGS}
    mode = quality.get('mode')
    spec = None
    if mode == 'source':
        if not quality.get('requirement_id'):
            raise ValueError('请选择分析质量要求。')
        spec = get_requirement(quality.get('requirement_id'))
        validate_spec_for_item(spec, context['item'])
        proposed = quality.get('levels') or []
        if [row.get('level_order') for row in proposed] != list(range(1, len(context['levels']) + 1)):
            raise ValueError('请逐一确认全部水平的质量要求。')
        for row in proposed:
            concentration = finite_number(row.get('concentration'), '浓度值', optional=True)
            if concentration is not None and concentration < 0:
                raise ValueError('浓度值不能为负数。')
            select_rule(spec, concentration, row.get('category', ''))
        build_review(connection, context['item'], source_spec=spec, **args)
    elif mode == 'recorded':
        recorded = {key: quality.get(key) for key in ('source_name', 'source_version', 'requirement_text')}
        build_review(connection, context['item'], recorded=recorded, **args)
    elif mode == 'sd_cv':
        from services.quality_setup_service import simple_quality_payload, simple_cv_spec
        payload = simple_quality_payload(context['item'], args, cv_limit=quality.get('cv_limit'),
            confirmed_by=quality.get('confirmed_by', ''), evidence=quality.get('evidence', ''),
            levels=quality.get('levels'), requirement_text=quality.get('requirement_text'),
            source_name=quality.get('source_name'), source_version=quality.get('source_version'))
        args = {key: payload[key] for key in _QUALITY_ARGS}
        spec = simple_cv_spec(context['item'], payload)
        if spec is not None:
            proposed = [dict(level_order=order, qc_level_id=int(row['id']), concentration=None,
                             category='', rule=spec['imprecision'][0])
                        for order, row in enumerate(context['levels'], 1)]
            preview_item = {**context['item'], 'quality_goal_json': _json(dict(levels=proposed))}
            build_review(connection, preview_item, source_spec=spec, **args)
        else:
            build_review(connection, context['item'], recorded={**{key: payload[key]
                for key in ('source_name', 'source_version', 'requirement_text')}, 'setup_mode': 'sd_cv'}, **args)
    else:
        raise ValueError('请选择质量要求来源。')
    source_fingerprint = _hash(dict(references=context['references'], levels=context['levels'],
                                    product_choice=choice, spec=spec,
                                    catalog_review=payload['search_record'] if mode == 'sd_cv' else None))
    summary = [
        ('项目', name), ('仪器', context['references']['lab_instrument_id']['display_name']),
        ('检验项目', context['item']['test_item_name']), ('方法学', context['item']['method_name']),
        ('单位', context['item']['unit_symbol']),
        ('质控品', context['references']['qc_material_id']['generic_name']),
    ]
    from services.project_config_service import QC_METHOD_LABELS, INPUT_VALUE_TYPE_LABELS
    summary.extend([('质控方法', QC_METHOD_LABELS[context['item']['qc_method']]),
                    ('输入值', INPUT_VALUE_TYPE_LABELS[context['item']['input_value_type']]),
                    ('建靶有效点数', str(context['item']['target_n']))])
    if mode == 'sd_cv':
        summary.append(('质量要求', '本批次SD控制' + (f"；实验室CV≤{payload['cv_limit']:g}%" if payload['cv_limit'] is not None else '；未设置CV上限')))
    elif spec:
        summary.append(('质量要求', spec['standard'] + ' / ' + spec['version'] + '；' + spec['imprecision_text']))
    return dict(summary=summary, material_labels=[material_label(row) for row in context['levels']],
                source_fingerprint=source_fingerprint,
                preview_hash=_hash(dict(values=values, source_fingerprint=source_fingerprint)))


def save_quick_project_setup(values, *, expected_preview_hash, request_key):
    """Save project, requirements, batch and binding in one existing transaction."""
    if not request_key or len(str(request_key)) > 100:
        raise ValueError('请重新打开项目设置。')
    payload_hash = _hash(values)
    from migrations.quick_project_setup import ensure_quick_project_setup_schema
    from services.project_workspace_service import save_project_details, save_single_test_item, resolve_batch_binding
    from services.project_config_service import (get_project_template, activate_project_template,
        list_lot_config_items, activate_lot_config)
    from services.material_workflow_service import create_material_config
    with atomic_write() as connection:
        ensure_quick_project_setup_schema(connection)
        old = connection.execute('SELECT * FROM qc_quick_setup_receipts WHERE request_key=?', (str(request_key),)).fetchone()
        if old:
            if old['payload_sha256'] != payload_hash:
                raise ValueError('本次设置已保存，请返回项目核对，勿重复创建。')
            return json.loads(old['receipt_json'])
        preview = _prepare(connection, values)
        if preview['preview_hash'] != expected_preview_hash:
            raise ValueError('项目资料或可用质控品已修改。填写内容仍保留，请返回上一步检查，再查看保存预览。')
        item_values = {key: values.get(key) for key in _FIELDS}
        item_values.update(product_choice_review=_product_review(values), notes=str(values.get('item_notes') or ''))
        project_values = dict(template_name=values['template_name'], lab_instrument_id=values['lab_instrument_id'],
            qc_material_id=values['qc_material_id'], default_reagent_id=values['reagent_id'],
            default_method_id=values['method_id'], default_qc_method=values['qc_method'],
            default_level_count=values['level_count'], project_group=values.get('project_group', ''),
            notes=values.get('notes', ''), test_item_id=values['test_item_id'],
            product_choice_review=item_values['product_choice_review'])
        template_id = save_project_details(project_values)
        item_id = save_single_test_item(template_id, item_values,
            expected_revision=int(get_project_template(template_id)['revision_no']))
        _confirm_quality('project', item_id, values['quality'])
        activate_project_template(template_id)
        config_id = create_material_config(template_id=template_id, selections={item_id: values['level_ids']},
            config_name=str(values.get('config_name') or ''))
        config_item_id = int(list_lot_config_items(config_id).iloc[0]['id'])
        _confirm_quality('lot', config_item_id, values['quality'])
        activate_lot_config(config_id)
        binding = resolve_batch_binding(config_item_id)
        receipt = dict(template_id=template_id, template_item_id=item_id, lot_config_id=config_id,
                       lot_config_item_id=config_item_id, binding=binding)
        connection.execute('''INSERT INTO qc_quick_setup_receipts
            (request_key,payload_sha256,preview_hash,template_id,template_item_id,lot_config_id,lot_config_item_id,receipt_json)
            VALUES(?,?,?,?,?,?,?,?)''', (str(request_key), payload_hash, expected_preview_hash,
            template_id, item_id, config_id, config_item_id, _json(receipt)))
        return receipt


def _confirm_quality(scope, item_id, quality):
    from services.quality_target_service import adopt_requirement
    from services.quality_review_service import save_recorded_requirement
    args = {key: quality.get(key) for key in _QUALITY_ARGS}
    if quality['mode'] == 'sd_cv':
        from services.quality_setup_service import save_simple_quality
        return save_simple_quality(scope, item_id, **args, cv_limit=quality.get('cv_limit'),
            levels=quality.get('levels'), requirement_text=quality.get('requirement_text'),
            source_name=quality.get('source_name'), source_version=quality.get('source_version'))
    if quality['mode'] == 'source':
        return adopt_requirement(scope, item_id, quality['requirement_id'], **args,
            levels=quality.get('levels', []) if scope == 'lot' else [],
            supplemental_cv=quality.get('supplemental_cv'))
    return save_recorded_requirement(scope, item_id, **args,
        **{key: quality.get(key) for key in ('source_name', 'source_version', 'requirement_text')})
