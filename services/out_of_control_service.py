"""Versioned handling of abnormal QC results; never calculates or edits QC results.

Read operations use a read-only SQLite connection and never materialize contexts,
systems or evaluations. A handling revision freezes the selected evidence; latest
evaluation evidence is returned separately for future maintenance integration.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from hashlib import sha256
import json
import sqlite3
from zoneinfo import ZoneInfo

import database
from services.search_service import fuzzy_match

SOURCE_TABLES = {'lj_result': ('results', 'lj_result_id', 'lj'),
                 'zscore_run': ('zscore_runs', 'zscore_run_id', 'zscore')}
STATUS_LABELS = {'pending': '待处理', 'in_progress': '处理中',
                 'pending_confirmation': '待确认', 'completed': '已完成'}
CLASSIFICATION_LABELS = {'reject': '失控', 'warning': '警告', 'accept': '在控'}
CAUSE_CATEGORIES = ('试剂', '仪器', '质控品', '操作', '环境', '其他')
TEXT_FIELDS = ('cause_category', 'cause_analysis', 'corrective_action',
               'supplementary_note', 'effect_description', 'effect_evidence',
               'handler_text', 'confirmer_text', 'confirmed_at',
               'patient_impact_scope', 'patient_impact_assessment',
               'patient_impact_start', 'patient_impact_end',
               'patient_impact_actions', 'patient_impact_basis')
REQUIRED_SUBMISSION = {'cause_category': '原因分类', 'cause_analysis': '原因分析',
                       'corrective_action': '纠正措施', 'effect_description': '效果说明',
                       'effect_evidence': '效果依据', 'handler_text': '处理人'}


def _now():
    return datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M:%S')


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                      separators=(',', ':'))


def _decode(value, default=None):
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value) if value else default
    except (ValueError, TypeError):
        return default


def _required(value, label):
    value = str(value or '').strip()
    if not value:
        raise ValueError(f'请填写{label}。')
    return value


def _positive_id(value):
    try:
        result = int(value)
        if isinstance(value, bool) or result <= 0 or str(result) != str(value).strip():
            raise ValueError
        return result
    except (ValueError, TypeError):
        raise ValueError('记录编号无效，请重新选择。') from None


def _time(value):
    try:
        result = datetime.fromisoformat(str(value).strip())
        if result.tzinfo is not None:
            result = result.astimezone(ZoneInfo('Asia/Shanghai')).replace(tzinfo=None)
        return result.replace(microsecond=0)
    except (TypeError, ValueError):
        raise ValueError('请填写有效的日期和时间。') from None


@contextmanager
def _reader():
    # A missing file must not trigger get_connection's legacy migration behavior.
    connection = sqlite3.connect(database.get_db_path().resolve().as_uri() + '?mode=ro', uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute('BEGIN')
        yield connection
    finally:
        connection.close()


def _row(connection, sql, args=()):
    row = connection.execute(sql, args).fetchone()
    return dict(row) if row else None


def _classification(value):
    return {'失控': 'reject', '警告': 'warning', '在控': 'accept', '符合质控': 'accept',
            'reject': 'reject', 'warning': 'warning', 'accept': 'accept'}.get(str(value))


def _rule_names(value):
    value = _decode(value, value) if isinstance(value, str) else value
    if isinstance(value, str):
        return [value] if value.strip() else []
    return [str(x.get('rule_id', x.get('rule', ''))) if isinstance(x, dict) else str(x)
            for x in (value or [])]


def _identity(connection, config, missing):
    identity = config.get('identity') or []
    template_id = config.get('project_template_item_id')
    expected = [template_id, *[identity[i] for i in (0, 3, 4, 5, 6, 7)]] if template_id and len(identity) >= 8 else None
    system = None
    if config.get('system_id'):
        system = _row(connection, 'SELECT * FROM qc_detection_systems WHERE id=?', (config['system_id'],))
    elif expected:
        # Compare parsed JSON rather than whitespace-dependent serialization.
        for candidate in connection.execute('SELECT * FROM qc_detection_systems WHERE template_item_id=?', (template_id,)):
            if _decode(candidate['identity_json']) == expected:
                system = dict(candidate)
                break
    if not system or not expected or _decode(system['identity_json']) != expected:
        missing.append({'field': 'system_id', 'reason': '检测系统关系未记录或不一致'})
        return None, None
    for field, index in (('lab_instrument_id', 0), ('test_item_id', 3),
                         ('input_value_type', 4), ('unit_id', 5), ('method_id', 6), ('reagent_id', 7)):
        if config.get(field) is not None and config[field] != identity[index]:
            missing.append({'field': 'system_id', 'reason': '检测系统身份与原资料不一致'})
            return None, None
    return system['id'], expected


def _snapshot(connection, source_type, source_id):
    if source_type not in SOURCE_TABLES:
        raise ValueError('该记录不适用于此失控处理入口。')
    source_id = _positive_id(source_id)
    table, context_column, method = SOURCE_TABLES[source_type]
    result = _row(connection, f'SELECT * FROM {table} WHERE id=?', (source_id,))
    if result is None:
        raise ValueError('未找到原检测记录，请重新核对。')
    missing = []
    context = _row(connection, f'SELECT * FROM qc_result_contexts WHERE {context_column}=?', (source_id,))
    config = _decode(context['config_snapshot_json'], {}) if context else {}
    if config.get('record_type', 'routine') != 'routine' or config.get('purpose', 'routine') not in ('routine', ''):
        raise ValueError('该记录类型尚不适用于此失控处理入口。')
    evaluation = None
    context_levels = []
    profile = None
    if context:
        evaluation = _row(connection, 'SELECT * FROM qc_result_evaluations WHERE context_id=? ORDER BY id DESC LIMIT 1', (context['id'],))
        context_levels = [dict(x) for x in connection.execute('SELECT * FROM qc_result_context_levels WHERE context_id=? ORDER BY level_order', (context['id'],))]
        if context.get('target_profile_id'):
            profile = _row(connection, 'SELECT * FROM qc_target_profiles WHERE id=? AND qc_method=? AND batch_id=?', (context['target_profile_id'], method, result['batch_id']))
            if profile:
                profile['levels'] = _decode(profile['levels_json'], [])
    for key, available in (('origin_context_id', context), ('origin_evaluation_id', evaluation), ('target_profile_id', profile)):
        if not available:
            missing.append({'field': key, 'reason': '原记录未保存'})
    payload = _decode(evaluation['evaluation_json'], {}) if evaluation else {}
    if evaluation:
        evaluation['payload'] = payload
    system_id, system_identity = _identity(connection, config, missing)
    batch = _row(connection, 'SELECT project_id FROM batches WHERE id=?', (result['batch_id'],))
    project_id = result.get('project_id') or (batch or {}).get('project_id')
    raw_levels = [dict(x) for x in connection.execute('SELECT * FROM zscore_level_results WHERE run_id=? ORDER BY level_id,id', (source_id,))] if method == 'zscore' else [dict(result)]
    evaluated = payload.get('result', payload) if method == 'lj' else payload
    if method == 'zscore' and 'run' in payload:
        evaluated = payload['run']
    phase_value = evaluated.get('phase', result.get('phase'))
    phase = 'formal' if phase_value in ('formal_qc', '正式数据') else ('building' if phase_value in ('target_building', '建靶数据', '待建靶') else 'unknown')
    classification = _classification(evaluated.get('status') if method == 'lj' else evaluated.get('run_status', result.get('run_status')))
    if method == 'lj' and not evaluation:
        classification, phase = None, 'unknown'
    rules = _rule_names(evaluated.get('rule_hits', evaluated.get('rules', [])) if method == 'lj' else evaluated.get('rule_hits_run', result.get('rule_hits_run', [])))
    if phase == 'unknown' or classification is None:
        missing.append({'field': 'classification', 'reason': '检测阶段或原判读尚待核实'})
    evidence_levels = {str(x.get('level_id')): x for x in evaluated.get('level_results', [])} if method == 'zscore' else {}
    levels = []
    for order, original in enumerate(raw_levels, 1):
        evidence = evaluated if method == 'lj' else evidence_levels.get(str(original['level_id']), original)
        material = next((x for x in context_levels if x['level_order'] == order), {})
        item = dict(original)
        item.update(level_id=original.get('level_id', 'L1'), level_order=order,
                    value=original.get('value', original.get('raw_value')),
                    classification=classification if method == 'lj' else _classification(evidence.get('status', evidence.get('level_status'))),
                    rule_names=rules if method == 'lj' else _rule_names(evidence.get('rule_hits_local')),
                    qc_level_id=material.get('qc_level_id'), qc_lot_id=material.get('qc_lot_id'),
                    lot_no=material.get('lot_no') or None, level_name=material.get('level_name') or None,
                    level_code=material.get('level_code') or None,
                    target_mean=evidence.get('target_mean', payload.get('target_mean')),
                    target_sd=evidence.get('target_sd', payload.get('target_sd')))
        levels.append(item)
        if not material or not material.get('lot_no'):
            missing.append({'field': f'levels.{order}.lot_no', 'reason': '实际质控品批号未记录'})
    expected_levels = int(result.get('level_count', 1))
    levels_complete = len(levels) == expected_levels and len({x['level_id'] for x in levels}) == expected_levels
    evaluation_complete = bool(evaluation) and (method == 'lj' or {x['level_id'] for x in levels} <= set(evidence_levels))
    if not levels_complete:
        missing.append({'field': 'levels', 'reason': '本次检测水平资料不完整'})
    if evaluation and not evaluation_complete:
        missing.append({'field': 'evaluation.levels', 'reason': '原判读版本的水平证据不完整'})
    quality = {key: _decode(config.get(key), config.get(key)) for key in ('quality_goal_json', 'quality_review_json') if config.get(key)}
    if not quality:
        missing.append({'field': 'quality_snapshot', 'reason': '采用质量要求未记录'})
    for key in ('test_item_name', 'instrument_name', 'input_value_type', 'unit_symbol', 'method_name'):
        if not config.get(key):
            missing.append({'field': key, 'reason': '该次检测保存时未登记此项资料，请查阅当时的检验记录核对'})
    if not (context or {}).get('reagent_lot_no'):
        missing.append({'field': 'reagent_lot_no', 'reason': '实际试剂批号未记录'})
    return {'snapshot_schema_version': 1, 'frozen_at': _now(),
            'source_type': source_type, 'source_id': source_id, 'qc_method': method, 'record_type': 'routine',
            'origin_context_id': context['id'] if context else None,
            'origin_evaluation_id': evaluation['id'] if evaluation else None,
            'evaluation_provenance': evaluation.get('reason') if evaluation else 'current_record_only',
            'test_time': result['test_time'], 'project_id': project_id, 'batch_id': result['batch_id'],
            'project_name': config.get('test_item_name'), 'test_item_name': config.get('test_item_name'),
            'instrument_id': config.get('lab_instrument_id'), 'instrument_name': config.get('instrument_name'),
            'input_value_type': config.get('input_value_type'), 'unit_symbol': config.get('unit_symbol'),
            'method_name': config.get('method_name'), 'system_id': system_id, 'system_identity': system_identity,
            'phase': phase, 'classification': classification, 'rule_names': rules,
            'manual_note': result.get('manual_note', ''), 'result': result, 'levels': levels,
            'levels_complete': levels_complete, 'evaluation_complete': evaluation_complete,
            'context': context, 'context_levels': context_levels,
            'config_snapshot': config, 'target_profile': profile, 'quality_snapshot': quality or None,
            'evaluation': evaluation, 'missing_fields': missing}


def read_source(source_type, source_id):
    """Read existing source evidence without running an analysis or writing snapshots."""
    with _reader() as connection:
        return _snapshot(connection, source_type, source_id)


def _get_event(connection, event_id, revision_no=None):
    event = _row(connection, 'SELECT * FROM qc_ooc_events WHERE id=?', (_positive_id(event_id),))
    if not event:
        raise ValueError('未找到处理记录，请重新打开。')
    revision_no = event['current_revision_no'] if revision_no is None else _positive_id(revision_no)
    revision = _row(connection, 'SELECT * FROM qc_ooc_revisions WHERE event_id=? AND revision_no=?', (event_id, revision_no))
    if revision is None:
        raise ValueError('未找到所选处理版本。')
    origin = _decode(event.pop('origin_snapshot_json'), {})
    content = _decode(revision.pop('content_json'), {})
    event.update(event_id=event['id'], origin_snapshot=origin, revision_no=revision_no,
                 status=revision['status'], content=content, saved_at=revision['saved_at'],
                 saved_by=revision['saved_by'], action=revision['action'], change_reason=revision['change_reason'],
                 attachment_ids=content.get('attachment_ids', []))
    event['history'] = [dict(x) for x in connection.execute('SELECT revision_no,previous_revision_no,status,action,saved_by,saved_at,change_reason FROM qc_ooc_revisions WHERE event_id=? ORDER BY revision_no', (event_id,))]
    event['status_history'] = [dict(x) for x in connection.execute('SELECT * FROM qc_ooc_status_history WHERE event_id=? ORDER BY revision_no', (event_id,))]
    links = []
    for row in connection.execute('SELECT * FROM qc_ooc_retests WHERE event_id=? AND revision_no=? ORDER BY source_type,source_id', (event_id, revision_no)):
        link = dict(row)
        link['snapshot'] = _decode(link.pop('snapshot_json'), {})
        link['differences'] = _decode(link.pop('differences_json'), [])
        links.append(link)
    event['retest_refs'] = links
    latest = _row(connection, 'SELECT * FROM qc_result_evaluations WHERE context_id=? ORDER BY id DESC LIMIT 1', (event['origin_context_id'],)) if event['origin_context_id'] else None
    if latest:
        latest['payload'] = _decode(latest['evaluation_json'], {})
    event['latest_evaluation'] = latest
    event['evaluation_changed'] = bool(latest and latest['id'] != event['origin_evaluation_id'])
    event['status_label'] = STATUS_LABELS[event['status']]
    event['report_refs'] = []
    if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='qc_event_reports'").fetchone():
        event['report_refs'] = [dict(row) for row in connection.execute(
            'SELECT id AS report_id,revision_no,report_no,generated_at,file_name FROM qc_event_reports WHERE event_id=? ORDER BY revision_no,id', (event_id,))]
    return event


def get_event(event_id, revision_no=None):
    with _reader() as connection:
        return _get_event(connection, event_id, revision_no)


def _request(connection, request_id, payload):
    request_id = _required(request_id, '本次保存标识')
    if len(request_id) > 200:
        raise ValueError('本次保存无法继续，请重新打开处理页面，核对内容后再保存。')
    digest = sha256(_json(payload).encode()).hexdigest()
    old = _row(connection, 'SELECT * FROM qc_ooc_requests WHERE request_id=?', (request_id,))
    if old and old['payload_digest'] != digest:
        raise ValueError('本次保存内容已改变，请重新核对后再提交。')
    return digest, (_get_event(connection, old['event_id'], old['revision_no']) if old else None)


def _save_request(connection, request_id, digest, event_id, revision_no):
    connection.execute('INSERT INTO qc_ooc_requests(request_id,payload_digest,event_id,revision_no,created_at) VALUES(?,?,?,?,?)',
                       (request_id, digest, event_id, revision_no, _now()))


def open_event(source_type, source_id, request_id, actor_text, explicit_warning=False):
    source_id, actor_text = _positive_id(source_id), _required(actor_text, '登记人')
    payload = {'operation': 'open', 'source_type': source_type, 'source_id': source_id,
               'actor_text': actor_text, 'explicit_warning': bool(explicit_warning)}
    with database.atomic_write() as connection:
        digest, old = _request(connection, request_id, payload)
        if old:
            return old
        existing = _row(connection, 'SELECT id,current_revision_no FROM qc_ooc_events WHERE source_type=? AND source_id=?', (source_type, source_id))
        if existing:
            _save_request(connection, request_id, digest, existing['id'], existing['current_revision_no'])
            return _get_event(connection, existing['id'])
        snapshot = _snapshot(connection, source_type, source_id)
        if snapshot['phase'] != 'formal' or snapshot['classification'] not in ('reject', 'warning'):
            raise ValueError('仅可对已核实的正式期失控或警告登记处理；该检测的阶段和判读请先核对。')
        if not snapshot['levels_complete']:
            raise ValueError('本次检测水平资料不完整，请先核对原记录。')
        if snapshot['classification'] == 'warning' and not explicit_warning:
            raise ValueError('本次为警告，请明确选择发起处理。')
        now = _now()
        event_id = connection.execute('''INSERT INTO qc_ooc_events(source_type,source_id,qc_method,
            origin_context_id,origin_evaluation_id,original_classification,origin_snapshot_json,opened_by,opened_at)
            VALUES(?,?,?,?,?,?,?,?,?)''', (source_type, source_id, snapshot['qc_method'], snapshot['origin_context_id'],
                                         snapshot['origin_evaluation_id'], snapshot['classification'], _json(snapshot), actor_text, now)).lastrowid
        content = {key: '' for key in TEXT_FIELDS}
        content.update(attachment_ids=[], retest_refs=[], confirmation_checked=False)
        connection.execute('''INSERT INTO qc_ooc_revisions(event_id,revision_no,status,action,content_json,saved_by,saved_at)
            VALUES(?,1,'pending','open',?,?,?)''', (event_id, _json(content), actor_text, now))
        connection.execute('''INSERT INTO qc_ooc_status_history(event_id,revision_no,from_status,to_status,actor_text,occurred_at)
            VALUES(?,1,NULL,'pending',?,?)''', (event_id, actor_text, now))
        _save_request(connection, request_id, digest, event_id, 1)
        return _get_event(connection, event_id)


def _differences(origin, retest):
    items = []
    def compare(field, label, before, after):
        # Two unknown values are not evidence of an equal material or parameter.
        if before != after or before in (None, '') or after in (None, ''):
            items.append({'field': field, 'label': label, 'original': before, 'retest': after})
    for index, (before, after) in enumerate(zip(origin['levels'], retest['levels']), 1):
        compare(f'levels.{index}.qc_lot_id', f'水平{index}质控品批号',
                [before.get('qc_lot_id'), before.get('lot_no')] if before.get('qc_lot_id') and before.get('lot_no') else None,
                [after.get('qc_lot_id'), after.get('lot_no')] if after.get('qc_lot_id') and after.get('lot_no') else None)
    for field, label in (('reagent_lot_id', '实际试剂批号'), ('target_profile_id', '参数版本')):
        compare(field, label, (origin.get('context') or {}).get(field), (retest.get('context') or {}).get(field))
    # The saved calculation evidence is relevant even when a profile reference was absent.
    compare('parameters', '实际采用的均值和标准差',
            [[x.get('target_mean'), x.get('target_sd')] for x in origin['levels']],
            [[x.get('target_mean'), x.get('target_sd')] for x in retest['levels']])
    return items


def _validate_retest(connection, origin, source_type, source_id):
    if source_type != origin['source_type']:
        raise ValueError('复测必须使用同一检测方法的完整记录。')
    if _positive_id(source_id) == origin['source_id']:
        raise ValueError('原检测不能关联为自身复测。')
    retest = _snapshot(connection, source_type, source_id)
    if not origin['system_id'] or not retest['system_id']:
        raise ValueError('检测时的仪器或方法资料不完整，暂不能关联复测。请核对原检测和复测的检验记录。')
    if origin['system_id'] != retest['system_id'] or origin['system_identity'] != retest['system_identity']:
        raise ValueError('复测不属于同一仪器和检测系统，请重新选择。')
    if _time(retest['test_time']) <= _time(origin['test_time']):
        raise ValueError('复测时间必须晚于原检测；相同时间不能推定先后。')
    if not origin['levels_complete'] or not retest['levels_complete'] or len(origin['levels']) != len(retest['levels']):
        raise ValueError('复测水平组合不完整或与原检测不一致。')
    if [x['level_id'] for x in origin['levels']] != [x['level_id'] for x in retest['levels']]:
        raise ValueError('复测水平组成与原检测不一致。')
    if len(origin['context_levels']) != len(origin['levels']) or len(retest['context_levels']) != len(retest['levels']):
        raise ValueError('实际材料的水平组成未完整记录，无法关联复测。')
    if any(not row.get('qc_level_id') for row in [*origin['context_levels'], *retest['context_levels']]):
        raise ValueError('检测时使用的质控水平资料不完整，暂不能关联复测。请核对各水平的质控品和批号。')
    if not retest['evaluation_complete'] or retest['classification'] is None or retest['phase'] != 'formal':
        raise ValueError('复测的正式判读证据不完整，请先核对。')
    return retest, _differences(origin, retest)


def list_retest_candidates(event_id):
    with _reader() as connection:
        event = _get_event(connection, event_id)
        origin, candidates = event['origin_snapshot'], []
        table = SOURCE_TABLES[event['source_type']][0]
        for row in connection.execute(f'SELECT id FROM {table} ORDER BY test_time,id'):
            try:
                snapshot, differences = _validate_retest(connection, origin, event['source_type'], row['id'])
            except ValueError:
                continue
            candidates.append({'source_type': snapshot['source_type'], 'source_id': snapshot['source_id'],
                               'test_time': snapshot['test_time'], 'project_name': snapshot['project_name'],
                               'instrument_name': snapshot['instrument_name'], 'classification': snapshot['classification'],
                               'snapshot': snapshot, 'differences': differences,
                               'requires_difference_reason': bool(differences)})
        return candidates


def _normalize_content(content):
    if not isinstance(content, dict):
        raise ValueError('处理资料格式无效，请重新打开。')
    normalized = {key: str(content.get(key) or '').strip() for key in TEXT_FIELDS}
    if normalized['cause_category'] and normalized['cause_category'] not in CAUSE_CATEGORIES:
        raise ValueError('请从现有原因分类中选择；其他原因可在分析中说明。')
    if normalized['patient_impact_assessment'] not in ('', '待评估', '需要评估', '无需评估'):
        raise ValueError('请选择有效的患者影响评估状态。')
    normalized['confirmation_checked'] = content.get('confirmation_checked') is True
    if not isinstance(content.get('attachment_ids', []), (list, tuple)) or not isinstance(content.get('retest_refs', []), (list, tuple)):
        raise ValueError('附件或复测清单格式无效，请重新选择。')
    normalized['attachment_ids'] = sorted(set(str(x) for x in content.get('attachment_ids', [])))
    references = {}
    for value in content.get('retest_refs', []):
        if not isinstance(value, dict):
            raise ValueError('复测记录格式无效，请重新选择。')
        key = (str(value.get('source_type')), _positive_id(value.get('source_id')))
        reason = str(value.get('difference_reason') or '').strip()
        if key in references and references[key]['difference_reason'] != reason:
            raise ValueError('同一复测的差异说明不一致，请重新核对。')
        references[key] = {'source_type': key[0], 'source_id': key[1], 'difference_reason': reason}
    normalized['retest_refs'] = [references[key] for key in sorted(references)]
    for key in ('patient_impact_start', 'patient_impact_end'):
        if normalized[key]:
            normalized[key] = _time(normalized[key]).strftime('%Y-%m-%d %H:%M:%S')
    if normalized['patient_impact_start'] and normalized['patient_impact_end'] and normalized['patient_impact_end'] < normalized['patient_impact_start']:
        raise ValueError('患者影响范围的结束时间不能早于开始时间。')
    return normalized


def save_handling(event_id, expected_revision_no, request_id, action, content, actor_text, change_reason=''):
    event_id, expected_revision_no = _positive_id(event_id), _positive_id(expected_revision_no)
    actor_text, change_reason = _required(actor_text, '本次操作人'), str(change_reason or '').strip()
    content = _normalize_content(content)
    payload = {'operation': 'save', 'event_id': event_id, 'expected_revision_no': expected_revision_no,
               'action': action, 'content': content, 'actor_text': actor_text, 'change_reason': change_reason}
    with database.atomic_write() as connection:
        digest, old = _request(connection, request_id, payload)
        if old:
            return old
        event = _get_event(connection, event_id)
        if event['current_revision_no'] != expected_revision_no:
            raise ValueError('资料已更新，请重新打开核对。')
        transitions = {('pending', 'save_draft'): 'in_progress', ('in_progress', 'save_draft'): 'in_progress',
                       ('in_progress', 'submit'): 'pending_confirmation', ('pending_confirmation', 'confirm'): 'completed',
                       ('pending_confirmation', 'return'): 'in_progress', ('completed', 'revise'): 'in_progress'}
        next_status = transitions.get((event['status'], action))
        if not next_status:
            raise ValueError('当前处理状态不允许此操作，请先退回或登记修订。')
        previous_content = _normalize_content(event['content'])
        if action in ('return', 'revise'):
            _required(change_reason, '退回或修订原因')
        if action == 'confirm':
            confirmation_fields = {'confirmer_text', 'confirmed_at', 'confirmation_checked'}
            if any(content[key] != previous_content[key] for key in content if key not in confirmation_fields):
                raise ValueError('待确认资料不可直接修改，请先退回后再编辑。')
        if action in ('submit', 'confirm'):
            missing = [label for key, label in REQUIRED_SUBMISSION.items() if not content[key]]
            if missing:
                raise ValueError('请补充：' + '、'.join(missing) + '。')
        now = _now()
        if action == 'confirm':
            _required(content['confirmer_text'], '确认人')
            _required(content['confirmed_at'], '确认时间')
            if not content['confirmation_checked']:
                raise ValueError('请明确确认处理效果后再完成。')
            confirmed = _time(content['confirmed_at'])
            if confirmed < _time(event['saved_at']) or confirmed > _time(now):
                raise ValueError('确认时间须在提交待确认之后，且不能晚于当前时间。')
            content['confirmed_at'] = confirmed.strftime('%Y-%m-%d %H:%M:%S')
            content['handled_at'] = event['content'].get('handled_at', event['saved_at'])
        else:
            content.update(confirmer_text='', confirmed_at='', confirmation_checked=False,
                           handled_at=now)
        if action == 'save_draft' and not content['handler_text']:
            content['handler_text'] = actor_text
        old_refs = {(x['source_type'], x['source_id']) for x in previous_content['retest_refs']}
        new_refs = {(x['source_type'], x['source_id']) for x in content['retest_refs']}
        if old_refs - new_refs and not change_reason:
            raise ValueError('移除已关联复测时，请填写变更原因。')
        links = []
        for ref in content['retest_refs']:
            snapshot, differences = _validate_retest(connection, event['origin_snapshot'], ref['source_type'], ref['source_id'])
            if action == 'confirm':
                previous_link = next(x for x in event['retest_refs'] if x['source_type'] == ref['source_type'] and x['source_id'] == ref['source_id'])
                if previous_link['origin_evaluation_id'] != snapshot['origin_evaluation_id']:
                    raise ValueError('复测判读已更新，请先退回核对后重新提交确认。')
            if differences and not ref['difference_reason']:
                raise ValueError('复测的材料批号、参数或未记录资料有差异，请填写差异说明。')
            links.append({**ref, 'snapshot': snapshot, 'differences': differences})
        if content['attachment_ids']:
            from services.out_of_control_attachment_service import validate_attachment_ids
            content['attachment_ids'] = validate_attachment_ids(connection, event_id, content['attachment_ids'])
        revision_no = expected_revision_no + 1
        connection.execute('''INSERT INTO qc_ooc_revisions(event_id,revision_no,previous_revision_no,status,action,
            content_json,saved_by,saved_at,change_reason) VALUES(?,?,?,?,?,?,?,?,?)''',
                           (event_id, revision_no, expected_revision_no, next_status, action, _json(content), actor_text, now, change_reason))
        for link in links:
            snapshot = link['snapshot']
            connection.execute('''INSERT INTO qc_ooc_retests(event_id,revision_no,source_type,source_id,origin_context_id,
                origin_evaluation_id,difference_reason,snapshot_json,differences_json) VALUES(?,?,?,?,?,?,?,?,?)''',
                               (event_id, revision_no, link['source_type'], link['source_id'], snapshot['origin_context_id'],
                                snapshot['origin_evaluation_id'], link['difference_reason'], _json(snapshot), _json(link['differences'])))
        if content['attachment_ids']:
            from services.out_of_control_attachment_service import link_revision_attachments
            link_revision_attachments(connection, event_id, revision_no, content['attachment_ids'])
        if event['status'] != next_status:
            connection.execute('''INSERT INTO qc_ooc_status_history(event_id,revision_no,from_status,to_status,
                actor_text,occurred_at,reason) VALUES(?,?,?,?,?,?,?)''',
                               (event_id, revision_no, event['status'], next_status, actor_text, now, change_reason))
        connection.execute('UPDATE qc_ooc_events SET current_revision_no=? WHERE id=?', (revision_no, event_id))
        _save_request(connection, request_id, digest, event_id, revision_no)
        return _get_event(connection, event_id)


def lookup_events_for_sources(source_type, source_ids):
    if source_type not in SOURCE_TABLES:
        raise ValueError('不支持的检测来源。')
    source_ids = sorted({_positive_id(value) for value in source_ids})
    if not source_ids:
        return []
    with _reader() as connection:
        result = []
        for start in range(0, len(source_ids), 500):
            chunk = source_ids[start:start + 500]
            query = 'SELECT id FROM qc_ooc_events WHERE source_type=? AND source_id IN (' + ','.join('?' for _ in chunk) + ') ORDER BY id'
            result.extend(_get_event(connection, row['id']) for row in connection.execute(query, [source_type, *chunk]))
        return result


def _list_row(snapshot, event=None):
    return {'event_id': event['event_id'] if event else None,
            'candidate_key': f"{snapshot['source_type']}:{snapshot['source_id']}",
            'source_type': snapshot['source_type'], 'source_id': snapshot['source_id'],
            'qc_method': snapshot['qc_method'], 'record_type': snapshot['record_type'],
            'project_id': snapshot['project_id'], 'batch_id': snapshot['batch_id'],
            'project_key': f"{snapshot['qc_method']}:{snapshot['project_id']}",
            'project_name': snapshot['project_name'], 'instrument_id': snapshot['instrument_id'],
            'instrument_name': snapshot['instrument_name'], 'test_time': snapshot['test_time'],
            'original_classification': snapshot['classification'],
            'status': event['status'] if event else 'pending',
            'current_revision_no': event['current_revision_no'] if event else None,
            'last_handled_at': event['saved_at'] if event else None,
            'origin_snapshot': snapshot, 'report_refs': event.get('report_refs', []) if event else []}


def list_pending(start_date=None, end_date=None, project_id=None, instrument_id=None,
                 statuses=None, search='', include_cross_day=True, date_basis='test_time', qc_method=None):
    """Return items within date range, plus earlier unfinished items separately.

    Default excludes completed events. Pass statuses=['completed'] or all four
    statuses for history. count counts distinct items plus cross_day. Missing LJ
    evaluations are returned as verification_gaps, never assumed to be normal.
    """
    if date_basis not in ('test_time', 'last_handled_at'):
        raise ValueError('请选择按检测时间或处理时间查询。')
    if qc_method not in (None, 'lj', 'zscore'):
        raise ValueError('请选择有效质控方法。')
    statuses = set(statuses) if statuses is not None else set(STATUS_LABELS) - {'completed'}
    if not statuses <= set(STATUS_LABELS):
        raise ValueError('处理状态无效。')
    start = str(start_date)[:10] if start_date else None
    end = str(end_date)[:10] if end_date else None
    for value in (start, end):
        if value:
            try:
                datetime.strptime(value, '%Y-%m-%d')
            except ValueError:
                raise ValueError('请选择有效查询日期。') from None
    if start and end and start > end:
        raise ValueError('查询结束日期不能早于开始日期。')
    items, cross_day, gaps = [], [], []
    with _reader() as connection:
        events = {_key['source_type'] + ':' + str(_key['source_id']): _get_event(connection, _key['id'])
                  for _key in connection.execute('SELECT id,source_type,source_id FROM qc_ooc_events')}
        rows = [_list_row(event['origin_snapshot'], event) for event in events.values()]
        for source_type, (table, _, _) in SOURCE_TABLES.items():
            for source_row in connection.execute(f'SELECT id FROM {table} ORDER BY test_time,id'):
                if f"{source_type}:{source_row['id']}" in events:
                    continue
                snapshot = _snapshot(connection, source_type, source_row['id'])
                if snapshot['phase'] == 'building':
                    continue
                if snapshot['phase'] == 'unknown' or snapshot['classification'] is None or not snapshot['levels_complete']:
                    gaps.append(_list_row(snapshot))
                elif snapshot['phase'] == 'formal' and snapshot['classification'] == 'reject':
                    rows.append(_list_row(snapshot))
        item_search_terms = {}
        def search_terms(row):
            config = row['origin_snapshot'].get('config_snapshot') or {}
            identity = config.get('identity') or []
            item_id = config.get('test_item_id') or (identity[3] if len(identity) > 3 else None)
            if item_id not in item_search_terms:
                # Live vocabulary supplements retrieval only. The displayed
                # original names and frozen handling evidence stay untouched.
                item = connection.execute('''SELECT chinese_name,english_name,abbreviation,standard_code
                    FROM md_test_items WHERE id=?''', (item_id,)).fetchone()
                aliases = [r[0] for r in connection.execute('''SELECT alias_text FROM md_aliases
                    WHERE entity_type='test_item' AND entity_id=? AND is_disabled=0''', (item_id,))]
                item_search_terms[item_id] = [*(tuple(item) if item else ()), *aliases]
            return [row['project_name'], row['instrument_name'], row['origin_snapshot'].get('manual_note'),
                    config.get('template_name'), config.get('test_item_name'),
                    config.get('test_item_abbreviation'), *item_search_terms[item_id]]

        def matches(row):
            return ((project_id is None or row['project_id'] == int(project_id))
                    and (qc_method is None or row['qc_method'] == qc_method)
                    and (instrument_id is None or row['instrument_id'] == int(instrument_id))
                    and (not str(search or '').strip() or fuzzy_match(search, *search_terms(row))))
        for row in rows:
            if row['status'] not in statuses or not matches(row):
                continue
            date = str(row.get(date_basis) or '')[:10]
            if date and (not start or date >= start) and (not end or date <= end):
                items.append(row)
            elif include_cross_day and start and date and date < start and row['status'] != 'completed':
                cross_day.append(row)
        gaps = [row for row in gaps if matches(row) and (not start or row['test_time'][:10] >= start) and (not end or row['test_time'][:10] <= end)]
        for group in (items, cross_day, gaps):
            group.sort(key=lambda x: (x.get(date_basis) or '', x['source_type'], x['source_id']), reverse=True)
    return {'items': items, 'cross_day': cross_day, 'verification_gaps': gaps,
            'count': len(items) + len(cross_day), 'total_count': len(items) + len(cross_day),
            'pending_count': sum(row['status'] != 'completed' for row in [*items, *cross_day]),
            'date_basis': date_basis}


def count_pending(**filters):
    return list_pending(**filters)['pending_count']
