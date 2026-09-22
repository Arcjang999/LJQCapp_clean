"""Searchable reference lists with persistent record selection and read-only details."""
from functools import partial
from hashlib import sha1

import pandas as pd
import streamlit as st
from services.search_service import filter_frame, SEARCH_HELP

from database import get_connection
from services import master_data_service as master
from services.master_data_edit_service import EDIT_FIELDS, get_master_record_context
from ui.master_data_dialogs import (
    ALIAS_TYPE_LABELS, ENTITY_LABELS, FIELD_LABELS, open_master_data_dialog,
)

_LISTERS = {
    'manufacturer': master.list_manufacturers, 'test_item': master.list_test_items,
    'instrument_model': master.list_instrument_models, 'lab_instrument': master.list_lab_instruments,
    'reagent': master.list_reagents, 'method': master.list_methods, 'unit': master.list_units,
}
_COLUMNS = {
    'manufacturer': {'display_name': '厂家名称', 'legal_name': '法定名称', 'country_or_region': '国家或地区'},
    'test_item': {'chinese_name': '检验项目', 'standard_code': '标准代码', 'abbreviation': '常用缩写',
                  'category_name': '专业分类', 'specimen_type': '样本类型', 'default_unit': '默认单位'},
    'instrument_model': {'manufacturer_name': '厂家', 'generic_name': '通用名称', 'brand_name': '品牌',
                         'model': '型号', 'registration_no': '注册证 / 备案编号'},
    'lab_instrument': {'display_name': '仪器名称', 'manufacturer_name': '厂家', 'model': '型号',
                       'asset_code': '资产编号', 'department_name': '所属科室', 'location': '放置位置'},
    'reagent': {'generic_name': '试剂名称', 'manufacturer_name': '厂家', 'trade_name': '商品名称',
                'specification': '规格型号', 'registration_no': '注册证 / 备案编号'},
    'method': {'method_name': '方法学名称', 'method_category': '专业分类', 'method_code': '方法代码', 'principle': '检测原理'},
    'unit': {'symbol': '单位符号', 'unit_name': '单位名称', 'ucum_code': 'UCUM 代码', 'quantity_kind': '量纲 / 类型'},
    'alias': {'alias_text': '别名内容', 'alias_type': '别名类型'},
}
_ORIGINS = {'official': '系统收录', 'hospital': '本地新增', 'import': '导入'}


def _text(value):
    return '' if value is None or pd.isna(value) else str(value)


def _rows(entity_type, *, query='', include_disabled=False, parent_id=None):
    if entity_type == 'alias':
        frame = master.list_aliases(entity_type='test_item', entity_id=parent_id, include_disabled=include_disabled)
    elif entity_type == 'test_item':
        return master.list_test_items(query=query, include_disabled=include_disabled)
    else:
        frame = _LISTERS[entity_type](include_disabled=include_disabled)
    if query.strip() and not frame.empty:
        fields = list(_COLUMNS[entity_type])
        frame = filter_frame(frame, query, fields)
    return frame


def _prepare_saved(entity_type, parent_id):
    saved = st.session_state.get('md_saved_entity')
    # Editing the alias used to find a test item must keep its parent visible.
    # Handle this before rendering the parent search widget; the alias list then
    # consumes the normal save notification below.
    if saved and saved[0] == 'alias' and entity_type == 'test_item':
        alias = get_master_record_context('alias', int(saved[1]))['record']
        item_id = int(alias['entity_id'])
        parent = get_master_record_context('test_item', item_id)['record']
        if parent['is_disabled']:
            st.session_state['md_show_disabled_test_item'] = True
        matches = _rows('test_item', query=st.session_state.get('md_search_test_item', ''),
                        include_disabled=st.session_state.get('md_show_disabled_test_item', False))
        if item_id not in matches.id.tolist():
            st.session_state['md_search_test_item'] = ''
        st.session_state['md_selected_test_item'] = item_id
    if not saved or saved[0] != entity_type:
        return
    record_id = int(saved[1])
    context = get_master_record_context(entity_type, record_id)
    if entity_type == 'alias' and context['record']['entity_id'] != parent_id:
        return
    search_key, disabled_key = 'md_search_' + entity_type, 'md_show_disabled_' + entity_type
    if context['record']['is_disabled']:
        st.session_state[disabled_key] = True
    current = _rows(entity_type, query=st.session_state.get(search_key, ''),
                    include_disabled=st.session_state.get(disabled_key, False), parent_id=parent_id)
    if record_id not in current.id.tolist():
        st.session_state[search_key] = ''
    st.session_state['md_selected_' + entity_type] = record_id
    st.session_state.pop('md_saved_entity', None)
    notice = st.session_state.pop('md_notice', None)
    if notice:
        st.success(notice)


