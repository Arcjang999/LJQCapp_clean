"""Batch selection, parameter history and one explicit parameter-edit dialog."""
from __future__ import annotations

from datetime import datetime
from functools import partial
from hashlib import sha1
from uuid import uuid4

import pandas as pd
import streamlit as st

from services.cv_service import calculate_cv_percent
from services.project_config_service import QC_METHOD_LABELS
from services.target_profile_edit_service import (
    get_target_profile_context, list_target_profile_batches, save_target_profile_settings,
)
from ui.cv import render_cv_requirement, render_target_cv

MODAL_KEY = 'target_profile_dialog'
_CLEANUP_KEY = 'target_profile_dialog_cleanup'
_FILTERS_KEY = 'target_profile_filters'
SOURCE_LABELS = {'manual': '实验室确认', 'manufacturer': '经实验室确认的厂家赋值',
                 'revision': '已有均值和标准差修订', 'building': '本批质控结果建立'}
_USAGE_LABELS = {'active': '正式使用', 'parallel': '新旧批同时使用', 'ended': '停止使用', 'pending': '待开始使用'}


def _prefix(state):
    return 'target_profile_' + state['token'] + '_'


def _key(state, field, value):
    key = _prefix(state) + field
    if key not in st.session_state:
        st.session_state[key] = value
    return key


def _clear_prefix(prefix):
    for key in list(st.session_state):
        if key.startswith(prefix):
            st.session_state.pop(key, None)


def open_target_profile_dialog(binding_id):
    context = get_target_profile_context(binding_id)
    if not context['editable']:
        raise ValueError(context['read_only_reason'])
    initial = dict(levels=[dict(level_id=row['level_id'],
        mean=float(row['mean']) if row['mean'] is not None else None,
        sd=float(row['sd']) if row['sd'] is not None else None) for row in context['levels']],
        source='revision' if context['current'] else 'manual', effective_at=context['initial_time'],
        evidence='', confirmed_by='', confirmed=False)
    st.session_state[MODAL_KEY] = dict(binding_id=int(binding_id), token=uuid4().hex, context=context,
        fingerprint=context['fingerprint'], initial=initial,
        draft={**initial, 'levels': [dict(row) for row in initial['levels']]}, discard=False)
    st.rerun(scope='app')


def _close():
    state = st.session_state.pop(MODAL_KEY, None)
    if state:
        st.session_state.setdefault(_CLEANUP_KEY, []).append(_prefix(state))
    st.rerun(scope='app')


def _names(context):
    return {row['level_id']: row for row in context['levels']}


def _parameters_table(levels, context):
    names = _names(context)
    return pd.DataFrame([{'浓度水平': names.get(row['level_id'], {}).get('level_name', row['level_id']),
        '浓度编号': names.get(row['level_id'], {}).get('level_code', ''),
        '批号': names.get(row['level_id'], {}).get('lot_no', ''), '均值': row['mean'],
        '标准差': row['sd'], '设定变异系数（%）': calculate_cv_percent(row['mean'], row['sd'])} for row in levels])


def _render_profile(profile, context):
    st.caption(f"V{profile['version_no']}｜生效时间：{profile['effective_at']}｜来源：{SOURCE_LABELS.get(profile['source'], profile['source'])}")
    st.dataframe(_parameters_table(profile['levels'], context), hide_index=True, width='stretch')
    st.write('依据：' + profile['evidence'])
    st.caption('确认人：' + profile['confirmed_by'])


def _select_row(key, ids, state_key):
    rows = st.session_state.get(key, {}).get('selection', {}).get('rows', [])
    st.session_state[state_key] = ids[rows[0]] if rows and 0 <= rows[0] < len(ids) else None


def _table(frame, ids, key, selection_key):
    selected = st.session_state.get(selection_key)
    if selected not in ids:
        st.session_state[selection_key] = None
        selected = None
    st.dataframe(frame, key=key, hide_index=True, width='stretch',
        height=min(330, max(115, 36*len(ids)+38)), selection_mode='single-row',
        selection_default={'selection': {'rows': [ids.index(selected)] if selected in ids else []}},
        on_select=partial(_select_row, key, ids, selection_key))
    return st.session_state.get(selection_key)


