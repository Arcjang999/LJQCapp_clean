"""Reversible batch status changes require an explicit, revision-checked dialog."""
import streamlit as st
from database import atomic_write
from services.project_config_service import get_lot_config, set_lot_config_disabled


_PENDING_KEY = 'batch_status_confirmation'


def confirm_batch_status(config_id: int, *, disabled: bool, expected_revision: int):
    """Open one dialog; retain its original revision across ordinary app reruns."""
    st.session_state[_PENDING_KEY] = dict(config_id=int(config_id), disabled=bool(disabled),
                                        expected_revision=int(expected_revision))
    st.rerun(scope='app')


def render_pending_batch_confirmation():
    pending = st.session_state.get(_PENDING_KEY)
    if pending:
        _render_batch_status(**pending)


@st.dialog('确认批次操作', width='small', dismissible=False)
def _render_batch_status(config_id: int, *, disabled: bool, expected_revision: int):
    config = get_lot_config(config_id)
    action = '停用' if disabled else '恢复'
    st.write(f"{action}批次：{config['config_name']}")
    st.caption('检测记录、历次设定的均值和标准差及报告保留。' + ('恢复后请重新确认批次设置。' if not disabled else '停用后不再用于新增检测。'))
    reason = st.text_input('停用原因 *', key=f'batch_status_reason_{config_id}_{disabled}') if disabled else ''
    left, right = st.columns(2)
    if left.button('取消', type='primary', key='batch_status_cancel'):
        st.session_state.pop(_PENDING_KEY, None)
        st.rerun(scope='app')
    if right.button('确认' + action, key='batch_status_confirm'):
        try:
            if disabled and not reason.strip():
                raise ValueError('请填写停用原因。')
            with atomic_write():
                current = get_lot_config(config_id)
                if int(current['revision_no']) != expected_revision:
                    raise ValueError('批次设置已修改，请取消后重新打开，核对最新内容。')
                set_lot_config_disabled(config_id, is_disabled=disabled, reason=reason)
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.session_state.pop(_PENDING_KEY, None)
            st.session_state['v11_selected_lot_config_id'] = None if disabled else config_id
            st.session_state.pop('v11_lot_config_selector', None)
            st.session_state['v11_copy_notice'] = f'批次已{action}。'
            st.rerun(scope='app')
