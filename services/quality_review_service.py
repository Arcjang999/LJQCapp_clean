"""Explicit source review is a configuration gate, separate from QC calculations.

Name matching only supplies candidates. A person must adopt a source or document
why each candidate does not apply; Ct/log never inherit concentration CV rules.
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
    from services.quality_target_service import suggested_requirements
    return [row for row in suggested_requirements(item['test_item_name']) if row['origin'] == 'builtin']


def _identity(item):
    return {key: item.get(key) for key in (
        'test_item_id', 'method_id', 'unit_id', 'input_value_type', 'level_count',
        'cv_limit', 'quality_target_source_text',
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
                 exclusions=None, recorded=None):
    """Build a frozen review after validating all current candidate dispositions."""
    from services.quality_target_service import source_label
    confirmed_by = _text(confirmed_by, '确认人', 80)
    evidence = _text(evidence, '适用依据')
    exclusions = dict(exclusions or {})
    candidates = standard_candidates(item)
    selected_standard = source_spec if source_spec and source_spec['origin'] == 'builtin' else None
    if selected_standard and selected_standard['id'] not in {row['id'] for row in candidates}:
        candidates.append(selected_standard)
    reviewed = []
    for candidate in candidates:
        adopted = bool(selected_standard and candidate['id'] == selected_standard['id'])
        reason = evidence if adopted else _text(
            exclusions.get(candidate['id']), f"标准“{candidate['name']} / {candidate['standard']}”的不适用原因")
        reviewed.append(dict(id=candidate['id'], fingerprint=_digest(candidate),
                             source=source_label(candidate),
                             disposition='adopted' if adopted else 'not_applicable', reason=reason))
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
    return dict(version=1, status='confirmed', identity=_identity(item),
                goal_fingerprint=_digest(_json(item.get('quality_goal_json'))),
                confirmed_by=confirmed_by, evidence=evidence,
                reviewed_at=datetime.now().isoformat(timespec='seconds'),
                decision='standard' if selected_standard else ('custom' if source_spec else 'record_only'),
                selected_source_id=source_spec['id'] if source_spec else None,
                selected_source_fingerprint=_digest(source_spec) if source_spec else None,
                candidates=reviewed, recorded=recorded or {}, levels=levels)


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
                              requirement_text, confirmed_by, evidence, exclusions=None):
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
        # Keep legacy CV untouched; a text-only requirement must not invent a CV.
        table = 'qc_project_template_items' if scope == 'project' else 'qc_lot_config_items'
        connection.execute(f"UPDATE {table} SET quality_goal_json='{{}}' WHERE id=?", (item_id,))
        item = item_context(scope, item_id, connection)
        review = build_review(connection, item, confirmed_by=confirmed_by, evidence=evidence,
                              exclusions=exclusions, recorded=recorded)
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
        if review.get('version') != 1 or review.get('identity') != _identity(item):
            return ['检验项目、方法学、单位或质量目标已修改，请重新确认质量目标。']
        if review.get('goal_fingerprint') != _digest(_json(item.get('quality_goal_json'))):
            return ['质量目标已修改，请重新确认。']
        if not review.get('confirmed_by') or not review.get('evidence'):
            return ['请填写质量目标确认人和适用依据。']
        if scope == 'lot' and review.get('levels') != _levels(connection, item):
            return ['质控品已更换，请重新核对各水平的质量目标。']
        reviewed = {row['id']: row for row in review.get('candidates', [])}
        for candidate in standard_candidates(item):
            prior = reviewed.get(candidate['id'])
            if not prior or prior.get('fingerprint') != _digest(candidate):
                return ['该检验项目的标准已更新，请重新确认所选标准是否适用。']
            if prior.get('disposition') not in ('adopted', 'not_applicable') or not prior.get('reason'):
                return ['请确认列出的标准是否适用；不适用时请填写原因。']
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
            if any(row.get('disposition') == 'adopted' for row in reviewed.values()):
                return ['已有适用标准，请按该标准设置质量目标。']
        else:
            return ['请先确认质量目标。']
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
