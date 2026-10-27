"""Selected-record tools and one guarded, draft-preserving action dialog."""
from copy import deepcopy
import logging
import sqlite3
from uuid import uuid4

import pandas as pd
from services.time_service import detection_input_max
import streamlit as st

from services.record_maintenance_service import get_record_context, save_record_action
from services.value_type_service import get_input_value_type_label

MODAL_KEY = 'qc_record_dialog'
ACTION_LABELS = {'view': '查看记录', 'edit': '编辑记录', 'keep': '保留记录',
                 'disable': '禁用记录', 'restore': '恢复记录'}


def _other_dialog_open():
    return any(st.session_state.get(key) for key in
        ('qc_transfer_dialog', 'show_instant_transfer_dialog', 'zscore_reference_dialog'))


def open_record_dialog(method, batch_id, record_id, action='view'):
    if st.session_state.get(MODAL_KEY) or _other_dialog_open():
        return
    context = get_record_context(method, record_id, batch_id)
    row = context['record']
    initial = {'reason': '', 'test_time': pd.Timestamp(row['test_time']).to_pydatetime(),
        'operator': str(row.get('operator') or ''), 'manual_note': str(row.get('manual_note') or ''),
        'reagent_lot_changed': bool(row.get('reagent_lot_changed', 0)),
        'value': float(row.get('value') or 0),
        'level_values': {level['level_id']: float(level['raw_value']) for level in row.get('level_results', [])}}
    st.session_state[MODAL_KEY] = {'method': method, 'batch_id': int(batch_id), 'record_id': int(record_id),
        'action': action, 'context': context, 'token': uuid4().hex, 'initial': initial,
        'draft': deepcopy(initial), 'discard': False}
    st.rerun(scope='app')


def render_record_toolbar(method, batch_id, selected_id, *, allow_edit, allow_status, included=None, abnormal=False):
    notice = st.session_state.pop(f'qc_record_notice_{method}_{batch_id}', '')
    if notice:
        st.success(notice)
    busy = bool(st.session_state.get(MODAL_KEY)) or _other_dialog_open()
    unavailable = selected_id is None or busy
    tools = [('查看', 'view', unavailable)]
    # Unavailable phase operations are absent; no grey editor is used as a detail view.
    if allow_edit:
        tools.append(('编辑', 'edit', unavailable))
    if abnormal and method in ('lj', 'zscore'):
        tools.append(('异常处理', 'abnormal', unavailable))
    if allow_status:
        tools.append(('保留', 'keep', unavailable or included is False))
        if included is not False:
            tools.append(('禁用', 'disable', unavailable))
        if included is not True:
            tools.append(('恢复', 'restore', unavailable))
    columns = st.columns(len(tools))
    for column, (label, action, disabled) in zip(columns, tools):
        if column.button(label, key=f'qc_record_{method}_{batch_id}_{action}', disabled=disabled, width='stretch'):
            if action == 'abnormal':
                from ui.out_of_control import navigate_event
                context = get_record_context(method, selected_id, batch_id)
                row = context['record']
                warning = row.get('run_status', row.get('status')) in ('warning', '警告')
                if method == 'lj':
                    from pages.lj_sections import build_lj_workbench_context
                    frame = build_lj_workbench_context(batch_id)['qc_df']
                    current = frame.loc[frame.id == selected_id]
                    warning = not current.empty and current.iloc[0]['status'] == '警告'
                navigate_event('lj_result' if method == 'lj' else 'zscore_run', int(selected_id), warning=warning)
            else:
                try:
                    open_record_dialog(method, batch_id, selected_id, action)
                except ValueError as exc:
                    st.error(str(exc))


def _close():
    state = st.session_state.pop(MODAL_KEY, None)
    if state:
        st.session_state.setdefault('qc_record_cleanup', []).append('record_' + state['token'] + '_')
    st.rerun(scope='app')


def _widget_key(state, field, value):
    key = 'record_' + state['token'] + '_' + field
    if key not in st.session_state:
        st.session_state[key] = value
    return key


def _details(context):
    row, batch = context['record'], context['batch']
    st.caption(f"{batch.get('project_name', '')} · 质控批号 {batch.get('lot_no') or '-'}")
    st.caption(f"检测时间：{pd.Timestamp(row['test_time']).strftime('%Y-%m-%d %H:%M')} · 检测人：{row.get('operator') or '-'}")
    label = get_input_value_type_label(context['input_value_type'])
    if context['method'] == 'zscore':
        labels = {f'Level {i}': level.get('level_name') or f'水平{i}'
                  for i, level in enumerate(context['source'].get('levels', []), 1)}
        st.dataframe(pd.DataFrame([{'水平': labels.get(level['level_id'], level['level_id'].replace('Level ', '水平')),
            label: level['raw_value'], '参与建靶统计': '是' if level.get('is_building_included', 1) else '否'}
            for level in row['level_results']]), hide_index=True, width='stretch')
    else:
        st.metric(label, f"{float(row['value']):.4f}")
    if row.get('manual_note'):
        st.markdown('**备注**')
        st.write(row['manual_note'])


