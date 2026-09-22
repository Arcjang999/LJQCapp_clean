"""Explicit source review is a configuration gate, separate from QC calculations.

Name matching supplies candidates; structured conditions determine adoption.
Frozen legacy lots keep their original sources and calculations.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime

from database import atomic_write, get_connection


def _json(value):
    return json.loads(value or '{}') if isinstance(value, str) else dict(value or {})


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _text(value, label, limit=2000):
    value = str(value or '').strip()
    if not value:
        raise ValueError(f'{label}不能为空。')
    if len(value) > limit:
        raise ValueError(f'{label}不能超过 {limit} 字。')
    return value


def standard_candidates(item):
    from services.quality_applicability_service import numeric_candidates
    return numeric_candidates(item)


def _identity(item):
    return {key: item.get(key) for key in (
        'test_item_id', 'method_id', 'unit_id', 'input_value_type', 'level_count',
        'cv_limit', 'quality_target_source_text',
        'method_name', 'method_code', 'unit_symbol', 'test_item_name', 'reagent_id',
    )}


def _levels(connection, item):
    if 'lot_config_id' not in item:
        return []
    return [dict(row) for row in connection.execute('''SELECT level_order,qc_level_id
        FROM qc_lot_config_item_levels WHERE lot_config_item_id=? AND is_disabled=0
        ORDER BY level_order''', (item['id'],))]


def is_frozen_lot(connection, item):
    config = connection.execute('SELECT status,activated_at FROM qc_lot_configs WHERE id=?',
                                (item['lot_config_id'],)).fetchone()
    return bool(config and (config['status'] != 'draft' or config['activated_at'])) or bool(
        connection.execute('SELECT 1 FROM qc_workbench_bindings WHERE lot_config_item_id=?',
                           (item['id'],)).fetchone())


def build_review(connection, item, *, source_spec=None, confirmed_by, evidence,
                 exclusions=None, recorded=None, context=None, search_record=None,
                 adopted_standard_ids=None, registered_standards=None):
    """Build one review for every entry point; free-text exclusions cannot waive it."""
    from services.quality_target_service import source_label
    from services.quality_applicability_service import (assess, context_errors,
        validate_search_record, validate_registered_sources)
    confirmed_by = _text(confirmed_by, '确认人', 80)
    evidence = _text(evidence, '适用依据')
    context = dict(context or {})
    problems = context_errors(item, context)
    if problems:
        raise ValueError('适用条件待补充：' + ' '.join(problems))
    candidates = assess(item, context)
    pending = [r for r in candidates if r['status'] == 'pending']
    if pending:
        raise ValueError('标准适用情况待核查：' + '；'.join(r['reason'] for r in pending))
    selected_standard = source_spec if source_spec and source_spec['origin'] == 'builtin' else None
    numerical = [r for r in candidates if r['kind'] == 'numeric' and r['status'] == 'applicable']
    if numerical and (not selected_standard or any(r['id'] != selected_standard['id'] for r in numerical)):
        raise ValueError('已有明确适用标准，必须采用，不能用排除理由或实验室自定要求替代；多个数值依据须先核对冲突及适用条件。')
    if selected_standard and selected_standard['id'] not in {r['id'] for r in numerical}:
        raise ValueError('所选标准与当前检验项目或适用条件不符，不能采用。')
    process = [r for r in candidates if r['kind'] != 'numeric' and r['status'] == 'applicable']
    selected_process = {r['id'] for r in process} if adopted_standard_ids is None else set(adopted_standard_ids)
    if selected_process != {r['id'] for r in process}:
        raise ValueError('请采用全部适用的过程、对照或定性要求，不能遗漏或关联不适用条款。')
    registered = validate_registered_sources(registered_standards, context)
    search = {}
    if not numerical:
        search = validate_search_record(search_record, confirmed_by=confirmed_by, has_process=bool(process))
        if search['conclusion'] == 'registered' and not registered:
            raise ValueError('已找到未收录标准时，请补充标准全文及核查记录后再确认。')
        if registered and search['conclusion'] != 'registered':
            raise ValueError('补充了适用标准，请将查找结论设置为已登记补充标准。')
        if source_spec and any(r['kind'] == 'numeric_record' for r in registered):
            raise ValueError('补充数值要求须填写完整条款并人工核对，不能仅用自定 CV 替代。')
    cv_evaluation = any(level.get('rule', {}).get('kind') == 'cv'
                        for level in _json(item.get('quality_goal_json')).get('levels', []))
    reviewed = []
    for candidate in candidates:
        adopted = candidate['status'] == 'applicable'
        reviewed.append(dict(id=candidate['id'], fingerprint=_digest(candidate),
                             source=(source_label(candidate['spec']) if candidate['kind'] == 'numeric' else
                                     candidate['source']['standard'] + '；' + candidate['clause'] + '；' + candidate['spec']['name']),
                             kind=candidate['kind'], standard=candidate['source'],
                             clause=candidate['clause'], pages=candidate['pages'],
                             requirements=candidate['spec'].get('requirements', []),
                             automatic_evaluation=adopted and candidate['kind'] == 'numeric' and cv_evaluation,
                             disposition='adopted' if adopted else 'not_applicable',
                             reason=candidate['reason'], evidence=evidence))
    levels = _levels(connection, item)
    if 'lot_config_id' in item and len(levels) != int(item['level_count']):
        raise ValueError('请先保存全部质控水平，再确认质量目标。')
    if recorded:
        recorded = {key: _text(recorded.get(key), label) for key, label in (
            ('source_name', '依据名称'), ('source_version', '依据版本或编号'),
            ('requirement_text', '实验室自定要求'),
        )}
    if not source_spec and not recorded:
        raise ValueError('请选择分析质量要求，或填写实验室自定要求及依据。')
    return dict(version=2, status='confirmed', identity=_identity(item), context=context,
                goal_fingerprint=_digest(_json(item.get('quality_goal_json'))),
                confirmed_by=confirmed_by, evidence=evidence,
                reviewed_at=datetime.now().isoformat(timespec='seconds'),
                decision='standard' if selected_standard else ('custom' if source_spec else 'record_only'),
                selected_source_id=source_spec['id'] if source_spec else None,
                selected_source_fingerprint=_digest(source_spec) if source_spec else None,
                candidates=reviewed, recorded=recorded or {}, levels=levels,
                search_record=search, registered_standards=registered)


def save_pending_review(scope, item_id, *, context, search_record=None, registered_standards=None,
                        draft_recorded=None):
    """Save incomplete conditions explicitly as a draft, never an authorization."""
    from services.quality_target_service import item_context, _require_draft
    with atomic_write() as connection:
        item = item_context(scope, item_id, connection)
        if scope == 'lot':
            _require_draft(connection, item)
        review = _json(item.get('quality_review_json'))
        review.update(version=2, status='pending', context=context,
                      search_record=search_record or {}, registered_standards=registered_standards or [])
        if draft_recorded is not None:
            review['draft_recorded'] = {k: str(draft_recorded.get(k) or '')[:2000]
                for k in ('source_name', 'source_version', 'requirement_text')}
        save_review(connection, scope, item_id, review)
        if scope == 'project':
            connection.execute("UPDATE qc_project_templates SET status='draft', revision_no=revision_no+1 WHERE id=?", (item['template_id'],))
        else:
            from services.project_config_service import _save_snapshot
            connection.execute('UPDATE qc_lot_configs SET revision_no=revision_no+1 WHERE id=?', (item['lot_config_id'],))
            _save_snapshot(connection, item['lot_config_id'], action_type='edit', change_summary='保存待补充的标准适用条件')
    return review


def save_review(connection, scope, item_id, review):
    table = 'qc_project_template_items' if scope == 'project' else 'qc_lot_config_items'
    connection.execute(f'UPDATE {table} SET quality_review_json=? WHERE id=?',
                       (json.dumps(review, ensure_ascii=False, allow_nan=False), item_id))


def pending_review_copy(value):
    review = _json(value)
    if review:
        review.update(status='pending', levels=[])
    return json.dumps(review, ensure_ascii=False, allow_nan=False)


def save_recorded_requirement(scope, item_id, *, source_name, source_version,
                              requirement_text, confirmed_by, evidence, exclusions=None,
                              context=None, search_record=None, adopted_standard_ids=None,
                              registered_standards=None):
    """Record unsupported/no-applicable-source requirements; adds no new evaluator."""
    from services.quality_target_service import item_context, _require_draft
    if scope not in ('project', 'lot'):
        raise ValueError('无法确定要设置质量目标的项目或批次，请重新打开设置。')
    with atomic_write() as connection:
        item = item_context(scope, item_id, connection)
        if scope == 'lot':
            _require_draft(connection, item)
        recorded = dict(source_name=source_name, source_version=source_version,
                        requirement_text=requirement_text)
        # Preserve concentration CV compatibility; other scales cannot inherit it.
        table = 'qc_project_template_items' if scope == 'project' else 'qc_lot_config_items'
        connection.execute(f"UPDATE {table} SET quality_goal_json='{{}}' WHERE id=?", (item_id,))
        if (context or {}).get('result_scale') != 'concentration':
            connection.execute(f'UPDATE {table} SET cv_limit=NULL WHERE id=?', (item_id,))
        item = item_context(scope, item_id, connection)
        review = build_review(connection, item, confirmed_by=confirmed_by, evidence=evidence,
                              exclusions=exclusions, recorded=recorded, context=context,
                              search_record=search_record, adopted_standard_ids=adopted_standard_ids,
                              registered_standards=registered_standards)
        save_review(connection, scope, item_id, review)
        if scope == 'lot':
            from services.project_config_service import _save_snapshot
            connection.execute('''UPDATE qc_lot_configs SET revision_no=revision_no+1,
                updated_at=CURRENT_TIMESTAMP WHERE id=?''', (item['lot_config_id'],))
            _save_snapshot(connection, item['lot_config_id'], action_type='edit',
                           change_summary='确认实验室质量要求、来源及标准适用性核对')
        else:
            connection.execute("""UPDATE qc_project_templates SET status='draft',
                revision_no=revision_no+1,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (item['template_id'],))
    return review


