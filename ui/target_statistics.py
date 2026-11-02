"""Explicit choice of the data range used for later target-parameter updates."""
import streamlit as st
import pandas as pd
from datetime import datetime, timedelta

from ui.required_fields import required_field


STATISTICS_MODE_LABELS = {'monthly': '月度统计', 'cumulative': '累计统计'}
STATISTICS_MODE_HELP = (
    '月度统计：用上一个自然月的有效数据计算均值和SD。'
    '累计统计：用同一质控品批号、同一水平，从开始到最新记录的全部有效数据计算均值和SD。'
    '新统计值经手动确认切换后才启用。'
)


def render_target_statistics_mode(value, *, key, label='均值和SD统计方式', required=True, disabled=False):
    options = list(STATISTICS_MODE_LABELS)
    if key in st.session_state and st.session_state[key] not in [None, *options]:
        st.session_state[key] = None
    current = st.session_state.get(key, value)
    missing = current not in options
    index = None if key in st.session_state else options.index(value) if value in options else None
    with required_field(key, missing=required and missing, message='请选择月度统计或累计统计。'):
        return st.selectbox(label, options, index=index,
            key=key, format_func=STATISTICS_MODE_LABELS.get, placeholder='请选择统计方式',
            help=STATISTICS_MODE_HELP, disabled=disabled)


def _field(state, name, value):
    key = 'target_profile_' + state['token'] + '_' + name
    if key not in st.session_state:
        st.session_state[key] = value
    return key


def statistics_period(source):
    start, end = source.get('period_start'), source.get('period_end')
    if not start or not end:
        return ''
    if source.get('strategy') == 'monthly':
        end = (datetime.fromisoformat(end) - timedelta(microseconds=1)).date().isoformat()
        start = str(start)[:10]
        return start + ' 至 ' + end
    return statistics_time(start) + ' 至 ' + statistics_time(end)


def statistics_time(value):
    return datetime.fromisoformat(str(value)).isoformat(sep=' ', timespec='seconds') if value else ''


def _material_summary(actual):
    code, name = str(actual.get('level_code') or ''), str(actual.get('level_name') or actual['level_id'])
    level = name + (f'（{code}）' if code and code != '/' and code not in name else '')
    return f"{actual.get('catalog_no') or '无货号'}｜{level}｜批号 {actual.get('lot_no') or '未记录'}"


def render_target_statistics_switch(state, current, *, on_cancel, on_saved):
    """Calculate only inside a user-opened dialog; confirmation performs the sole write."""
    from services.target_statistics_service import get_target_statistics_candidate, save_target_statistics_candidate
    context, draft = state['context'], state['draft']
    st.subheader('切换均值和SD')
    st.caption(context['template']['template_name'] + ' · ' + context['snapshot'].get('test_item_name', ''))
    if not current['editable']:
        st.info(current['read_only_reason'])
        if st.button('关闭', key='target_statistics_close'):
            on_cancel()
        return
    draft['strategy'] = render_target_statistics_mode(draft.get('strategy'),
        key=_field(state, 'statistics_mode', draft.get('strategy')))
    strategy = draft['strategy']
    candidate = state.get('candidate')
    if not strategy:
        state.pop('candidate', None)
        candidate = None
    refresh = bool(candidate) and st.button('重新计算本次候选', key='target_statistics_refresh')
    if strategy and (not candidate or candidate['strategy'] != strategy or refresh):
        try:
            candidate = get_target_statistics_candidate(state['binding_id'], strategy)
        except ValueError as error:
            st.error(str(error))
            state.pop('candidate', None)
            candidate = None
        else:
            state['candidate'] = candidate
            state['context'] = context = current
            state['fingerprint'] = current['fingerprint']
            draft['confirmed'] = False
            st.session_state[_field(state, 'confirmed', False)] = False
    if candidate:
        saved = candidate.get('current_profile')
        previous = {row['level_id']: row for row in (saved or {}).get('levels', [])}
        source_levels = {row.get('qc_level_id'): row for row in context['snapshot'].get('levels', [])}
        rows, products = [], {}
        for level in candidate['levels']:
            old = previous.get(level['level_id'], {})
            actual = {**source_levels.get(level.get('qc_level_id'), {}), **level}
            product = (actual.get('catalog_no') or '', actual.get('generic_name') or '', actual.get('manufacturer_name') or '')
            products[product] = dict(zip(('产品货号', '产品名称', '厂家'), product))
            rows.append({'质控品与原厂批号': _material_summary(actual),
                '当前均值': old.get('mean'), '当前SD': old.get('sd'),
                '本次均值': level.get('mean'), '本次SD': level.get('sd'), '本次CV（%）': level.get('cv'),
                '有效点数': level.get('n'), '检测日数': level.get('days'),
                '首条记录': statistics_time(level.get('start')), '末条记录': statistics_time(level.get('end'))})
        st.dataframe(pd.DataFrame(rows), hide_index=True, width='stretch')
        with st.expander('产品名称和厂家'):
            st.dataframe(pd.DataFrame(products.values()), hide_index=True, width='stretch')
        if statistics_period(candidate):
            st.caption('统计范围：' + statistics_period(candidate))
        st.caption(candidate['rule'])
        for message in candidate.get('issues', []):
            st.error(str(message))
        if current['fingerprint'] != state['fingerprint']:
            st.warning('参数、设置或检测结果已修改，请重新计算本次候选。')
    draft['effective_at'] = st.datetime_input('生效时间', key=_field(state, 'effective_at', draft['effective_at']))
    evidence_key = _field(state, 'evidence', draft['evidence'])
    with required_field(evidence_key, missing=not str(st.session_state[evidence_key] or '').strip(),
                        message='请填写本次切换依据。'):
        draft['evidence'] = st.text_area('切换依据', key=evidence_key)
    person_key = _field(state, 'confirmed_by', draft['confirmed_by'])
    with required_field(person_key, missing=not str(st.session_state[person_key] or '').strip(), message='请填写确认人。'):
        draft['confirmed_by'] = st.text_input('确认人', key=person_key)
    draft['confirmed'] = st.checkbox('已核对全部水平的新统计值和生效时间',
        key=_field(state, 'confirmed', draft['confirmed']))
    cancel, save = st.columns(2)
    if cancel.button('取消', key='target_statistics_cancel', width='stretch'):
        on_cancel()
    ready = bool(candidate and candidate.get('ready') and strategy and draft['confirmed']
                 and draft['confirmed_by'].strip() and draft['evidence'].strip())
    if save.button('确认切换', key='target_statistics_save', type='primary', width='stretch', disabled=not ready):
        try:
            profile = save_target_statistics_candidate(state['binding_id'], candidate,
                expected_fingerprint=candidate['fingerprint'], expected_policy_revision=candidate['policy_revision'],
                confirmed_by=draft['confirmed_by'], evidence=draft['evidence'],
                effective_at=draft['effective_at'], confirmed=draft['confirmed'])
        except ValueError as error:
            st.error(str(error))
        else:
            on_saved(profile)
