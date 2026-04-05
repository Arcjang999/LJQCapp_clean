from __future__ import annotations

import pandas as pd
import streamlit as st

from pages.management import (
    guard_work_tab_selection,
    prepare_zscore_project_batch_context,
    render_zscore_project_batch_management,
)
from pages.zscore_sections import (
    build_zscore_workbench_context,
    format_zscore_level_display,
    render_zscore_chart_analysis_section,
    render_zscore_chart_controls,
    render_zscore_entry_section,
    render_zscore_export_import_section,
    render_zscore_maintenance_section,
    render_zscore_vendor_reference_section,
)
from ui.common import (
    TEXT,
    format_optional_float,
    format_zscore_template_display_name,
    render_level_summary_cards,
    render_section_intro,
    render_zscore_batch_header,
)
from zscore_logic import format_zscore_level_label_summary


def _build_zscore_level_cards(context: dict[str, object]) -> list[dict[str, object]]:
    level_label_map = context["level_label_map"]
    level_target_profiles = context["level_target_profiles"]
    required_level_ids = context["required_level_ids"]
    cv_limit = context["cv_limit"]

    cards: list[dict[str, object]] = []
    for level_id in required_level_ids:
        profile = level_target_profiles[level_id]
        display_label, level_caption = format_zscore_level_display(level_id, level_label_map)
        footer = ""
        provisional_cv = profile.get("provisional_cv")
        if cv_limit is not None and provisional_cv is not None:
            footer = (
                f"建靶 CV {format_optional_float(provisional_cv, digits=2, suffix='%')} | "
                f"批次要求 ≤ {cv_limit:.2f}%"
            )
        cards.append(
            {
                "title": display_label,
                "subtitle": level_caption,
                "chips": [
                    f"已收集 {profile['collected_n']} 次",
                    f"建靶要求 {profile['required_n']} 次",
                    f"已达条件 {'是' if profile['is_ready'] else '否'}",
                    f"当前阶段 {profile['phase_label']}",
                ],
                "sections": [
                    {
                        "title": "建靶统计",
                        "stats": [
                            ("均值", format_optional_float(profile.get("provisional_mean"))),
                            ("SD", format_optional_float(profile.get("provisional_sd"))),
                            ("CV%", format_optional_float(profile.get("provisional_cv"), digits=2, suffix="%")),
                        ],
                    },
                    {
                        "title": "正式靶值",
                        "stats": [
                            ("均值", format_optional_float(profile.get("final_target_mean"))),
                            ("SD", format_optional_float(profile.get("final_target_sd"))),
                            ("CV%", format_optional_float(profile.get("final_target_cv"), digits=2, suffix="%")),
                        ],
                    },
                    {
                        "title": "正式期实时统计",
                        "stats": [
                            ("均值", format_optional_float(profile.get("realtime_mean"))),
                            ("SD", format_optional_float(profile.get("realtime_sd"))),
                            ("CV%", format_optional_float(profile.get("realtime_cv"), digits=2, suffix="%")),
                        ],
                    },
                ],
                "footer": footer,
            }
        )
    return cards


def _render_zscore_level_summary_cards_section(context: dict[str, object]) -> None:
    render_section_intro(
        title="各水平统计摘要",
        caption="各 level 改为摘要卡栅格，优先展示建靶进度、正式靶值与正式期实时统计。",
        eyebrow="摘要区",
        badges=[f"{len(context['required_level_ids'])} 张摘要卡", context["overall_phase_label"]],
        tone="muted",
    )
    render_level_summary_cards(_build_zscore_level_cards(context))


