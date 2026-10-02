"""Reference data page: lists and details, with explicit edit/confirmation dialogs."""
import streamlit as st

from services.master_data_service import list_sources
from ui.common import render_section_intro
from ui.master_data_dialogs import render_pending_master_data_dialog
from ui.master_data_workspace import render_master_data_workspace


def _tabs(labels, key):
    saved = st.session_state.setdefault('md_page_tabs', {})
    if key not in st.session_state and saved.get(key) in labels:
        st.session_state[key] = saved[key]
    tabs = st.tabs(labels, key=key, on_change='rerun')
    saved[key] = st.session_state.get(key, labels[0])
    return tabs


def _render_manufacturers_tab():
    from pages.reference_management_page import render_manufacturer_management
    render_manufacturer_management()


def _render_test_items_tab():
    st.caption('先按名称、代码或别名查找检验项目，核对后选用。名称目录收录了 WS/T 886—2026 的296项；该标准实施日期为2026年11月1日，选用名称后仍须另行确认适用的质量要求。')
    render_master_data_workspace('test_item')
    with st.expander('词库来源'):
        sources = list_sources()
        columns = {'source_name': '来源名称', 'publisher': '发布机构', 'version_label': '版本',
                   'effective_date': '生效日期', 'active_record_count': '收录条目数'}
        st.dataframe(sources[list(columns)].rename(columns=columns).fillna(''), hide_index=True, width='stretch')


def _render_instruments_tab():
    from pages.reference_management_page import render_instrument_management
    render_instrument_management()


def _render_reagents_tab():
    from pages.reference_management_page import render_reagent_management
    render_reagent_management()


def _render_qc_materials_tab():
    from pages.qc_materials_page import render_qc_material_workspace
    render_qc_material_workspace(render_dialog=False)


def _render_methods_units_tab():
    methods, units = _tabs(['方法学', '单位'], 'md_method_unit_tabs')
    with methods:
        render_master_data_workspace('method')
    with units:
        render_master_data_workspace('unit')


def render_master_data_page():
    action_column, _ = st.columns([0.24, 0.76], gap='small')
    with action_column:
        if st.button('返回当前工作台', key='close_master_data_page', width='stretch'):
            st.session_state['show_master_data_page'] = False
            st.rerun()
    from ui.common import render_module_header
    render_module_header(
        title='基础资料', eyebrow='资料管理',
        caption='维护医院使用的检验项目、厂家、仪器、试剂、质控品、方法学和单位。请先搜索已有资料，未找到时再新增。',
        tone='materials',
    )
    tabs = _tabs(['厂家', '检验项目', '仪器', '试剂', '质控品与批号', '方法学与单位'], 'md_category_tabs')
    for tab, render in zip(tabs, (_render_manufacturers_tab, _render_test_items_tab,
            _render_instruments_tab, _render_reagents_tab, _render_qc_materials_tab, _render_methods_units_tab)):
        with tab:
            render()
    # Only one dialog can be rendered in a run. Keep any other draft pending.
    render_pending_master_data_dialog()
    if not st.session_state.get('master_data_dialog'):
        from ui.material_catalog import render_pending_material_dialog
        render_pending_material_dialog()
    if not st.session_state.get('master_data_dialog') and not st.session_state.get('material_dialog'):
        from ui.reagent_lifecycle_workspace import render_pending_reagent_lifecycle_dialog
        render_pending_reagent_lifecycle_dialog()
