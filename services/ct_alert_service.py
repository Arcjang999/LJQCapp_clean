"""Independent Ct acceptance reminders; original QC outcomes stay unchanged."""
from __future__ import annotations

from copy import deepcopy
import json
import math
import operator as comparison


OPERATORS = {'<': comparison.lt, '<=': comparison.le,
             '>': comparison.gt, '>=': comparison.ge}
OPERATOR_LABELS = {'<': '<', '<=': '≤', '>': '>', '>=': '≥'}


def normalize_ct_alert(config, input_value_type='ct', *, allow_incomplete=False):
    """Return a canonical optional policy; drafts may retain empty inputs."""
    if config is None or config is False:
        return None
    if not isinstance(config, dict):
        raise ValueError('请重新设置实验室自定Ct线。')
    enabled = config.get('enabled', False)
    if not isinstance(enabled, bool):
        raise ValueError('请重新勾选实验室自定Ct线。')
    if not enabled:
        return None
    if input_value_type != 'ct':
        raise ValueError('实验室自定Ct线适用于Ct输入项目。')
    if config.get('version', 1) != 1:
        raise ValueError('Ct要求版本无效，请重新设置。')
    raw_operation = config.get('operator')
    if raw_operation is not None and not isinstance(raw_operation, str):
        raise ValueError('请选择Ct要求。')
    operation = {'≤': '<=', '≥': '>='}.get(raw_operation, raw_operation)
    if operation not in OPERATORS:
        if not allow_incomplete or operation not in (None, ''):
            raise ValueError('请选择Ct要求。')
        operation = ''
    raw = config.get('threshold')
    if raw is None or raw == '':
        if not allow_incomplete:
            raise ValueError('请填写Ct界值。')
        threshold = None
    else:
        try:
            if isinstance(raw, bool):
                raise ValueError
            threshold = float(raw)
            if not math.isfinite(threshold):
                raise ValueError
        except (TypeError, ValueError, OverflowError):
            raise ValueError('Ct界值须填写有效数值。') from None
    return dict(enabled=True, operator=operation, threshold=threshold, version=1)


def format_ct_number(value):
    """Keep the adopted float's shortest round-trip text without integer .0."""
    text = repr(float(value))
    return text[:-2] if text.endswith('.0') else text


def ct_requirement_text(config):
    policy = normalize_ct_alert(config)
    if policy is None:
        return ''
    return f"Ct {OPERATOR_LABELS[policy['operator']]} {format_ct_number(policy['threshold'])}"


def evaluate_ct_alert(value, config):
    policy = normalize_ct_alert(config)
    result = dict(enabled=policy is not None, evaluated=False, alert=False,
                  value=None, requirement=ct_requirement_text(policy), message='', config=policy)
    if policy is None:
        return result
    try:
        if isinstance(value, bool):
            raise ValueError
        number = float(value)
        if not math.isfinite(number):
            raise ValueError
    except (ValueError, TypeError, OverflowError):
        return result
    alert = not OPERATORS[policy['operator']](number, policy['threshold'])
    result.update(evaluated=True, alert=alert, value=number,
                  message=f"实测Ct：{format_ct_number(number)}｜Ct要求：{OPERATOR_LABELS[policy['operator']]}{format_ct_number(policy['threshold'])}")
    return result


def combined_ct_status(status, alert=False):
    """Return presentation only, including red priority and pending labels."""
    original = str(status or '')
    label = {'accept': '在控', 'warning': '警告', 'reject': '失控',
             'pending': '待判读', 'target_building': '建靶中'}.get(original, original)
    if original == 'reject' or '失控' in original:
        tone = 'danger'
    elif original == 'warning' or '警告' in original or '疑似离群' in original:
        tone = 'warning'
    elif original in {'accept', '在控', '符合质控', '有效点'}:
        tone = 'success'
    else:
        tone = 'neutral'
    if alert:
        if tone == 'success':
            label = 'Ct提醒'
        else:
            label = (label + '；' if label else '') + 'Ct提醒'
        if tone != 'danger':
            tone = 'warning'
    return label, tone


def _json(value):
    if isinstance(value, str):
        return json.loads(value or '{}')
    return value or {}


def ct_alert_from_source(source):
    source = _json(source)
    review = _json(source.get('quality_review_json'))
    return normalize_ct_alert(review.get('ct_alert'), source.get('input_value_type', 'ct'))


def runtime_ct_alert(method, batch_id):
    from services.quality_review_service import runtime_review
    return normalize_ct_alert(runtime_review(method, batch_id).get('ct_alert'))


def _record_policies(method, batch_id):
    """A record's own source takes priority, including a saved disabled policy."""
    from database import read_snapshot
    from services.lot_lifecycle_service import CONTEXT_COLUMNS, RESULT_TABLES
    column, table = CONTEXT_COLUMNS[method], RESULT_TABLES[method]
    with read_snapshot() as connection:
        rows = connection.execute(f'''SELECT x.{column} AS result_id,x.config_snapshot_json
            FROM qc_result_contexts x JOIN {table} r ON r.id=x.{column}
            WHERE r.batch_id=? ORDER BY x.id''', (batch_id,)).fetchall()
    return {int(row['result_id']): ct_alert_from_source(row['config_snapshot_json']) for row in rows}


