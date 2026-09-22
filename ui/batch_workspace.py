"""Batch lists and read-only details; edits are saved explicitly in dialogs."""
from functools import partial
from hashlib import sha1
from uuid import uuid4

import pandas as pd
import streamlit as st
from services.search_service import fuzzy_match, SEARCH_HELP

from database import atomic_write
from services.project_config_service import (
    QC_METHOD_LABELS, INPUT_VALUE_TYPE_LABELS, TARGET_SOURCE_LABELS,
    get_lot_config, list_lot_configs, list_project_templates, list_lot_config_items,
    list_config_snapshots, validate_lot_config, activate_lot_config,
)
from services.quality_target_service import decode
from services.quality_review_service import validate_quality_review

_CREATE_KEY = 'batch_workspace_create'
_STATUS_LABELS = {'draft': '待确认', 'active': '设置已确认', 'disabled': '已停用', 'superseded': '已替代'}


def _select_row(widget_key, ids, state_key):
    rows = st.session_state.get(widget_key, {}).get('selection', {}).get('rows', [])
    st.session_state[state_key] = ids[rows[0]] if rows and 0 <= rows[0] < len(ids) else None


def _table(frame, display, state_key, prefix):
    ids = [int(value) for value in frame.id]
    revision = str(st.session_state.get('batch_table_version', 0))
    fingerprint = sha1((','.join(map(str, ids)) + ':' + revision).encode()).hexdigest()[:10]
    key = prefix + '_' + fingerprint
    selected = st.session_state.get(state_key)
    defaults = [ids.index(selected)] if selected in ids else []
    st.dataframe(display, hide_index=True, width='stretch', height=min(320, max(115, 36 * len(ids) + 38)),
        key=key, selection_mode='single-row', selection_default={'selection': {'rows': defaults}},
        on_select=partial(_select_row, key, ids, state_key))
    return st.session_state.get(state_key) if st.session_state.get(state_key) in ids else None


def render_batch_item_detail(item_id):
    from services.batch_edit_service import get_batch_item_context
    from services.cv_service import calculate_cv_percent
    from ui.batch_dialogs import open_batch_item_dialog
    from ui.quality_targets import render_spec, render_review_summary
    ctx = get_batch_item_context(item_id)
    item, levels, binding = ctx['item'], ctx['levels'], ctx['binding']
    st.subheader(item['test_item_name'])
    st.caption(' · '.join(str(value or '未填写') for value in [QC_METHOD_LABELS[item['qc_method']],
        item.get('method_name'), item.get('reagent_name'), item.get('unit_symbol')]))
    edit, quality = st.columns(2)
    if edit.button('编辑水平设置', key=f'batch_edit_levels_{item_id}', disabled=not ctx['editable'], width='stretch'):
        open_batch_item_dialog(item_id)
    if quality.button('质量目标', key=f'batch_edit_quality_{item_id}', disabled=not ctx['editable'], width='stretch'):
        open_batch_item_dialog(item_id, kind='quality')
    if not ctx['editable']:
        st.caption(ctx['read_only_reason'])
    if levels:
        st.dataframe(pd.DataFrame([{ '水平': level['level_order'], '浓度水平': level['level_name'],
            '浓度编号': level.get('level_code') or '', '批号': level.get('lot_no') or '',
            '效期': level.get('expiry_date') or ''} for level in levels]), hide_index=True, width='stretch')
    else:
        st.info('尚未选择质控品水平，请点击“编辑水平设置”。')
    profile = None
    if binding and binding['qc_method'] != 'instant':
        from services.lot_lifecycle_service import target_profile
        profile = target_profile(binding['qc_method'], binding['runtime_batch_id'])
    if profile:
        st.caption(f"当前生效参数 V{profile['version_no']}｜生效时间：{profile['effective_at']}")
        names = {f"Level {int(row['level_order'])}": row['level_name'] for row in levels}
        st.dataframe(pd.DataFrame([{'质控水平': names.get(level['level_id'], level['level_id']),
            '设定均值': level['mean'], 'SD': level['sd'],
            '设定变异系数（%）': calculate_cv_percent(level['mean'], level['sd'])}
            for level in profile['levels']]), hide_index=True, width='stretch')
        st.caption('如需调整控制参数，请前往“批号使用与追溯 → 均值和标准差管理”。')
    elif levels:
        st.dataframe(pd.DataFrame([{'质控水平': level['level_name'],
            '均值和标准差来源': TARGET_SOURCE_LABELS.get(level['target_source'], level['target_source']),
            '设定均值': level['target_mean'], 'SD': level['target_sd'],
            '设定变异系数（%）': calculate_cv_percent(level['target_mean'], level['target_sd']),
            '参数确认': '已确认' if level['target_confirmed'] else '待确认', '备注': level.get('notes') or ''}
            for level in levels]), hide_index=True, width='stretch')
        if item['qc_method'] == 'instant':
            st.caption('即时法按有效结果计算均值和标准差，当前计算结果请在工作台查看。')
        elif any(level['target_source'] == 'building' for level in levels):
            st.caption(f"用本批质控结果建立均值和标准差，所需有效数据点：{item['target_n']}。")
    st.markdown('**本批次质量目标**')
    goal, review = decode(item.get('quality_goal_json')), decode(item.get('quality_review_json'))
    if goal:
        render_spec(goal['spec'])
        if goal.get('levels'):
            st.dataframe(pd.DataFrame([{'水平': row['level_order'], '浓度': row.get('concentration'),
                '类别': row.get('category'), '要求': f"{row['rule']['operator']} {row['rule']['value']:g}{row['rule']['unit']}"}
                for row in goal['levels']]), hide_index=True, width='stretch')
    elif item.get('cv_limit') is not None:
        st.caption(f"允许不精密度（CV）：≤{item['cv_limit']:g}%｜依据：{item.get('quality_target_source_text') or '未填写'}")
    render_review_summary(review)
    if ctx['editable'] and (validate_quality_review('lot', item_id) or goal.get('pending')):
        st.info('请点击“质量目标”，核对并确认本批次使用的要求。')
    elif not goal and not review and item.get('cv_limit') is None:
        st.caption('本批次未设置质量目标。')


