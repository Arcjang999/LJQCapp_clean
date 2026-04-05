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
                f"当前项目：{batch['project_name']}。先确认当前批次上下文，"
                "再录入本次结果，并在右侧查看图表与 latest analysis。"
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
                f"批次 #{batch['id']}",
                phase_label,
                f"建靶 {target_n} 次",
                "latest analysis 取最新保存结果",
            ],
        )

        st.divider()
        entry_col, chart_col = st.columns([1.0, 1.18], gap="large")
        with entry_col:
            with st.container(border=True):
                render_section_intro(
                    title="批次数据录入",
                    caption="左侧作为主操作区，录入、建靶统计与实时统计保持在同一组里。",
                    eyebrow="主操作区",
                    badges=[phase_label, f"建靶 {target_n} 次"],
                    tone="accent",
                )
                render_lj_entry_and_stats_section(context, selected_batch_id)
        with chart_col:
            with st.container(border=True):
                render_section_intro(
                    title="图表与判读",
                    caption="右侧作为分析区，图表与 latest analysis 放在同一视觉组中。",
                    eyebrow="分析区",
                    badges=[phase_label, "图表 + latest analysis"],
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
                caption="维护区后置为独立功能块，避免和录入、图表、结论混在一起。",
                eyebrow="维护区",
                badges=[f"当前记录 {len(qc_df)} 条"],
                tone="muted",
            )
            _render_lj_maintenance_summary(qc_df)
            render_lj_maintenance_section(qc_df)

        st.divider()
        with st.container(border=True):
            render_section_intro(
                title="导出与导入",
                caption="上半区保留导出，下半区集中 CSV 导入，让功能边界更清楚。",
                eyebrow="功能区",
                badges=["导出", "CSV 导入"],
                tone="muted",
            )
            render_lj_export_import_section(context, selected_batch_id, figure, chart_state)
