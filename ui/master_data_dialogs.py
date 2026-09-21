"""Edit and status dialogs for laboratory reference records."""
from __future__ import annotations

from uuid import uuid4

import pandas as pd
import streamlit as st

from services.master_data_service import list_instrument_models, list_manufacturers, list_units

MODAL_KEY = 'master_data_dialog'
_CLEANUP_KEY = 'master_data_dialog_cleanup'
ENTITY_LABELS = {
    'manufacturer': '厂家', 'test_item': '检验项目', 'instrument_model': '仪器型号',
    'lab_instrument': '仪器', 'reagent': '试剂', 'method': '方法学', 'unit': '单位', 'alias': '项目别名',
}
FIELD_LABELS = {
    'display_name': '显示名称', 'legal_name': '法定名称', 'country_or_region': '国家或地区',
    'registration_holder_name': '注册人 / 备案人', 'notes': '备注',
    'chinese_name': '检验项目名称', 'standard_code': '标准代码', 'english_name': '英文名称',
    'abbreviation': '常用缩写', 'category_name': '专业分类', 'specimen_type': '样本类型',
    'default_unit_id': '默认单位', 'manufacturer_id': '厂家', 'generic_name': '通用名称',
    'brand_name': '品牌', 'model': '型号', 'registration_no': '注册证 / 备案编号',
    'device_category_code': '医疗器械分类代码', 'catalog_no': '产品货号',
    'instrument_model_id': '仪器型号', 'asset_code': '资产编号', 'serial_number': '序列号',
    'department_name': '所属科室', 'instrument_group': '仪器组', 'location': '放置位置',
    'trade_name': '商品名称', 'specification': '规格型号', 'applicable_instrument_text': '适用仪器',
    'method_name': '方法学名称', 'method_code': '方法代码', 'method_category': '专业分类',
    'principle': '检测原理', 'symbol': '单位符号', 'unit_name': '单位名称',
    'ucum_code': 'UCUM 代码', 'quantity_kind': '量纲 / 类型',
    'entity_id': '检验项目', 'entity_type': '所属资料', 'alias_text': '别名内容', 'alias_type': '别名类型',
}
_FIELDS = {
    'manufacturer': ('display_name', 'legal_name', 'country_or_region', 'registration_holder_name', 'notes'),
    'test_item': ('chinese_name', 'standard_code', 'abbreviation', 'english_name', 'category_name',
                  'specimen_type', 'default_unit_id', 'notes'),
    'instrument_model': ('manufacturer_id', 'generic_name', 'brand_name', 'model', 'registration_no',
                         'device_category_code', 'catalog_no', 'notes'),
    'lab_instrument': ('instrument_model_id', 'display_name', 'asset_code', 'serial_number',
                       'department_name', 'instrument_group', 'location', 'notes'),
    'reagent': ('manufacturer_id', 'generic_name', 'trade_name', 'specification', 'registration_no',
                'catalog_no', 'applicable_instrument_text', 'notes'),
    'method': ('method_name', 'method_code', 'method_category', 'principle', 'notes'),
    'unit': ('symbol', 'unit_name', 'ucum_code', 'quantity_kind', 'notes'),
    'alias': ('alias_text', 'alias_type'),
}
_REQUIRED = {'display_name', 'chinese_name', 'manufacturer_id', 'generic_name', 'model',
             'instrument_model_id', 'method_name', 'symbol', 'alias_text'}
_RELATIONS = {'manufacturer_id', 'instrument_model_id', 'default_unit_id'}
ALIAS_TYPE_LABELS = {'short_name': '简称', 'english': '英文名称', 'brand': '品牌',
    'lis_code': 'LIS代码', 'historical': '历史名称', 'vendor_text': '厂家文本', 'custom': '自定义'}


def _text(value):
    return '' if value is None or pd.isna(value) else str(value)


def _prefix(state):
    return 'md_dialog_' + state['token'] + '_'


def _key(state, field, value):
    key = _prefix(state) + field
    if key not in st.session_state:
        st.session_state[key] = value
    return key


def _clear_widgets(prefix):
    for key in list(st.session_state):
        if key.startswith(prefix):
            st.session_state.pop(key, None)


