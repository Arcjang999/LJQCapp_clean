"""Product specifications and per-item physical-control selection."""
import streamlit as st
from services.material_workflow_service import (material_label, available_materials,
    create_material_config, copy_material_config)


def render_material_registration(material_labels=None, material_map=None):
    from ui.material_catalog import render_material_catalog
    render_material_catalog()


def select_item_materials(items,material_id,prefix,existing=None):
    materials=available_materials(material_id)
    by_id={r['id']:r for r in materials.to_dict('records')}
    selections={}
    for item in items.to_dict('records'):
        from services.project_config_service import QC_METHOD_LABELS
        st.markdown(f"**{item['test_item_name']}｜{QC_METHOD_LABELS[item['qc_method']]}｜{item.get('method_name') or '方法学未填写'}**")
        values=[]
        prior=(existing or {}).get(item['id'],[])
        for position in range(item['level_count']):
            default=prior[position] if len(prior)>position and prior[position] in by_id else None
            options=[None]+list(by_id)
            lid=st.selectbox(f'第 {position+1} 个水平的质控品',options,index=options.index(default),
                format_func=lambda k:'请选择浓度水平和批号' if k is None else material_label(by_id[k]),
                key=f'{prefix}_{item["id"]}_{position}')
            if lid is not None: values.append(lid)
        selections[item['id']]=values
    if materials.empty:st.info('此质控品尚无可选批号，请先在基础资料中添加浓度水平、浓度编号、批号和效期。')
    st.caption('请为每个水平选择质控品，并核对浓度编号、批号和效期。')
    return selections


def render_material_config_creation(template_id, *, on_created=None):
    from services.project_config_service import get_project_template,list_template_items
    from services.quality_target_service import decode,source_label
    from ui.cv import CV_REQUIREMENT_HELP
    import pandas as pd
    template=get_project_template(template_id);items=list_template_items(template_id)
    selections=select_item_materials(items,template['qc_material_id'],f'create_material_{template_id}')
    requirements={}
    for item in items.to_dict('records'):
        goal=decode(item.get('quality_goal_json','{}'))
        if goal:
            st.caption(f"{item['test_item_name']}｜质量目标：{source_label(goal['spec'])}；保存后请核对各浓度水平的质量目标。")
        else:
            with st.expander(f"{item['test_item_name']}：允许不精密度（选填）"):
                value=st.number_input('允许不精密度（CV%，选填）',value=None if pd.isna(item['cv_limit']) else float(item['cv_limit']),
                    min_value=0.0,format='%.4f',help=CV_REQUIREMENT_HELP,key=f'v12_create_cv_{template_id}_{item["id"]}')
                source=st.text_input('允许不精密度依据（选填）',value=item['quality_target_source_text'] or '',key=f'v12_create_cv_source_{template_id}_{item["id"]}')
                requirements[item['id']]=dict(cv_limit=value,quality_target_source_text=source)
    name=st.text_input('批次名称（留空自动生成）',key='v11_create_config_name')
    if st.button('保存批次设置',type='primary',key='v11_create_config_button',width='stretch'):
        try: config=create_material_config(template_id=template_id,selections=selections,config_name=name,quality_requirements=requirements)
        except ValueError as exc:st.error(str(exc))
        else:
            st.session_state['v11_selected_lot_config_id']=config
            st.session_state.pop('v11_lot_config_selector',None)
            if on_created is not None:
                on_created(config)
            st.rerun()


def render_material_replacement(configs):
    from services.project_config_service import get_lot_config,list_lot_config_items,list_lot_item_levels
    labels={r['id']:r['config_name'] for r in configs.to_dict('records')}
    with st.expander('选择各浓度水平的新批号（未换批的水平保留原批号）',expanded=True):
        source_id=st.selectbox('原批次',[None]+list(labels),format_func=lambda k:'请选择' if k is None else labels[k],key='material_copy_source')
        if source_id is None:return
        source=get_lot_config(source_id);items=list_lot_config_items(source_id)
        selected=st.multiselect('需要换批的检验项目',items['id'].tolist(),default=items['id'].tolist(),
            format_func=lambda k:items[items.id==k].iloc[0]['test_item_name'],key=f'material_copy_items_{source_id}')
        items=items[items.id.isin(selected)]
        existing={r['id']:list_lot_item_levels(r['id'])['qc_level_id'].tolist() for r in items.to_dict('records')}
        selections=select_item_materials(items,source['qc_material_id'],f'copy_material_{source_id}',existing)
        name=st.text_input('新批次名称（选填）',key=f'material_copy_name_{source_id}')
        if st.button('保存新批次',key='material_copy_save',disabled=items.empty,type='primary'):
            try: config=copy_material_config(source_config_id=source_id,selections=selections,config_name=name)
            except ValueError as exc:st.error(str(exc))
            else:
                st.session_state['v11_selected_lot_config_id']=config
                st.session_state.pop('v11_lot_config_selector',None)
                st.session_state['v11_management_tabs']='批次管理'
                st.rerun()
