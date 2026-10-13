"""Apply existing record rules and save the decision evidence in one transaction."""
from hashlib import sha256
import json

import pandas as pd

from database import atomic_write, read_snapshot
from services.lot_lifecycle_service import RESULT_TABLES, require_writable, get_result_edit_availability
from services.value_type_service import compute_legacy_log_value, validate_project_numeric_value


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _raw_record(connection, method, record_id):
    if method not in RESULT_TABLES:
        raise ValueError('请选择有效的质控方法。')
    row = connection.execute(f'SELECT * FROM {RESULT_TABLES[method]} WHERE id=?', (int(record_id),)).fetchone()
    if row is None:
        raise ValueError('检测记录已变化，请返回列表重新选择。')
    record = dict(row)
    if method == 'zscore':
        record['level_results'] = [dict(level) for level in connection.execute(
            'SELECT * FROM zscore_level_results WHERE run_id=? ORDER BY level_id,id', (int(record_id),))]
    return record


def _fingerprint(record):
    # Exclude recalculated statistics; retain all original input and manual decisions.
    keys = ('id', 'batch_id', 'test_time', 'operator', 'value', 'log_value', 'manual_note',
            'reagent_lot_changed', 'is_building_included', 'is_effective', 'manual_status', 'handled_at')
    data = {key: record[key] for key in keys if key in record}
    if 'level_results' in record:
        data['level_results'] = [{key: row.get(key) for key in
            ('id', 'level_id', 'raw_value', 'log_value', 'is_building_included', 'manual_status', 'handled_at')}
            for row in record['level_results']]
    return sha256(_json(data).encode()).hexdigest()


def get_record_context(method, record_id, batch_id=None):
    with read_snapshot() as connection:
        record = _raw_record(connection, method, record_id)
        actual_batch_id = int(record['batch_id'])
        if batch_id is not None and actual_batch_id != int(batch_id):
            raise ValueError('检测记录与当前批次不一致，请重新选择。')
        if method == 'instant':
            from database import get_instant_batch
            batch = dict(get_instant_batch(actual_batch_id))
        elif method == 'zscore':
            from database import get_zscore_batch
            batch = dict(get_zscore_batch(actual_batch_id))
        else:
            from database import get_batch
            batch = dict(get_batch(actual_batch_id))
        source = connection.execute('''SELECT config_snapshot_json FROM qc_result_contexts
            WHERE ''' + {'lj': 'lj_result_id', 'zscore': 'zscore_run_id', 'instant': 'instant_result_id'}[method] + '=?',
            (int(record_id),)).fetchone()
        snapshot = json.loads(source[0]) if source else {}
        history = [dict(row) for row in connection.execute('''SELECT action,reason,created_at
            FROM qc_record_maintenance_events WHERE qc_method=? AND record_id=? ORDER BY id DESC''',
            (method, int(record_id)))]
        availability = get_result_edit_availability(method, int(record_id))
        if method == 'zscore' and availability['allowed']:
            from zscore_logic import get_zscore_runs
            run = next((item for item in get_zscore_runs(actual_batch_id)
                        if int(item.get('run_id', item['id'])) == int(record_id)), None)
            if run and run.get('is_locked_for_maintenance'):
                availability = {'allowed': False, 'reason': '该批次已完成建靶，原始检测值仅供查询，不能再编辑。'}
        return {'method': method, 'record': record, 'batch': batch, 'source': snapshot,
            'input_value_type': snapshot.get('input_value_type') or batch.get('input_value_type') or 'raw',
            'fingerprint': _fingerprint(record), 'history': history,
            'edit_allowed': availability['allowed'], 'edit_reason': availability['reason']}


