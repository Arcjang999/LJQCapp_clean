"""QC replacement lists and a single explicit-save dialog for both existing copy routes."""
from copy import deepcopy
from functools import partial
from hashlib import sha1
from uuid import uuid4

import pandas as pd
import streamlit as st

from services.material_workflow_service import config_material_summary, material_label
from services.project_config_service import list_lot_configs, list_project_templates, QC_METHOD_LABELS
from services.qc_replacement_edit_service import get_qc_replacement_context, save_qc_replacement

MODAL_KEY = 'qc_replacement_dialog'
_MODE_LABELS = {'materials': '选择各水平批号', 'lot': '全部水平采用同一批号'}
_STATUS_LABELS = {'draft': '待确认', 'active': '设置已确认', 'disabled': '已停用', 'superseded': '已替代'}


def _select(widget_key, ids):
    selected = st.session_state.get(widget_key, {}).get('selection', {}).get('rows', [])
    st.session_state['qc_replace_selected_config'] = ids[selected[0]] if selected and 0 <= selected[0] < len(ids) else None


def _key(state, field, initial):
    key = 'qcr_' + state['token'] + '_' + field
    if key not in st.session_state:
        st.session_state[key] = initial
    return key


def _capture_draft(state):
    """Keep fields from the previous branch before Streamlit removes its widgets."""
    draft = state['draft']
    prefix = 'qcr_' + state['token'] + '_'
    for field, target in (('items', 'item_ids'), ('target_lot', 'target_lot'), ('name', 'name')):
        if prefix + field in st.session_state:
            draft[target] = deepcopy(st.session_state[prefix + field])
    for item_id, values in draft['selections'].items():
        for position in range(len(values)):
            key = prefix + f'item_{item_id}_{position}'
            if key in st.session_state:
                values[position] = st.session_state[key]


def open_qc_replacement_dialog(config_id):
    context = get_qc_replacement_context(int(config_id))
    selections = {}
    for item in context['items']:
        levels = context['levels'][int(item['id'])]
        selections[int(item['id'])] = [int(levels[index]['qc_level_id']) if index < len(levels) else None
                                       for index in range(int(item['level_count']))]
    initial = dict(mode='materials', item_ids=[int(item['id']) for item in context['items'] if item['is_enabled']],
                   selections=selections, target_lot=None, name='')
    st.session_state[MODAL_KEY] = dict(config_id=int(config_id), token=uuid4().hex, context=context,
        fingerprint=context['fingerprint'], draft=deepcopy(initial), initial=initial, discard=False, review_after_discard=None)
    st.rerun(scope='app')


def _close():
    state = st.session_state.pop(MODAL_KEY, None)
    if state:
        st.session_state.setdefault('qc_replace_cleanup', []).append('qcr_' + state['token'] + '_')
    st.rerun(scope='app')


def _open_config(config_id, *, created=False):
    state = st.session_state.pop(MODAL_KEY, None)
    if state:
        st.session_state.setdefault('qc_replace_cleanup', []).append('qcr_' + state['token'] + '_')
    st.session_state['v11_pending_copied_config_id' if created else 'v11_pending_existing_config_id'] = int(config_id)
    st.rerun(scope='app')


def _display_levels(context):
    return pd.DataFrame([{'检验项目': item['test_item_name'], '质控方法': QC_METHOD_LABELS[item['qc_method']],
        '水平': level['level_order'], '浓度水平': level['level_name'], '浓度编号': level.get('level_code') or '',
        '批号': level.get('lot_no') or '', '效期': level.get('expiry_date') or ''}
        for item in context['items'] for level in context['levels'][int(item['id'])]])


