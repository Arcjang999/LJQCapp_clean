"""Read-only, exact-identity daily batch selection and optimistic context versions."""
from __future__ import annotations

from hashlib import sha256
import json

import database
from services.lot_lifecycle_service import effective_qc_state, timestamp, usage_revision


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _decode(value):
    return json.loads(value or '{}') if isinstance(value, str) else (value or {})


def _digest(value):
    return sha256(_json(value).encode()).hexdigest()


def _positive(value, label):
    try:
        result = int(value)
        if isinstance(value, bool) or str(result) != str(value).strip() or result <= 0:
            raise ValueError
        return result
    except (TypeError, ValueError):
        raise ValueError(f'请选择有效的{label}。') from None


def issue(message, *, row_key=None, field='context', qc_level_id=None, settings_target=None):
    if settings_target is None:
        settings_target = {'quality':'quality', 'target_profile':'parameters', 'reagent_lot_id':'reagent',
            'usage':'usage', 'levels':'materials', 'material':'materials', 'expiry_date':'materials'}.get(field, 'project')
    return dict(row_key=row_key, field=field, qc_level_id=qc_level_id,
                message=str(message), settings_target=settings_target)


def _rows(c, sql, params=()):
    return [dict(row) for row in c.execute(sql, params)]


def list_daily_choices(template_id=None):
    """Choices come from actual level relationships, never a header lot/name guess."""
    template = _positive(template_id, '项目') if template_id is not None else None
    with database.read_snapshot() as c:
        links = _rows(c, '''SELECT DISTINCT t.lab_instrument_id,t.qc_material_id,l.qc_material_lot_id
            FROM qc_project_templates t JOIN qc_lot_configs x ON x.template_id=t.id
            JOIN qc_lot_config_items i ON i.lot_config_id=x.id
            JOIN qc_lot_config_item_levels a ON a.lot_config_item_id=i.id AND a.is_disabled=0
            JOIN md_qc_levels l ON l.id=a.qc_level_id
            WHERE i.is_disabled=0 AND i.is_enabled=1 AND (? IS NULL OR t.id=?)''', (template, template))
        instruments = _rows(c, 'SELECT id,display_name AS name,is_disabled FROM lab_instruments ORDER BY display_name,id')
        materials = _rows(c, 'SELECT id,generic_name AS name,is_disabled FROM md_qc_materials ORDER BY generic_name,id')
        lots = _rows(c, 'SELECT id,qc_material_id,lot_no,expiry_date,is_disabled FROM md_qc_material_lots ORDER BY lot_no,id')
        instruments = [r for r in instruments if any(x['lab_instrument_id'] == r['id'] for x in links)]
        for r in materials:
            r['instrument_ids'] = sorted({x['lab_instrument_id'] for x in links if x['qc_material_id'] == r['id']})
        for r in lots:
            r['instrument_ids'] = sorted({x['lab_instrument_id'] for x in links if x['qc_material_lot_id'] == r['id']})
        return dict(instruments=instruments, materials=[r for r in materials if r['instrument_ids']],
                    lots=[r for r in lots if r['instrument_ids']])


def _levels(c, item_id):
    return _rows(c, '''SELECT a.*,l.level_name,l.level_code,l.concentration_label,l.specification_id,
        l.is_disabled AS level_disabled,l.qc_material_lot_id,q.qc_material_id,q.lot_no,q.expiry_date,
        q.is_disabled AS lot_disabled,m.source_profile_id,m.verification_id
        FROM qc_lot_config_item_levels a JOIN md_qc_levels l ON l.id=a.qc_level_id
        JOIN md_qc_material_lots q ON q.id=l.qc_material_lot_id
        LEFT JOIN qc_level_combination_members m ON m.lot_config_item_id=a.lot_config_item_id AND m.qc_level_id=a.qc_level_id
        WHERE a.lot_config_item_id=? AND a.is_disabled=0 ORDER BY a.level_order,a.id''', (item_id,))


