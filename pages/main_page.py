from __future__ import annotations

import streamlit as st


MAIN_ENTRY_LABEL = "主页"
MASTER_DATA_ENTRY_LABEL = "基础资料"
PROJECT_MANAGEMENT_ENTRY_LABEL = "项目/批次管理"
LJ_ENTRY_LABEL = "单水平（LJ）"
ZSCORE_ENTRY_LABEL = "多水平法"
INSTANT_ENTRY_LABEL = "即时法"

METHOD_ENTRY_OPTIONS = [
    MAIN_ENTRY_LABEL,
    LJ_ENTRY_LABEL,
    ZSCORE_ENTRY_LABEL,
    INSTANT_ENTRY_LABEL,
]

LEGACY_METHOD_ENTRY_MAP = {
    "首页": MAIN_ENTRY_LABEL,
    "主页": MAIN_ENTRY_LABEL,
    "Main": MAIN_ENTRY_LABEL,
    "基础资料": MASTER_DATA_ENTRY_LABEL,
    "项目管理": PROJECT_MANAGEMENT_ENTRY_LABEL,
    "项目/批次管理": PROJECT_MANAGEMENT_ENTRY_LABEL,
    "LJ": LJ_ENTRY_LABEL,
    "单水平（LJ法）": LJ_ENTRY_LABEL,
    "单水平（LJ）": LJ_ENTRY_LABEL,
    "单水平 LJ": LJ_ENTRY_LABEL,
    "Z-score": ZSCORE_ENTRY_LABEL,
    "多水平（Z-score法）": ZSCORE_ENTRY_LABEL,
    "多水平法": ZSCORE_ENTRY_LABEL,
    "多水平 Z-score": ZSCORE_ENTRY_LABEL,
    "Instant": INSTANT_ENTRY_LABEL,
    "即时法": INSTANT_ENTRY_LABEL,
    "涓婚〉": MAIN_ENTRY_LABEL,
    "鍗曟按骞筹紙LJ娉曪級": LJ_ENTRY_LABEL,
    "澶氭按骞筹紙Z-score娉曪級": ZSCORE_ENTRY_LABEL,
    "鍗虫椂娉?": INSTANT_ENTRY_LABEL,
}


def switch_top_level_method(target_method: str) -> None:
    normalized_target = LEGACY_METHOD_ENTRY_MAP.get(str(target_method or "").strip(), str(target_method or "").strip())
    if normalized_target in METHOD_ENTRY_OPTIONS:
        st.session_state["pending_top_level_method"] = normalized_target
    st.rerun()


def normalize_top_level_method_selection() -> None:
    pending_value = str(st.session_state.pop("pending_top_level_method", "") or "").strip()
    current_value = str(st.session_state.get("top_level_method_selector", "") or "").strip()
    candidate_value = pending_value or current_value
    normalized_value = LEGACY_METHOD_ENTRY_MAP.get(candidate_value, candidate_value)

    if normalized_value in METHOD_ENTRY_OPTIONS:
        st.session_state["top_level_method_selector"] = normalized_value
        return

    st.session_state["top_level_method_selector"] = METHOD_ENTRY_OPTIONS[0]


def _render_frontline_user_guide() -> None:
    from ui.user_guide import render_frontline_user_guide
    render_frontline_user_guide()


def render_main_entry_page() -> None:
    from ui.common import open_global_page, render_module_card, render_module_header
    from ui.project_navigation import render_project_navigation

    if not st.session_state.get("workspace_project_id"):
        render_module_header("项目工作台", "选择项目，开始整组录入或查看单项质控。", tone="projects", eyebrow="工作首页")
        modules = [
            ("今日质控总览", "查看各项目结果与待处理事项", "daily", "home_daily_overview", "show_daily_overview_page"),
            ("异常处理", "分析原因、关联复测并确认处理", "handling", "home_out_of_control", "show_out_of_control_page"),
            ("批量月报", "按月汇总、生成报告并查看历史", "reports", "home_batch_monthly", "show_batch_monthly_reports_page"),
        ]
        for column, (title, caption, tone, key, page) in zip(st.columns(3, gap="medium"), modules):
            with column, st.container(key="home_module_" + tone, border=True):
                render_module_card(title, caption, tone=tone)
                if st.button("打开" + title, key=key, width="stretch"):
                    open_global_page(page)
        st.subheader("我的项目")
    render_project_navigation()


def render_instant_placeholder_page() -> None:
    from pages.instant_page import render_instant_page
    render_instant_page()