def _render_material_choice(state):
    context, draft = state['context'], state['draft']
    items = {int(item['id']): item for item in context['items']}
    draft['item_ids'] = st.multiselect('需要换批的检验项目', list(items),
        format_func=lambda identifier: items[identifier]['test_item_name'] + ' · ' + QC_METHOD_LABELS[items[identifier]['qc_method']],
        placeholder='请选择检验项目',
        key=_key(state, 'items', draft['item_ids']))
    active = {int(row['id']): row for row in context['materials']}
    original = {int(row['id']): row for row in context['all_materials']}
    selections, preview = {}, []
    for item_id in draft['item_ids']:
        item = items[item_id]
        st.markdown('**' + item['test_item_name'] + ' · ' + QC_METHOD_LABELS[item['qc_method']] + '**')
        previous = context['levels'][item_id]
        values = draft['selections'].get(item_id, [])
        chosen = []
        for position in range(int(item['level_count'])):
            current = values[position] if position < len(values) else None
            old = previous[position] if position < len(previous) else {}
            options = [None, *active]
            if current is not None and current not in options:
                options.append(current)
            def label(value):
                if value is None:
                    return '请选择浓度水平和批号'
                row = active.get(value) or original.get(value)
                return (material_label(row) if row else '原记录无法查询') + ('（已停用，请重新选择）' if value not in active else '')
            value = st.selectbox(f'第 {position+1} 个水平的质控品', options,
                placeholder='请选择浓度水平和批号',
                key=_key(state, f'item_{item_id}_{position}', current), format_func=label)
            chosen.append(value)
            row = active.get(value) or original.get(value) or {}
            preview.append({'检验项目': item['test_item_name'], '水平': position+1,
                '原浓度水平 / 批号': (old.get('level_name') or '未选择') + ' / ' + (old.get('lot_no') or '未记录'),
                '新浓度水平 / 批号': (row.get('level_name') or '未选择') + ' / ' + (row.get('lot_no') or '未记录'),
                '本次更换': '否' if value == old.get('qc_level_id') else '是'})
        draft['selections'][item_id] = chosen
        selections[item_id] = [value for value in chosen if value is not None]
    if preview:
        st.dataframe(pd.DataFrame(preview), hide_index=True, width='stretch')
    if not active:
        st.info('此质控品没有可选批号，请先到基础资料登记浓度水平、浓度编号、批号和效期。')
    st.caption('未换批的水平可保留原批号；已有均值和标准差作为参考，保存后仍需核对。')
    return selections, None


def _render_lot_choice(state):
    context, draft = state['context'], state['draft']
    source = context['config']
    lots = {int(row['id']): row for row in context['lots'] if int(row['id']) != int(source['qc_material_lot_id'])}
    draft['target_lot'] = st.selectbox('新质控品批号', [None, *lots],
        placeholder='请选择新质控品批号',
        key=_key(state, 'target_lot', draft['target_lot']),
        format_func=lambda value: '请选择新质控品批号' if value is None else f"{lots[value]['lot_no']}｜效期 {lots[value]['expiry_date']}")
    existing = next((row for row in context['existing'] if row['qc_material_lot_id'] == draft['target_lot'] and not row['combination_key']), None)
    if existing:
        st.info('此批号已经建立批次，可直接打开继续核对或使用，原有检测记录保留。')
        if st.button('打开已有新批次', key='qc_replace_open_existing'):
            if draft != state['initial']:
                state['discard'] = True
                state['review_after_discard'] = existing['id']
                st.rerun(scope='app')
            _open_config(existing['id'])
    elif draft['target_lot']:
        st.dataframe(pd.DataFrame([{'检验项目': item['test_item_name'], '质控方法': QC_METHOD_LABELS[item['qc_method']],
            '水平数': item['level_count'], '新批次参数': '参考值待核对' if any(level['target_source'] != 'building' for level in context['levels'][int(item['id'])])
                else '重新收集数据建立均值和标准差'} for item in context['items']]), hide_index=True, width='stretch')
    if not lots:
        st.info('请先在基础资料登记新批号及其浓度水平。')
    return None, existing


@st.dialog('更换质控品批次', width='large', dismissible=False)
def _render_dialog():
    state = st.session_state[MODAL_KEY]
    _capture_draft(state)
    context, draft = state['context'], state['draft']
    st.subheader(context['config']['config_name'])
    if state['discard']:
        st.warning('本次填写内容尚未保存，是否放弃？')
        left, right = st.columns(2)
        if left.button('继续编辑', key='qc_replace_continue', type='primary', width='stretch'):
            state['discard'] = False
            state['review_after_discard'] = None
            st.rerun(scope='app')
        if right.button('放弃修改', key='qc_replace_discard', width='stretch'):
            if state.get('review_after_discard') is not None:
                _open_config(state['review_after_discard'])
            _close()
        return
    current = get_qc_replacement_context(state['config_id'])
    if not current['editable']:
        st.info(current['read_only_reason'])
        if st.button('关闭', key='qc_replace_close'):
            _close()
        return
    if current['fingerprint'] != state['fingerprint']:
        st.warning('原批次或质控品资料已修改。当前输入仍保留，请关闭后重新打开，核对最新资料。')
    if not context['config']['material_selection_mode']:
        draft['mode'] = st.radio('更换方式', list(_MODE_LABELS), format_func=_MODE_LABELS.get,
                                horizontal=True, key=_key(state, 'mode', draft['mode']))
    selections, existing = _render_material_choice(state) if draft['mode'] == 'materials' else _render_lot_choice(state)
    draft['name'] = st.text_input('新批次名称（选填）', key=_key(state, 'name', draft['name']))
    st.caption('沿用单位、方法学、试剂和参数建立点数。新批次不带入旧检测记录；保存后核对各水平的均值和标准差、质量目标，再确认批次设置。')
    left, right = st.columns(2)
    if left.button('取消', key='qc_replace_cancel', width='stretch'):
        if draft != state['initial']:
            state['discard'] = True
            st.rerun(scope='app')
        _close()
    if right.button('建立待确认新批次', key='qc_replace_save', disabled=bool(existing), type='primary', width='stretch'):
        try:
            new_id = save_qc_replacement(source_config_id=state['config_id'], expected_fingerprint=state['fingerprint'],
                mode=draft['mode'], selections=selections, target_lot_id=draft['target_lot'], config_name=draft['name'])
        except ValueError as error:
            st.error(str(error))
        else:
            _open_config(new_id, created=True)


