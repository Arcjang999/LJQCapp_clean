"""Project-first navigation; list rendering does not synchronize or write bindings."""
from functools import partial
from hashlib import sha1

import pandas as pd
import streamlit as st
from services.project_config_service import (
    list_project_templates, list_template_items, get_project_template, QC_METHOD_LABELS,
    validate_project_template, activate_project_template,
)
from services.project_workspace_service import matching_project_ids, list_item_batches, resolve_batch_binding
from ui.project_dialogs import open_project_dialog, render_pending_project_dialog


def filter_projects(projects, prefix):
    if projects.empty:
        return projects
    search, group_col, way_col, method_col = st.columns([2, 1, 1, 1.5])
    search_text = search.text_input('搜索项目', key=prefix+'_search', placeholder='项目名称或仪器')
    groups = sorted({str(x) for x in projects['project_group'] if x})
    ways = sorted({x for cell in projects['qc_methods'].dropna() for x in cell.split(',')})
    methods = sorted({x for cell in projects['method_names'].dropna() for x in cell.split(',')})
    def choose(column, label, options, key, formatter=str):
        if st.session_state.get(key) not in options:
            st.session_state[key] = options[0]
        return column.selectbox(label, options, key=key, format_func=formatter)
    group = choose(group_col, '分组', ['全部']+groups, prefix+'_group')
    way = choose(way_col, '质控方法', ['全部']+ways, prefix+'_way', lambda x: QC_METHOD_LABELS.get(x,x))
    method = choose(method_col, '方法学', ['全部']+methods, prefix+'_method')
    if search_text.strip():
        mask = projects[['template_name','instrument_name']].fillna('').agg(' '.join, axis=1).str.contains(search_text.strip(), case=False, regex=False)
        projects = projects[mask]
    if group != '全部':
        projects = projects[projects.project_group == group]
    if way != '全部' or method != '全部':
        ids = matching_project_ids(qc_method='' if way=='全部' else way, method_name='' if method=='全部' else method)
        projects = projects[projects.id.isin(ids)]
    return projects


def open_configured_batch(binding):
    from pages.main_page import LJ_ENTRY_LABEL, ZSCORE_ENTRY_LABEL, INSTANT_ENTRY_LABEL
    from ui.common import TEXT
    method = binding['qc_method']
    prefix = {'lj':'', 'zscore':'zscore_', 'instant':'instant_'}[method]
    for suffix in ('project_selector','batch_selector'):
        st.session_state.pop(prefix+suffix, None)
        st.session_state.pop('v12_'+method+'_'+suffix, None)
    st.session_state[prefix+'selected_project_id'] = int(binding['runtime_project_id'])
    st.session_state[prefix+'selected_batch_id'] = int(binding['runtime_batch_id'])
    st.session_state[method+'_workbench_tabs'] = TEXT['current_batch']
    st.session_state['pending_top_level_method'] = {'lj':LJ_ENTRY_LABEL,'zscore':ZSCORE_ENTRY_LABEL,'instant':INSTANT_ENTRY_LABEL}[method]
    for key in ('show_quality_targets_page','show_project_management_page','show_master_data_page','show_report_history_page','show_settings_page'):
        st.session_state[key] = False
    st.rerun()


def open_project_setup(template_id, *, config_id=None):
    from ui.common import open_global_page
    st.session_state['v11_selected_template_id'] = int(template_id)
    st.session_state.pop('v11_template_selector', None)
    if config_id is not None:
        st.session_state['v11_pending_existing_config_id'] = int(config_id)
    st.session_state['batch_project_filter'] = int(template_id)
    st.session_state['batch_search'] = ''
    st.session_state['v11_management_tabs'] = '批次管理'
    open_global_page('show_project_management_page')
    st.rerun()


def _select_row(key, ids, state_key, enter_project=False):
    event = st.session_state.get(key, {})
    rows = event.get('selection', {}).get('rows', [])
    value = ids[rows[0]] if rows and 0 <= rows[0] < len(ids) else None
    st.session_state[state_key] = value
    if enter_project and value is not None:
        st.session_state['workspace_project_id'] = value


def _table(frame, display, state_key, prefix, *, enter_project=False):
    ids = [int(x) for x in frame.id]
    fingerprint = sha1((','.join(map(str,ids)) + ':' + str(st.session_state.get('project_table_version',0))).encode()).hexdigest()[:10]
    key = prefix + '_' + fingerprint
    selected = st.session_state.get(state_key)
    default_rows = [ids.index(selected)] if selected in ids else []
    st.dataframe(display, hide_index=True, width='stretch', height=min(380, max(115, 36*len(ids)+38)),
        key=key, selection_mode='single-row', selection_default={'selection':{'rows':default_rows}},
        on_select=partial(_select_row,key,ids,state_key,enter_project))
    return st.session_state.get(state_key) if st.session_state.get(state_key) in ids else None