def _select_row(widget_key, ids, selection_key):
    rows = st.session_state.get(widget_key, {}).get('selection', {}).get('rows', [])
    st.session_state[selection_key] = ids[rows[0]] if rows and 0 <= rows[0] < len(ids) else None


def _table(entity_type, frame, parent_id):
    ids = [int(value) for value in frame.id]
    selection_key = 'md_selected_' + entity_type
    selected = st.session_state.get(selection_key)
    if selected not in ids:
        st.session_state[selection_key] = None
        selected = None
    version = str(st.session_state.get('md_table_version', 0))
    fingerprint = sha1((str(parent_id) + ':' + ','.join(map(str, ids)) + ':' + version).encode()).hexdigest()[:12]
    key = 'md_table_' + entity_type + '_' + fingerprint
    display = frame[list(_COLUMNS[entity_type])].copy().fillna('')
    if entity_type == 'alias':
        display['alias_type'] = display.alias_type.map(ALIAS_TYPE_LABELS).fillna('其他')
    display.rename(columns=_COLUMNS[entity_type], inplace=True)
    if 'origin_type' in frame:
        display['来源'] = frame.origin_type.map(_ORIGINS).fillna('本地资料')
    display['状态'] = frame.is_disabled.map({0: '启用', 1: '已停用'})
    st.dataframe(display, hide_index=True, width='stretch', height=min(340, max(115, 36 * len(ids) + 38)),
        key=key, selection_mode='single-row',
        selection_default={'selection': {'rows': [ids.index(selected)] if selected in ids else []}},
        on_select=partial(_select_row, key, ids, selection_key))
    return st.session_state.get(selection_key)


def _detail_value(entity_type, field, record, list_row):
    value = record.get(field)
    if field == 'manufacturer_id':
        return list_row.get('manufacturer_name') or '未填写'
    if field == 'default_unit_id':
        return list_row.get('default_unit') or '未设置'
    if field == 'instrument_model_id':
        return '｜'.join(_text(list_row.get(name)) for name in ('manufacturer_name', 'brand_name', 'model')
                         if _text(list_row.get(name))) or '未填写'
    if field == 'alias_type':
        return ALIAS_TYPE_LABELS.get(value, '其他')
    return _text(value) or '未填写'


def _source_records(entity_type, entity_id):
    with get_connection() as connection:
        return pd.read_sql_query('''
            SELECT s.source_name AS 来源名称, s.publisher AS 发布机构,
                   s.version_label AS 版本, r.external_record_id AS 来源代码,
                   s.effective_date AS 生效日期
            FROM md_source_records r JOIN md_sources s ON s.id=r.source_id
            WHERE r.entity_type=? AND r.entity_id=? ORDER BY s.source_code
        ''', connection, params=(entity_type, entity_id)).fillna('')


