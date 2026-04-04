from __future__ import annotations

import streamlit as st

from database import init_db
from pages.lj_page import render_lj_page
from pages.main_page import (
    METHOD_ENTRY_OPTIONS,
    normalize_top_level_method_selection,
    render_instant_placeholder_page,
    render_main_entry_page,
)
from pages.zscore_page import render_zscore_page
from ui.common import APP_TITLE, inject_global_styles, render_page_chrome


st.set_page_config(page_title=APP_TITLE, layout="wide")
init_db()
inject_global_styles()

normalize_top_level_method_selection()
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
else:
    render_instant_placeholder_page()