def open_master_data_dialog(entity_type, entity_id=None, *, kind='edit', parent_id=None):
    from services.master_data_edit_service import get_master_record_context
    if entity_type not in ENTITY_LABELS or kind not in ('edit', 'status'):
        raise ValueError('请选择要维护的基础资料。')
    if kind == 'status' and entity_id is None:
        raise ValueError('请先选择要停用或恢复的记录。')
    context = get_master_record_context(entity_type, int(entity_id)) if entity_id is not None else None
    record = context['record'] if context else {}
    if entity_type == 'alias':
        if record and record['entity_type'] != 'test_item':
            raise ValueError('此处只维护检验项目的别名，请重新选择。')
        parent_id = record.get('entity_id', parent_id)
        if parent_id is None:
            raise ValueError('请先选择检验项目，再添加别名。')
        parent = get_master_record_context('test_item', int(parent_id))['record']
    else:
        parent = None
    initial = {field: (int(record[field]) if record.get(field) is not None else None)
               if field in _RELATIONS else _text(record.get(field)) for field in _FIELDS[entity_type]}
    if entity_type == 'alias':
        initial.update(entity_type='test_item', entity_id=int(parent_id))
        initial['alias_type'] = record.get('alias_type') or 'custom'
    previous = st.session_state.get(MODAL_KEY)
    if previous:
        st.session_state.setdefault(_CLEANUP_KEY, []).append(_prefix(previous))
    st.session_state[MODAL_KEY] = dict(entity_type=entity_type, entity_id=entity_id, kind=kind,
        token=uuid4().hex, context=context, parent=parent, initial=initial, draft=dict(initial),
        fingerprint=context['fingerprint'] if context else None, discard=False, reason='')
    st.rerun(scope='app')


def _close():
    state = st.session_state.pop(MODAL_KEY, None)
    if state:
        st.session_state.setdefault(_CLEANUP_KEY, []).append(_prefix(state))
    st.rerun(scope='app')


def _saved(state, record_id, message):
    entity = state['entity_type']
    st.session_state['md_selected_' + entity] = int(record_id)
    st.session_state['md_table_version'] = int(st.session_state.get('md_table_version', 0)) + 1
    st.session_state['md_notice'] = message
    st.session_state['md_saved_entity'] = (entity, int(record_id))
    _close()


def _relation_options(field, original):
    if field == 'manufacturer_id':
        records = list_manufacturers(include_disabled=True).to_dict('records')
        label = lambda row: row['display_name']
    elif field == 'instrument_model_id':
        records = list_instrument_models(include_disabled=True).to_dict('records')
        label = lambda row: '｜'.join(_text(row.get(key)) for key in ('manufacturer_name', 'brand_name', 'model') if _text(row.get(key)))
    else:
        records = list_units(include_disabled=True).to_dict('records')
        label = lambda row: row['symbol'] + ('｜' + row['unit_name'] if row.get('unit_name') else '')
    rows = {int(row['id']): row for row in records if not row['is_disabled'] or int(row['id']) == original}
    labels = {key: label(row) + ('（已停用）' if row['is_disabled'] else '') for key, row in rows.items()}
    if original is not None and original not in labels:
        labels[original] = '原记录（无法查询）'
    return [None] + list(labels), labels


def _render_field(state, field, locked):
    draft, initial = state['draft'], state['initial']
    label = FIELD_LABELS[field] + (' *' if field in _REQUIRED else '')
    key = _key(state, field, draft[field])
    if field in _RELATIONS:
        options, labels = _relation_options(field, initial[field])
        # A previously selected active relation can become disabled while this dialog is open.
        # Keep the user's draft visible; the service will reject adopting a disabled record.
        if draft[field] is not None and draft[field] not in options:
            options.append(draft[field])
            labels[draft[field]] = '所选记录已停用或不存在，请重新选择'
        draft[field] = st.selectbox(label, options, key=key, disabled=locked,
            format_func=lambda value: ('不设置默认单位' if field == 'default_unit_id' else '请选择')
            if value is None else labels[value])
    elif field == 'alias_type':
        draft[field] = st.selectbox(label, list(ALIAS_TYPE_LABELS), key=key, disabled=locked,
                                    format_func=ALIAS_TYPE_LABELS.get)
    elif field == 'notes':
        draft[field] = st.text_area(label, key=key, disabled=locked)
    else:
        draft[field] = st.text_input(label, key=key, disabled=locked)


def _record_label(entity, record):
    field = {'manufacturer': 'display_name', 'test_item': 'chinese_name', 'instrument_model': 'model',
        'lab_instrument': 'display_name', 'reagent': 'generic_name', 'method': 'method_name',
        'unit': 'symbol', 'alias': 'alias_text'}[entity]
    return _text(record.get(field))


def _cancel(state):
    if state['draft'] != state['initial']:
        state['discard'] = True
        st.rerun(scope='app')
    _close()


