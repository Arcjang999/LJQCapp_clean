"""A short setup path using the existing applicability and immutable goals."""
from __future__ import annotations

from services.time_service import local_today

import hashlib
import json
import re
from datetime import date

from database import atomic_write
from services.quality_applicability_service import assess, context_errors, catalog_index
from services.quality_target_service import finite_number


SD_REQUIREMENT = ('按本批次确认的均值和标准差，使用所选质控方法的现有规则判断结果；'
                  'L-J及多水平按SD位置和连续规则判读，即时法使用格拉布斯检验。'
                  'SD控制不代表长期精密度达标。')


def catalog_fingerprint():
    """A local catalog identity; does not claim that all published standards exist here."""
    from services.quality_target_service import list_catalog
    data = dict(index=catalog_index(), numeric=sorted(
        [r for r in list_catalog() if r['origin'] == 'builtin'], key=lambda r: r['id']))
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def quality_setup_plan(item, context):
    rows = assess(item, context)
    problems = context_errors(item, context)
    pending = [r for r in rows if r['kind'] != 'process' and r['status'] == 'pending']
    numeric = [r for r in rows if r['kind'] == 'numeric' and r['status'] == 'applicable']
    cv_supported = (item.get('input_value_type') == 'raw' and context.get('result_scale') == 'concentration'
                    and context.get('result_kind') == 'quantitative')
    state, spec, reason = 'sd_cv', None, '软件已收录的标准中，尚未找到适合本项目的数值限值。按所选质控方法判断结果，有实验室CV要求时可另填上限。'
    if problems or pending:
        state = 'blocked'
        reason = ' '.join(problems + [r['reason'] for r in pending])
    elif len(numeric) > 1:
        state, reason = 'blocked', '匹配到多个数值标准，请先核对单位和适用条件。'
    elif numeric:
        state, spec, reason = 'standard', numeric[0]['spec'], '直接引用已匹配的现行数值标准。'
    return dict(state=state, numeric_spec=spec, reason=reason, cv_supported=cv_supported, assessments=rows)


def local_catalog_review(item, context):
    plan = quality_setup_plan(item, context)
    if plan['state'] != 'sd_cv':
        raise ValueError(plan['reason'] if plan['state'] == 'blocked' else '已有明确适用数值标准，请直接引用。')
    return dict(scope='local_catalog', query=str(item.get('test_item_name') or ''),
                checked_on=local_today().isoformat(), conclusion='no_numeric',
                rationale='已核对当前软件收录的项目、方法、尺度和单位；未匹配适用数值限值。此记录不表示已查询全部国家标准。',
                catalog_fingerprint=catalog_fingerprint())


def simple_quality_payload(item, condition_args, *, cv_limit=None, confirmed_by='', evidence='',
                           levels=None, requirement_text=None, source_name=None, source_version=None):
    context = dict(condition_args.get('context') or {})
    plan = quality_setup_plan(item, context)
    if plan['state'] != 'sd_cv':
        raise ValueError(plan['reason'] if plan['state'] == 'blocked' else '已有明确适用数值标准，请直接引用。')
    limit = finite_number(cv_limit, '实验室CV上限', optional=True)
    if limit is not None:
        if limit <= 0:
            raise ValueError('实验室CV上限须大于0。')
        if not plan['cv_supported']:
            raise ValueError('这种结果类型暂不能自动比较CV上限。请按所选质控方法判断结果，并参考说明书或实验室要求。')
    defaults = dict(context=context,
        search_record=condition_args.get('search_record') or local_catalog_review(item, context),
        adopted_standard_ids=condition_args.get('adopted_standard_ids'),
        registered_standards=condition_args.get('registered_standards'),
        process_requirements=condition_args.get('process_requirements'))
    text = SD_REQUIREMENT if requirement_text is None else str(requirement_text).strip()
    if not text:
        raise ValueError('请保留SD控制说明或填写本实验室的实际要求。')
    text = re.sub(r'\s*实验室自定义CV上限为≤[^%]+%，另行比较正式期在控结果的精密度，不替代SD失控判读。', '', text).strip() or SD_REQUIREMENT
    if limit is not None:
        cv_text = f'实验室自定义CV上限为≤{limit:g}%，另行比较正式期在控结果的精密度，不替代SD失控判读。'
        if cv_text not in text:
            text += ' ' + cv_text
    return dict(defaults, mode='sd_cv', cv_limit=limit, confirmed_by=confirmed_by,
        evidence=str(evidence).strip() or (
            f"已核对{item.get('test_item_name', '')}、{item.get('method_name', '')}、"
            f"{item.get('unit_symbol', '')}及所选水平；采用本实验室SD控制" +
            (f'和CV≤{limit:g}%。' if limit is not None else '，未设置CV上限。')),
        source_name=str(source_name or '本实验室SD质控方案'),
        source_version=str(source_version or local_today().isoformat()),
        requirement_text=text, levels=list(levels or []))