def _reagents(c, source, when):
    identity = source.get('identity') or []
    system_id = None
    if source.get('project_template_item_id') and len(identity) >= 8:
        # This is the exact identity used by source_context, without its INSERT.
        key = json.dumps([source['project_template_item_id'], *[identity[i] for i in (0, 3, 4, 5, 6, 7)]])
        found = c.execute('SELECT id FROM qc_detection_systems WHERE identity_json=?', (key,)).fetchone()
        system_id = found[0] if found else None
    if system_id is None:
        return dict(system_id=None, usage_revision=0, reagent_options=[], suggested_reagent_lot_id=None)
    options = _rows(c, '''SELECT l.*,v.id AS verification_id FROM md_reagent_lots l
        JOIN md_reagents r ON r.id=l.reagent_id JOIN qc_lot_verifications v ON v.reagent_lot_id=l.id
        WHERE r.is_disabled=0 AND l.is_disabled=0 AND l.expiry_date>=? AND v.system_id=?
        AND v.conclusion='pass' AND v.confirmed_at<=? AND v.id=(SELECT v2.id FROM qc_lot_verifications v2
        WHERE v2.system_id=v.system_id AND v2.reagent_lot_id=l.id AND v2.confirmed_at<=?
        ORDER BY v2.confirmed_at DESC,v2.id DESC LIMIT 1) ORDER BY l.id''', (when[:10], system_id, when, when))
    current = c.execute('''SELECT reagent_lot_id FROM qc_reagent_lot_usage WHERE system_id=? AND effective_at<=?
        ORDER BY effective_at DESC,id DESC LIMIT 1''', (system_id, when)).fetchone()
    return dict(system_id=system_id, usage_revision=usage_revision(c, system_id), reagent_options=options,
                suggested_reagent_lot_id=current[0] if current else None)


def _quality_issues(c, item, source):
    """Apply the existing task-0 review to the confirmed source; no frozen-lot bypass."""
    from services.quality_target_service import item_context, get_requirement, validate_spec_for_item
    from services.quality_review_service import build_review, _digest as quality_digest, _identity
    review = _decode(source.get('quality_review_json'))
    goal = _decode(source.get('quality_goal_json'))
    if review.get('version') != 2 or review.get('status') != 'confirmed':
        return ['质量要求尚未确认，请在项目与批次设置中核对来源和适用条件。']
    try:
        current = item_context('lot', item['id'], c)
        if review.get('identity') != _identity(current):
            raise ValueError('项目、尺度、单位或质量要求已变化，请重新核对配置。')
        if quality_digest(goal) != review.get('goal_fingerprint') or goal != _decode(item.get('quality_goal_json')):
            raise ValueError('检测时采用的质量要求与当前配置不一致，请重新核对。')
        spec = None
        if review['decision'] in ('standard', 'custom'):
            spec = get_requirement(review['selected_source_id'])
            if quality_digest(spec) != review.get('selected_source_fingerprint') or goal.get('spec') != spec:
                raise ValueError('采用的分析质量要求已变化，请重新确认来源。')
            validate_spec_for_item(spec, current)
            expected = {(r['level_order'], r['qc_level_id']) for r in _levels(c, item['id'])}
            if goal.get('pending') or expected != {(r['level_order'], r['qc_level_id']) for r in goal.get('levels', [])}:
                raise ValueError('请逐水平核对质量要求、浓度及适用依据。')
        rebuilt = build_review(c, current, source_spec=spec, confirmed_by=review.get('confirmed_by'),
            evidence=review.get('evidence'), context=review.get('context'), search_record=review.get('search_record'),
            recorded=review.get('recorded'), registered_standards=review.get('registered_standards'),
            process_requirements=review.get('process_requirements'),
            adopted_standard_ids=[r['id'] for r in review.get('candidates', []) if r['kind'] != 'numeric' and r['disposition'] == 'adopted'])
        if rebuilt['candidates'] != review.get('candidates') or rebuilt['registered_standards'] != review.get('registered_standards'):
            raise ValueError('标准条款或适用条件已变化，请重新核对质量要求。')
        if review.get('context', {}).get('result_scale') == 'qualitative':
            raise ValueError('已登记定性依据；当前工作台不支持阳性或阴性结果录入。')
    except (ValueError, KeyError, TypeError) as exc:
        return [str(exc)]
    return []