def _render_zscore_maintenance_summary(context: dict[str, object]) -> None:
    history_runs = context["history_runs"]
    latest_run = context["latest_run"]
    abnormal_run_count = sum(
        1 for run in history_runs if str(run.get("run_status") or "") in {"warning", "reject"}
    )
    latest_test_time = (
        pd.to_datetime(latest_run["test_time"]).strftime("%Y-%m-%d %H:%M")
        if latest_run is not None and latest_run.get("test_time") is not None
        else "-"
    )
    render_level_summary_cards(
        [
            {
                "title": "维护概览",
                "chips": [
                    f"当前 run {len(history_runs)} 条",
                    f"异常 run {abnormal_run_count} 条",
                    f"最新检测 {latest_test_time}",
                ],
                "sections": [],
                "footer": "维护区只承接备注修改、可维护 formal run 的编辑/删除，不改变业务口径。",
            }
        ]
    )


def render_zscore_page() -> None:
    st.subheader("Z-score")
    st.caption(
        "Z-score 页面用于多水平 IQC 流程管理；项目创建时固定为双水平或三水平，批次自动继承。"
    )
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
    cv_limit = context["cv_limit"]
    level_count = int(context["level_count"])
    required_n = int(context["required_n"])
    template_label = format_zscore_template_display_name(template)
    level_summary = format_zscore_level_label_summary(batch, context["required_level_ids"])

    with work_tab:
        render_zscore_batch_header(
            project_name=batch["project_name"],
            batch_id=batch["id"],
            phase_label=context["overall_phase_label"],
            level_count=level_count,
            required_n=required_n,
            template_label=template_label,
            instrument=batch["instrument"],
            reagent=batch["reagent"],
            qc_material=batch["qc_material"],
            concentration=batch["concentration"],
            level_summary=level_summary,
            lot_no=batch["lot_no"],
            cv_limit=cv_limit,
        )

        st.divider()
        entry_col, chart_col = st.columns([0.94, 1.24], gap="large")

        with entry_col:
            with st.container(border=True):
                render_section_intro(
                    title="Run 录入",
                    caption="左侧压缩为主录入卡，聚焦本次 run 与各水平输入。",
                    eyebrow="主操作区",
                    badges=[f"{level_count} 水平", f"建靶 {required_n} 次"],
                    tone="accent",
                )
                render_zscore_entry_section(context, selected_batch_id)

        with chart_col:
            with st.container(border=True):
                render_section_intro(
                    title="图表与判读",
                    caption="右侧作为完整分析卡，图表工具条、图表与 latest analysis 统一收在这里。",
                    eyebrow="分析区",
                    badges=[context["overall_phase_label"], "图表 + latest analysis"],
                    tone="accent",
                )
                phase_scope, view_mode, selected_level, y_axis_mode, standard_sd_limit = render_zscore_chart_controls(
                    template,
                    context["default_phase_scope"],
                    context["level_label_map"],
                )
                chart_panel_state = render_zscore_chart_analysis_section(
                    context,
                    phase_scope,
                    view_mode,
                    selected_level,
                    y_axis_mode,
                    standard_sd_limit,
                )

        st.divider()
        _render_zscore_level_summary_cards_section(context)

        st.divider()
        with st.container(border=True):
            render_section_intro(
                title="厂家参考值",
                caption="厂家参考值继续下沉为辅助区，保留折叠展示，不和正式靶值与实时统计竞争注意力。",
                eyebrow="辅助区",
                badges=["折叠展示", "辅助信息"],
                tone="muted",
            )
            render_zscore_vendor_reference_section(context, selected_batch_id)

        st.divider()
        with st.container(border=True):
            render_section_intro(
                title="记录维护",
                caption="维护区后置为独立功能块，和录入、图表、统计摘要分开。",
                eyebrow="维护区",
                badges=[f"当前 run {len(context['history_runs'])} 条"],
                tone="muted",
            )
            _render_zscore_maintenance_summary(context)
            render_zscore_maintenance_section(context)

        st.divider()
        with st.container(border=True):
            render_section_intro(
                title="导出与导入",
                caption="导出与 CSV 导入集中成明确功能块，上半导出、下半导入。",
                eyebrow="功能区",
                badges=["导出", "CSV 导入"],
                tone="muted",
            )
            render_zscore_export_import_section(context, selected_batch_id, chart_panel_state)