def simple_cv_spec(item, payload):
    """Build a private laboratory limit without writing during preview."""
    limit = payload['cv_limit']
    if limit is None:
        return None
    spec = dict(origin='custom', name=item['test_item_name'], aliases=[],
        standard='实验室自定义CV', version=payload['source_version'], effective_date=payload['source_version'],
        setup_mode='sd_cv', laboratory_basis={key: payload[key] for key in
            ('source_name', 'source_version', 'requirement_text')},
        measurement_unit=item['unit_symbol'], scope='实验室自定义室内质量要求',
        imprecision=[dict(kind='cv', value=limit, operator='<=', unit='%')],
        imprecision_text=f'CV ≤{limit:g}%', bias_text='', tea_text='', source_url='',
        source_clause=payload['requirement_text'], source_page=None,
        notes='实验室确认的CV上限；非国家数值标准，SD控制规则另行使用。')
    # Version labels may be a laboratory code on reopening; the effective date
    # remains the date of the actual setup, never a fabricated SOP version.
    try:
        date.fromisoformat(spec['effective_date'])
    except (TypeError, ValueError):
        spec['effective_date'] = local_today().isoformat()
    spec['id'] = 'custom-simple-' + hashlib.sha256(
        json.dumps(spec, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]
    return spec


def save_simple_quality(scope, item_id, *, cv_limit=None, confirmed_by, evidence='', context,
                        search_record=None, adopted_standard_ids=None, registered_standards=None,
                        process_requirements=None, levels=None, requirement_text=None,
                        source_name=None, source_version=None):
    from services.quality_target_service import item_context, adopt_requirement, _require_draft
    from services.quality_review_service import save_recorded_requirement
    with atomic_write() as connection:
        item = item_context(scope, item_id, connection)
        if scope == 'lot':
            _require_draft(connection, item)
        payload = simple_quality_payload(item, dict(context=context, search_record=search_record,
            adopted_standard_ids=adopted_standard_ids, registered_standards=registered_standards,
            process_requirements=process_requirements), cv_limit=cv_limit, confirmed_by=confirmed_by,
            evidence=evidence, levels=levels, requirement_text=requirement_text,
            source_name=source_name, source_version=source_version)
        args = {key: payload[key] for key in ('confirmed_by', 'evidence', 'context', 'search_record',
            'adopted_standard_ids', 'registered_standards', 'process_requirements')}
        spec = simple_cv_spec(item, payload)
        if spec is not None:
            connection.execute('INSERT OR IGNORE INTO qc_quality_catalog(id,origin,payload_json) VALUES(?,?,?)',
                (spec['id'], 'custom', json.dumps(spec, ensure_ascii=False, allow_nan=False)))
            proposed = payload['levels']
            if scope == 'lot' and not proposed:
                proposed = [dict(level_order=row[0], concentration=None, category='') for row in connection.execute(
                    'SELECT level_order FROM qc_lot_config_item_levels WHERE lot_config_item_id=? AND is_disabled=0 ORDER BY level_order', (item_id,))]
            return adopt_requirement(scope, item_id, spec['id'], levels=proposed, **args)
        table = 'qc_project_template_items' if scope == 'project' else 'qc_lot_config_items'
        connection.execute(f"UPDATE {table} SET cv_limit=NULL,quality_target_source_text='' WHERE id=?", (item_id,))
        return save_recorded_requirement(scope, item_id, **args,
            setup_mode='sd_cv',
            **{key: payload[key] for key in ('source_name', 'source_version', 'requirement_text')})