def _render_detail(entity_type, record_id, list_row):
    context = get_master_record_context(entity_type, record_id)
    record = context['record']
    st.markdown('**所选' + ENTITY_LABELS[entity_type] + '**')
    if record['is_disabled']:
        st.info('此记录已停用。已有项目和检测历史仍保留，需要继续使用时可恢复。')
    elif context['lock_reason'] and entity_type != 'alias':
        st.caption(context['lock_reason'])
    fields = [field for field in FIELD_LABELS if field in EDIT_FIELDS[entity_type]
              and field not in ('entity_id', 'entity_type')]
    details = [{'资料': FIELD_LABELS[field], '内容': _detail_value(entity_type, field, record, list_row)}
               for field in fields]
    if record.get('disabled_reason') and record['is_disabled']:
        details.append({'资料': '停用原因', '内容': record['disabled_reason']})
    st.dataframe(pd.DataFrame(details), hide_index=True, width='stretch')
    sources = _source_records(entity_type, record_id)
    if not sources.empty:
        with st.expander('资料来源'):
            st.dataframe(sources, hide_index=True, width='stretch')
    if entity_type == 'test_item':
        if st.session_state.get('md_alias_parent') != record_id:
            st.session_state['md_alias_parent'] = record_id
            st.session_state['md_selected_alias'] = None
            st.session_state['md_search_alias'] = ''
        st.markdown('**项目别名**')
        st.caption('为所选检验项目维护简称、英文名称或 LIS 代码，便于搜索。')
        render_master_data_workspace('alias', parent_id=record_id, allow_create=not record['is_disabled'])


def render_master_data_workspace(entity_type, *, parent_id=None, allow_create=True):
    """Render one list; callers render the shared pending dialog once after all lists."""
    if entity_type not in _COLUMNS:
        raise ValueError('不支持的基础资料类型。')
    if entity_type == 'alias' and parent_id is None:
        st.info('请先选择检验项目。')
        return
    # Streamlit removes widget state when the user leaves this page. Keep the
    # search context separately so returning to the list restores it.
    settings = st.session_state.setdefault('md_list_settings', {})
    for suffix, default in (('search_', ''), ('show_disabled_', False)):
        key = 'md_' + suffix + entity_type
        if key not in st.session_state:
            st.session_state[key] = settings.get(key, default)
    _prepare_saved(entity_type, parent_id)
    label = ENTITY_LABELS[entity_type]
    left, right = st.columns([3, 1])
    query = left.text_input('搜索' + label, key='md_search_' + entity_type, help=SEARCH_HELP,
                           placeholder='名称、代码或别名' if entity_type == 'test_item' else '名称或代码')
    include_disabled = right.checkbox('显示已停用' + label, key='md_show_disabled_' + entity_type)
    settings['md_search_' + entity_type] = query
    settings['md_show_disabled_' + entity_type] = include_disabled
    frame = _rows(entity_type, query=query, include_disabled=include_disabled, parent_id=parent_id)
    selected = st.session_state.get('md_selected_' + entity_type)
    if selected not in frame.id.tolist():
        st.session_state['md_selected_' + entity_type] = None
        selected = None
    row = frame[frame.id == selected].iloc[0].to_dict() if selected is not None else None
    add, edit, status = st.columns(3)
    if add.button('新增' + label, key='md_create_' + entity_type, type='primary',
                  disabled=not allow_create, width='stretch'):
        open_master_data_dialog(entity_type, parent_id=parent_id)
    if edit.button('编辑' + label, key='md_edit_' + entity_type, disabled=row is None,
                   width='stretch'):
        open_master_data_dialog(entity_type, selected, parent_id=parent_id)
    action = '恢复' if row and row['is_disabled'] else '停用'
    if status.button(action + label, key='md_status_' + entity_type, disabled=row is None,
                     width='stretch'):
        open_master_data_dialog(entity_type, selected, kind='status', parent_id=parent_id)
    st.caption(f'共 {len(frame)} 条。选择一行查看详情。')
    selected = _table(entity_type, frame, parent_id)
    if selected is not None:
        _render_detail(entity_type, int(selected), frame[frame.id == selected].iloc[0].to_dict())
    elif frame.empty:
        st.info('没有符合条件的记录，请调整搜索内容，或新增资料。')
