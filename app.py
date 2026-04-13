from __future__ import annotations

import streamlit as st

from database import init_db
from pages.instant_page import render_instant_page
from pages.lj_page import render_lj_page
from pages.main_page import (
    METHOD_ENTRY_OPTIONS,
    normalize_top_level_method_selection,
    render_main_entry_page,
)
from pages.zscore_page import render_zscore_page
from ui.common import APP_TITLE, inject_global_styles, render_page_chrome


def _consume_pending_navigation_intent() -> None:
    pending_source = str(st.session_state.get("pending_navigation_source", "") or "").strip()
    if pending_source != "instant_transfer":
        return

    pending_project_id = st.session_state.pop("pending_lj_project_id", None)
    pending_batch_id = st.session_state.pop("pending_lj_batch_id", None)
    st.session_state.pop("pending_navigation_source", None)

    st.session_state["top_level_method_selector"] = "单水平（LJ法）"

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
init_db()
inject_global_styles()

normalize_top_level_method_selection()
_consume_pending_navigation_intent()
render_page_chrome()
selected_method = st.radio(
    "功能入口",
    options=METHOD_ENTRY_OPTIONS,
    horizontal=True,
    key="top_level_method_selector",
)

if selected_method == "主页":
    render_main_entry_page()
elif selected_method == "单水平（LJ法）":
    render_lj_page()
elif selected_method == "多水平（Z-score法）":
    render_zscore_page()
elif selected_method == "即时法":
    render_instant_page()
else:
    render_main_entry_page()
