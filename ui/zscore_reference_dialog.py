"""Edit multilevel manufacturer references without changing confirmed controls."""
from copy import deepcopy
from uuid import uuid4

import pandas as pd
import streamlit as st

from database import atomic_write
from services.lot_lifecycle_service import require_writable
from ui.common import format_optional_float, format_optional_input_value, parse_numeric_input
from zscore_logic import get_zscore_level_targets, upsert_zscore_level_target


REFERENCE_DIALOG_KEY = 'zscore_reference_dialog'
_REFERENCE_FIELDS = ('vendor_reference_mean', 'vendor_reference_sd', 'vendor_reference_source_note')


def _reference_values(profiles, level_ids):
    return {level_id: {
        'mean': format_optional_input_value(profiles[level_id].get('vendor_reference_mean')),
        'sd': format_optional_input_value(profiles[level_id].get('vendor_reference_sd')),
        'source': str(profiles[level_id].get('vendor_reference_source_note') or ''),
    } for level_id in level_ids}


def _profile_identity(profiles, level_ids):
    return {level_id: {field: profiles[level_id].get(field) for field in _REFERENCE_FIELDS}
            for level_id in level_ids}


def open_zscore_reference_dialog(batch_id, template_id, required_n, level_ids, labels):
    profiles = get_zscore_level_targets(batch_id, template_id, required_n=required_n)
    initial = _reference_values(profiles, level_ids)
    st.session_state[REFERENCE_DIALOG_KEY] = dict(
        batch_id=int(batch_id), template_id=template_id, required_n=int(required_n),
        level_ids=list(level_ids), labels=dict(labels), token=uuid4().hex,
        initial=initial, draft=deepcopy(initial), identity=_profile_identity(profiles, level_ids),
        discard=False,
    )
    st.rerun(scope='app')


def _capture(state):
    for level_id in state['level_ids']:
        for field in ('mean', 'sd', 'source'):
            key = f"zscore_reference_{state['token']}_{level_id}_{field}"
            if key in st.session_state:
                state['draft'][level_id][field] = st.session_state[key]


def _close(message=None):
    state = st.session_state.pop(REFERENCE_DIALOG_KEY, None)
    if state:
        st.session_state.setdefault('zscore_reference_cleanup', []).append(f"zscore_reference_{state['token']}_")
    if message:
        st.session_state['zscore_reference_notice'] = message
    st.rerun(scope='app')


@st.dialog('编辑厂家参考值', width='large', dismissible=False)
def _render_reference_dialog():
    state = st.session_state[REFERENCE_DIALOG_KEY]
    _capture(state)
    if state['discard']:
        st.warning('本次厂家参考值尚未保存，是否放弃修改？')
        back, discard = st.columns(2)
        if back.button('继续编辑', key='zscore_reference_continue', type='primary'):
            state['discard'] = False
            st.rerun()
        if discard.button('放弃修改', key='zscore_reference_discard'):
            _close()
        return
    st.caption('厂家参考值仅供核对，不改变本批次已确认的均值、标准差和判读。各项可留空。')
    for level_id in state['level_ids']:
        st.markdown('**' + state['labels'].get(level_id, level_id) + '**')
        left, right = st.columns(2)
        for area, field, label in ((left, 'mean', '参考均值'), (right, 'sd', '参考标准差'), (st, 'source', '来源备注（可选）')):
            key = f"zscore_reference_{state['token']}_{level_id}_{field}"
            if key not in st.session_state:
                st.session_state[key] = state['draft'][level_id][field]
            state['draft'][level_id][field] = area.text_input(label, key=key)
    cancel, save = st.columns(2)
    if cancel.button('取消', key='zscore_reference_cancel'):
        state['discard'] = state['draft'] != state['initial']
        if state['discard']:
            st.rerun()
        _close()
    if save.button('保存厂家参考值', key='zscore_reference_save', type='primary'):
        values, errors = {}, []
        for level_id, row in state['draft'].items():
            mean, _, _ = parse_numeric_input(row['mean'])
            sd, _, _ = parse_numeric_input(row['sd'])
            label = state['labels'].get(level_id, level_id)
            if row['mean'].strip() and mean is None:
                errors.append(label + '：参考均值必须为有效正数。')
            if row['sd'].strip() and sd is None:
                errors.append(label + '：参考标准差必须为有效正数。')
            values[level_id] = dict(vendor_reference_mean=mean, vendor_reference_sd=sd,
                                    vendor_reference_source_note=row['source'].strip() or None)
        if errors:
            for error in errors:
                st.error(error)
            return
        try:
            with atomic_write() as connection:
                require_writable(connection, 'zscore', state['batch_id'])
                current = get_zscore_level_targets(state['batch_id'], state['template_id'], required_n=state['required_n'])
                if _profile_identity(current, state['level_ids']) != state['identity']:
                    raise ValueError('厂家参考资料已修改。当前输入仍保留，请取消后重新打开，核对最新资料。')
                for level_id, payload in values.items():
                    upsert_zscore_level_target(state['batch_id'], level_id, **payload)
        except ValueError as exc:
            st.error(str(exc))
        else:
            _close('厂家参考值已保存。')


def render_zscore_reference_summary(batch_id, template_id, required_n, profiles, level_ids, labels):
    notice = st.session_state.pop('zscore_reference_notice', None)
    if notice:
        st.success(notice)
    with st.expander('厂家参考值（仅作参考）', expanded=False):
        st.dataframe(pd.DataFrame([{
            '水平': labels.get(level_id, level_id),
            '参考均值': format_optional_float(profiles[level_id].get('vendor_reference_mean')),
            '参考标准差': format_optional_float(profiles[level_id].get('vendor_reference_sd')),
            '参考变异系数（%）': format_optional_float(profiles[level_id].get('vendor_reference_cv'), digits=2),
            '来源备注': profiles[level_id].get('vendor_reference_source_note') or '未填写',
        } for level_id in level_ids]), hide_index=True, width='stretch')
        try:
            from database import read_snapshot
            with read_snapshot() as connection:
                require_writable(connection, 'zscore', batch_id, read_only=True)
            writable = True
        except ValueError:
            writable = False
        busy = any(st.session_state.get(key) for key in ('qc_record_dialog', 'qc_transfer_dialog', REFERENCE_DIALOG_KEY))
        if st.button('编辑厂家参考值', key=f'zscore_reference_open_{batch_id}', disabled=not writable or busy):
            open_zscore_reference_dialog(batch_id, template_id, required_n, level_ids, labels)


def render_pending_zscore_reference_dialog():
    for prefix in st.session_state.pop('zscore_reference_cleanup', []):
        for key in list(st.session_state):
            if key.startswith(prefix):
                st.session_state.pop(key, None)
    if st.session_state.get(REFERENCE_DIALOG_KEY):
        _render_reference_dialog()