def _history(context):
    if context['history']:
        with st.expander('维护记录'):
            st.dataframe(pd.DataFrame([{'操作': ACTION_LABELS.get(row['action'], '维护'),
                '原因': row['reason'], '时间': row['created_at']} for row in context['history']]),
                hide_index=True, width='stretch')
    from database import read_snapshot
    from ui.traceability import evaluation_tables
    import json
    column = {'lj': 'lj_result_id', 'zscore': 'zscore_run_id', 'instant': 'instant_result_id'}[context['method']]
    with read_snapshot() as connection:
        evaluations = connection.execute('''SELECT e.id,e.reason,e.created_at,e.evaluation_json
            FROM qc_result_evaluations e JOIN qc_result_contexts x ON x.id=e.context_id
            WHERE x.''' + column + '=? ORDER BY e.id', (context['record']['id'],)).fetchall()
        actual = connection.execute('SELECT reagent_lot_no,reagent_expiry_date FROM qc_result_contexts WHERE ' + column + '=?',
            (context['record']['id'],)).fetchone()
    with st.expander('实际批号与判读历史'):
        lot = (actual['reagent_lot_no'] or '未记录') if actual else '未记录'
        expiry = (actual['reagent_expiry_date'] or '-') if actual else '-'
        st.caption(f"实际试剂批号：{lot} · 试剂效期：{expiry}")
        if not evaluations:
            st.caption('该记录未保存判读详情。')
        else:
            labels = {row['id']: ('首次判读' if index == 0 else '复核判读') + ' · ' + row['created_at']
                      for index, row in enumerate(evaluations)}
            selected = st.radio('选择判读记录', list(labels), format_func=labels.get,
                key=f"record_evaluation_{context['method']}_{context['record']['id']}")
            row = next(row for row in evaluations if row['id'] == selected)
            summary, levels = evaluation_tables(json.loads(row['evaluation_json']), context['input_value_type'],
                {f'Level {i}': level.get('level_name', f'水平{i}') for i, level in enumerate(context['source'].get('levels', []), 1)})
            st.dataframe(summary, hide_index=True, width='stretch')
            if not levels.empty:
                st.dataframe(levels, hide_index=True, width='stretch')


@st.dialog('检测记录', width='large', dismissible=False)
def _render_record_dialog():
    state = st.session_state[MODAL_KEY]
    context, draft = state['context'], state['draft']
    action = state['action']
    st.markdown('**' + ACTION_LABELS[action] + '**')
    if state['discard']:
        st.warning('填写的内容尚未保存，是否放弃？')
        cancel, stay = st.columns(2)
        if cancel.button('放弃修改', key='record_discard', width='stretch'):
            _close()
        if stay.button('继续编辑', key='record_continue', width='stretch'):
            state['discard'] = False
            st.rerun()
        return
    _details(context)
    if action == 'view':
        if context['method'] in ('lj', 'zscore') and not context['edit_allowed']:
            st.info(context['edit_reason'])
        _history(context)
        if st.button('关闭', key='record_view_close', width='stretch'):
            _close()
        return
    if action == 'edit':
        if not context['edit_allowed']:
            st.info(context['edit_reason'])
            if st.button('关闭', key='record_locked_close'):
                _close()
            return
        st.divider()
        draft['test_time'] = st.datetime_input('检测时间', key=_widget_key(state, 'test_time', draft['test_time']),
            max_value=detection_input_max(draft['test_time']))
        draft['operator'] = st.text_input('检测人', key=_widget_key(state, 'operator', draft['operator']))
        if context['method'] == 'lj':
            draft['value'] = st.number_input(get_input_value_type_label(context['input_value_type']), format='%.4f',
                key=_widget_key(state, 'value', draft['value']))
            draft['reagent_lot_changed'] = st.checkbox('本次为试剂批号变更点',
                key=_widget_key(state, 'reagent_changed', draft['reagent_lot_changed']))
        else:
            for level, value in draft['level_values'].items():
                label = level.replace('Level ', '水平') + ' ' + get_input_value_type_label(context['input_value_type'])
                draft['level_values'][level] = st.number_input(label, format='%.4f',
                    key=_widget_key(state, 'level_' + level, value))
        draft['manual_note'] = st.text_area('备注（可选）', key=_widget_key(state, 'note', draft['manual_note']))
        st.caption('保存后重新计算本批次，原内容和维护原因会留在维护记录中。')
    elif action == 'disable':
        st.info('禁用后仍可在“已禁用”列表查看原始值；建靶统计和默认质控图将跳过这条记录。')
    elif action == 'restore':
        st.info('恢复后重新纳入统计，并重新计算本批次及离群提示。')
    else:
        st.info('保留后这条记录继续参与统计。请填写保留原因，供以后核对。')
    if context['method'] == 'zscore' and action != 'edit':
        st.caption('本次操作同时应用于这次检测的全部水平。')
    draft['reason'] = st.text_area('维护原因', key=_widget_key(state, 'reason', draft['reason']))
    cancel, save = st.columns(2)
    if cancel.button('取消', key='record_cancel', width='stretch'):
        if draft != state['initial']:
            state['discard'] = True
            st.rerun()
        _close()
    if save.button('保存修改' if action == 'edit' else '确认' + ACTION_LABELS[action].replace('记录', ''),
                   key='record_save', width='stretch', type='primary'):
        try:
            save_record_action(state['method'], state['batch_id'], state['record_id'], action,
                reason=draft['reason'], expected_fingerprint=context['fingerprint'], request_key=state['token'], changes=draft)
        except (ValueError, TypeError) as exc:
            st.error(str(exc))
        except (sqlite3.Error, OSError):
            logging.exception('Record maintenance could not be saved')
            st.error('记录尚未保存，填写内容仍保留。请检查数据保存位置后重试。')
        else:
            st.session_state[f"qc_record_notice_{state['method']}_{state['batch_id']}"] = ACTION_LABELS[action] + '已保存，统计与图表已更新。'
            _close()


def render_pending_record_dialog(method, batch_id):
    for prefix in st.session_state.pop('qc_record_cleanup', []):
        for key in list(st.session_state):
            if key.startswith(prefix):
                st.session_state.pop(key, None)
    state = st.session_state.get(MODAL_KEY)
    # Keep the frozen owner reachable if another session retires the selected batch.
    if state and state['method'] == method:
        _render_record_dialog()