def _item(c, item, config, when):
    row_key = str(item['id'])
    issues = []
    def problem(message, field='context', level=None):
        issues.append(issue(message, row_key=row_key, field=field, qc_level_id=level))
    levels = _levels(c, item['id'])
    binding_row = c.execute('SELECT * FROM qc_workbench_bindings WHERE lot_config_item_id=?', (item['id'],)).fetchone()
    binding = dict(binding_row) if binding_row else {}
    source = _decode(binding.get('source_snapshot_json'))
    method = item['qc_method']
    if config['status'] != 'active' or config['is_disabled'] or config['template_status'] != 'active' or config['template_disabled']:
        problem('项目或批次设置尚未确认，或已停用。')
    if not binding or binding.get('binding_status') != 'active' or binding.get('qc_method') != method:
        problem('此批次尚未准备好，请先从项目与批次设置中打开并核对。', 'binding')
    if len(levels) != item['level_count'] or (method == 'zscore' and item['level_count'] not in (2, 3)) or (method != 'zscore' and item['level_count'] != 1):
        problem('质控水平不完整或与所选方法不一致，请核对完整水平。', 'levels')
    identity = [config['lab_instrument_id'], config['qc_material_id'], config['qc_material_lot_id'],
                item['test_item_id'], item['input_value_type'], item['unit_id'], item['method_id'], item['reagent_id']]
    compare = (0, 1, 3, 4, 5, 6, 7) if config['material_selection_mode'] else range(8)
    frozen_identity = source.get('identity') or []
    if len(frozen_identity) < 8 or any(identity[k] != frozen_identity[k] for k in compare):
        problem('当前批次与所选仪器或检测项目不一致，请重新核对项目设置。', 'binding')
    if [r['qc_level_id'] for r in levels] != [r.get('qc_level_id') for r in source.get('levels', [])]:
        problem('实际材料或水平顺序已变化，请重新核对批次组合。', 'levels')
    for table, identifier, label in [('lab_instruments', identity[0], '仪器'), ('md_qc_materials', identity[1], '质控品'),
        ('md_test_items', identity[3], '检测项'), ('md_units', identity[5], '单位'), ('md_methods', identity[6], '方法学'), ('md_reagents', identity[7], '试剂')]:
        if not identifier or not c.execute(f'SELECT 1 FROM {table} WHERE id=? AND is_disabled=0', (identifier,)).fetchone():
            problem(f'{label}未设置或已停用，请先核对基础资料。')
    for level in levels:
        if level['qc_material_id'] != config['qc_material_id'] or level['lot_disabled'] or level['level_disabled']:
            problem('该水平材料不属于所选产品或已停用。', 'material', level['qc_level_id'])
        if not level['expiry_date'] or level['expiry_date'] < when[:10]:
            problem('该水平实际批号未填写效期或在检测日已过期。', 'expiry_date', level['qc_level_id'])
        level.update(level_id=f"Level {level['level_order']}", role='retained' if level.get('source_profile_id') else 'configured',
                     combination_revision=config['revision_no'])
    state = effective_qc_state(c, item['id'], when)
    if state in ('pending', 'ended') or effective_qc_state(c, item['id']) in ('pending', 'ended'):
        problem('批次在所选检测时间尚未使用或已停止使用，请核对使用期间。', 'usage')
    table = 'instant_batches' if method == 'instant' else 'batches'
    batch_row = c.execute(f'SELECT * FROM {table} WHERE id=?', (binding.get('runtime_batch_id'),)).fetchone()
    batch = dict(batch_row) if batch_row else {}
    if not batch or batch.get('is_disabled') or batch.get('transfer_status') == 'transferred':
        problem('批次未准备、已停用或已转入单水平法，不能保存新检测。', 'binding')
    profile_row = c.execute('''SELECT * FROM qc_target_profiles WHERE qc_method=? AND batch_id=? AND effective_at<=?
        ORDER BY effective_at DESC,id DESC LIMIT 1''', (method, binding.get('runtime_batch_id'), when)).fetchone()
    profile = dict(profile_row) if profile_row else None
    if profile:
        profile['levels'] = json.loads(profile['levels_json'])
    elif method != 'instant' and any(r['target_source'] != 'building' for r in levels):
        problem('尚无适用于检测时间的完整均值和标准差版本，请先确认参数。', 'target_profile')
    if source:
        for message in _quality_issues(c, item, source):
            problem(message, 'quality')
    reagent = _reagents(c, source, when)
    if not reagent['system_id']:
        problem('此检验项目尚未确认使用，请在“项目与批次”中核对并确认设置。', 'binding')
    if not reagent['reagent_options']:
        problem('尚无适用于本次检测的试剂批号，请先登记并核对试剂使用资料。', 'reagent_lot_id')
    result_table = {'lj': 'results', 'zscore': 'zscore_runs', 'instant': 'instant_results'}[method]
    history = _rows(c, f'SELECT * FROM {result_table} WHERE batch_id=? ORDER BY id', (binding.get('runtime_batch_id'),))
    profiles = _rows(c, 'SELECT * FROM qc_target_profiles WHERE qc_method=? AND batch_id=? ORDER BY id', (method, binding.get('runtime_batch_id')))
    result_column = {'lj': 'lj_result_id', 'zscore': 'zscore_run_id', 'instant': 'instant_result_id'}[method]
    latest_eval = c.execute(f'''SELECT COALESCE(MAX(e.id),0) FROM qc_result_evaluations e JOIN qc_result_contexts x ON x.id=e.context_id
        JOIN {result_table} r ON r.id=x.{result_column} WHERE r.batch_id=?''', (binding.get('runtime_batch_id'),)).fetchone()[0]
    level_history = _rows(c, '''SELECT l.* FROM zscore_level_results l JOIN zscore_runs r ON r.id=l.run_id
        WHERE r.batch_id=? ORDER BY l.id''', (binding.get('runtime_batch_id'),)) if method == 'zscore' else []
    # Complete row hashes also notice supported maintenance and changes without a revision counter.
    revision = _digest(dict(config=config, item=item, levels=levels, binding=binding, batch=batch,
        profiles=profiles, history=history, level_history=level_history, latest_evaluation_id=latest_eval,
        reagent=reagent, state=state, issues=issues))
    return dict(row_key=row_key, lot_config_item_id=item['id'], template_item_id=item['source_template_item_id'],
        template_id=config['template_id'], lot_config_id=config['id'], qc_method=method,
        runtime_project_id=binding.get('runtime_project_id'), runtime_batch_id=binding.get('runtime_batch_id'),
        test_item_id=item['test_item_id'], test_item_name=item['test_item_name'], project_name=config['template_name'],
        instrument_name=config['instrument_name'], unit_id=item['unit_id'], unit_symbol=item['unit_symbol'],
        method_name=item['method_name'], input_value_type=item['input_value_type'], sort_order=item['sort_order'],
        target_n=item['target_n'], level_count=item['level_count'], levels=levels, phase='formal' if profile else 'building',
        usage_state=state, target_profile=profile, quality_goal=_decode(source.get('quality_goal_json')),
        quality_review=_decode(source.get('quality_review_json')), source_snapshot=source,
        revision=revision, config_revision=config['revision_no'], template_revision=config['template_revision'],
        **reagent, writable=not issues, issues=issues)


