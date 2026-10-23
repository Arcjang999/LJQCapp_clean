"""Register a physical control without leaving the current project or batch draft."""
from uuid import uuid4

import streamlit as st


def open_material_registration(product_id, *, key: str, rerun: bool = True) -> None:
    state_key = key + '_registration'
    if not st.session_state.get(state_key):
        st.session_state[state_key] = dict(product_id=int(product_id), level_id=None,
            record=None, nonce=uuid4().hex)
    if rerun:
        st.rerun(scope='app')


def render_material_registration_shortcut(product_id, *, key: str) -> bool:
    from ui.material_catalog import _material_editor

    state_key = key + '_registration'
    notice = st.session_state.pop(key + '_registration_notice', '')
    if notice:
        st.success(notice)
    state = st.session_state.get(state_key)
    if st.button('登记质控品批号', key=key + '_register', disabled=product_id is None or bool(state)):
        open_material_registration(product_id, key=key)
    if not state:
        return False

    def close():
        st.session_state.pop(state_key, None)
        st.rerun(scope='app')

    def saved(level_id):
        st.session_state[key + '_registration_saved'] = int(level_id)
        st.session_state[key + '_registration_notice'] = '批号已登记。请在各水平的下拉框中选择刚登记的记录；多水平可继续登记其余水平。'
        close()

    with st.container(border=True):
        st.markdown('**登记质控品批号**')
        st.caption('按包装填写浓度水平、批号和效期，点击“保存”后继续当前设置。每个浓度水平分别登记，当前项目和批次填写内容会保留。')
        _material_editor(state, on_saved=saved, on_cancel=close)
    return True