def _render_project_list():
    tools = st.columns([1,1,2])
    include = tools[2].checkbox('显示已停用项目', key='home_show_disabled_projects')
    all_projects = list_project_templates(include_disabled=True)
    projects = all_projects if include else all_projects[all_projects.is_disabled==0]
    projects = filter_projects(projects, 'home_project_filter')
    if st.session_state.get('home_selected_project_id') not in projects.id.tolist():
        st.session_state['home_selected_project_id'] = None
    if tools[0].button('新建项目', type='primary', key='home_create_project', width='stretch'):
        open_project_dialog('project')
    selected = st.session_state.get('home_selected_project_id')
    row = next((r for r in all_projects.to_dict('records') if r['id']==selected), None)
    if tools[1].button('编辑项目', key='home_edit_project', width='stretch', disabled=not row):
        open_project_dialog('project', selected)
    if projects.empty:
        st.info('尚无符合条件的项目。可以新建项目，或调整筛选条件。')
        return
    display = projects[['template_name','project_group','instrument_name','item_count','status','is_disabled']].copy()
    display['status'] = ['已停用' if r['is_disabled'] else ('设置已确认' if r['status']=='active' else '待完善') for r in projects.to_dict('records')]
    display = display.drop(columns=['is_disabled']).rename(columns={'template_name':'项目名称','project_group':'分组',
        'instrument_name':'仪器','item_count':'检验项目数','status':'状态'})
    st.caption('勾选项目行首的选择框进入工作台；返回列表后保留原选择。')
    selected_id = _table(projects, display, 'home_selected_project_id', 'home_projects', enter_project=True)
    if selected_id is not None and st.button('打开所选项目', key='home_open_project'):
        st.session_state['workspace_project_id'] = selected_id
        st.rerun()


def _render_workspace(template_id):
    try:
        template = dict(get_project_template(template_id))
    except ValueError:
        st.session_state.pop('workspace_project_id', None)
        st.rerun()
    back, edit, manage = st.columns([1,1,1])
    if back.button('返回项目列表', key='workspace_back', width='stretch'):
        st.session_state.pop('workspace_project_id', None)
        st.rerun()
    if edit.button('编辑项目', key='workspace_edit_project', width='stretch'):
        open_project_dialog('project', template_id)
    if manage.button('批次管理', key='workspace_manage_batches', width='stretch', disabled=bool(template['is_disabled'])):
        open_project_setup(template_id)
    st.subheader(template['template_name'])
    st.caption(' · '.join(str(template.get(k) or '') for k in ('project_group','instrument_name','qc_material_name') if template.get(k)))
    if template['is_disabled']:
        st.info('项目已停用，资料和历史仍保留。恢复后可重新确认设置。')
        return
    items = list_template_items(template_id)
    controls = st.columns([1,1,1,1])
    selected = st.session_state.get(f'workspace_item_{template_id}')
    if selected not in items.id.tolist():
        selected = int(items.iloc[0].id) if len(items)==1 else None
        st.session_state[f'workspace_item_{template_id}'] = selected
    if controls[0].button('添加检验项目', key='workspace_add_item', type='primary', width='stretch'):
        open_project_dialog('item', template_id)
    if controls[1].button('编辑检验项目', key='workspace_edit_item', disabled=selected is None, width='stretch'):
        open_project_dialog('item', template_id, selected)
    if controls[2].button('质量目标', key='workspace_quality', disabled=selected is None, width='stretch'):
        open_project_dialog('quality', template_id, selected)
    if controls[3].button('移除检验项目', key='workspace_remove_item', disabled=selected is None, width='stretch'):
        open_project_dialog('remove_item', template_id, selected)
    if items.empty:
        st.info('先添加检验项目。默认设置会自动带入，每一项都可以单独调整。')
    else:
        from services.quality_review_service import validate_project_quality
        display = items[['test_item_name','method_name','qc_method','level_count','unit_symbol']].copy()
        display['qc_method'] = display.qc_method.map(QC_METHOD_LABELS)
        display['质量目标'] = ['待核对' if validate_project_quality(int(r.id)) else '已确认' for _,r in items.iterrows()]
        display = display.rename(columns={'test_item_name':'检验项目','method_name':'方法学','qc_method':'质控方法','level_count':'水平数','unit_symbol':'单位'})
        selected = _table(items, display, f'workspace_item_{template_id}', f'workspace_items_{template_id}')
    errors = validate_project_template(template_id)
    if template['status'] != 'active':
        if errors:
            st.warning('请完成以下设置：' + '；'.join(errors))
        if st.button('确认项目设置', key='workspace_confirm_project', disabled=bool(errors)):
            try:
                activate_project_template(template_id)
            except ValueError as exc:
                st.error(str(exc))
            else:
                st.session_state['project_workspace_notice'] = '项目设置已确认，可以建立批次。'
                st.rerun()
    if selected is not None:
        item = items.loc[items.id==selected].iloc[0]
        st.markdown('**当前检验项目：' + str(item.test_item_name) + '**')
        batches = list_item_batches(selected)
        if batches.empty:
            st.info('此检验项目尚未建立批次。请先确认项目设置，再新增批次并选择质控品批号。')
        else:
            choices = {int(r['config_item_id']):r for r in batches.to_dict('records')}
            key = f'workspace_batch_{selected}'
            if st.session_state.get(key) not in choices:
                st.session_state[key] = next(iter(choices))
            cid = st.selectbox('当前批次', list(choices), key=key,
                format_func=lambda v: choices[v]['config_name']+' · '+('设置已确认' if choices[v]['status']=='active' else '待确认'))
            config = choices[cid]
            from services.material_workflow_service import config_material_summary
            st.caption(config_material_summary(config['lot_config_id']))
            go, settings = st.columns(2)
            if go.button('进入当前批次', type='primary', key='home_open_batch', disabled=config['status']!='active', width='stretch'):
                try:
                    binding = resolve_batch_binding(cid)
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    open_configured_batch(binding)
            if settings.button('批次设置', key='workspace_batch_settings', width='stretch'):
                open_project_setup(template_id, config_id=config['lot_config_id'])
        if st.button('新增批次', key='workspace_create_batch', disabled=template['status']!='active'):
            st.session_state['workspace_create_batch_for'] = template_id
            st.rerun()