def get_daily_context(lab_instrument_id, qc_material_id, qc_material_lot_id, test_time,
                      template_id=None, lot_config_id=None):
    instrument = _positive(lab_instrument_id, '仪器')
    material = _positive(qc_material_id, '质控品')
    lot = _positive(qc_material_lot_id, '实际批号')
    when = timestamp(test_time)
    template = _positive(template_id, '项目') if template_id is not None else None
    selected_config = _positive(lot_config_id, '批次组合') if lot_config_id is not None else None
    selection = dict(lab_instrument_id=instrument, qc_material_id=material, qc_material_lot_id=lot,
                     template_id=template, lot_config_id=selected_config)
    with database.read_snapshot() as c:
        configs = _rows(c, '''SELECT DISTINCT x.*,t.template_name,t.revision_no AS template_revision,
            t.status AS template_status,t.is_disabled AS template_disabled,n.display_name AS instrument_name
            FROM qc_lot_configs x JOIN qc_project_templates t ON t.id=x.template_id
            JOIN lab_instruments n ON n.id=x.lab_instrument_id
            JOIN qc_lot_config_items i ON i.lot_config_id=x.id AND i.is_disabled=0 AND i.is_enabled=1
            JOIN qc_lot_config_item_levels a ON a.lot_config_item_id=i.id AND a.is_disabled=0
            JOIN md_qc_levels l ON l.id=a.qc_level_id JOIN md_qc_material_lots q ON q.id=l.qc_material_lot_id
            WHERE x.lab_instrument_id=? AND x.qc_material_id=? AND q.id=? AND q.qc_material_id=?
            AND (? IS NULL OR x.template_id=?) ORDER BY x.template_id,x.id''', (instrument, material, lot, material, template, template))
        combinations = []
        for config in configs:
            members = _rows(c, 'SELECT id FROM qc_lot_config_items WHERE lot_config_id=? AND is_enabled=1 AND is_disabled=0 ORDER BY sort_order,id', (config['id'],))
            combinations.append(dict(lot_config_id=config['id'], template_id=config['template_id'],
                name=config['config_name'], template_name=config['template_name'], status=config['status'],
                is_disabled=bool(config['is_disabled']), levels=[r for m in members for r in _levels(c, m['id'])]))
        requires = len(configs) > 1 and selected_config is None
        chosen = next((r for r in configs if r['id'] == selected_config), None) if selected_config else (configs[0] if len(configs) == 1 else None)
        errors = []
        if requires:
            errors.append(issue('此批号对应多个完整组合，请明确选择一个批次组合。', field='lot_config_id'))
        elif chosen is None:
            errors.append(issue('未找到属于所选仪器、产品和实际批号的项目组合，请核对设置。'))
        items = []
        if chosen:
            selection['lot_config_id'] = chosen['id']
            configured = _rows(c, '''SELECT i.*,t.chinese_name AS test_item_name,u.symbol AS unit_symbol,m.method_name
                FROM qc_lot_config_items i JOIN md_test_items t ON t.id=i.test_item_id
                LEFT JOIN md_units u ON u.id=i.unit_id LEFT JOIN md_methods m ON m.id=i.method_id
                WHERE i.lot_config_id=? AND i.is_disabled=0 AND i.is_enabled=1
                AND EXISTS(SELECT 1 FROM qc_lot_config_item_levels a JOIN md_qc_levels l ON l.id=a.qc_level_id
                    WHERE a.lot_config_item_id=i.id AND a.is_disabled=0 AND l.qc_material_lot_id=?)
                ORDER BY i.sort_order,i.id''', (chosen['id'], lot))
            items = [_item(c, item, chosen, when) for item in configured]
        revision = _digest(dict(selection=selection, test_time=when,
            combinations=[(r['id'], r['revision_no'], r['template_revision'], r['status'], r['is_disabled']) for r in configs],
            items=[(r['row_key'], r['revision']) for r in items]))
        return dict(selection=selection, test_time=when, context_revision=revision, requires_combination=requires,
                    combinations=combinations, items=items, issues=errors)
