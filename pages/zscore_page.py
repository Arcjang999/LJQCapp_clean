from __future__ import annotations

import streamlit as st

from pages.management import (
    guard_work_tab_selection,
    prepare_zscore_project_batch_context,
    render_zscore_project_batch_management,
)
from pages.zscore_sections import (
    build_zscore_workbench_context,
    render_zscore_chart_analysis_section,
    render_zscore_chart_controls,
    render_zscore_entry_section,
    render_zscore_export_import_section,
    render_zscore_maintenance_section,
)
from ui.common import (
    TEXT,
    format_zscore_template_display_name,
    render_zscore_batch_summary_row,
)


def render_zscore_page() -> None:
    st.subheader("Z-score")
    st.caption("Z-score 页面用于多水平 IQC 流程管理；项目创建时固定双水平或三水平，批次自动继承。")
    projects_df, selected_project_id, batches_df, selected_batch_id = prepare_zscore_project_batch_context()
    manage_tab, work_tab = st.tabs([TEXT["manage"], TEXT["current_batch"]])
    render_zscore_project_batch_management(
        manage_tab,
        projects_df,
        selected_project_id,
        batches_df,
        selected_batch_id,
    )
    guard_work_tab_selection(work_tab, selected_project_id, selected_batch_id)

    context = build_zscore_workbench_context(selected_batch_id)
    batch = context["batch"]
    template = context["template"]

    with work_tab:
        entry_col, chart_col = st.columns([1.0, 1.18], gap="large")

        with st.container():
            render_zscore_batch_summary_row(
                batch,
                context["overall_phase_label"],
                context["formal_rules_enabled"],
                format_zscore_template_display_name(template),
                context["required_level_ids"],
            )

        with chart_col:
            st.subheader("图表与判读")
            phase_scope, view_mode, selected_level, y_axis_mode, standard_sd_limit = render_zscore_chart_controls(
                template,
                context["default_phase_scope"],
                context["level_label_map"],
            )

        with entry_col:
            render_zscore_entry_section(context, selected_batch_id)

        with chart_col:
            chart_panel_state = render_zscore_chart_analysis_section(
                context,
                phase_scope,
                view_mode,
                selected_level,
                y_axis_mode,
                standard_sd_limit,
            )
            render_zscore_export_import_section(context, selected_batch_id, chart_panel_state)

        st.divider()
        render_zscore_maintenance_section(context)