def _render_detail(context):
    snapshot = context['snapshot']
    binding_id = context['binding']['id']
    st.subheader(snapshot.get('test_item_name') or '检验项目')
    st.caption(context['template']['template_name'] + ' · ' + snapshot.get('instrument_name', '') + ' · ' + context['config']['config_name'])
    render_cv_requirement(snapshot.get('cv_limit'), snapshot.get('quality_target_source_text', ''), snapshot.get('quality_goal_json'))
    current = context['current']
    st.markdown('**当前生效的均值和标准差**')
    if current:
        _render_profile(current, context)
    else:
        st.info('尚无生效的确认版本。可核对全部水平后确认均值和标准差；按本批质控结果建立的流程继续保留。')
    if not context['editable']:
        st.info(context['read_only_reason'])
    action = '调整均值和标准差' if current else '确认均值和标准差'
    if st.button(action, key='target_profile_open', disabled=not context['editable'], type='primary'):
        try:
            open_target_profile_dialog(binding_id)
        except ValueError as error:
            st.error(str(error))
    st.markdown('**均值和标准差变更记录**')
    profiles = context['profiles']
    if not profiles:
        st.caption('暂无确认记录。')
        return
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    rows = [{'版本': f"V{profile['version_no']}", '生效时间': profile['effective_at'],
             '状态': '当前生效' if current and profile['id'] == current['id'] else '尚未生效' if profile['effective_at'] > now else '历史版本',
             '来源': SOURCE_LABELS.get(profile['source'], profile['source']), '确认人': profile['confirmed_by']}
            for profile in profiles]
    state_key = f'target_profile_selected_version_{binding_id}'
    ids = [profile['id'] for profile in profiles]
    if state_key not in st.session_state:
        st.session_state[state_key] = current['id'] if current else ids[-1]
    fingerprint = sha1(str((binding_id, ids)).encode()).hexdigest()[:12]
    selected = _table(pd.DataFrame(rows), ids, 'target_profile_history_' + fingerprint, state_key)
    if selected is not None:
        st.markdown('**所选版本详情**')
        _render_profile(next(profile for profile in profiles if profile['id'] == selected), context)


def _filter_changed(key):
    st.session_state.setdefault(_FILTERS_KEY, {})[key] = st.session_state.get(key)


def render_target_profile_workspace():
    contexts = list_target_profile_batches()
    if not contexts:
        st.info('确认单水平或多水平的批次设置后，可在此管理均值和标准差。即时法继续按原流程累计质控结果。')
        return
    projects = {context['template']['id']: context['template']['template_name'] for context in contexts}
    for key, default in [('target_profile_search', ''), ('target_profile_project', None)]:
        if key not in st.session_state:
            st.session_state[key] = st.session_state.get(_FILTERS_KEY, {}).get(key, default)
    if st.session_state['target_profile_project'] not in projects:
        st.session_state['target_profile_project'] = None
    notice = st.session_state.pop('target_profile_notice', '')
    if notice:
        st.success(notice)
    left, right = st.columns([2, 1])
    query = left.text_input('搜索检验项目、仪器或批号', key='target_profile_search',
        on_change=_filter_changed, args=('target_profile_search',))
    project_id = right.selectbox('项目', [None] + list(projects), key='target_profile_project',
        placeholder='全部项目',
        format_func=lambda value: '全部项目' if value is None else projects[value],
        on_change=_filter_changed, args=('target_profile_project',))
    st.session_state[_FILTERS_KEY] = dict(target_profile_search=query, target_profile_project=project_id)
    selected_contexts = []
    rows = []
    for context in contexts:
        source, binding = context['snapshot'], context['binding']
        row = {'项目': context['template']['template_name'], '检验项目': source.get('test_item_name', ''),
            '仪器': source.get('instrument_name', ''), '质控方法': QC_METHOD_LABELS[binding['qc_method']],
            '批号': '；'.join(dict.fromkeys(level['lot_no'] for level in context['levels'])),
            '使用状态': _USAGE_LABELS.get(context['state'], context['state']),
            '当前参数': f"V{context['current']['version_no']}" if context['current'] else '尚未确认',
            '可调整': '是' if context['editable'] else '仅供查询'}
        if project_id is not None and context['template']['id'] != project_id:
            continue
        if query.strip() and query.strip().casefold() not in ' '.join(row[field] for field in ('项目', '检验项目', '仪器', '批号')).casefold():
            continue
        selected_contexts.append(context)
        rows.append(row)
    ids = [context['binding']['id'] for context in selected_contexts]
    st.caption(f'共 {len(ids)} 个批次。选择一行查看当前参数及变更记录。')
    key = 'target_profile_batches_' + sha1(str((ids, st.session_state.get('target_profile_table_version', 0))).encode()).hexdigest()[:12]
    selected = _table(pd.DataFrame(rows), ids, key, 'target_profile_selected_binding')
    if selected is not None:
        _render_detail(next(context for context in selected_contexts if context['binding']['id'] == selected))
    elif not rows:
        st.info('没有符合条件的批次，请调整搜索内容或项目。')


