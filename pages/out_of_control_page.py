from __future__ import annotations

from copy import deepcopy
from functools import partial
import hashlib

import pandas as pd
import streamlit as st

from services.out_of_control_service import list_pending
from services.search_service import SEARCH_HELP
from ui.out_of_control import STATUS_LABELS, display, navigate_event, render_event_detail, show_error


_FILTER_KEYS = ('ooc_search', 'ooc_statuses', 'ooc_project_filter', 'ooc_instrument_filter',
                'ooc_all_dates', 'ooc_date_basis', 'ooc_start_date', 'ooc_end_date')


def render_out_of_control_page():
    selection = st.session_state.get('ooc_selection')
    if selection:
        render_event_detail(selection)
        return
    if st.session_state.pop('ooc_restore_list_filters', False):
        for key, value in st.session_state.get('ooc_list_filters', {}).items():
            if value is None:
                st.session_state.pop(key, None)
            else:
                st.session_state[key] = deepcopy(value)
    if st.button('返回当前页面', key='ooc_list_back'):
        st.session_state['show_out_of_control_page'] = False
        st.rerun()
    from ui.common import render_module_header
    render_module_header('异常处理',
        '单击记录行首的选择框，再点“处理”查看原结果、填写措施和关联复测。',
        tone='handling',eyebrow='异常跟进')
    a, b = st.columns(2)
    search = a.text_input('查找项目或仪器', key='ooc_search', help=SEARCH_HELP)
    statuses = b.multiselect('处理状态', list(STATUS_LABELS),
        default=['pending', 'in_progress', 'pending_confirmation'], format_func=STATUS_LABELS.get, key='ooc_statuses')
    try:
        available = list_pending(statuses=list(STATUS_LABELS), include_cross_day=False)['items']
    except Exception as exc:
        show_error(exc)
        return
    projects = {r['project_key']: r for r in available if r.get('project_id') is not None}
    instruments = {r['instrument_id']: r for r in available if r.get('instrument_id') is not None}
    a, b = st.columns(2)
    if st.session_state.get('ooc_project_filter') not in [None, *projects]:
        st.session_state['ooc_project_filter'] = None
    project = a.selectbox('检验项目', [None, *projects], key='ooc_project_filter',
        format_func=lambda v: '全部项目' if v is None else f"{projects[v]['project_name']}｜{_method_label(projects[v])}")
    instrument = b.selectbox('仪器', [None, *instruments], key='ooc_instrument_filter',
        format_func=lambda v: '全部仪器' if v is None else f"{instruments[v]['instrument_name']}（{v}）")
    all_dates = st.checkbox('全部日期', value=True, key='ooc_all_dates')
    basis = st.radio('日期按', ['test_time', 'last_handled_at'],
        format_func=lambda v: '检测时间' if v == 'test_time' else '最近处理时间', horizontal=True, key='ooc_date_basis')
    start = end = None
    if not all_dates:
        a, b = st.columns(2)
        start = a.date_input('开始日期', key='ooc_start_date')
        end = b.date_input('结束日期', key='ooc_end_date')
        if end < start:
            st.error('结束日期不能早于开始日期。')
            return
    try:
        selected_project = projects.get(project, {})
        result = list_pending(start_date=start, end_date=end, statuses=statuses or None,
            project_id=selected_project.get('project_id'), qc_method=selected_project.get('qc_method'),
            instrument_id=instrument, search=search, date_basis=basis, include_cross_day=True)
    except Exception as exc:
        show_error(exc)
        return
    st.metric('待处理事项', result.get('pending_count', result['count']))
    _rows(result['items'], 'main')
    if not all_dates and result.get('cross_day'):
        st.markdown('**其他日期尚未完成**')
        _rows(result['cross_day'], 'cross_day')
    if result.get('verification_gaps'):
        st.info(f"另有 {len(result['verification_gaps'])} 条检测缺少可核对的正式判读依据，请查阅原始资料。")


def _select_row(table_key, identities, prefix):
    rows = st.session_state.get(table_key, {}).get('selection', {}).get('rows', [])
    st.session_state[f'ooc_list_selected_{prefix}'] = (
        identities[rows[0]] if rows and 0 <= rows[0] < len(identities) else None)


def _rows(items, prefix):
    selection_key = f'ooc_list_selected_{prefix}'
    options = {(r['source_type'], int(r['source_id'])): r for r in items}
    identities = list(options)
    selected = st.session_state.get(selection_key)
    if selected not in options:
        selected = None
        st.session_state[selection_key] = None
    if not items:
        st.info('当前条件下没有处理事项。')
        return
    fingerprint = hashlib.sha256(repr(identities).encode()).hexdigest()[:12]
    table_key = f'ooc_list_table_{prefix}_{fingerprint}'
    st.caption(f'共 {len(items)} 项。单击行首选择框选中记录，再点下方“处理”。多水平检测按一整次列出。')
    st.dataframe(pd.DataFrame([{'检测时间': r.get('test_time'), '项目': r.get('project_name'),
        '仪器': r.get('instrument_name'), '质控方法': _method_label(r), '原检测结论': display(r.get('original_classification', r.get('classification'))),
        '处理状态': STATUS_LABELS.get(r.get('status'), '待处理'), '最近处理时间': r.get('last_handled_at') or '未处理'}
        for r in items]), hide_index=True, width='stretch', key=table_key,
        selection_mode='single-row',
        selection_default={'selection': {'rows': [identities.index(selected)] if selected else []}},
        on_select=partial(_select_row, table_key, identities, prefix))
    if selected is not None:
        row = options[selected]
        st.caption(f"已选：{row.get('test_time', '')} · {row.get('project_name', '')} · "
                   f"{_method_label(row)} · {STATUS_LABELS.get(row.get('status'), '待处理')}")
    if st.button('处理', key=f'ooc_continue_{prefix}', type='primary', disabled=selected is None):
        row = options[selected]
        st.session_state['ooc_list_filters'] = {
            key: deepcopy(st.session_state[key]) for key in _FILTER_KEYS if key in st.session_state}
        st.session_state['ooc_restore_list_filters'] = True
        navigate_event(*selected, event_id=row.get('event_id'), from_list=True,
            warning=row.get('original_classification', row.get('classification')) in ('warning', '警告'))


def _method_label(row):
    if row.get('qc_method') == 'lj': return '单水平（LJ）'
    return f"多水平法（{len(row.get('origin_snapshot', {}).get('levels', []))}水平）"