def render_project_navigation():
    notice = st.session_state.pop('project_workspace_notice', '')
    if notice:
        st.success(notice)
    template_id = st.session_state.get('workspace_project_id')
    if template_id is None:
        _render_project_list()
    else:
        _render_workspace(int(template_id))
    if st.session_state.get('workspace_create_batch_for'):
        _render_batch_creation_dialog()
    else:
        render_pending_project_dialog()


@st.dialog('新增批次', width='large', dismissible=False)
def _render_batch_creation_dialog():
    from ui.materials import render_material_config_creation
    tid = st.session_state['workspace_create_batch_for']
    def created(config_id):
        st.session_state.pop('workspace_create_batch_for', None)
        st.session_state['workspace_project_id'] = tid
        st.session_state['v11_pending_existing_config_id'] = config_id
        st.session_state['v11_management_tabs'] = '批次管理'
        from ui.common import open_global_page
        open_global_page('show_project_management_page')
    render_material_config_creation(tid, on_created=created)
    if st.button('取消', key='workspace_cancel_batch'):
        st.session_state.pop('workspace_create_batch_for', None)
        st.rerun(scope='app')


def render_workspace_return_bar():
    tid = st.session_state.get('workspace_project_id')
    if not tid:
        return
    try:
        template = get_project_template(tid)
    except ValueError:
        return
    title, switch = st.columns([3,1])
    title.markdown('**'+str(template['template_name'])+'** · '+str(template['instrument_name']))
    if switch.button('切换检验项目 / 批次', key='workspace_switch_context', width='stretch'):
        st.session_state['pending_top_level_method'] = '主页'
        st.rerun()


def render_project_defaults(template):
    from services.master_data_service import list_methods
    from services.material_workflow_service import save_project_defaults
    tid=template['id']
    with st.expander('项目默认设置'):
        group=st.text_input('分组',value=template['project_group'],key=f'default_group_{tid}')
        way=st.selectbox('默认质控方法',list(QC_METHOD_LABELS),index=list(QC_METHOD_LABELS).index(template['default_qc_method']),
            format_func=QC_METHOD_LABELS.get,key=f'default_way_{tid}')
        methods=list_methods();names={r['id']:r['method_name'] for r in methods.to_dict('records')}
        options=[None]+list(names)
        method=st.selectbox('默认方法学',options,index=options.index(template['default_method_id']) if template['default_method_id'] in options else 0,
            format_func=lambda k:'逐项选择' if k is None else names[k],key=f'default_method_{tid}')
        count=st.selectbox('质控水平数',[2,3],index=1 if template['default_level_count']==3 else 0,key=f'default_count_{tid}',disabled=way!='zscore')
        st.caption('新增检验项目时会带入这些设置，各项仍可单独修改。已添加的检验项目和批次不变。')
        if st.button('保存默认设置',key=f'save_project_defaults_{tid}'):
            try:save_project_defaults(tid,qc_method=way,method_id=method,level_count=count,project_group=group)
            except ValueError as exc:st.error(str(exc))
            else:
                for key in (f'v11_bulk_qc_method_{tid}',f'v11_bulk_method_{tid}',f'v11_bulk_level_count_{tid}'):
                    st.session_state.pop(key,None)
                st.rerun()
