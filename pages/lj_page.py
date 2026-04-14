from __future__ import annotations

import pandas as pd
import streamlit as st

from pages.lj_report_section import render_lj_monthly_report_section
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


def _clean_lj_display_part(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _build_lj_batch_display(batch) -> str:
    batch_dict = dict(batch)
    lot_no = _clean_lj_display_part(batch_dict.get("lot_no"))
    if lot_no:
        return f"质控批号 {lot_no}"
    created_at = _clean_lj_display_part(batch_dict.get("created_at"))
    if created_at:
        return f"创建于 {created_at}"
    return "当前批次"


def _build_lj_source_message(batch) -> str:
    batch_dict = dict(batch)
    source_method = str(batch_dict.get("source_method", "") or "").strip().lower()
    if source_method != "instant":
        return ""
    source_project = str(batch_dict.get("source_instant_project_name", "") or "").strip() or "即时法项目"
    source_batch = str(batch_dict.get("source_instant_batch_lot_no", "") or "").strip() or "未填写质控批号"
    transfer_time = str(batch_dict.get("source_transfer_time", "") or "").strip() or "-"
    return (
        f"来源：即时法｜来源项目：{source_project}｜来源批次：{source_batch or '-'}｜"
        f"转入时间：{transfer_time}"
    )


def render_lj_page() -> None:
    st.subheader("单水平（LJ法）")
    st.caption("适用于单水平项目的日常室内质控。建靶期重点查看离群值判断，进入正式期后按 Westgard 规则判读并生成月报。")
    projects_df, selected_project_id, batches_df, selected_batch_id = prepare_project_batch_context()
    manage_tab, work_tab, report_tab = st.tabs([TEXT["manage"], TEXT["current_batch"], "LJ 月报"])
    render_project_batch_management(
        manage_tab,
        projects_df,
        selected_project_id,
        batches_df,
        selected_batch_id,
    )
    guard_work_tab_selection(work_tab, selected_project_id, selected_batch_id)
    guard_work_tab_selection(report_tab, selected_project_id, selected_batch_id)
    render_lj_work_tab(work_tab, selected_batch_id)
    with report_tab:
        render_lj_monthly_report_section(selected_batch_id)


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
    input_value_type_label = context["input_value_type_label"]
    stats = context["stats"]
    qc_df = context["qc_df"]
    phase_label = "正式质控" if stats.get("target_ready") else "建靶期"
    cv_limit = context["cv_limit"]
    target_n = int(batch["target_n"])
    batch_dict = dict(batch)
    batch_display = _build_lj_batch_display(batch)
    is_from_instant = str(batch_dict.get("source_method", "") or "").strip().lower() == "instant"

    with work_tab:
        render_workbench_context_bar(
            title="单水平（LJ法）当前批次",
            caption=(
                f"当前项目：{batch['project_name']}。"
                f"请先确认当前批次、输入值类型（{input_value_type_label}）与阶段。"
                "建靶期重点查看离群值判断，正式期重点查看 Westgard 判读。"
            ),
            items=[
                ("项目名称", batch["project_name"]),
                ("批次标识", batch_display),
                ("输入值类型", input_value_type_label),
                ("当前阶段", phase_label),
                ("建靶要求次数", f"{target_n} 次"),
                ("仪器", batch["instrument"]),
                ("试剂", batch["reagent"]),
                ("质控品", batch["qc_material"]),
                ("浓度", batch["concentration"]),
                ("质控品批号", batch["lot_no"]),
                ("CV 要求", "-" if cv_limit is None else f"≤ {cv_limit:.2f}%"),
                *(
                    [("来源", "由即时法转入")]
                    if is_from_instant
                    else []
                ),
            ],
            badges=[
                batch_display,
                input_value_type_label,
                phase_label,
                f"建靶要求 {target_n} 次",
                *(
                    ["由即时法转入"]
                    if is_from_instant
                    else []
                ),
            ],
        )
        source_message = _build_lj_source_message(batch)
        if source_message:
            st.info(source_message)

        render_section_intro(
            title="当前动作区",
            caption="左侧聚焦本次录入与当前统计，右侧聚焦图表与最新分析；单水平页面只保留当前阶段最需要的判断信息。",
            badges=["单水平（LJ法）", phase_label, input_value_type_label],
            tone="accent",
        )
        entry_col, chart_col = st.columns([1.0, 1.18], gap="large")
        with entry_col:
            with st.container():
                render_section_intro(
                    title="结果录入与统计",
                    caption=f"在同一区完成{input_value_type_label}录入、建靶统计和正式期实时统计查看。",
                    tone="accent",
                )
                render_lj_entry_and_stats_section(context, selected_batch_id)
        with chart_col:
            with st.container():
                render_section_intro(
                    title="图表与最新结果分析",
                    caption="在同一区查看质控图、当前判读和异常备注入口。",
                    tone="accent",
                )
                figure, chart_state = render_lj_chart_and_analysis_section(context)

        render_section_intro(
            title="历史与次要操作区",
            caption="规则回顾、记录表、维护区和导入导出统一放在页面下部，避免主操作区被次级信息打断。",
            badges=["记录回顾", "维护", "导入导出"],
            tone="muted",
        )

        with st.container(border=True):
            render_section_intro(
                title="规则与记录概览",
                caption="建靶期重点查看离群判断相关记录，正式期重点回顾 Westgard 规则与完整记录。",
                tone="default",
            )
            render_lj_rule_summary_section(stats)
            render_lj_records_section(qc_df, context["input_value_type"])

        lower_left, lower_right = st.columns([1.0, 1.0], gap="large")
        with lower_left:
            with st.container():
                render_section_intro(
                    title="检测记录维护",
                    caption="集中处理建靶期离群点与历史记录维护，避免主区重复提示。",
                    tone="muted",
                )
                _render_lj_maintenance_summary(qc_df)
                render_lj_maintenance_section(context)
        with lower_right:
            with st.container(border=True):
                render_section_intro(
                    title="导出与导入",
                    caption="导出当前批次数据和图表，并按模板导入 CSV。",
                    tone="muted",
                )
                render_lj_export_import_section(context, selected_batch_id, figure, chart_state)
