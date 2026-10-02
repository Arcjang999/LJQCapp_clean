"""Validate one daily group without writes, then save all methods in one transaction."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
import sqlite3

import database
from services.daily_context_service import get_daily_context, issue, _positive, _digest, _json
from services.lot_lifecycle_service import timestamp
from services.value_type_service import parse_project_input_value


METHOD_TABLES = {'lj': ('results', 'lj_result_id', 'lj_result'),
                 'zscore': ('zscore_runs', 'zscore_run_id', 'zscore_run'),
                 'instant': ('instant_results', 'instant_result_id', 'instant_result')}


class DailyEntryError(ValueError):
    def __init__(self, errors, *, retryable=False):
        self.errors = errors
        self.retryable = retryable
        super().__init__('；'.join(dict.fromkeys(row['message'] for row in errors)))


def _normalize(request):
    if not isinstance(request, dict):
        raise DailyEntryError([issue('请重新核对本次录入。')])
    payload = deepcopy(request)
    payload.pop('validation_hash', None)
    if not str(payload.get('submission_id') or '').strip() or len(str(payload['submission_id'])) > 200:
        raise DailyEntryError([issue('本次录入标识无效，请建立新一轮录入。', field='submission_id')])
    payload['submission_id'] = str(payload['submission_id']).strip()
    payload['operator'] = str(payload.get('operator') or '').strip()
    payload['purpose'] = payload.get('purpose', 'routine')
    if not isinstance(payload.get('selection'), dict) or not isinstance(payload.get('items'), list):
        raise DailyEntryError([issue('请先选择明确的仪器、质控品、实际批号及完整项目。')])
    if not str(payload.get('test_time') or '').strip():
        raise DailyEntryError([issue('请填写本次检测时间。', field='test_time')])
    try:
        payload['test_time'] = timestamp(payload['test_time'])
    except (ValueError, TypeError):
        raise DailyEntryError([issue('请填写有效的本次检测时间。', field='test_time')]) from None
    for row in payload['items']:
        if not isinstance(row, dict):
            raise DailyEntryError([issue('项目录入格式无效，请重新核对。')])
        row['row_key'] = str(row.get('row_key', ''))
        row['manual_note'] = str(row.get('manual_note') or '').strip()
        row['allow_same_time'] = row.get('allow_same_time') is True
    return payload


def _validate(c, payload):
    errors, warnings, prepared = [], [], []
    if not payload['operator']:
        errors.append(issue('请填写本次检测人。', field='operator'))
    if payload['purpose'] != 'routine':
        errors.append(issue('此入口用于常规质控，请核对本次用途。', field='purpose'))
    try:
        context = get_daily_context(**payload['selection'], test_time=payload['test_time'])
    except (ValueError, TypeError) as exc:
        message = str(exc) if isinstance(exc, ValueError) else '请重新选择完整的仪器、产品、批号与组合。'
        return dict(valid=False, errors=[*errors, issue(message)], warnings=[], frozen_request=None, summary=[]), []
    errors.extend(context['issues'])
    if not payload.get('context_revision') or payload['context_revision'] != context['context_revision']:
        errors.append(issue('项目、材料、参数或检测记录已变化，请保留输入并重新核对本次录入。', field='context_revision'))
    by_id = {row['row_key']: row for row in context['items']}
    if not payload['items']:
        errors.append(issue('请至少选择一个要保存的检测项。', field='items'))
    seen = set()
    for requested in payload['items']:
        key = requested['row_key']
        def problem(message, field='context', level=None):
            errors.append(issue(message, row_key=key, field=field, qc_level_id=level))
        if key in seen:
            problem('同一检测项重复出现，请合并为一行。', 'row_key')
            continue
        seen.add(key)
        item = by_id.get(key)
        if item is None:
            problem('检测项不属于当前所选仪器、产品、实际批号与组合。', 'row_key')
            continue
        errors.extend(item['issues'])
        for field in ('lot_config_item_id', 'runtime_project_id', 'runtime_batch_id', 'unit_id', 'input_value_type', 'qc_method'):
            if field in requested and requested[field] != item[field]:
                problem('检测项身份、单位或输入尺度已变化，请重新核对。', field)
        values = requested.get('levels')
        if not isinstance(values, list):
            problem('请填写该检测项的全部水平。', 'levels')
            continue
        found, parsed = {}, []
        for value in values:
            if not isinstance(value, dict):
                problem('水平输入格式无效。', 'levels')
                continue
            try:
                identifier = _positive(value.get('qc_level_id'), '质控水平')
            except ValueError as exc:
                problem(str(exc), 'qc_level_id')
                continue
            if identifier in found:
                problem('同一质控水平重复填写。', 'qc_level_id', identifier)
            found[identifier] = value.get('value')
        expected = {row['qc_level_id'] for row in item['levels']}
        if set(found) != expected:
            for missing in sorted(expected - set(found)):
                problem('缺少该水平检测值，必须完整保存本次检测。', 'value', missing)
            for extra in sorted(set(found) - expected):
                problem('此水平不属于当前完整组合。', 'qc_level_id', extra)
        for level in item['levels']:
            lid = level['qc_level_id']
            if lid not in found:
                continue
            raw = found[lid]
            # str(0) must stay "0": Ct/log can legitimately be zero.
            number, log_value, error = parse_project_input_value('' if raw is None else str(raw), item['input_value_type'])
            if error:
                problem(error, 'value', lid)
            else:
                parsed.append(dict(qc_level_id=lid, level_id=level['level_id'], value=number, log_value=log_value))
        try:
            reagent_id = _positive(requested.get('reagent_lot_id'), '本次实际试剂批号')
        except ValueError as exc:
            problem(str(exc), 'reagent_lot_id')
            reagent_id = None
        if reagent_id is not None and reagent_id not in {r['id'] for r in item['reagent_options']}:
            problem('所选试剂批号未通过该检测系统验证、已过期或不适用于本次检测时间。', 'reagent_lot_id')
        table = METHOD_TABLES[item['qc_method']][0]
        same = c.execute(f'SELECT id FROM {table} WHERE batch_id=? AND test_time=? ORDER BY id',
                         (item['runtime_batch_id'], payload['test_time'])).fetchall()
        if same:
            warning = issue('同批次已有相同检测时间的记录；请确认本次是另一次检测后再保存。', row_key=key, field='allow_same_time')
            warnings.append(warning)
            if not requested['allow_same_time']:
                errors.append(warning)
        prepared.append(dict(item=item, values=parsed, reagent_lot_id=reagent_id, manual_note=requested['manual_note']))
    frozen = None
    if not errors:
        frozen = deepcopy(payload)
        try:
            frozen['validation_hash'] = _digest(payload)
        except (ValueError, TypeError):
            errors.append(issue('输入格式无效，请核对全部数值。', field='items'))
            frozen = None
    summary = [dict(row_key=r['item']['row_key'], test_item_name=r['item']['test_item_name'],
        qc_method=r['item']['qc_method'], levels=len(r['values']), phase=r['item']['phase'],
        target_profile_id=(r['item']['target_profile'] or {}).get('id'), reagent_lot_id=r['reagent_lot_id']) for r in prepared]
    return dict(valid=not errors, errors=errors, warnings=warnings, frozen_request=frozen if not errors else None, summary=summary), prepared


def validate_submission(request):
    """Return all row/level issues; never create results, bindings, parameters or receipts."""
    try:
        payload = _normalize(request)
    except DailyEntryError as exc:
        return dict(valid=False, errors=exc.errors, warnings=[], frozen_request=None, summary=[])
    with database.read_snapshot() as c:
        result, _ = _validate(c, payload)
        return result


def _save_item(payload, prepared):
    item, levels = prepared['item'], prepared['values']
    method, batch_id = item['qc_method'], item['runtime_batch_id']
    selection = dict(reagent_lot_id=prepared['reagent_lot_id'], expected_revision=item['usage_revision'])
    common = dict(batch_id=batch_id, test_time=payload['test_time'], operator=payload['operator'], lot_selection=selection)
    if method == 'lj':
        result_id = database.add_result(**common, value=levels[0]['value'], log_value=levels[0]['log_value'], manual_note=prepared['manual_note'])
    elif method == 'instant':
        from services.instant_service import save_instant_result
        result_id = save_instant_result(**common, value=levels[0]['value'], log_value=levels[0]['log_value'], manual_note=prepared['manual_note'])
    else:
        from zscore_logic import create_zscore_run, get_template_id_for_level_count
        run = create_zscore_run(**common, level_results=[dict(level_id=r['level_id'], raw_value=r['value'], log_value=r['log_value']) for r in levels],
            template_id=get_template_id_for_level_count(item['level_count']), required_n=item['target_n'], manual_note=prepared['manual_note'])
        result_id = run['id']
    return int(result_id)


def _receipt_item(c, prepared, result_id):
    item = prepared['item']
    method = item['qc_method']
    _, column, source_type = METHOD_TABLES[method]
    context = c.execute(f'SELECT * FROM qc_result_contexts WHERE {column}=?', (result_id,)).fetchone()
    evaluation = c.execute('SELECT * FROM qc_result_evaluations WHERE context_id=? ORDER BY id DESC LIMIT 1', (context['id'],)).fetchone() if context else None
    if context is None or evaluation is None:
        raise ValueError('检测依据或判读未完整保存，本组已取消保存。')
    body = json.loads(evaluation['evaluation_json'])
    result = body.get('result', body)
    status = result.get('run_status', result.get('status'))
    classification = {'符合质控': 'accept', '在控': 'accept', '失控': 'reject', '警告': 'warning'}.get(status, status)
    if method == 'instant':
        classification = 'suspect' if result.get('is_outlier_suspect') else 'building'
        effective_count = int(result.get('effective_sequence') or 0)
        if effective_count < 3:
            conclusion = '即时法尚不足3个有效点'
        elif result.get('is_outlier_suspect'):
            conclusion = '即时法疑似离群，请核对'
        elif status == '警告':
            conclusion = '即时法警告，请核对'
        elif effective_count >= 20:
            conclusion = '即时法已达到20个有效点，可核对是否转入单水平法'
        else:
            conclusion = '即时法当前在控'
    elif result.get('phase') in ('正式数据', 'formal_qc'):
        conclusion = {'accept': '在控', 'warning': '警告', 'reject': '失控'}.get(classification, '判读依据待核对')
    else:
        conclusion = '参数建立中'
    level_evidence = []
    for value, material in zip(prepared['values'], item['levels']):
        measured = next((r for r in result.get('level_results', []) if r.get('level_id') == value['level_id']), result if method != 'zscore' else {})
        level_evidence.append(dict(qc_level_id=value['qc_level_id'], level_id=value['level_id'],
            level_name=material['level_name'], level_code=material['level_code'],
            qc_material_lot_id=material['qc_material_lot_id'], lot_no=material['lot_no'], expiry_date=material['expiry_date'],
            value=value['value'], raw_value=value['value'], log_value=value['log_value'],
            input_value_type=item['input_value_type'], unit_symbol=item['unit_symbol'],
            status=measured.get('status'), rule_hits=measured.get('rule_hits', [])))
    return dict(row_key=item['row_key'], lot_config_item_id=item['lot_config_item_id'], qc_method=method, method=method,
        runtime_project_id=item['runtime_project_id'], runtime_batch_id=item['runtime_batch_id'],
        source_type=source_type, source_id=result_id, result_id=result_id,
        run_id=result_id if method == 'zscore' else None, context_id=context['id'], evaluation_id=evaluation['id'],
        classification=classification, phase=result.get('phase', 'building' if method == 'instant' else None), level_count=item['level_count'],
        conclusion=conclusion,
        levels=level_evidence,
        test_item_name=item['test_item_name'], target_profile_id=context['target_profile_id'])


def get_submission(submission_id):
    with database.read_snapshot() as c:
        row = c.execute('SELECT receipt_json FROM qc_daily_submissions WHERE submission_id=?', (str(submission_id),)).fetchone()
        return json.loads(row[0]) if row else None


def submit_daily(request):
    """A retry returns its original receipt; a new group commits or rolls back as one."""
    payload = _normalize(request)
    try:
        digest = _digest(payload)
    except (ValueError, TypeError):
        raise DailyEntryError([issue('输入格式已改变，请保留输入并重新核对。', field='validation_hash')]) from None
    if request.get('validation_hash') != digest:
        raise DailyEntryError([issue('输入已改变或尚未核对，请保留输入并重新核对后保存。', field='validation_hash')])
    active_key = None
    try:
        with database.atomic_write() as c:
            # BEGIN IMMEDIATE serializes both receipt lookup and the whole write group.
            previous = c.execute('SELECT payload_hash,receipt_json FROM qc_daily_submissions WHERE submission_id=?', (payload['submission_id'],)).fetchone()
            if previous:
                if previous['payload_hash'] != digest:
                    raise DailyEntryError([issue('本次提交标识已用于不同内容，请核对原回执或建立新一轮检测。', field='submission_id')])
                return json.loads(previous['receipt_json'])
            validation, prepared = _validate(c, payload)
            if not validation['valid']:
                raise DailyEntryError(validation['errors'])
            ids = []
            for row in prepared:
                active_key = row['item']['row_key']
                ids.append(_save_item(payload, row))
            receipts = [_receipt_item(c, row, identifier) for row, identifier in zip(prepared, ids)]
            saved_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            receipt = dict(submission_id=payload['submission_id'], saved_at=saved_at, operator=payload['operator'],
                test_time=payload['test_time'], purpose='routine', items=receipts, count=len(receipts), status='saved')
            c.execute('''INSERT INTO qc_daily_submissions(submission_id,payload_hash,request_json,receipt_json,saved_at,operator)
                VALUES(?,?,?,?,?,?)''', (payload['submission_id'], digest, _json(payload), _json(receipt), saved_at, payload['operator']))
            for row in receipts:
                c.execute('''INSERT INTO qc_daily_submission_items(submission_id,row_key,lot_config_item_id,qc_method,result_id,context_id,evaluation_id)
                    VALUES(?,?,?,?,?,?,?)''', (payload['submission_id'], row['row_key'], row['lot_config_item_id'], row['qc_method'],
                                               row['result_id'], row['context_id'], row['evaluation_id']))
            return receipt
    except DailyEntryError:
        raise
    except sqlite3.OperationalError as exc:
        locked = 'locked' in str(exc).lower() or 'busy' in str(exc).lower()
        message = '资料正在保存，请稍后在当前页面重新点击“确认保存整组”，无需重新录入。' if locked else '本组保存未完成，请保留输入并重试。'
        raise DailyEntryError([issue(message, row_key=active_key, field='save')], retryable=True) from exc
    except Exception as exc:
        message = str(exc) if isinstance(exc, ValueError) else '本组未保存任何结果，全部输入仍保留。请重新核对后保存。'
        raise DailyEntryError([issue(message, row_key=active_key, field='save')], retryable=True) from exc
