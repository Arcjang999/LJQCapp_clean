"""Read-only lot-event history and explicit, append-only reagent corrections."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from functools import partial
from hashlib import sha1
import json
from uuid import uuid4

import pandas as pd
import streamlit as st
from services.search_service import fuzzy_match, SEARCH_HELP

from database import get_connection
from services.lot_lifecycle_service import timestamp
from services.project_config_service import QC_METHOD_LABELS
from services.reagent_lifecycle_edit_service import (
    get_reagent_workspace_context, get_reagent_correction_context, save_reagent_correction,
)
from ui.traceability import event_history_table

MODAL_KEY = 'reagent_history_dialog'
_CLEANUP_KEY = 'reagent_history_dialog_cleanup'
_FILTERS_KEY = 'reagent_history_filters'


def _prefix(state):
    return 'reagent_history_' + state['token'] + '_'


def _key(state, field, value):
    key = _prefix(state) + field
    if key not in st.session_state:
        st.session_state[key] = value
    return key


def _rows(context):
    events = context['history_events']
    if not events:
        return []
    with get_connection() as connection:
        rows = event_history_table(connection, pd.DataFrame(events)).to_dict('records')
    systems = {system['id']: system for system in context['systems']}
    events_by_id = {event['id']: event for event in events}
    for row in rows:
        event = events_by_id[row['事件编号']]
        row['检验项目、仪器及试剂'] = systems.get(event['system_id'], {}).get('label', '历史检测资料')
        row['时间状态'] = '尚未生效' if event['effective_at'] > datetime.now().strftime('%Y-%m-%d %H:%M:%S') else '已记录'
        if event['event_type'] == 'target':
            row['操作'] = '均值和标准差确认'
    return rows


def _event_summary(context, event):
    subset = {**context, 'history_events': [event]}
    return _rows(subset)[0]


def _verification_detail(verification):
    st.caption(f"验证记录 {verification['id']}｜{'通过' if verification['conclusion'] == 'pass' else '未通过'}｜确认时间：{verification['confirmed_at']}")
    st.write('验证依据：' + str(verification.get('evidence') or '未记录'))
    st.caption('确认人：' + str(verification.get('confirmed_by') or '未记录'))


def _render_event(context, event, *, original=False):
    row = _event_summary(context, event)
    st.markdown('**原事件**' if original else '**所选事件详情**')
    st.caption(f"事件 {event['id']}｜{row['操作']}｜{row['检验项目、仪器及试剂']}")
    st.write(f"调整前：{row['调整前']} → 调整后：{row['调整后']}")
    st.write('生效时间：' + str(event['effective_at']))
    if row['时间状态'] == '尚未生效':
        st.caption('尚未生效，请按所列生效时间核对。')
    st.write('原因或依据：' + str(event.get('reason') or '未记录'))
    st.caption('操作者：' + str(event.get('operator') or '未记录'))
    if event.get('corrects_event_id'):
        st.caption(f"本条更正事件 {event['corrects_event_id']}，原事件继续保留。")
    verification = next((row for row in context['verifications'] if row['id'] == event.get('verification_id')), None)
    if verification:
        _verification_detail(verification)
    elif row['关联验证'] != '—':
        st.caption('关联验证：' + row['关联验证'])


def _filter_changed(key):
    st.session_state.setdefault(_FILTERS_KEY, {})[key] = st.session_state.get(key)


def _select_event(key, ids):
    rows = st.session_state.get(key, {}).get('selection', {}).get('rows', [])
    st.session_state['reagent_history_selected_event'] = ids[rows[0]] if rows and 0 <= rows[0] < len(ids) else None


def _matches(row, event, query, system_id):
    return (system_id is None or event['system_id'] == system_id) and (
        fuzzy_match(query, *row.values()))


def _render_result_history(context):
    bindings = {row['id']: row for row in context['all_bindings']}
    if not bindings:
        return
    labels = {}
    for identifier, binding in bindings.items():
        snapshot = json.loads(binding.get('source_snapshot_json') or '{}')
        lot_numbers = '；'.join(dict.fromkeys(str(level['lot_no']) for level in snapshot.get('levels', []) if level.get('lot_no')))
        labels[identifier] = '｜'.join(str(value) for value in (
            snapshot.get('test_item_name', '检验项目'), snapshot.get('instrument_name', ''),
            lot_numbers or snapshot.get('lot_no', ''), QC_METHOD_LABELS.get(binding['qc_method'], ''), f"批次 {binding['runtime_batch_id']}"))
    key = 'reagent_history_result_binding'
    if key not in st.session_state:
        st.session_state[key] = st.session_state.get(_FILTERS_KEY, {}).get(key)
    if st.session_state[key] not in bindings:
        st.session_state[key] = None
    selected = st.selectbox('查看历史批次（含停用／停止使用）', [None] + list(bindings),
        key=key, placeholder='请选择批次', format_func=lambda value: '请选择批次' if value is None else labels[value],
        on_change=_filter_changed, args=(key,))
    st.session_state.setdefault(_FILTERS_KEY, {})[key] = selected
    if selected is not None:
        # Import here: the page owns the common result-provenance view and imports this workspace.
        from pages.lot_lifecycle_section import render_result_provenance
        binding = bindings[selected]
        render_result_provenance(binding['qc_method'], binding['runtime_batch_id'])


def render_reagent_history_workspace():
    context = get_reagent_workspace_context()
    systems = {row['id']: row for row in context['systems']}
    events = {row['id']: row for row in context['history_events']}
    rows = _rows(context)
    for key, default in [('reagent_history_search', ''), ('reagent_history_system', None)]:
        if key not in st.session_state:
            st.session_state[key] = st.session_state.get(_FILTERS_KEY, {}).get(key, default)
    if st.session_state['reagent_history_system'] not in systems:
        st.session_state['reagent_history_system'] = None
    saved = st.session_state.pop('reagent_history_saved_event', None)
    if saved in events:
        row = next(row for row in rows if row['事件编号'] == saved)
        if not _matches(row, events[saved], st.session_state['reagent_history_search'], st.session_state['reagent_history_system']):
            st.session_state['reagent_history_search'] = ''
            st.session_state['reagent_history_system'] = events[saved]['system_id']
        st.session_state['reagent_history_selected_event'] = saved
    notice = st.session_state.pop('reagent_history_notice', '')
    if notice:
        st.success(notice)
    left, right = st.columns([2, 2])
    query = left.text_input('搜索批号、操作或原因', key='reagent_history_search', help=SEARCH_HELP,
        on_change=_filter_changed, args=('reagent_history_search',))
    system_id = right.selectbox('检验项目、仪器及试剂', [None] + list(systems), key='reagent_history_system',
        placeholder='全部检验项目',
        format_func=lambda value: '全部' if value is None else systems[value]['label'],
        on_change=_filter_changed, args=('reagent_history_system',))
    st.session_state.setdefault(_FILTERS_KEY, {}).update(reagent_history_search=query, reagent_history_system=system_id)
    visible = [row for row in rows if _matches(row, events[row['事件编号']], query, system_id)]
    ids = [row['事件编号'] for row in visible]
    selected = st.session_state.get('reagent_history_selected_event')
    st.caption(f'共 {len(ids)} 条记录。选择一行查看详情；原事件保留，更正另记一条。')
    if visible:
        key = 'reagent_history_table_' + sha1(str((ids, st.session_state.get('reagent_history_table_version', 0))).encode()).hexdigest()[:12]
        columns = ['事件编号', '检验项目、仪器及试剂', '操作', '调整前', '调整后', '生效时间', '时间状态', '确认人']
        st.dataframe(pd.DataFrame(visible)[columns], key=key, hide_index=True, width='stretch',
            height=min(350, max(115, 36 * len(ids) + 38)), selection_mode='single-row',
            selection_default={'selection': {'rows': [ids.index(selected)] if selected in ids else []}},
            on_select=partial(_select_event, key, ids))
        selected = st.session_state.get('reagent_history_selected_event')
    else:
        st.info('暂无符合条件的历史记录。')
    if selected in ids:
        event = events[selected]
        _render_event(context, event)
        children = [row for row in context['history_events'] if row.get('corrects_event_id') == selected]
        if children:
            st.caption('后续更正：' + '、'.join(f"事件 {row['id']}" for row in children))
        if event['event_type'] in ('reagent', 'correction'):
            current = get_reagent_correction_context(selected)
            if not current['can_correct']:
                st.info(current['blocking_reason'])
            if st.button('更正试剂使用记录', key='reagent_history_open', type='primary', disabled=not current['can_correct']):
                try:
                    open_reagent_history_dialog(selected)
                except ValueError as error:
                    st.error(str(error))
    st.caption('更正只追加试剂使用记录，既往检测结果仍保留当时保存的实际批号及判定。')
    _render_result_history(context)


def open_reagent_history_dialog(event_id):
    context = get_reagent_correction_context(event_id)
    if not context['can_correct']:
        raise ValueError(context['blocking_reason'])
    event = context['event']
    active_ids = {row['id'] for row in context['lots']}
    initial = dict(reagent_lot_id=event['next_id'] if event['next_id'] in active_ids else None,
        verification_id=event.get('verification_id'), effective_at=datetime.fromisoformat(event['effective_at']),
        operator='', reason='', confirmed=False)
    if initial['verification_id'] not in _applicable_verifications(context, initial['reagent_lot_id'], initial['effective_at']):
        initial['verification_id'] = None
    st.session_state[MODAL_KEY] = dict(event_id=int(event_id), token=uuid4().hex, context=context,
        fingerprint=context['fingerprint'], initial=deepcopy(initial), draft=deepcopy(initial), discard=False)
    st.rerun(scope='app')


def _close():
    state = st.session_state.pop(MODAL_KEY, None)
    if state:
        st.session_state.setdefault(_CLEANUP_KEY, []).append(_prefix(state))
    st.rerun(scope='app')


def _effective_time(value):
    if value is None:
        return None
    try:
        return timestamp(value)
    except (ValueError, TypeError, OverflowError):
        return None


def _applicable_verifications(context, lot_id, effective_at):
    when = _effective_time(effective_at)
    if when is None:
        return {}
    rows = [row for row in context['verifications'] if row['system_id'] == context['event']['system_id']
        and row.get('reagent_lot_id') == lot_id and row['confirmed_at'] <= when]
    latest = max(rows, key=lambda row: (row['confirmed_at'], row['id'])) if rows else None
    return {latest['id']: latest} if latest and latest['conclusion'] == 'pass' else {}


def _render_editor(state, current):
    if state['discard']:
        st.warning('本次更正尚未保存，是否放弃？')
        left, right = st.columns(2)
        if left.button('继续编辑', key='reagent_history_continue', type='primary'):
            state['discard'] = False
            st.rerun(scope='app')
        if right.button('放弃修改', key='reagent_history_discard'):
            _close()
        return
    if not current['can_correct']:
        st.info(current['blocking_reason'])
        if st.button('关闭', key='reagent_history_readonly_close'):
            _close()
        return
    context, draft = state['context'], state['draft']
    if current['fingerprint'] != state['fingerprint']:
        st.warning('试剂批号或验证、使用记录已修改。当前输入仍保留，请关闭后重新打开并核对。')
    _render_event(context, context['event'], original=True)
    st.markdown('**更正后的试剂使用记录**')
    product_id = context['system']['snapshot']['identity'][7]
    lots = {row['id']: row for row in context['lots'] if row['reagent_id'] == product_id}
    lot_key = _key(state, 'reagent_lot_id', draft['reagent_lot_id'])
    if st.session_state[lot_key] not in lots:
        st.session_state[lot_key] = None
    draft['reagent_lot_id'] = st.selectbox('更正后的试剂批号', [None] + list(lots), key=lot_key,
        placeholder='请选择试剂批号',
        format_func=lambda value: '请选择试剂批号' if value is None else f"{lots[value]['lot_no']}｜效期 {lots[value]['expiry_date']}")
    draft['effective_at'] = st.datetime_input('实际生效时间', value=None, key=_key(state, 'effective_at', draft['effective_at']))
    if _effective_time(draft['effective_at']) is None:
        st.info('请填写有效的生效时间。')
    verifications = _applicable_verifications(context, draft['reagent_lot_id'], draft['effective_at'])
    verification_key = _key(state, 'verification_id', draft['verification_id'])
    if st.session_state[verification_key] not in verifications:
        st.session_state[verification_key] = None
    draft['verification_id'] = st.selectbox('已通过的验证记录', [None] + list(verifications), key=verification_key,
        placeholder='请选择验证记录',
        format_func=lambda value: '请选择验证记录' if value is None else f"验证 {value}｜{verifications[value]['confirmed_at']}｜{verifications[value]['confirmed_by']}")
    if draft['verification_id'] in verifications:
        _verification_detail(verifications[draft['verification_id']])
    elif draft['reagent_lot_id'] is not None and not verifications and _effective_time(draft['effective_at']) is not None:
        st.info('所选批号在该生效时间没有可采用的通过验证，请核对验证结论和时间。')
    draft['operator'] = st.text_input('操作者', key=_key(state, 'operator', draft['operator']))
    draft['reason'] = st.text_area('更正原因', key=_key(state, 'reason', draft['reason']))
    draft['confirmed'] = st.checkbox('已核对原事件、试剂批号、验证记录及生效时间；既往检测结果保留原批号',
        key=_key(state, 'confirmed', draft['confirmed']))
    cancel, save = st.columns(2)
    if cancel.button('取消', key='reagent_history_cancel', width='stretch'):
        if draft != state['initial']:
            state['discard'] = True
            st.rerun(scope='app')
        _close()
    if save.button('保存更正记录', key='reagent_history_save', type='primary', width='stretch'):
        try:
            if _effective_time(draft['effective_at']) is None:
                raise ValueError('请填写有效的生效时间。')
            if not draft['confirmed']:
                raise ValueError('请明确确认本次更正内容。')
            if draft['reagent_lot_id'] is None or draft['verification_id'] is None:
                raise ValueError('请选择试剂批号及适用的通过验证记录。')
            event_id = save_reagent_correction(state['event_id'], dict(draft), expected_fingerprint=state['fingerprint'])
        except (ValueError, TypeError) as error:
            st.error(str(error))
        else:
            st.session_state['reagent_history_selected_event'] = event_id
            st.session_state['reagent_history_saved_event'] = event_id
            st.session_state['reagent_history_table_version'] = int(st.session_state.get('reagent_history_table_version', 0)) + 1
            st.session_state['reagent_history_notice'] = '更正记录已保存。原事件及既往检测结果继续保留。'
            _close()


@st.dialog('更正试剂使用记录', width='large', dismissible=False)
def _render_dialog():
    state = st.session_state.get(MODAL_KEY)
    if not state:
        return
    try:
        current = get_reagent_correction_context(state['event_id'])
    except ValueError as error:
        st.error(str(error))
        if st.button('关闭', key='reagent_history_missing_close'):
            _close()
        return
    _render_editor(state, current)


def render_pending_reagent_history_dialog():
    for prefix in st.session_state.pop(_CLEANUP_KEY, []):
        for key in list(st.session_state):
            if key.startswith(prefix):
                st.session_state.pop(key, None)
    if st.session_state.get(MODAL_KEY):
        _render_dialog()