def _render_editor(state, current):
    if state['discard']:
        st.warning('本次修改尚未保存，是否放弃？')
        left, right = st.columns(2)
        if left.button('继续编辑', key='target_profile_continue', type='primary'):
            state['discard'] = False
            st.rerun(scope='app')
        if right.button('放弃修改', key='target_profile_discard'):
            _close()
        return
    context, draft = state['context'], state['draft']
    if not current['editable']:
        st.info(current['read_only_reason'])
        if st.button('关闭', key='target_profile_readonly_close'):
            _close()
        return
    if current['fingerprint'] != state['fingerprint']:
        st.warning('此批次的参数或设置已修改。当前输入仍保留，请关闭后重新打开并核对。')
    snapshot = context['snapshot']
    st.caption(context['template']['template_name'] + ' · ' + snapshot.get('test_item_name', '') + ' · ' + context['config']['config_name'])
    if context['profiles']:
        st.caption('已有版本的最后生效时间：' + context['profiles'][-1]['effective_at'] + '。本次生效时间须晚于该时间。')
    draft['source'] = st.selectbox('均值和标准差来源', ['manual', 'manufacturer', 'revision'],
        key=_key(state, 'source', draft['source']), format_func=SOURCE_LABELS.get)
    for index, level in enumerate(context['levels']):
        row = draft['levels'][index]
        label = level['level_name'] + (f"（编号 {level['level_code']}）" if level['level_code'] else '')
        st.markdown(f"**{label}｜批号 {level['lot_no']}**")
        if level['retained_version']:
            st.caption(f"未更换水平的原参数 V{level['retained_version']} 已预填，请核对后确认。")
        left, middle, right = st.columns(3)
        row['mean'] = left.number_input('均值', key=_key(state, f'{index+1}_mean', row['mean']), format='%.6f')
        row['sd'] = middle.number_input('标准差', key=_key(state, f'{index+1}_sd', row['sd']), min_value=0.0, format='%.6f')
        with right:
            render_target_cv(row['mean'], row['sd'], snapshot.get('cv_limit'), input_value_type=snapshot.get('input_value_type', 'raw'))
    draft['effective_at'] = st.datetime_input('生效时间', key=_key(state, 'effective_at', draft['effective_at']))
    draft['evidence'] = st.text_area('均值和标准差依据及调整原因', key=_key(state, 'evidence', draft['evidence']))
    draft['confirmed_by'] = st.text_input('确认人', key=_key(state, 'confirmed_by', draft['confirmed_by']))
    draft['confirmed'] = st.checkbox('已确认全部水平的均值和标准差适用；新参数生效后重新累计连续规则，旧结果保留原参数',
        key=_key(state, 'confirmed', draft['confirmed']))
    cancel, save = st.columns(2)
    if cancel.button('取消', key='target_profile_cancel', width='stretch'):
        if draft != state['initial']:
            state['discard'] = True
            st.rerun(scope='app')
        _close()
    if save.button('保存均值和标准差', key='target_profile_save', type='primary', width='stretch'):
        try:
            profile_id = save_target_profile_settings(state['binding_id'], draft['levels'],
                expected_fingerprint=state['fingerprint'], source=draft['source'], evidence=draft['evidence'],
                confirmed_by=draft['confirmed_by'], effective_at=draft['effective_at'], confirmed=draft['confirmed'])
        except ValueError as error:
            st.error(str(error))
        else:
            st.session_state['target_profile_selected_binding'] = state['binding_id']
            st.session_state[f"target_profile_selected_version_{state['binding_id']}"] = profile_id
            st.session_state['target_profile_table_version'] = int(st.session_state.get('target_profile_table_version', 0)) + 1
            st.session_state['target_profile_notice'] = '均值和标准差已保存，请查看新版本及生效时间。'
            _close()


@st.dialog('均值和标准差', width='large', dismissible=False)
def _render_dialog():
    state = st.session_state.get(MODAL_KEY)
    if not state:
        return
    try:
        current = get_target_profile_context(state['binding_id'])
    except ValueError as error:
        st.error(str(error))
        if st.button('关闭', key='target_profile_missing_close'):
            _close()
        return
    _render_editor(state, current)


def render_pending_target_profile_dialog():
    for prefix in st.session_state.pop(_CLEANUP_KEY, []):
        _clear_prefix(prefix)
    if st.session_state.get(MODAL_KEY):
        _render_dialog()
