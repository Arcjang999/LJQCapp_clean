"""One stable record selection shared by each method's workbench tools."""
from functools import partial
from hashlib import sha256

import streamlit as st


def style_judgment_columns(frame, columns):
    columns = [column for column in columns if column in frame]
    if not columns:
        return frame
    def style(value):
        text = str(value)
        if '失控' in text:
            return 'background-color: #fdeaea; color: #8f1f28'
        if '警告' in text or '疑似离群' in text or 'Ct提醒' in text:
            return 'background-color: #fff6db; color: #785b00'
        if text in ('在控', '符合质控'):
            return 'background-color: #edf8ef; color: #1d5f2a'
        return ''
    return frame.style.map(style, subset=columns)


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
    status_columns = [column for column in display_df if column in ('判定', '判读', '本次判定结果', '状态')]
    table = style_judgment_columns(display_df, status_columns)
    column_config = {column: st.column_config.TextColumn(column,
        help='绿色为在控；黄色为质控警告或Ct提醒；红色为失控。具体原因见记录详情。') for column in status_columns}
    st.dataframe(table, hide_index=True, width='stretch', column_config=column_config,
        height=min(420, max(120, 36 * len(display_df) + 38)), key=table_key,
        selection_mode='single-row',
        selection_default={'selection': {'rows': [record_ids.index(selected)] if selected is not None else []}},
        on_select=partial(_select_record, table_key, record_ids, selection_key))
    return st.session_state.get(selection_key)
