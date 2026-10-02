from __future__ import annotations

import logging

import streamlit as st

from database import init_db
from pages.instant_page import render_instant_page
from pages.lj_page import render_lj_page
from pages.main_page import (
    INSTANT_ENTRY_LABEL,
    LJ_ENTRY_LABEL,
    MAIN_ENTRY_LABEL,
    ZSCORE_ENTRY_LABEL,
    normalize_top_level_method_selection,
    render_main_entry_page,
)
from pages.master_data_page import render_master_data_page
from pages.project_management_page import render_project_management_page
from pages.report_history_page import render_report_history_page
from pages.settings_page import render_settings_page
from pages.quality_targets_page import render_quality_targets_page
from pages.zscore_page import render_zscore_page
from ui.common import APP_TITLE, inject_global_styles, render_page_chrome


def _consume_pending_navigation_intent() -> None:
    pending_source = str(st.session_state.get("pending_navigation_source", "") or "").strip()
    if pending_source != "instant_transfer":
        return

    pending_project_id = st.session_state.pop("pending_lj_project_id", None)
    pending_batch_id = st.session_state.pop("pending_lj_batch_id", None)
    st.session_state.pop("pending_navigation_source", None)

    st.session_state["top_level_method_selector"] = LJ_ENTRY_LABEL
    st.session_state['lj_workbench_tabs'] = '当前批次'
    st.session_state.pop('v12_lj_project_selector', None)
    st.session_state.pop('v12_lj_batch_selector', None)

    if pending_project_id is not None:
        try:
            st.session_state["selected_project_id"] = int(pending_project_id)
            st.session_state["project_selector"] = "请选择项目"
        except (TypeError, ValueError):
            st.session_state.pop("selected_project_id", None)
            st.session_state["project_selector"] = "请选择项目"

    if pending_batch_id is not None:
        try:
            st.session_state["selected_batch_id"] = int(pending_batch_id)
            st.session_state["batch_selector"] = "请选择批次"
        except (TypeError, ValueError):
            st.session_state.pop("selected_batch_id", None)
            st.session_state["batch_selector"] = "请选择批次"


st.set_page_config(page_title=APP_TITLE, layout="wide")
st.set_option("client.showSidebarNavigation", False)
st.set_option("client.toolbarMode", "minimal")
st.set_option("client.showErrorDetails", "none")
init_db()
from services.product_directory_service import ensure_builtin_bondson_directory
try:
    ensure_builtin_bondson_directory()
except (ValueError, OSError):
    logging.exception('Bundled product directory could not be prepared')
    st.warning('内置产品名录暂未准备完成。请在质控品管理中核对目录资料，或联系维护人员；已登记资料仍可使用。')

normalize_top_level_method_selection()
_consume_pending_navigation_intent()
inject_global_styles()
render_page_chrome()
from ui.daily_navigation import render_daily_return
render_daily_return()

if bool(st.session_state.get("show_user_guide_page", False)):
    from ui.user_guide import render_user_guide_page
    render_user_guide_page()
    st.stop()

if bool(st.session_state.get("show_batch_monthly_reports_page", False)):
    from ui.batch_monthly_reports import render_batch_monthly_reports_page
    render_batch_monthly_reports_page()
    st.stop()

if bool(st.session_state.get("show_daily_overview_page", False)):
    from ui.daily_overview import render_daily_overview
    render_daily_overview()
    st.stop()

if bool(st.session_state.get("show_daily_entry_page", False)):
    from ui.daily_entry import render_daily_entry
    render_daily_entry()
    st.stop()

if bool(st.session_state.get("show_out_of_control_page", False)):
    from pages.out_of_control_page import render_out_of_control_page
    render_out_of_control_page()
    st.stop()

if bool(st.session_state.get("show_quality_targets_page", False)):
    render_quality_targets_page()
    st.stop()

if bool(st.session_state.get("show_settings_page", False)):
    render_settings_page()
    st.stop()

if bool(st.session_state.get("show_report_history_page", False)):
    render_report_history_page()
    st.stop()

if bool(st.session_state.get("show_master_data_page", False)):
    render_master_data_page()
    st.stop()

if bool(st.session_state.get("show_qc_materials_page", False)):
    from pages.qc_materials_page import render_qc_materials_page
    render_qc_materials_page()
    st.stop()

if bool(st.session_state.get("show_reference_management_page", False)):
    from pages.reference_management_page import render_reference_management_page
    render_reference_management_page()
    st.stop()

if bool(st.session_state.get("show_project_management_page", False)):
    render_project_management_page()
    st.stop()

# A configured item chooses its workbench; method selection belongs to its setup.
selected_method = st.session_state.get('top_level_method_selector', MAIN_ENTRY_LABEL)
if selected_method != MAIN_ENTRY_LABEL and st.button('返回项目工作台',key='back_project_home'):
    from ui.daily_navigation import return_to_project_workspace
    return_to_project_workspace()

if selected_method != MAIN_ENTRY_LABEL:
    from ui.project_navigation import render_workspace_return_bar
    render_workspace_return_bar()

if selected_method == MAIN_ENTRY_LABEL:
    render_main_entry_page()
elif selected_method == LJ_ENTRY_LABEL:
    render_lj_page()
elif selected_method == ZSCORE_ENTRY_LABEL:
    render_zscore_page()
elif selected_method == INSTANT_ENTRY_LABEL:
    render_instant_page()
else:
    render_main_entry_page()