def _chosen_policy(identifier, policies, fallback):
    if policies is None:
        return fallback
    try:
        return policies.get(int(identifier), fallback)
    except (ValueError, TypeError, OverflowError):
        return fallback


def _display_source_status(row, *, zscore=False):
    status = row.get('run_status', row.get('status', 'pending')) if zscore else row.get('status', 'pending')
    if (row.get('phase') in {'target_building', '建靶数据', '参数建立数据'}
            and (str(status) in {'accept', '在控', '符合质控', '有效点'}
                 or (zscore and str(status) in {'warning', 'reject'}))):
        return 'target_building'
    return status


def _annotate_frame(frame, config, *, zscore=False, policies=None):
    import pandas as pd
    if frame is None:
        return None
    result = frame.copy(deep=True)
    policy = normalize_ct_alert(config)
    alerts, evaluations, requirements, details, statuses, tones, configs = [], [], [], [], [], [], []
    for row in result.to_dict('records'):
        identifier = row.get('run_id') if zscore else row.get('id', row.get('result_id'))
        chosen = policy if row.get('is_preview', False) else _chosen_policy(identifier, policies, policy)
        value = row.get('raw_value', row.get('value')) if zscore else row.get('value', row.get('raw_value'))
        evaluated = evaluate_ct_alert(value, chosen)
        status = _display_source_status(row, zscore=zscore)
        shown, tone = combined_ct_status(status, evaluated['alert'])
        alerts.append(evaluated['alert']); evaluations.append(evaluated)
        requirements.append(evaluated['requirement']); details.append(evaluated['message'])
        statuses.append(shown); tones.append(tone); configs.append(chosen)
    result['ct_alert'] = pd.Series(alerts, index=result.index, dtype=bool)
    result['ct_evaluation'] = evaluations
    result['ct_alert_config'] = configs
    result['ct_requirement'] = requirements
    result['ct_alert_detail'] = details
    result['ct_threshold'] = [value['threshold'] if value else None for value in configs]
    result['ct_operator'] = [value['operator'] if value else '' for value in configs]
    result['display_status'] = statuses
    result['display_tone'] = tones
    unique = {json.dumps(value, sort_keys=True) for value in configs}
    result.attrs['ct_alert'] = configs[0] if configs and len(unique) == 1 else policy if not configs else None
    result.attrs['ct_alert_configs'] = [json.loads(value) for value in sorted(unique)]
    return result


def annotate_lj_frame(frame, config=None):
    return _annotate_frame(frame, config)


def annotate_zscore_frame(frame, config=None):
    return _annotate_frame(frame, config, zscore=True)


def annotate_zscore_runs(runs, config=None, *, policies=None):
    result = deepcopy(runs)
    policy = normalize_ct_alert(config)
    for run in result:
        identifier = run.get('run_id', run.get('id'))
        chosen = _chosen_policy(identifier, policies, policy)
        evaluations = []
        for level in run.get('level_results', []):
            evaluation = evaluate_ct_alert(level.get('raw_value', level.get('value')), chosen)
            level['ct_evaluation'] = evaluation
            level['ct_alert'] = evaluation['alert']
            level['ct_requirement'] = evaluation['requirement']
            level['ct_alert_detail'] = evaluation['message']
            evaluations.append(evaluation)
        alert = any(row['alert'] for row in evaluations)
        run['ct_alert'] = alert
        run['ct_alert_config'] = chosen
        run['ct_requirement'] = ct_requirement_text(chosen)
        run['ct_evaluation'] = dict(enabled=chosen is not None, evaluated=bool(evaluations) and all(
            row['evaluated'] for row in evaluations), alert=alert, config=chosen,
            requirement=ct_requirement_text(chosen), levels=evaluations)
        run['ct_alert_detail'] = '；'.join(row['message'] for row in evaluations if row['alert'])
        run['display_status'], run['display_tone'] = combined_ct_status(_display_source_status(run, zscore=True), alert)
    return result


def annotate_runtime_frame(method, batch_id, frame):
    if method == 'zscore' and frame is not None and 'run_status' not in frame.columns:
        from database import read_snapshot
        with read_snapshot() as connection:
            statuses = {int(row['id']): row['run_status'] for row in connection.execute(
                'SELECT id,run_status FROM zscore_runs WHERE batch_id=?', (batch_id,))}
        frame = frame.copy(deep=True)
        frame['run_status'] = [row.get('status', 'pending') if row.get('is_preview', False)
            else _chosen_policy(row.get('run_id'), statuses, row.get('status', 'pending'))
            for row in frame.to_dict('records')]
    return _annotate_frame(frame, runtime_ct_alert(method, batch_id), zscore=method == 'zscore',
                           policies=_record_policies(method, batch_id))


def annotate_runtime_runs(batch_id, runs):
    return annotate_zscore_runs(runs, runtime_ct_alert('zscore', batch_id),
                                 policies=_record_policies('zscore', batch_id))