def render_pending_qc_replacement_dialog():
    for prefix in st.session_state.pop('qc_replace_cleanup', []):
        for key in list(st.session_state):
            if key.startswith(prefix):
                st.session_state.pop(key, None)
    if st.session_state.get(MODAL_KEY):
        _render_dialog()


def render_qc_replacement_workspace():
    st.caption('选择原批次，查看各水平批号后更换。新批次的均值和标准差、质量目标仍需核对；旧批次及检测记录保留。')
    settings = st.session_state.setdefault('qc_replace_filters', {})
    projects = {int(row['id']): row['template_name'] for row in list_project_templates(include_disabled=True).to_dict('records')}
    for key, default in (('qc_replace_search', ''), ('qc_replace_project', None)):
        if key not in st.session_state:
            st.session_state[key] = settings.get(key, default)
    if st.session_state.get('qc_replace_project') not in [None, *projects]:
        st.session_state['qc_replace_project'] = None
    search, project = st.columns(2)
    query = search.text_input('搜索原批次', key='qc_replace_search', placeholder='批次、检验项目或质控品批号')
    project_id = project.selectbox('项目', [None, *projects], placeholder='全部项目', format_func=lambda value: '全部项目' if value is None else projects[value], key='qc_replace_project')
    settings.update(qc_replace_search=query, qc_replace_project=project_id)
    configs = list_lot_configs(template_id=project_id)
    summaries = {int(row.id): config_material_summary(int(row.id)) for _, row in configs.iterrows()}
    if query.strip() and not configs.empty:
        from services.project_config_service import list_lot_config_items
        texts = configs.apply(lambda row: ' '.join([str(row.config_name), summaries[int(row.id)],
            ' '.join(list_lot_config_items(int(row.id)).test_item_name.astype(str))]), axis=1)
        configs = configs[texts.str.contains(query.strip(), case=False, regex=False)]
    ids = [int(value) for value in configs.id]
    if st.session_state.get('qc_replace_selected_config') not in ids:
        st.session_state['qc_replace_selected_config'] = None
    selected = st.session_state.get('qc_replace_selected_config')
    create, register = st.columns(2)
    if create.button('更换质控品批次', key='qc_replace_open', type='primary', disabled=selected is None, width='stretch'):
        open_qc_replacement_dialog(selected)
    if register.button('登记新质控品批号', key='qc_replace_register', width='stretch'):
        from ui.common import open_global_page
        st.session_state['md_category_tabs'] = '质控品与批号'
        open_global_page('show_master_data_page')
    if configs.empty:
        st.info('没有符合条件的批次，请调整筛选；首次使用请先在“批次管理”建立批次。')
        return
    display = configs[['config_name', 'template_name', 'instrument_name', 'qc_material_name', 'status']].copy()
    display['质控品批号'] = [summaries[int(identifier)] for identifier in configs.id]
    display['status'] = display.status.map(_STATUS_LABELS)
    display.rename(columns={'config_name':'批次名称','template_name':'项目','instrument_name':'仪器','qc_material_name':'质控品','status':'状态'}, inplace=True)
    key = 'qc_replace_table_' + sha1(','.join(map(str, ids)).encode()).hexdigest()[:12]
    st.dataframe(display, hide_index=True, width='stretch', height=min(320, max(115,36*len(ids)+38)),
        key=key, selection_mode='single-row', selection_default={'selection':{'rows':[ids.index(selected)] if selected in ids else []}},
        on_select=partial(_select,key,ids))
    if selected is not None:
        context = get_qc_replacement_context(selected)
        st.markdown('**原批次：' + context['config']['config_name'] + '**')
        detail = _display_levels(context)
        if not detail.empty:
            st.dataframe(detail, hide_index=True, width='stretch')
        if not context['editable']:
            st.caption(context['read_only_reason'])
