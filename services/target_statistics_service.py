"""Read-only monthly/cumulative candidates with explicit, versioned activation."""
from __future__ import annotations

from datetime import datetime, timedelta
from hashlib import sha256
import json
import math
from statistics import mean, stdev

import database
from services.cv_service import calculate_cv_percent
from services.time_service import local_now


STRATEGIES = ('monthly', 'cumulative')
RULE = ('纳入有效建靶结果和正式期在控结果；多水平正式期按整次在控纳入。'
        '按同一检测系统、同一实际质控品及批号、同一水平统计；更换均值和SD版本后仍连续累计。SD使用样本标准差。')
_TABLES = {'lj': ('results', 'lj_result_id'), 'zscore': ('zscore_runs', 'zscore_run_id')}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _decode(value):
    return json.loads(value or '{}') if isinstance(value, str) else (value or {})


def _digest(value):
    return sha256(_json(value).encode()).hexdigest()


def _system(source):
    identity = source.get('identity') or []
    item = source.get('project_template_item_id')
    return [item, *[identity[index] for index in (0, 3, 4, 5, 6, 7)]] if item and len(identity) >= 8 else None


def _clock(value=None):
    import pandas as pd
    try:
        now = pd.Timestamp(local_now() if value is None else value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError('统计截止时间无效，请重新打开。') from None
    if pd.isna(now):
        raise ValueError('统计截止时间无效，请重新打开。')
    if now.tzinfo is not None:
        now = now.tz_convert('Asia/Shanghai').tz_localize(None)
    return now.to_pydatetime()


def _validate_activation_clock(candidate):
    cutoff, now = _clock(candidate.get('as_of')), _clock()
    if cutoff > now:
        raise ValueError('统计截止时间不能晚于当前北京时间，请重新计算。')
    if cutoff.strftime('%Y-%m') != now.strftime('%Y-%m'):
        raise ValueError('统计月份已变化，请重新计算。')
    if (candidate.get('strategy') == 'cumulative' and candidate.get('latest_eligible_at')
            and _clock(candidate['latest_eligible_at']) > cutoff):
        raise ValueError('已有更晚的有效结果，请按最新结果重新计算累计统计。')


def _window(strategy, as_of):
    if strategy not in STRATEGIES:
        raise ValueError('请选择上月统计或累计统计。')
    now = _clock(as_of)
    this_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if strategy == 'monthly':
        previous = (this_month - timedelta(days=1)).replace(day=1)
        return previous, this_month, False
    return None, now, True


def _eligible_lj(batch_id):
    from qc_logic import calculate_qc_results, LJ_BUILDING_PHASE_LABEL, LJ_FORMAL_PHASE_LABEL
    batch = database.get_batch(batch_id)
    evaluated, _ = calculate_qc_results(database.get_results(batch_id), int(batch['target_n']))
    result = {}
    for row in evaluated.to_dict('records'):
        included = (row.get('phase') == LJ_BUILDING_PHASE_LABEL and bool(row.get('is_building_included', 1)))
        included = included or (row.get('phase') == LJ_FORMAL_PHASE_LABEL and row.get('status') == '符合质控')
        result[int(row['id'])] = bool(included)
    return result


def _eligible_zscore(connection, batch_id):
    runs = [dict(row) for row in connection.execute('SELECT * FROM zscore_runs WHERE batch_id=? ORDER BY test_time,id', (batch_id,))]
    result = {}
    for row in runs:
        levels = [dict(level) for level in connection.execute('SELECT * FROM zscore_level_results WHERE run_id=? ORDER BY level_id', (row['id'],))]
        if row['phase'] == 'target_building':
            result[row['id']] = {level['level_id'] for level in levels if level['is_building_included']}
        elif (row['phase'] == 'formal_qc' and row['run_status'] == 'accept'
                and len(levels) == row['level_count']
                and all(level['is_in_control_for_realtime_stats'] and level['level_status'] == 'accept' for level in levels)):
            result[row['id']] = {level['level_id'] for level in levels}
        else:
            result[row['id']] = set()
    return result


def _initial_parameters_ready(context):
    if context['current']:
        return True
    batch_id = context['binding']['runtime_batch_id']
    if context['binding']['qc_method'] == 'lj':
        from qc_logic import calculate_qc_results
        batch = database.get_batch(batch_id)
        _, stats = calculate_qc_results(database.get_results(batch_id), int(batch['target_n']))
        return bool(stats.get('target_ready'))
    from zscore_logic import (resolve_zscore_batch_context, get_zscore_level_targets, should_enable_formal_rules)
    setup = resolve_zscore_batch_context(batch_id)
    profiles = get_zscore_level_targets(batch_id, setup['template_id'], required_n=setup['required_n'], at_time=local_now())
    return should_enable_formal_rules(profiles, setup['required_level_ids'])


def _candidate(connection, binding_id, strategy, as_of):
    from services.target_profile_edit_service import _context
    context = _context(connection, int(binding_id))
    binding, snapshot = context['binding'], context['snapshot']
    method, system = binding['qc_method'], _system(snapshot)
    if not system:
        raise ValueError('本批次缺少可追溯的检测系统，请核对项目设置。')
    start, end, inclusive = _window(strategy, as_of)
    table, column = _TABLES[method]
    current_ids = {int(level['qc_level_id']) for level in context['levels'] if level.get('qc_level_id')}
    samples = {identifier: [] for identifier in current_ids}
    watched, source_batches, eligible = [], {}, {}
    latest_eligible_at = None
    # Frozen per-result identity, rather than the latest batch header or Level N,
    # determines whether a physical material is part of this detection system.
    records = connection.execute(f'''SELECT r.*,x.id AS context_id,x.config_snapshot_json,
        x.target_profile_id,e.id AS evaluation_id,e.evaluation_json FROM {table} r
        JOIN qc_result_contexts x ON x.{column}=r.id
        LEFT JOIN qc_result_evaluations e ON e.id=(SELECT MAX(e2.id) FROM qc_result_evaluations e2 WHERE e2.context_id=x.id)
        WHERE json_extract(x.config_snapshot_json,'$.project_template_item_id')=? ORDER BY r.test_time,r.id''', (system[0],)).fetchall()
    for saved in records:
        row = dict(saved)
        source = _decode(row['config_snapshot_json'])
        if _system(source) != system:
            continue
        context_levels = [dict(level) for level in connection.execute(
            'SELECT * FROM qc_result_context_levels WHERE context_id=? ORDER BY level_order,id', (row['context_id'],))]
        if not any(level['qc_level_id'] in current_ids for level in context_levels):
            continue
        raw_levels = ([dict(level) for level in connection.execute(
            'SELECT * FROM zscore_level_results WHERE run_id=? ORDER BY level_id,id', (row['id'],))]
            if method == 'zscore' else [])
        watched.append(dict(result=row, material_levels=context_levels, values=raw_levels))
        batch_id = int(row['batch_id'])
        if batch_id not in eligible:
            eligible[batch_id] = _eligible_lj(batch_id) if method == 'lj' else _eligible_zscore(connection, batch_id)
            source_batches[batch_id] = dict(batch=dict(connection.execute('SELECT * FROM batches WHERE id=?', (batch_id,)).fetchone()),
                profiles=[dict(profile) for profile in connection.execute('SELECT * FROM qc_target_profiles WHERE qc_method=? AND batch_id=? ORDER BY id', (method, batch_id))])
        when = _clock(row['test_time'])
        in_window = not ((start is not None and when < start) or (when > end if inclusive else when >= end))
        if source.get('record_type', 'routine') != 'routine' or source.get('purpose', 'routine') not in ('routine', ''):
            continue
        for position, level in enumerate(context_levels, 1):
            physical_id = level['qc_level_id']
            if physical_id not in current_ids:
                continue
            source_level_id = f"Level {level['level_order']}"
            if method == 'lj':
                accepted = eligible[batch_id].get(row['id'], False)
                value = row['value']
            else:
                accepted = source_level_id in eligible[batch_id].get(row['id'], set())
                measured = next((entry for entry in raw_levels if entry['level_id'] == source_level_id), {})
                value = measured.get('raw_value')
            if accepted and value is not None and math.isfinite(float(value)):
                latest_eligible_at = max(latest_eligible_at, when) if latest_eligible_at else when
                if in_window:
                    samples[physical_id].append(dict(method=method, batch_id=batch_id, result_id=row['id'],
                        context_id=row['context_id'], evaluation_id=row['evaluation_id'], source_level_id=source_level_id,
                        qc_level_id=physical_id, test_time=when.isoformat(sep=' '), value=float(value)))
    levels, issues = [], []
    if not context['editable']:
        issues.append(context['read_only_reason'])
    initial_ready = _initial_parameters_ready(context)
    if not initial_ready:
        issues.append('首次均值和SD尚未建立，请先按本批次要求完成全部水平的建靶。')
    for material in context['levels']:
        physical_id = material.get('qc_level_id')
        included = samples.get(physical_id, [])
        values = [row['value'] for row in included]
        times = [row['test_time'] for row in included]
        average = mean(values) if values else None
        deviation = stdev(values) if len(values) >= 2 else None
        ready = len(values) >= 2 and deviation is not None and deviation > 0 and math.isfinite(deviation)
        if snapshot.get('input_value_type') == 'raw' and (average is None or average <= 0):
            ready = False
        if not ready:
            issues.append(f"{material['level_name']}的有效结果不足2次，或均值、SD不满足启用要求。请补齐有效结果，并核对原始结果及SD。")
        levels.append({**material, 'n': len(values), 'count': len(values), 'days': len({value[:10] for value in times}),
            'input_value_type': snapshot.get('input_value_type', 'raw'), 'unit_symbol': snapshot.get('unit_symbol', ''),
            'start': min(times) if times else None, 'end': max(times) if times else None,
            'mean': average, 'sd': deviation, 'cv': calculate_cv_percent(average, deviation),
            'included_sources': included, 'ready': ready})
    from services.target_statistics_policy_service import get_target_statistics_policy
    policy = get_target_statistics_policy('lot', binding['lot_config_item_id'], connection=connection)
    current_profile = ({key: context['current'][key] for key in
        ('id', 'version_no', 'effective_at', 'source', 'levels', 'evidence', 'confirmed_by')}
        if context['current'] else None)
    payload = dict(version=1, binding_id=int(binding_id), strategy=strategy, as_of=_clock(as_of).isoformat(sep=' '),
        cutoff=end.isoformat(sep=' '), period_start=start.isoformat(sep=' ') if start else min((row['start'] for row in levels if row['start']), default=None),
        period_end=end.isoformat(sep=' '), system_identity=system, qc_method=method, rule=RULE, levels=levels,
        input_value_type=snapshot.get('input_value_type', 'raw'), period_end_inclusive=inclusive,
        initial_parameters_ready=initial_ready,
        latest_eligible_at=latest_eligible_at.isoformat(sep=' ') if latest_eligible_at else None,
        current_profile=current_profile, policy_revision=policy['revision'], current_strategy=policy['mode'],
        ready=not issues and bool(levels), issues=list(dict.fromkeys(issues)))
    if binding.get('transfer_chain'):
        payload['transfer_chain'] = binding['transfer_chain']
    payload['fingerprint'] = _digest(dict(candidate=payload, context_fingerprint=context['fingerprint'],
        source_records=watched, source_batches=source_batches))
    return payload


def get_target_statistics_candidate(binding_id, strategy, *, as_of=None):
    when = _clock(as_of)
    with database.read_snapshot() as connection:
        return _candidate(connection, binding_id, strategy, when)


def validate_statistics_source(connection, method, batch_id, levels, source_statistics, *, effective_at=None):
    """A serialized source chain cannot authorize foreign records or edited values."""
    supplied = _decode(source_statistics)
    if not isinstance(supplied, dict):
        raise ValueError('统计来源格式无效，请重新计算。')
    from services.target_binding_service import target_binding_for_batch
    binding = target_binding_for_batch(connection, method, batch_id)
    if supplied.get('binding_id') != binding['id']:
        raise ValueError('统计来源不属于本批次，请重新核对。')
    _validate_activation_clock(supplied)
    current = _candidate(connection, binding['id'], supplied.get('strategy'), supplied.get('as_of'))
    if supplied != current or not current['ready']:
        raise ValueError('统计来源或有效结果已变化，请重新计算并核对。')
    expected = [dict(level_id=row['level_id'], mean=row['mean'], sd=row['sd']) for row in current['levels']]
    if levels != expected:
        raise ValueError('启用参数与统计候选不一致，请重新核对。')
    latest_source = max((_clock(sample['test_time']) for level in current['levels']
                         for sample in level['included_sources']), default=None)
    if effective_at is not None and latest_source and _clock(effective_at) < latest_source:
        raise ValueError('生效时间不能早于统计所用的最后一条检测，请核对生效时间。')
    return current


def save_target_statistics_candidate(binding_id, candidate, *, expected_fingerprint,
        confirmed_by, evidence, effective_at, confirmed=False, expected_policy_revision=None):
    if not confirmed:
        raise ValueError('请确认全部水平的统计结果和生效时间。')
    if not isinstance(candidate, dict) or candidate.get('binding_id') != int(binding_id):
        raise ValueError('请重新打开本批次的统计结果。')
    if not expected_fingerprint or candidate.get('fingerprint') != expected_fingerprint:
        raise ValueError('统计候选已变化，请重新核对。')
    _validate_activation_clock(candidate)
    with database.atomic_write() as connection:
        current = _candidate(connection, binding_id, candidate.get('strategy'), candidate.get('as_of'))
        if current != candidate or current['fingerprint'] != expected_fingerprint:
            raise ValueError('统计来源或有效结果已变化，请重新计算；当前输入仍可保留。')
        if not current['ready']:
            raise ValueError('；'.join(current['issues']))
        from services.lot_lifecycle_service import create_target_profile
        from services.target_binding_service import resolve_target_binding
        binding = resolve_target_binding(connection, binding_id)
        parameters = [dict(level_id=row['level_id'], mean=row['mean'], sd=row['sd']) for row in current['levels']]
        profile = create_target_profile(method=binding['qc_method'], batch_id=binding['runtime_batch_id'],
            levels=parameters, source='revision', evidence=evidence, confirmed_by=confirmed_by,
            effective_at=effective_at, source_statistics=current,
            expected_statistics_policy_revision=expected_policy_revision)
        return profile
