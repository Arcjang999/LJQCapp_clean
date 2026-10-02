"""Shared reference workspaces, available directly and from basic data."""
import pandas as pd
import streamlit as st

from database import read_snapshot
from services.master_data_service import MANUFACTURER_CATEGORIES
from services.settings_service import get_report_settings
from ui.master_data_workspace import render_master_data_workspace


def render_manufacturer_management():
    pending = st.session_state.pop('reference_pending_manufacturer_category', None)
    labels = MANUFACTURER_CATEGORIES
    if pending in labels:
        st.session_state['reference_manufacturer_category'] = pending
    if st.session_state.get('reference_manufacturer_category') not in labels:
        st.session_state['reference_manufacturer_category'] = next(iter(labels))
    category = st.radio('选择厂家类别', list(labels), format_func=labels.get, horizontal=True,
                        key='reference_manufacturer_category')
    st.caption('按仪器、试剂、质控品分别维护厂家；同一厂家经营多类产品时，可勾选对应类别，无需重复登记。')
    render_master_data_workspace('manufacturer', manufacturer_category=category)


def _instrument_projects(instrument_id):
    with read_snapshot() as connection:
        return pd.read_sql_query('''SELECT p.id,p.template_name,p.is_disabled,
            m.generic_name AS material_name,COUNT(i.id) AS item_count
            FROM qc_project_templates p JOIN md_qc_materials m ON m.id=p.qc_material_id
            LEFT JOIN qc_project_template_items i ON i.template_id=p.id AND i.is_disabled=0
            WHERE p.lab_instrument_id=? GROUP BY p.id ORDER BY p.is_disabled,p.template_name''',
            connection, params=(int(instrument_id),))


def render_instrument_management():
    from ui.common import open_global_page, open_reference_management
    settings = get_report_settings()
    if settings.lab_name:
        st.info('当前实验室：' + settings.lab_name + ('｜' + settings.department_name if settings.department_name else ''))
    else:
        st.info('请先在系统设置中填写实验室名称。本页登记本实验室实际使用的仪器，并查看各仪器下的项目。')
    setup, manufacturer = st.columns(2)
    if setup.button('维护实验室信息', key='instrument_lab_settings'):
        open_global_page('show_settings_page')
    if manufacturer.button('维护仪器厂家', key='instrument_manufacturers'):
        open_reference_management('manufacturer', category='instrument')
    st.caption('新增仪器时一并填写厂家、型号和本机资料；同型号的多台仪器分别登记。选中仪器可查看其关联项目。')
    render_master_data_workspace('lab_instrument')
    instrument_id = st.session_state.get('md_selected_lab_instrument')
    if instrument_id is not None:
        projects = _instrument_projects(instrument_id)
        st.subheader('此仪器下的项目')
        if projects.empty:
            st.info('此仪器还没有项目。新建项目时选择这台仪器，再添加检验项目。')
        else:
            display = projects[['template_name', 'material_name', 'item_count']].rename(columns={
                'template_name': '项目', 'material_name': '质控品', 'item_count': '检验项目数'})
            display['状态'] = projects.is_disabled.map({0: '启用', 1: '已停用'})
            st.dataframe(display, hide_index=True, width='stretch')
            choices = {int(row['id']): row for row in projects.to_dict('records')}
            key = 'instrument_project_' + str(instrument_id)
            if st.session_state.get(key) not in choices:
                st.session_state[key] = None
            selected = st.selectbox('打开关联项目', [None, *choices], key=key,
                placeholder='请选择项目',
                format_func=lambda value: '请选择项目' if value is None else choices[value]['template_name'])
            if st.button('进入所选项目', key='instrument_open_project', disabled=selected is None):
                from ui.daily_navigation import return_to_project_workspace
                return_to_project_workspace(selected)


def _open_reagent_product(product_id):
    st.session_state['reference_reagent_product_id'] = product_id


def _back_to_reagent_products():
    product_id = st.session_state.pop('reference_reagent_product_id', None)
    if product_id is not None:
        st.session_state['md_selected_reagent'] = product_id
    st.session_state['md_table_version'] = st.session_state.get('md_table_version', 0) + 1
    st.rerun()


def render_reagent_management():
    from ui.common import open_reference_management
    from services.master_data_service import list_reagents
    from ui.master_data_workspace import render_master_data_record
    from ui.master_data_dialogs import open_master_data_dialog
    from ui.reagent_lifecycle_workspace import render_reagent_lifecycle_workspace

    product_id = st.session_state.get('reference_reagent_product_id')
    if product_id is None:
        if st.button('维护试剂厂家', key='reagent_manufacturers'):
            open_reference_management('manufacturer', category='reagent')
        st.subheader('试剂产品')
        st.caption('在列表中单选试剂，可编辑资料或停用；点击“管理批号”后，登记实际批号、验证或安排换批。')
        render_master_data_workspace('reagent', show_detail=False)
        selected = st.session_state.get('md_selected_reagent')
        if st.button('管理批号', key='reagent_manage_selected_lots', disabled=selected is None, width='stretch'):
            _open_reagent_product(int(selected))
            st.rerun()
        return

    products = {int(row['id']): row for row in list_reagents(include_disabled=True).to_dict('records')}
    if product_id not in products:
        _back_to_reagent_products()
    product = products[product_id]
    if st.button('返回试剂列表', key='reagent_back_to_products'):
        _back_to_reagent_products()
    st.subheader(product['generic_name'])
    st.caption(f'厂商：{product["manufacturer_name"] or "未填写"}｜货号：{product["catalog_no"] or "未填写"}')
    edit, status = st.columns(2)
    if edit.button('编辑试剂', key='reagent_product_edit', disabled=bool(product['is_disabled'])):
        open_master_data_dialog('reagent', product_id)
    if status.button('恢复试剂' if product['is_disabled'] else '停用试剂', key='reagent_product_status'):
        open_master_data_dialog('reagent', product_id, kind='status')
    with st.expander('产品资料'):
        render_master_data_record('reagent', product_id, product)
    st.subheader('批号管理')
    render_reagent_lifecycle_workspace(product_id=product_id)


def render_reference_management_page():
    from ui.common import render_module_header
    from ui.daily_navigation import return_to_project_workspace
    from ui.master_data_dialogs import render_pending_master_data_dialog
    from ui.reagent_lifecycle_workspace import render_pending_reagent_lifecycle_dialog
    kind = st.session_state.get('reference_management_kind', 'manufacturer')
    pages = {
        'manufacturer': ('厂家管理', '按产品类别维护厂家，供仪器、试剂和质控品分别选用。', render_manufacturer_management),
        'instrument': ('仪器管理', '登记本实验室仪器，查看各仪器下的项目。', render_instrument_management),
        'reagent': ('试剂管理', '先选择试剂产品，再管理它的批号、验证记录和换批安排。', render_reagent_management),
    }
    title, caption, render = pages.get(kind, pages['manufacturer'])
    if st.button('返回项目工作台', key='reference_management_home'):
        return_to_project_workspace()
    render_module_header(title, caption, tone='materials', eyebrow='资料管理')
    render()
    render_pending_master_data_dialog()
    if not st.session_state.get('master_data_dialog'):
        render_pending_reagent_lifecycle_dialog()
