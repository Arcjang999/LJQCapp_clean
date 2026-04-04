from __future__ import annotations

import streamlit as st

from pages.lj_sections import (
    build_lj_workbench_context,
    render_lj_chart_and_analysis_section,
    render_lj_entry_and_stats_section,
    render_lj_export_import_section,
    render_lj_maintenance_section,
    render_lj_records_section,
    render_lj_rule_summary_section,
)
from pages.management import (
    guard_work_tab_selection,
    prepare_project_batch_context,
    render_project_batch_management,
)
from ui.common import TEXT, render_batch_summary_row


def render_lj_page() -> None:
    projects_df, selected_project_id, batches_df, selected_batch_id = prepare_project_batch_context()
    manage_tab, work_tab = st.tabs([TEXT["manage"], TEXT["current_batch"]])
    render_project_batch_management(
        manage_tab,
        projects_df,
        selected_project_id,
        batches_df,
        selected_batch_id,
    )
    guard_work_tab_selection(work_tab, selected_project_id, selected_batch_id)
    render_lj_work_tab(work_tab, selected_batch_id)


def render_lj_work_tab(
    work_tab,
    selected_batch_id: int,
) -> None:
    context = build_lj_workbench_context(selected_batch_id)
    batch = context["batch"]

    with work_tab:
        st.caption(f"当前项目：{batch['project_name']}")
        with st.container():
            render_batch_summary_row(batch)

        st.divider()
        entry_col, chart_col = st.columns([1.0, 1.18], gap="large")
        with entry_col:
            render_lj_entry_and_stats_section(context, selected_batch_id)
        with chart_col:
            figure, chart_state = render_lj_chart_and_analysis_section(context)

        st.divider()
        render_lj_rule_summary_section(context["stats"])

        st.divider()
        render_lj_records_section(context["qc_df"])

        st.divider()
        maintenance_col, export_col = st.columns([0.9, 1.1], gap="large")
        with maintenance_col:
            render_lj_maintenance_section(context["qc_df"])
        with export_col:
            render_lj_export_import_section(context, selected_batch_id, figure, chart_state)
