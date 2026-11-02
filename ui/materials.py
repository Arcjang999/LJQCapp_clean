"""Product specifications and per-item physical-control selection."""
import streamlit as st
from services.material_workflow_service import (material_label, available_materials,
    create_material_config, copy_material_config)


def render_material_registration(material_labels=None, material_map=None):
    from ui.material_catalog import render_material_catalog
    render_material_catalog()


def select_item_materials(items,material_id,prefix,existing=None,*,registration_key=None):
    from ui.control_selection import control_products, render_control_slot, restore_registration_inputs
    registration_key = registration_key or 'material_registration_' + prefix
    result = restore_registration_inputs(registration_key)
    if result:
        item_id, position = result['slot']
        slot_prefix = f'{prefix}_{item_id}_{position}'
        st.session_state[slot_prefix + '_product'] = result['product_id']
        st.session_state[slot_prefix + '_level'] = result['level_id']
        st.session_state[slot_prefix + '_last_product'] = result['product_id']
    products = control_products()
    materials = available_materials().to_dict('records')
    selections={}
    for item in items.to_dict('records'):
        from services.project_config_service import QC_METHOD_LABELS
        st.markdown(f"**{item['test_item_name']}｜{QC_METHOD_LABELS[item['qc_method']]}｜{item.get('method_name') or '方法学未填写'}**")
        values=[]
        prior=(existing or {}).get(item['id'],[])
        for position in range(item['level_count']):
            default=prior[position] if len(prior)>position else None
            _, lid = render_control_slot(prefix=f'{prefix}_{item["id"]}_{position}', order=position+1,
                selected_level=default, selected_product=material_id if position == 0 and not default else None,
                registration_key=registration_key, slot=(int(item['id']), position),
                products=products, materials=materials,
                preserve_prefixes=(prefix + '_', 'v12_create_cv_', 'v12_create_cv_source_', 'material_copy_'))
            values.append(lid)
        selections[item['id']]=values
    return selections


def render_material_config_creation(template_id, *, on_created=None):
    from services.project_config_service import get_project_template,list_template_items
    from services.quality_target_service import decode,source_label
    from ui.cv import CV_REQUIREMENT_HELP
    import pandas as pd
    template=get_project_template(template_id);items=list_template_items(template_id)
    from ui.material_registration import render_material_registration_view
    registration_key=f'material_registration_create_{template_id}'
    if render_material_registration_view(registration_key):
        return True
    selections=select_item_materials(items,template['qc_material_id'],f'create_material_{template_id}',
        registration_key=registration_key)
    requirements={}
    for item in items.to_dict('records'):
        goal=decode(item.get('quality_goal_json','{}'))
        if goal:
            st.caption(f"{item['test_item_name']}｜质量目标：{source_label(goal['spec'])}。")
        else:
            with st.expander(f"{item['test_item_name']}：实验室CV上限（选填）"):
                value=st.number_input('实验室CV上限（选填，%）',value=None if pd.isna(item['cv_limit']) else float(item['cv_limit']),
                    min_value=0.0,format='%.4f',help=CV_REQUIREMENT_HELP,key=f'v12_create_cv_{template_id}_{item["id"]}')
                source=st.text_input('CV要求依据（选填）',value=item['quality_target_source_text'] or '',key=f'v12_create_cv_source_{template_id}_{item["id"]}')
                requirements[item['id']]=dict(cv_limit=value,quality_target_source_text=source)
    registering=False
    if st.button('保存批次设置',type='primary',key='v11_create_config_button',width='stretch',disabled=registering):
        try: config=create_material_config(template_id=template_id,selections=selections,quality_requirements=requirements)
        except ValueError as exc:st.error(str(exc))
        else:
            st.session_state['v11_selected_lot_config_id']=config
            st.session_state.pop('v11_lot_config_selector',None)
            if on_created is not None:
                on_created(config)
            st.rerun()
    return registering


def render_material_replacement(configs):
    from services.project_config_service import get_lot_config,list_lot_config_items,list_lot_item_levels
    from ui.material_registration import render_material_registration_view
    active_key = st.session_state.get('material_copy_active_registration_key')
    if active_key:
        for widget, value in st.session_state.pop(active_key + '_registration_restore', {}).items():
            st.session_state[widget] = value
        if render_material_registration_view(active_key):
            return
    from services.material_workflow_service import config_material_summary
    labels={r['id']:config_material_summary(r['id']) for r in configs.to_dict('records')}
    with st.expander('选择各浓度水平的新批号（未换批的水平保留原批号）',expanded=True):
        prior_source=st.session_state.get('material_copy_source')
        registering=bool(st.session_state.get(f'material_registration_copy_{prior_source}_registration'))
        source_id=st.selectbox('原批次',[None]+list(labels),format_func=lambda k:'请选择' if k is None else labels[k],key='material_copy_source',disabled=registering)
        if source_id is None:return
        source=get_lot_config(source_id);items=list_lot_config_items(source_id)
        item_names={int(row['id']):str(row['test_item_name']) for row in items.to_dict('records')}
        selected=st.multiselect('需要换批的检验项目',list(item_names),default=list(item_names),
            format_func=item_names.get,key=f'material_copy_items_{source_id}')
        items=items[items.id.isin(selected)]
        existing={r['id']:list_lot_item_levels(r['id'])['qc_level_id'].tolist() for r in items.to_dict('records')}
        from ui.material_registration import render_material_registration_view
        registration_key=f'material_registration_copy_{source_id}'
        st.session_state['material_copy_active_registration_key'] = registration_key
        if render_material_registration_view(registration_key):
            return
        selections=select_item_materials(items,source['qc_material_id'],f'copy_material_{source_id}',existing,
            registration_key=registration_key)
        registering=False
        if items.empty:
            st.info('请在“需要换批的检验项目”中至少选择一项。')
        if st.button('保存新批次',key='material_copy_save',disabled=items.empty or registering,type='primary'):
            try: config=copy_material_config(source_config_id=source_id,selections=selections)
            except ValueError as exc:st.error(str(exc))
            else:
                st.session_state['v11_selected_lot_config_id']=config
                st.session_state.pop('v11_lot_config_selector',None)
                st.session_state['v11_management_tabs']='批次管理'
                st.rerun()
