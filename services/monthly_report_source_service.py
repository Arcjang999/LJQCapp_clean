"""Read-only source references and version-separated use of existing CV rules."""
from hashlib import sha256
import json
import math

import pandas as pd

from database import get_connection


def json_safe(value):
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if hasattr(value, 'item'):
        return json_safe(value.item())
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return str(value)


def encode(value):
    return json.dumps(json_safe(value), ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return sha256(encode(value).encode()).hexdigest()


def monthly_source_version(method, batch_id, month):
    from services.report_period_service import resolve_report_period
    period = resolve_report_period(month)
    result = range_source_version(method, batch_id, period.start_date, period.end_date)
    return dict(result, report_month=period.report_month)


def range_source_version(method, batch_id, start_date, end_date, *, handling_versions=None):
    from services.report_period_service import resolve_report_period
    period = resolve_report_period(start_date=start_date, end_date=end_date)
    if method not in ('lj', 'zscore'):
        raise ValueError('此质控方法不支持常规月报。')
    from database import get_batch, get_zscore_batch
    from services.settings_service import get_report_settings_with_fallbacks
    from services.lot_lifecycle_service import source_context
    field, source_type = ('lj_result_id', 'lj_result') if method == 'lj' else ('zscore_run_id', 'zscore_run')
    table = 'results' if method == 'lj' else 'zscore_runs'
    def rows(connection, sql, args=()):
        return [dict(r) for r in connection.execute(sql, args)]
    with get_connection() as c:
        batch = dict((get_batch if method == 'lj' else get_zscore_batch)(batch_id))
        results = rows(c, f'SELECT * FROM {table} WHERE batch_id=? ORDER BY id', (batch_id,))
        contexts = rows(c, f'''SELECT x.* FROM qc_result_contexts x JOIN {table} r ON r.id=x.{field}
            WHERE r.batch_id=? ORDER BY x.id''', (batch_id,))
        evaluations = rows(c, f'''SELECT e.* FROM qc_result_evaluations e JOIN qc_result_contexts x ON x.id=e.context_id
            JOIN {table} r ON r.id=x.{field} WHERE r.batch_id=? AND e.id=(SELECT MAX(id) FROM qc_result_evaluations WHERE context_id=x.id)
            ORDER BY e.id''', (batch_id,))
        levels = rows(c, f'''SELECT l.* FROM qc_result_context_levels l JOIN qc_result_contexts x ON x.id=l.context_id
            JOIN {table} r ON r.id=x.{field} WHERE r.batch_id=? ORDER BY l.id''', (batch_id,))
        profiles = rows(c, 'SELECT * FROM qc_target_profiles WHERE qc_method=? AND batch_id=? ORDER BY id', (method, batch_id))
        events = rows(c, f'''SELECT e.*,v.content_json,v.status FROM qc_ooc_events e JOIN {table} r ON r.id=e.source_id
            JOIN qc_ooc_revisions v ON v.event_id=e.id AND v.revision_no=e.current_revision_no
            WHERE e.source_type=? AND r.batch_id=? ORDER BY e.id''', (source_type, batch_id))
        from services.report_period_service import filter_frame_to_period
        if method == 'lj':
            from database import get_results
            from qc_logic import calculate_qc_results, LJ_FORMAL_PHASE_LABEL
            evaluated, _ = calculate_qc_results(get_results(batch_id), int(batch['target_n']))
            selected = filter_frame_to_period(evaluated[evaluated.phase == LJ_FORMAL_PHASE_LABEL], period) if not evaluated.empty else evaluated
        else:
            selected = filter_frame_to_period(pd.DataFrame([row for row in results if row['phase'] == 'formal_qc']), period)
        selected_ids = set(selected['id'].tolist()) if not selected.empty else set()
        relevant = {str(row['id']): int(row['current_revision_no']) for row in events if row['source_id'] in selected_ids}
        if handling_versions is None:
            handling_versions = relevant
        elif not isinstance(handling_versions, dict):
            raise ValueError('处理版本选择无效，请重新核对。')
        else:
            try:
                normalized = {str(int(key)): int(value) for key, value in handling_versions.items()}
            except (ValueError, TypeError):
                raise ValueError('处理版本选择无效，请重新核对。') from None
            if set(normalized) != set(relevant) or any(value < 1 for value in normalized.values()):
                raise ValueError('处理清单或所选版本已变化，请重新核对报告。')
            handling_versions = normalized
        event_reports = rows(c, f'''SELECT p.id,p.event_id,p.revision_no,p.report_no,p.generated_at FROM qc_event_reports p
            JOIN qc_ooc_events e ON e.id=p.event_id JOIN {table} r ON r.id=e.source_id
            WHERE e.source_type=? AND r.batch_id=? ORDER BY p.id''', (source_type, batch_id))
        binding = rows(c, 'SELECT * FROM qc_workbench_bindings WHERE qc_method=? AND runtime_batch_id=? ORDER BY id', (method, batch_id))
        source_identity, _, _ = source_context(c, method, batch_id, read_only=True)
        usage_events = rows(c, 'SELECT * FROM qc_lot_change_events WHERE system_id=? AND effective_at>=? AND effective_at<? ORDER BY effective_at,id',
                            (source_identity.get('system_id'), str(period.start_time), str(period.end_exclusive)))
        revisions = rows(c, f'''SELECT v.* FROM qc_ooc_revisions v JOIN qc_ooc_events e ON e.id=v.event_id
            JOIN {table} r ON r.id=e.source_id WHERE e.source_type=? AND r.batch_id=? ORDER BY v.event_id,v.revision_no''', (source_type, batch_id))
        raw = dict(source_identity=source_identity, usage_events=usage_events, method=method, batch=batch,
            period=period.to_payload(), handling_versions=handling_versions, results=results, contexts=contexts,
            context_levels=levels, evaluations=evaluations, profiles=profiles, events=events,
            event_reports=event_reports, event_revisions=revisions, binding=binding, settings=get_report_settings_with_fallbacks().to_payload())
        if method == 'zscore':
            raw['level_results'] = rows(c, '''SELECT l.* FROM zscore_level_results l JOIN zscore_runs r ON r.id=l.run_id
                WHERE r.batch_id=? ORDER BY l.id''', (batch_id,))
        evaluation_by_context = {row['context_id']: row['id'] for row in evaluations}
        return dict(format_version=2, qc_method=method, batch_id=int(batch_id), report_month='',
            period_start=period.start_date, period_end=period.end_date, period_key=period.key,
            handling_versions=json_safe(handling_versions),
            fingerprint=digest(raw), result_ids=[r['id'] for r in results],
            contexts=[dict(context_id=r['id'], source_id=r[field], target_profile_id=r['target_profile_id'],
                           evaluation_id=evaluation_by_context.get(r['id'])) for r in contexts],
            parameter_versions=[dict(profile_id=r['id'], version_no=r['version_no']) for r in profiles],
            event_versions=[dict(event_id=r['id'], revision_no=r['current_revision_no']) for r in events],
            usage_event_ids=[row['id'] for row in usage_events], config_snapshot_id=batch.get('config_snapshot_id'))


def versioned_quality_summary(method, batch_id, month, formal_records, *, existing):
    """Select version/level inputs, then use the existing CV calculator/evaluator."""
    from services.cv_service import calculate_cv_percent
    from services.quality_target_service import evaluate_cv, requirement_text
    goal = existing.get('goal') or {}
    if not goal:
        return dict(existing, evaluation_reason='未采用可自动评价的 CV 要求；已登记的依据仅供查阅。') if existing else {}
    samples, profiles = {}, set()
    for record in formal_records:
        profile = record.get('target_profile_id')
        profile = None if profile is None or pd.isna(profile) else int(profile)
        profiles.add(profile)
        if method == 'lj':
            levels = [(1, record.get('value'), record.get('status') == '符合质控')]
        else:
            levels = [(int(level['level_id'].split()[-1]), level.get('raw_value'),
                       record.get('run_status') in ('accept', 'normal') and bool(level.get('is_in_control_for_realtime_stats')))
                      for level in record.get('level_results', [])]
        for order, value, accepted in levels:
            if accepted and value is not None and math.isfinite(float(value)):
                samples.setdefault((profile, order), []).append((float(value), str(record['test_time'])[:10]))
    with get_connection() as c:
        versions = {r['id']: r['version_no'] for r in c.execute(
            'SELECT id,version_no FROM qc_target_profiles WHERE qc_method=? AND batch_id=?', (method, batch_id))}
    rows = []
    for profile in sorted(profiles, key=lambda x: -1 if x is None else x):
        label = str(versions.get(profile, '未记录'))
        for entry in goal.get('levels', []):
            order = int(entry['level_order'])
            values = samples.get((profile, order), [])
            data = pd.Series([v[0] for v in values], dtype=float)
            days = len({v[1] for v in values})
            cv = calculate_cv_percent(data.mean(), data.std(ddof=1)) if len(data) > 1 else None
            decision = evaluate_cv(goal, order, cv, count=len(data), days=days)
            rows.append(dict(level=f'水平 {order} / 参数版本 {label}', level_order=order, profile_id=profile,
                parameter_version=label, requirement=requirement_text(entry['rule']), category=entry['category'],
                concentration=entry['concentration'], count=len(data), days=days, cv=cv, decision=decision))
    return json_safe(dict(existing, rows=rows,
        statistics_scope='分批次、参数版本及水平；仅正式期在控结果，多水平法按整次在控筛选；标准SD上限、偏倚和总误差不自动比较，质控图仍按所选规则判读'))


def monthly_processing_candidates(method, formal_records, handling_summaries):
    """Freeze unregistered rejected records together with exact event revisions."""
    source_type = 'lj_result' if method == 'lj' else 'zscore_run'
    candidates = {row['source_id']: dict(row) for row in handling_summaries}
    for record in formal_records:
        source_id = int(record['id'] if method == 'lj' else (record.get('run_id') or record['id']))
        rejected = record.get('status') == '失控' if method == 'lj' else record.get('run_status') == 'reject'
        if rejected and source_id not in candidates:
            candidates[source_id] = dict(source_type=source_type, source_id=source_id, event_id=None,
                revision_no=None, test_time=str(record['test_time']), status='pending',
                original_classification='reject', reports=[])
    return list(candidates.values())
