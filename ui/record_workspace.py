"""One stable record selection shared by each method's workbench tools."""
from functools import partial
from hashlib import sha256

import streamlit as st


def _select_record(table_key, record_ids, selection_key):
    rows = st.session_state.get(table_key, {}).get('selection', {}).get('rows', [])
    st.session_state[selection_key] = (
        record_ids[rows[0]] if rows and 0 <= rows[0] < len(record_ids) else None)


def render_record_selection(display_df, record_ids, *, scope):
    record_ids = [int(value) for value in record_ids]
    if len(record_ids) != len(display_df) or len(set(record_ids)) != len(record_ids):
        raise ValueError('检测记录列表不完整，请刷新后重新查看。')
    selection_key = scope + '_selected'
    selected = st.session_state.get(selection_key)
    if selected not in record_ids:
        selected = None
        st.session_state[selection_key] = None
    if display_df.empty:
        st.info('当前条件下没有检测记录。')
        return None
    # A changed order/content creates a fresh widget with the same selected identity.
    signature = sha256((repr(record_ids) + display_df.to_json(date_format='iso')).encode()).hexdigest()[:12]
    table_key = scope + '_table_' + signature
    st.caption('勾选行首选择框选中一条记录，再点击下方操作。')
    st.dataframe(display_df, hide_index=True, width='stretch',
        height=min(420, max(120, 36 * len(display_df) + 38)), key=table_key,
        selection_mode='single-row',
        selection_default={'selection': {'rows': [record_ids.index(selected)] if selected is not None else []}},
        on_select=partial(_select_record, table_key, record_ids, selection_key))
    return st.session_state.get(selection_key)