def _render_edit(state, current):
    from services.master_data_edit_service import save_master_record
    entity, record_id = state['entity_type'], state['entity_id']
    if state['discard']:
        st.warning('本次修改尚未保存，是否放弃？')
        left, right = st.columns(2)
        if left.button('继续编辑', key='md_dialog_continue', type='primary', width='stretch'):
            state['discard'] = False
            st.rerun(scope='app')
        if right.button('放弃修改', key='md_dialog_discard', width='stretch'):
            _close()
        return
    readonly = bool(current and current['record']['is_disabled'])
    locked = set(current['locked_fields']) if current else set()
    if readonly:
        st.info('此记录已停用，暂不能编辑。恢复后可继续维护。')
    elif current and current['lock_reason']:
        st.info(current['lock_reason'])
    if current and current['fingerprint'] != state['fingerprint']:
        st.warning('此记录已修改。当前填写内容仍保留，请关闭后重新打开，核对最新资料。')
    if entity == 'alias':
        st.caption('检验项目：' + state['parent']['chinese_name'])
    if entity == 'lab_instrument' and record_id is None:
        st.caption('先选择已有仪器型号；需要新增型号时，请取消后在“仪器型号”中新增。')
    fields = [field for field in _FIELDS[entity] if field != 'notes']
    for start in range(0, len(fields), 2):
        columns = st.columns(2)
        for column, field in zip(columns, fields[start:start+2]):
            with column:
                _render_field(state, field, readonly or field in locked)
    if 'notes' in _FIELDS[entity]:
        _render_field(state, 'notes', readonly or 'notes' in locked)
    left, right = st.columns(2)
    if left.button('关闭' if readonly else '取消', key='md_dialog_cancel', width='stretch'):
        _cancel(state)
    if readonly:
        if right.button('恢复', key='md_dialog_restore', type='primary', width='stretch'):
            open_master_data_dialog(entity, record_id, kind='status')
    elif right.button('保存', key='md_dialog_save', type='primary', width='stretch'):
        try:
            if record_id is None and entity in ('instrument_model', 'reagent') and state['draft']['manufacturer_id'] is None:
                raise ValueError('请选择厂家；如列表中没有，请取消后到“厂家”中新增。')
            values = {field: value for field, value in state['draft'].items()
                      if record_id is None or value != state['initial'][field]}
            saved_id = save_master_record(entity, values, entity_id=record_id,
                                          expected_fingerprint=state['fingerprint'])
        except ValueError as error:
            st.error(str(error))
        else:
            _saved(state, saved_id, ENTITY_LABELS[entity] + '已保存。')


def _render_status(state, current):
    from services.master_data_edit_service import change_master_status
    original = state['context']['record']
    disabled = not bool(original['is_disabled'])
    action = '停用' if disabled else '恢复'
    st.write(f"{action}：{_record_label(state['entity_type'], original)}")
    if current['fingerprint'] != state['fingerprint']:
        st.warning('此记录已修改，请取消后重新打开，核对最新资料。')
    if disabled:
        st.caption('停用后不再供新增时选择，已有项目和检测历史仍保留。')
        state['reason'] = st.text_area('停用原因 *', key=_key(state, 'reason', state['reason']))
    elif original.get('disabled_reason'):
        st.caption('原停用原因：' + original['disabled_reason'])
    left, right = st.columns(2)
    if left.button('取消', key='md_status_cancel', width='stretch'):
        _close()
    if right.button('确认' + action, key='md_status_confirm', type='primary', width='stretch'):
        try:
            change_master_status(state['entity_type'], state['entity_id'], disabled=disabled,
                                 expected_fingerprint=state['fingerprint'], reason=state['reason'])
        except ValueError as error:
            st.error(str(error))
        else:
            _saved(state, state['entity_id'], ENTITY_LABELS[state['entity_type']] + '已' + action + '。')


@st.dialog('基础资料', width='large', dismissible=False)
def _render_dialog():
    from services.master_data_edit_service import get_master_record_context
    state = st.session_state.get(MODAL_KEY)
    if not state:
        return
    title = ('新增' if state['entity_id'] is None else '编辑') if state['kind'] == 'edit' else (
        '恢复' if state['context']['record']['is_disabled'] else '停用')
    st.subheader(title + ENTITY_LABELS[state['entity_type']])
    try:
        current = get_master_record_context(state['entity_type'], state['entity_id']) if state['entity_id'] is not None else None
    except ValueError as error:
        st.error(str(error))
        if st.button('关闭', key='md_missing_close'):
            _close()
        return
    if state['kind'] == 'status':
        _render_status(state, current)
    else:
        _render_edit(state, current)


def render_pending_master_data_dialog():
    for prefix in st.session_state.pop(_CLEANUP_KEY, []):
        _clear_widgets(prefix)
    if st.session_state.get(MODAL_KEY):
        _render_dialog()
