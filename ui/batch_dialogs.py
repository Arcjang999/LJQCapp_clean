"""Separate dialogs for batch levels and quality requirements."""
from __future__ import annotations

from uuid import uuid4

import pandas as pd
import streamlit as st

from services.batch_edit_service import get_batch_item_context, save_batch_item_settings
from services.material_workflow_service import material_label
from services.project_config_service import TARGET_SOURCE_LABELS
from services.quality_target_service import decode

MODAL_KEY = 'batch_item_dialog'
_CLEANUP_KEY = 'batch_dialog_cleanup_prefixes'


def _number(value):
    return None if value is None or pd.isna(value) else float(value)


def _assignment(row):
    return dict(qc_level_id=int(row['qc_level_id']), target_source=row.get('target_source') or 'building',
                target_mean=_number(row.get('target_mean')), target_sd=_number(row.get('target_sd')),
                target_confirmed=bool(row.get('target_confirmed')), notes=str(row.get('notes') or ''))


def _quality_prefix(item_id):
    return f'quality_lot_{int(item_id)}_'


def _clear_prefix(prefix):
    for key in list(st.session_state):
        if key.startswith(prefix):
            st.session_state.pop(key, None)


def open_batch_item_dialog(item_id, kind='levels'):
    if kind not in ('levels', 'quality'):
        raise ValueError('请选择水平设置或质量目标。')
    context = get_batch_item_context(int(item_id))
    levels = [_assignment(row) for row in context['levels']]
    initial = dict(selected=[row['qc_level_id'] for row in levels],
                   levels={row['qc_level_id']: dict(row) for row in levels},
                   cv_limit=_number(context['item'].get('cv_limit')),
                   cv_source=str(context['item'].get('quality_target_source_text') or ''))
    if kind == 'quality':
        _clear_prefix(_quality_prefix(item_id))
    st.session_state[MODAL_KEY] = dict(item_id=int(item_id), kind=kind, token=uuid4().hex,
        revision=int(context['revision']), context=context, initial=initial,
        draft=dict(selected=list(initial['selected']), levels={key: dict(value) for key, value in initial['levels'].items()},
                   cv_limit=initial['cv_limit'], cv_source=initial['cv_source']), discard=False)
    st.rerun(scope='app')


def _close(message='', *, rerun=True):
    state = st.session_state.pop(MODAL_KEY, None)
    if state:
        prefixes = st.session_state.get(_CLEANUP_KEY, [])
        prefixes.append(_quality_prefix(state['item_id']) if state['kind'] == 'quality' else 'batch_level_' + state['token'])
        st.session_state[_CLEANUP_KEY] = prefixes
        st.session_state['v11_selected_lot_config_id'] = int(state['context']['config']['id'])
    if message:
        st.session_state['v11_copy_notice'] = message
    if rerun:
        st.rerun(scope='app')


def _key(state, suffix, value):
    key = 'batch_level_' + state['token'] + '_' + suffix
    if key not in st.session_state:
        st.session_state[key] = value
    return key


def _shown_assignments(draft):
    return [dict(draft['levels'][level_id], level_order=order)
            for order, level_id in enumerate(draft['selected'], start=1)]


def _dirty(state):
    draft, initial = state['draft'], state['initial']
    if draft['selected'] != initial['selected'] or draft['cv_limit'] != initial['cv_limit'] or draft['cv_source'] != initial['cv_source']:
        return True
    return any(draft['levels'][level_id] != initial['levels'].get(level_id) for level_id in draft['selected'])


def _readonly(context):
    st.info(context['read_only_reason'] or '本批次设置已确认，不能直接修改。')
    rows = []
    for row in context['levels']:
        rows.append({'水平': row['level_order'], '浓度水平': row['level_name'], '浓度编号': row.get('level_code', ''),
            '批号': row.get('lot_no', ''), '效期': row.get('expiry_date', ''),
            '均值和标准差来源': TARGET_SOURCE_LABELS.get(row['target_source'], row['target_source']),
            '均值': row.get('target_mean'), '标准差': row.get('target_sd'),
            '已确认': '是' if row.get('target_confirmed') else '否', '备注': row.get('notes', '')})
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, width='stretch')
    if st.button('关闭', key='batch_levels_close', width='stretch'):
        _close()


