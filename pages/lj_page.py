from __future__ import annotations

import pandas as pd
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
from ui.common import (
    TEXT,
    render_compact_stat_metrics,
    render_section_intro,
    render_workbench_context_bar,
)


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


def _render_lj_maintenance_summary(qc_df: pd.DataFrame) -> None:
    abnormal_count = (
        int(qc_df["status"].isin(["警告", "失控"]).sum())
        if not qc_df.empty and "status" in qc_df.columns
        else 0
    )
    latest_test_time = (
        pd.to_datetime(qc_df["test_time"]).max().strftime("%Y-%m-%d %H:%M")
        if not qc_df.empty and "test_time" in qc_df.columns
        else "-"
    )
    render_compact_stat_metrics(
        [
            ("当前记录数", str(len(qc_df))),
            ("异常记录", str(abnormal_count)),
            ("最近检测时间", latest_test_time),
        ]
    )


def render_lj_work_tab(
    work_tab,
    selected_batch_id: int,
) -> None:
    context = build_lj_workbench_context(selected_batch_id)
    batch = context["batch"]
    stats = context["stats"]
    qc_df = context["qc_df"]
    phase_label = "正式质控" if stats.get("target_ready") else "建靶期"
    cv_limit = context["cv_limit"]
    target_n = int(batch["target_n"])

    with work_tab:
        render_workbench_context_bar(
            title="LJ 当前批次",
            caption=(
                f"当前项目：{batch['project_name']}。"
                "请确认当前批次与阶段后，再录入检测结果并查看图表与最新结果分析。"
            ),
            items=[
                ("项目名称", batch["project_name"]),
                ("批次编号", batch["id"]),
                ("当前阶段", phase_label),
                ("建靶要求次数", f"{target_n} 次"),
                ("仪器", batch["instrument"]),
                ("试剂", batch["reagent"]),
                ("质控品", batch["qc_material"]),
                ("浓度", batch["concentration"]),
                ("质控品批号", batch["lot_no"]),
                ("CV 要求", "-" if cv_limit is None else f"≤ {cv_limit:.2f}%"),
            ],
            badges=[
                f"批次 {batch['id']}",
                phase_label,
                f"建靶要求 {target_n} 次",
            ],
        )

        st.divider()
        entry_col, chart_col = st.columns([1.0, 1.18], gap="large")
        with entry_col:
            with st.container(border=True):
                render_section_intro(
                    title="结果录入与统计",
                    caption="在同一区完成结果录入、建靶统计和实时统计查看。",
                    tone="accent",
                )
                render_lj_entry_and_stats_section(context, selected_batch_id)
        with chart_col:
            with st.container(border=True):
                render_section_intro(
                    title="图表与最新结果分析",
                    caption="在同一区查看质控图、当前判读和异常备注入口。",
                    tone="accent",
                )
                figure, chart_state = render_lj_chart_and_analysis_section(context)

        st.divider()
        render_lj_rule_summary_section(stats)

        st.divider()
        render_lj_records_section(qc_df)

        st.divider()
        with st.container(border=True):
            render_section_intro(
                title="检测记录维护",
                caption="可在此查看、编辑或删除历史检测记录。",
                tone="muted",
            )
            _render_lj_maintenance_summary(qc_df)
            render_lj_maintenance_section(qc_df)

        st.divider()
        with st.container(border=True):
            render_section_intro(
                title="导出与导入",
                caption="导出当前批次数据与图表，并按模板导入 CSV。",
                tone="muted",
            )
            render_lj_export_import_section(context, selected_batch_id, figure, chart_state)