def render_batch_detail(config_id):
    config = dict(get_lot_config(config_id))
    items = list_lot_config_items(config_id)
    st.subheader(config['config_name'])
    st.caption(f"{config['template_name']} · {config['instrument_name']} · {config['qc_material_name']} · "
        + _STATUS_LABELS.get(config['status'], config['status']))
    if items.empty:
        st.info('当前批次没有检验项目。')
    else:
        state_key = f'batch_selected_item_{config_id}'
        if st.session_state.get(state_key) not in items.id.tolist():
            st.session_state[state_key] = int(items.iloc[0].id) if len(items) == 1 else None
        display = items[['test_item_name', 'qc_method', 'method_name', 'input_value_type', 'level_count', 'assigned_level_count']].copy()
        display['qc_method'] = display.qc_method.map(QC_METHOD_LABELS)
        display['input_value_type'] = display.input_value_type.map(INPUT_VALUE_TYPE_LABELS)
        display = display.rename(columns={'test_item_name':'检验项目', 'qc_method':'质控方法', 'method_name':'方法学',
            'input_value_type':'检测值类型', 'level_count':'所需水平数', 'assigned_level_count':'已设置水平数'})
        st.caption('选择检验项目，查看质控品、均值和标准差及质量目标。')
        selected = _table(items, display, state_key, f'batch_items_{config_id}')
        if selected is not None:
            render_batch_item_detail(selected)
    st.divider()
    if config['is_disabled']:
        st.info('批次已停用，原设置和检测结果仍保留。')
        if st.button('恢复为待确认批次', key='v11_restore_lot_config_button'):
            from ui.config_confirmation import confirm_batch_status
            confirm_batch_status(config_id, disabled=False, expected_revision=int(config['revision_no']))
    else:
        errors = validate_lot_config(config_id)
        if config['status'] == 'draft':
            if errors:
                st.warning('请完成以下设置：\n\n' + '\n'.join(f'- {error}' for error in errors))
            if st.button('校验并确认批次设置', key=f'v11_activate_lot_config_{config_id}', type='primary', disabled=bool(errors)):
                try:
                    with atomic_write():
                        current = get_lot_config(config_id)
                        if current['revision_no'] != config['revision_no']:
                            raise ValueError('批次设置已修改，请重新核对。')
                        activate_lot_config(config_id)
                except ValueError as error:
                    st.error(str(error))
                else:
                    st.session_state['v11_copy_notice'] = '批次设置已确认。'
                    st.rerun()
        if st.button('停用批次', key=f'v11_disable_lot_config_{config_id}'):
            from ui.config_confirmation import confirm_batch_status
            confirm_batch_status(config_id, disabled=True, expected_revision=int(config['revision_no']))
    snapshots = list_config_snapshots(config_id).copy()
    snapshots['action_type'] = snapshots.action_type.replace({'create':'创建', 'edit':'修改', 'activate':'启用',
        'copy':'复制', 'disable':'停用', 'reactivate':'恢复使用'})
    with st.expander('设置变更记录'):
        st.dataframe(snapshots.rename(columns={'revision_no':'修订号', 'action_type':'动作', 'change_summary':'变更说明',
            'created_by':'操作者', 'created_at':'时间'})[['修订号','动作','变更说明','操作者','时间']], hide_index=True, width='stretch')


def _begin_create(template_id):
    # Old creation widgets also serve the project entry; clear only their drafts.
    for key in list(st.session_state):
        if key.startswith(('create_material_', 'v12_create_cv_', 'v12_create_cv_source_')) or key == 'v11_create_config_name':
            st.session_state.pop(key, None)
    st.session_state[_CREATE_KEY] = {'template_id': template_id, 'token': uuid4().hex, 'discard': False}
    st.rerun()