def validate_quality_review(scope, item_id, connection=None):
    """Gate every new/edited configuration; old frozen lots remain operational."""
    from services.quality_target_service import item_context, get_requirement

    def check(connection):
        item = item_context(scope, item_id, connection)
        if scope == 'lot' and is_frozen_lot(connection, item):
            return []
        review = _json(item.get('quality_review_json'))
        if scope == 'project' and not review:
            template = connection.execute('SELECT status FROM qc_project_templates WHERE id=?',
                                          (item['template_id'],)).fetchone()
            # An unchanged pre-migration active template remains usable. Any
            # supported edit resets it to draft and requires a fresh review.
            if template and template['status'] == 'active':
                return []
        if not review or review.get('status') != 'confirmed':
            return ['质量目标待确认：请选择适用标准；无适用标准时，请填写实验室自定要求及依据。']
        if review.get('version') == 1 and scope == 'project':
            template = connection.execute('SELECT status FROM qc_project_templates WHERE id=?', (item['template_id'],)).fetchone()
            old_identity = {k: item.get(k) for k in review.get('identity', {})}
            if template and template['status'] == 'active' and review.get('identity') == old_identity:
                return []
        if review.get('version') != 2 or review.get('identity') != _identity(item):
            return ['检验项目、方法学、单位或质量目标已修改，请重新确认质量目标。']
        if review.get('goal_fingerprint') != _digest(_json(item.get('quality_goal_json'))):
            return ['质量目标已修改，请重新确认。']
        if not review.get('confirmed_by') or not review.get('evidence'):
            return ['请填写质量目标确认人和适用依据。']
        if scope == 'lot' and review.get('levels') != _levels(connection, item):
            return ['质控品已更换，请重新核对各水平的质量目标。']
        reviewed = {row['id']: row for row in review.get('candidates', [])}
        decision = review.get('decision')
        goal = _json(item.get('quality_goal_json'))
        if decision in ('standard', 'custom'):
            if not goal or goal.get('spec', {}).get('id') != review.get('selected_source_id'):
                return ['质量目标不能为空，请重新选择并确认。']
            try:
                current = get_requirement(review['selected_source_id'])
            except ValueError:
                return ['未找到原来选用的分析质量要求，请重新选择并确认。']
            if _digest(current) != review.get('selected_source_fingerprint'):
                return ['选用的分析质量要求已更新，请重新确认。']
            if decision == 'standard' and current.get('origin') != 'builtin':
                return ['选用的标准信息已修改，请重新选择并确认。']
        elif decision == 'record_only':
            if not all(review.get('recorded', {}).get(key) for key in
                       ('source_name', 'source_version', 'requirement_text')):
                return ['请完整填写实验室自定要求及依据。']
            if any(row.get('disposition') == 'adopted' and row.get('kind') == 'numeric' for row in reviewed.values()):
                return ['已有适用标准，请按该标准设置质量目标。']
        else:
            return ['请先确认质量目标。']
        try:
            rebuilt = build_review(connection, item, source_spec=current if decision in ('standard', 'custom') else None,
                confirmed_by=review['confirmed_by'], evidence=review['evidence'],
                context=review.get('context'), search_record=review.get('search_record'),
                recorded=review.get('recorded'), registered_standards=review.get('registered_standards'),
                adopted_standard_ids=[r['id'] for r in review.get('candidates', [])
                    if r.get('kind') != 'numeric' and r.get('disposition') == 'adopted'])
        except (ValueError, KeyError, TypeError) as error:
            return ['质量目标待重新核查：' + str(error)]
        if rebuilt['candidates'] != review.get('candidates') or rebuilt['registered_standards'] != review.get('registered_standards'):
            return ['标准条款、适用条件或来源时效已变化，请重新确认。']
        if review.get('context', {}).get('result_scale') == 'qualitative':
            return ['已登记定性依据；当前工作台尚不支持阳性 / 阴性结果录入与判读，不能启用为数值质控。']
        return []

    if connection is not None:
        return check(connection)
    with get_connection() as connection:
        return check(connection)


def validate_project_quality(item_id):
    return validate_quality_review('project', item_id)


def runtime_review(method, batch_id):
    with get_connection() as connection:
        binding = connection.execute('''SELECT source_snapshot_json FROM qc_workbench_bindings
            WHERE qc_method=? AND runtime_batch_id=?''', (method, batch_id)).fetchone()
        if binding:
            return _json(_json(binding[0]).get('quality_review_json'))
        if method == 'lj':
            batch = connection.execute('SELECT source_config_snapshot_json FROM batches WHERE id=?',
                                       (batch_id,)).fetchone()
            if batch:
                return _json(_json(batch[0]).get('quality_review_json'))
    return {}
