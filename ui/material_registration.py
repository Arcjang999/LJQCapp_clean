"""Register a physical control without leaving the current project or batch draft."""
from uuid import uuid4
from copy import deepcopy

import streamlit as st


def open_material_registration(product_id, *, key: str, rerun: bool = True, slot=None,
                               kind='material', preserve_prefixes=()) -> None:
    state_key = key + '_registration'
    if not st.session_state.get(state_key):
        excluded = ('_save', '_cancel', '_confirm', '_register', '_new_product', '_new_lot', '_edit',
                    '_continue', '_discard', '_file', '_add_manufacturer',
                    '_relation_add', '_relation_edit', '_relation_status', '_keep')
        saved = {name: deepcopy(st.session_state[name]) for name in st.session_state
                 if any(name.startswith(prefix) for prefix in preserve_prefixes)
                 and not name.endswith(excluded)}
        st.session_state[state_key] = dict(product_id=int(product_id) if product_id is not None else None,
            level_id=None, record=None, nonce=uuid4().hex, kind=kind, slot=slot,
            parent_values=saved)
    if rerun:
        st.rerun(scope='app')


def render_material_registration_view(key):
    """Use the current dialog as a child form; never open a nested dialog."""
    from ui.material_catalog import _material_editor, _product_editor
    state = st.session_state.get(key + '_registration')
    if not state:
        return False
    st.markdown('<style>[data-testid="stDialog"]>div:has(>[role="dialog"]){width:min(760px,calc(100vw - 32px))!important;max-width:760px!important;}[role="dialog"]{width:100%!important;max-width:100%!important;}</style>', unsafe_allow_html=True)
    st.subheader('新增质控品产品' if state.get('kind') == 'product' else '登记质控品批号')

    def close():
        st.session_state[key + '_registration_restore'] = state.get('parent_values', {})
        st.session_state.pop(key + '_registration', None)
        st.rerun(scope='app')

    def saved(level_id):
        st.session_state[key + '_registration_saved'] = int(level_id)
        st.session_state[key + '_registration_result'] = dict(
            product_id=int(state['product_id']), level_id=int(level_id), slot=state.get('slot'))
        st.session_state[key + '_registration_notice'] = '已登记并选中本水平的质控品。'
        close()

    if state.get('kind') == 'product':
        def product_saved(product_id):
            state.update(product_id=int(product_id), level_id=None, record=None,
                         nonce=uuid4().hex, kind='material')
            st.rerun(scope='app')
        _product_editor(state, on_saved=product_saved, on_cancel=close)
    else:
        _material_editor(state, on_saved=saved, on_cancel=close)
    return True


def render_material_registration_shortcut(product_id, *, key: str) -> bool:
    from ui.material_catalog import _material_editor

    state_key = key + '_registration'
    notice = st.session_state.pop(key + '_registration_notice', '')
    if notice:
        st.success(notice)
    state = st.session_state.get(state_key)
    if st.button('登记质控品批号', key=key + '_register', disabled=product_id is None or bool(state)):
        open_material_registration(product_id, key=key)
    if product_id is None:
        st.caption('登记批号前，请先在上方选择质控品产品。')
    if not state:
        return False

    def close():
        st.session_state.pop(state_key, None)
        st.rerun(scope='app')

    def saved(level_id):
        st.session_state[key + '_registration_saved'] = int(level_id)
        st.session_state[key + '_registration_notice'] = '批号已登记，请在对应水平中选择；其他水平可继续登记。'
        close()

    with st.container(border=True):
        st.markdown('**登记质控品批号**')
        st.info('请先保存或取消本次登记。')
        st.caption('每个浓度水平分别按包装登记。项目和批次已填内容会保留。')
        _material_editor(state, on_saved=saved, on_cancel=close)
    return True