def _creation_values():
    return {key: value for key, value in st.session_state.items()
        if key.startswith(('create_material_', 'v12_create_cv_', 'v12_create_cv_source_')) or key == 'v11_create_config_name'}


@st.dialog('新增批次', width='large', dismissible=False)
def _render_creation_dialog():
    ctx = st.session_state[_CREATE_KEY]
    if ctx['discard']:
        st.warning('本次填写尚未保存。是否放弃修改？')
        left, right = st.columns(2)
        if left.button('继续编辑', key='batch_create_continue'):
            ctx['discard'] = False
            ctx['restore_draft'] = True
            st.rerun()
        if right.button('放弃修改', key='batch_create_discard'):
            st.session_state.pop(_CREATE_KEY, None)
            st.rerun(scope='app')
        return
    from ui.materials import render_material_config_creation
    from services.project_config_service import get_project_template
    if ctx.pop('restore_draft', False):
        for key, value in ctx.get('draft', {}).items():
            st.session_state[key] = value
    st.caption('项目：' + get_project_template(ctx['template_id'])['template_name'])
    def created(config_id):
        st.session_state.pop(_CREATE_KEY, None)
        st.session_state['v11_selected_lot_config_id'] = config_id
        st.session_state['batch_clear_search_pending'] = True
        st.session_state['v11_copy_notice'] = '批次已建立。请选择检验项目，核对水平设置和质量目标。'
        st.session_state['batch_table_version'] = st.session_state.get('batch_table_version', 0) + 1
        st.rerun(scope='app')
    render_material_config_creation(ctx['template_id'], on_created=created)
    if 'initial' not in ctx:
        ctx['initial'] = _creation_values()
    if st.button('取消', key='batch_create_cancel'):
        if _creation_values() != ctx['initial']:
            ctx['draft'] = _creation_values()
            ctx['discard'] = True
            st.rerun()
        st.session_state.pop(_CREATE_KEY, None)
        st.rerun(scope='app')


def render_pending_batch_dialogs():
    from ui.batch_dialogs import MODAL_KEY, render_batch_item_dialog
    from ui.config_confirmation import render_pending_batch_confirmation
    if st.session_state.get(_CREATE_KEY):
        _render_creation_dialog()
    elif st.session_state.get(MODAL_KEY):
        render_batch_item_dialog()
    else:
        render_pending_batch_confirmation()


def render_batch_workspace():
    if st.session_state.pop('batch_clear_search_pending', False):
        st.session_state['batch_search'] = ''
    notice = st.session_state.pop('v11_copy_notice', '')
    if notice:
        st.success(notice)
    projects = {int(row['id']): row for row in list_project_templates(include_disabled=True).to_dict('records')}
    search, project_column, include_column = st.columns([2, 2, 1.2])
    query = search.text_input('搜索批次', key='batch_search', help=SEARCH_HELP, placeholder='批次名称、批号或检验项目')
    if st.session_state.get('batch_project_filter') not in [None, *projects]:
        st.session_state['batch_project_filter'] = None
    project = project_column.selectbox('项目', [None, *projects], key='batch_project_filter',
        format_func=lambda value: '全部项目' if value is None else projects[value]['template_name'])
    include = include_column.checkbox('显示已停用批次', key='batch_show_disabled')
    can_create = project is not None and not projects[project]['is_disabled'] and projects[project]['status'] == 'active'
    if st.button('新增批次', type='primary', key='batch_create', disabled=not can_create):
        _begin_create(project)
    if not can_create:
        st.caption('新增批次前，请选择一个已确认设置的项目。')
    configs = list_lot_configs(template_id=project, include_disabled=include)
    if not configs.empty:
        from services.material_workflow_service import config_material_summary
        summaries = {int(row.id): config_material_summary(int(row.id)) for _, row in configs.iterrows()}
        if query.strip():
            names = {int(row.id): ' '.join(list_lot_config_items(int(row.id)).test_item_name.astype(str)) for _, row in configs.iterrows()}
            text = configs.apply(lambda row: ' '.join([str(row.config_name), summaries[int(row.id)], names[int(row.id)]]), axis=1)
            configs = configs[text.map(lambda value: fuzzy_match(query, value))]
        display = configs[['config_name','template_name','instrument_name','status','item_count']].copy()
        display['status'] = display.status.map(_STATUS_LABELS)
        display['质控品批号'] = [summaries[int(row.id)] for _, row in configs.iterrows()]
        display = display.rename(columns={'config_name':'批次名称','template_name':'项目','instrument_name':'仪器','status':'状态','item_count':'检验项目数'})
    state_key = 'v11_selected_lot_config_id'
    if st.session_state.get(state_key) not in configs.id.tolist():
        st.session_state[state_key] = None
    if configs.empty:
        st.info('没有符合条件的批次。可以调整筛选条件，或选择项目后新增批次。')
    else:
        st.caption('勾选批次行首的选择框查看详情。')
        selected = _table(configs, display, state_key, 'batch_configs')
        if selected is not None:
            render_batch_detail(selected)
    render_pending_batch_dialogs()