def _render_levels(state, current):
    original = state['context']
    item = original['item']
    st.subheader('水平设置 · ' + item['test_item_name'])
    st.caption(original['config']['config_name'])
    if not current['editable']:
        _readonly(current)
        return
    if int(current['revision']) != state['revision']:
        st.warning('批次设置已修改。当前填写内容仍保留，请关闭后重新打开，核对最新设置。')
    if state['discard']:
        st.warning('本次修改尚未保存，是否放弃？')
        left, right = st.columns(2)
        if left.button('继续编辑', type='primary', key='batch_levels_continue'):
            state['discard'] = False
            st.rerun(scope='app')
        if right.button('放弃修改', key='batch_levels_discard'):
            _close()
        return
    draft = state['draft']
    update_cv = not bool(decode(item.get('quality_goal_json')))
    preview_limit = st.session_state.get('batch_level_' + state['token'] + '_cv', draft['cv_limit'])
    available = {int(row.get('qc_level_id') or row['id']): dict(row) for row in original['available_levels']}
    active_ids = set(available)
    for row in original['levels']:
        available.setdefault(int(row['qc_level_id']), dict(row))
    def label(level_id):
        return material_label(available[level_id]) + ('（当前不可选用）' if level_id not in active_ids else '')
    draft['selected'] = st.multiselect('选择各水平使用的质控品', list(available),
        format_func=label, key=_key(state, 'selected', list(draft['selected'])))
    st.caption(f'本检验项目需要 {item["level_count"]} 个水平，按上方选择顺序保存。请同时核对浓度编号、批号和效期。')
    for order, level_id in enumerate(draft['selected'], start=1):
        row = draft['levels'].setdefault(level_id, dict(qc_level_id=level_id, target_source='building',
            target_mean=None, target_sd=None, target_confirmed=False, notes=''))
        st.markdown(f'**水平 {order}：{label(level_id)}**')
        row['target_source'] = st.selectbox('均值和标准差来源', list(TARGET_SOURCE_LABELS),
            format_func=TARGET_SOURCE_LABELS.get, key=_key(state, f'{level_id}_source', row['target_source']))
        left, right, cv_column = st.columns(3)
        row['target_mean'] = left.number_input('均值', format='%.6f',
            key=_key(state, f'{level_id}_mean', row['target_mean']))
        row['target_sd'] = right.number_input('标准差', min_value=0.0, format='%.6f',
            key=_key(state, f'{level_id}_sd', row['target_sd']))
        with cv_column:
            if row['target_source'] == 'building':
                st.metric('设定变异系数（%）', '待建立')
            else:
                from ui.cv import render_target_cv
                render_target_cv(row['target_mean'], row['target_sd'], preview_limit,
                    input_value_type=str(item['input_value_type']), pending=row['target_source'] == 'copied_pending')
        row['target_confirmed'] = st.checkbox('已核对本水平的均值和标准差',
            key=_key(state, f'{level_id}_confirmed', row['target_confirmed']))
        row['notes'] = st.text_area('备注', key=_key(state, f'{level_id}_notes', row['notes']))
        if row['target_source'] == 'building':
            st.caption('按本批质控结果建立均值和标准差。')
    if update_cv:
        from ui.cv import CV_REQUIREMENT_HELP
        draft['cv_limit'] = st.number_input('允许不精密度（CV%，选填）', min_value=0.0, format='%.4f',
            help=CV_REQUIREMENT_HELP, key=_key(state, 'cv', draft['cv_limit']))
        draft['cv_source'] = st.text_input('允许不精密度依据（选填）', key=_key(state, 'cv_source', draft['cv_source']))
    else:
        st.caption('本检验项目已设置质量目标，请通过“质量目标”查看或调整。')
    cancel, save = st.columns(2)
    if cancel.button('取消', key='batch_levels_cancel', width='stretch'):
        if _dirty(state):
            state['discard'] = True
            st.rerun(scope='app')
        _close()
    if save.button('保存水平设置', type='primary', key='batch_levels_save', width='stretch'):
        try:
            save_batch_item_settings(state['item_id'], _shown_assignments(draft),
                expected_revision=state['revision'], cv_limit=draft['cv_limit'],
                source_text=draft['cv_source'], update_cv=update_cv)
        except ValueError as error:
            st.error(str(error))
        else:
            _close('水平设置已保存，请核对质量目标并确认批次设置。')


def _render_quality(state, current):
    st.subheader('质量目标 · ' + state['context']['item']['test_item_name'])
    st.caption(state['context']['config']['config_name'])
    if state['discard']:
        st.warning('本次质量目标修改尚未保存，是否放弃？')
        left, right = st.columns(2)
        if left.button('继续编辑', type='primary', key='batch_quality_continue'):
            state['discard'] = False
            state['restore_quality_inputs'] = True
            st.rerun(scope='app')
        if right.button('放弃修改', key='batch_quality_discard'):
            _close()
        return
    if state.pop('restore_quality_inputs', False):
        for key, value in state.get('quality_values', {}).items():
            st.session_state[key] = value
    if int(current['revision']) != state['revision']:
        st.error('批次设置已修改，请关闭后重新打开质量目标，核对最新设置。')
    else:
        from ui.quality_targets import render_adoption
        try:
            render_adoption('lot', state['item_id'], embedded=True, expected_revision=state['revision'],
                            on_saved=lambda: _close('本批次质量目标已保存，请继续确认批次设置。', rerun=False))
        except ValueError as error:
            st.error(str(error))
        prefix = _quality_prefix(state['item_id'])
        state['quality_values'] = {key: st.session_state[key] for key in st.session_state
                                   if key.startswith(prefix) and key != prefix + 'adopt'}
        state.setdefault('quality_initial', dict(state['quality_values']))
    if st.button('取消' if current['editable'] else '关闭', key='batch_quality_close', width='stretch'):
        if state.get('quality_values', {}) != state.get('quality_initial', {}):
            state['discard'] = True
            st.rerun(scope='app')
        _close()


@st.dialog('批次设置', width='large', dismissible=False)
def _render_dialog():
    state = st.session_state.get(MODAL_KEY)
    if not state:
        return
    try:
        current = get_batch_item_context(state['item_id'])
    except ValueError as error:
        st.error(str(error))
        if st.button('关闭', key='batch_missing_close'):
            _close()
        return
    if state['kind'] == 'levels':
        _render_levels(state, current)
    else:
        _render_quality(state, current)


def render_batch_item_dialog():
    for prefix in st.session_state.pop(_CLEANUP_KEY, []):
        _clear_prefix(prefix)
    if st.session_state.get(MODAL_KEY):
        _render_dialog()