def save_record_action(method, batch_id, record_id, action, *, reason, expected_fingerprint, request_key, changes=None):
    if action not in ('edit', 'keep', 'disable', 'restore'):
        raise ValueError('请选择有效的记录操作。')
    reason = str(reason or '').strip()
    if not reason:
        raise ValueError('请填写维护原因。')
    if not request_key:
        raise ValueError('请重新打开维护窗口。')
    with atomic_write() as connection:
        previous = connection.execute('SELECT * FROM qc_record_maintenance_events WHERE request_key=?', (request_key,)).fetchone()
        if previous:
            if (previous['qc_method'], previous['batch_id'], previous['record_id'], previous['action']) != (
                method, int(batch_id), int(record_id), action):
                raise ValueError('当前选择的检测记录已改变，请返回记录列表重新选择。')
            return int(previous['id'])
        before = _raw_record(connection, method, record_id)
        if int(before['batch_id']) != int(batch_id):
            raise ValueError('检测记录与当前批次不一致，请重新选择。')
        if _fingerprint(before) != expected_fingerprint:
            raise ValueError('检测记录已被修改，请取消并重新打开，核对最新内容后保存。')
        require_writable(connection, method, int(batch_id))
        if action == 'edit':
            _edit_record(method, record_id, changes or {})
        else:
            if method == 'lj':
                from qc_logic import keep_lj_building_result, disable_lj_building_result, restore_lj_building_result
                actions = {'keep': keep_lj_building_result, 'disable': disable_lj_building_result, 'restore': restore_lj_building_result}
            elif method == 'zscore':
                from zscore_logic import keep_zscore_building_run, disable_zscore_building_run, restore_zscore_building_run
                actions = {'keep': keep_zscore_building_run, 'disable': disable_zscore_building_run, 'restore': restore_zscore_building_run}
            elif method == 'instant':
                from services.instant_service import keep_instant_result, disable_instant_result, restore_instant_result
                actions = {'keep': keep_instant_result, 'disable': disable_instant_result, 'restore': restore_instant_result}
            else:
                raise ValueError('请选择有效的质控方法。')
            actions[action](int(record_id))
        after = _raw_record(connection, method, record_id)
        cursor = connection.execute('''INSERT INTO qc_record_maintenance_events
            (request_key,qc_method,batch_id,record_id,action,reason,before_json,after_json)
            VALUES(?,?,?,?,?,?,?,?)''', (request_key, method, int(batch_id), int(record_id), action,
            reason, _json(before), _json(after)))
        return int(cursor.lastrowid)


def _edit_record(method, record_id, changes):
    if method not in ('lj', 'zscore'):
        raise ValueError('该方法仅支持记录状态维护。')
    context = get_record_context(method, record_id)
    if not context['edit_allowed']:
        raise ValueError(context['edit_reason'])
    time = pd.Timestamp(changes.get('test_time'))
    if pd.isna(time):
        raise ValueError('请填写检测时间。')
    operator = str(changes.get('operator') or '').strip()
    if not operator:
        raise ValueError('请填写检测人。')
    value_type = context['input_value_type']
    if method == 'lj':
        value = float(changes.get('value'))
        error = validate_project_numeric_value(value, value_type)
        if error:
            raise ValueError(error)
        from database import update_result
        update_result(int(record_id), time.strftime('%Y-%m-%d %H:%M:%S'), value, operator=operator,
            log_value=compute_legacy_log_value(value, value_type),
            reagent_lot_changed=int(changes.get('reagent_lot_changed', 0)), manual_note=str(changes.get('manual_note') or ''))
    else:
        values = changes.get('level_values') or {}
        required = {row['level_id'] for row in context['record']['level_results']}
        if set(values) != required:
            raise ValueError('请填写本次检测的全部水平。')
        levels = []
        for level in context['record']['level_results']:
            value = float(values[level['level_id']])
            error = validate_project_numeric_value(value, value_type)
            if error:
                raise ValueError(error)
            levels.append({'level_id': level['level_id'], 'raw_value': value,
                'log_value': compute_legacy_log_value(value, value_type)})
        from zscore_logic import update_saved_zscore_run
        update_saved_zscore_run(int(record_id), test_time=time.to_pydatetime(), operator=operator,
            level_results=levels, manual_note=str(changes.get('manual_note') or ''))
