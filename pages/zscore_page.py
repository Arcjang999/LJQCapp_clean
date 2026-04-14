from __future__ import annotations

import pandas as pd
import streamlit as st

from pages.management import (
    guard_work_tab_selection,
    prepare_zscore_project_batch_context,
    render_zscore_project_batch_management,
)
from pages.zscore_report_section import render_zscore_monthly_report_section
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
from zscore_logic import (
    PHASE_FORMAL_QC,
    calculate_formal_realtime_stats,
    format_zscore_level_label_summary,
)


def _build_empty_realtime_profiles(required_level_ids: list[str]) -> dict[str, dict[str, object]]:
    return {
        level_id: {
            "realtime_mean": None,
            "realtime_sd": None,
            "realtime_cv": None,
        }
        for level_id in required_level_ids
    }


def _build_level_building_summary(context: dict[str, object]) -> dict[str, dict[str, int]]:
    required_level_ids = context["required_level_ids"]
    history_runs = context["history_runs"]
    summary = {
        level_id: {
            "total_n": 0,
            "effective_n": 0,
            "disabled_n": 0,
        }
        for level_id in required_level_ids
    }
    for run in history_runs:
        if str(run.get("phase") or "") != "target_building":
            continue
        for level_result in run.get("level_results", []):
            level_id = str(level_result.get("level_id"))
            if level_id not in summary:
                continue
            summary[level_id]["total_n"] += 1
            if int(level_result.get("is_building_included", 1) or 0) == 1:
                summary[level_id]["effective_n"] += 1
            else:
                summary[level_id]["disabled_n"] += 1
    return summary


def _build_filtered_realtime_profiles(
    context: dict[str, object],
) -> tuple[
    dict[str, dict[str, object]],
    str | None,
    object | None,
    object | None,
    bool,
    str | None,
]:
    history_runs = context["history_runs"]
    required_level_ids = context["required_level_ids"]
    batch_id = int(context["batch"]["id"])
    formal_run_times = [
        pd.Timestamp(run["test_time"])
        for run in history_runs
        if str(run.get("phase") or "") == PHASE_FORMAL_QC and run.get("test_time") is not None
    ]
    if not formal_run_times:
        return _build_empty_realtime_profiles(required_level_ids), None, None, None, False, None

    default_start = min(formal_run_times).date()
    default_end = max(formal_run_times).date()
    start_key = f"zscore_level_summary_start_{batch_id}"
    end_key = f"zscore_level_summary_end_{batch_id}"
    start_date = pd.Timestamp(st.session_state.get(start_key, default_start)).date()
    end_date = pd.Timestamp(st.session_state.get(end_key, default_end)).date()
    if end_date < start_date:
        return (
            _build_empty_realtime_profiles(required_level_ids),
            f"{start_date} - {end_date}",
            start_date,
            end_date,
            True,
            "结束日期不能早于开始日期，当前已暂时清空正式期实时统计展示。",
        )

    start_timestamp = pd.Timestamp(start_date)
    end_timestamp = pd.Timestamp(end_date) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
    filtered_runs = [
        run
        for run in history_runs
        if run.get("test_time") is not None
        and start_timestamp <= pd.Timestamp(run["test_time"]) <= end_timestamp
    ]
    return (
        calculate_formal_realtime_stats(filtered_runs, required_level_ids),
        f"{start_date} - {end_date}",
        start_date,
        end_date,
        True,
        None,
    )


def _build_zscore_level_cards(
    context: dict[str, object],
    realtime_profiles: dict[str, dict[str, object]] | None = None,
) -> list[dict[str, object]]:
    level_label_map = context["level_label_map"]
    level_target_profiles = context["level_target_profiles"]
    required_level_ids = context["required_level_ids"]
    cv_limit = context["cv_limit"]
    realtime_profiles = realtime_profiles or _build_empty_realtime_profiles(required_level_ids)
    building_summary = _build_level_building_summary(context)

    cards: list[dict[str, object]] = []
    for level_id in required_level_ids:
        profile = level_target_profiles[level_id]
        realtime_profile = realtime_profiles.get(level_id, {})
        summary_item = building_summary.get(level_id, {})
        display_label, level_caption = format_zscore_level_display(level_id, level_label_map)
        building_mean = profile.get("final_target_mean") if profile.get("is_ready") else profile.get("provisional_mean")
        building_sd = profile.get("final_target_sd") if profile.get("is_ready") else profile.get("provisional_sd")
        building_cv = profile.get("final_target_cv") if profile.get("is_ready") else profile.get("provisional_cv")
        footer = ""
        if cv_limit is not None and building_cv is not None:
            footer = (
                f"建靶 CV {format_optional_float(building_cv, digits=2, suffix='%')} | "
                f"批次要求 ≤ {cv_limit:.2f}%"
            )
        cards.append(
            {
                "title": display_label,
                "subtitle": level_caption,
                "chips": [
                    f"总点数 {summary_item.get('total_n', 0)}",
                    f"生效建靶点 {summary_item.get('effective_n', 0)}",
                    f"已禁用点 {summary_item.get('disabled_n', 0)}",
                    f"建靶要求 {profile['required_n']} 次",
                    f"已达条件 {'是' if profile['is_ready'] else '否'}",
                    f"当前阶段 {profile['phase_label']}",
                ],
                "sections": [
                    {
                        "title": "建靶统计",
                        "stats": [
                            ("均值", format_optional_float(building_mean)),
                            ("SD", format_optional_float(building_sd)),
                            ("CV%", format_optional_float(building_cv, digits=2, suffix="%")),
                        ],
                    },
                    {
                        "title": "正式期实时统计",
                        "stats": [
                            ("均值", format_optional_float(realtime_profile.get("realtime_mean"))),
                            ("SD", format_optional_float(realtime_profile.get("realtime_sd"))),
                            ("CV%", format_optional_float(realtime_profile.get("realtime_cv"), digits=2, suffix="%")),
                        ],
                    },
                ],
                "footer": footer,
            }
        )
    return cards


def _render_zscore_level_summary_cards_section(context: dict[str, object]) -> None:
    realtime_profiles, range_text, start_date, end_date, has_formal_data, range_warning = _build_filtered_realtime_profiles(context)
    batch_id = int(context["batch"]["id"])
    render_section_intro(
        title="各水平统计摘要",
        caption="汇总各水平的建靶进度、建靶统计和正式期实时统计。",
        tone="muted",
    )
    if range_text is not None:
        st.caption(f"正式期实时统计按时间范围筛选：{range_text}。统计口径仍为正式期内在控数据。")
    else:
        st.caption("当前批次还没有正式期数据，正式期实时统计暂为空。")
    render_level_summary_cards(_build_zscore_level_cards(context, realtime_profiles=realtime_profiles))
    if has_formal_data:
        st.divider()
        range_cols = st.columns(2)
        range_cols[0].date_input(
            "开始日期",
            value=start_date,
            key=f"zscore_level_summary_start_{batch_id}",
        )
        range_cols[1].date_input(
            "结束日期",
            value=end_date,
            key=f"zscore_level_summary_end_{batch_id}",
        )
        st.caption("开始日期 / 结束日期会影响正式期实时统计数据。按日期统计，结束日期包含当日全部记录。")
        if range_warning:
            st.warning(range_warning)


def _render_zscore_maintenance_summary(context: dict[str, object]) -> None:
    history_runs = context["history_runs"]
    latest_run = context["latest_run"]
    level_summary = _build_level_building_summary(context)
    disabled_total = sum(item["disabled_n"] for item in level_summary.values())
    effective_total = sum(item["effective_n"] for item in level_summary.values())
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
                    f"当前记录 {len(history_runs)} 次",
                    f"生效建靶点 {effective_total} 个",
                    f"已禁用点 {disabled_total} 个",
                    f"异常记录 {abnormal_run_count} 次",
                    f"最新检测 {latest_test_time}",
                ],
                "sections": [],
                "footer": "可在此查看单 level 离群状态，并维护仍处于建靶期的水平点。",
            }
        ]
    )


def render_zscore_page() -> None:
    st.subheader("多水平（Z-score法）")
    st.caption(
        "适用于 2 水平或 3 水平项目的联合判断。页面重点突出多水平摘要、图表控制、视图切换和单 level 维护。"
    )
    projects_df, selected_project_id, batches_df, selected_batch_id = prepare_zscore_project_batch_context()
    manage_tab, work_tab, report_tab = st.tabs([TEXT["manage"], TEXT["current_batch"], "Z-score 月报"])
    render_zscore_project_batch_management(
        manage_tab,
        projects_df,
        selected_project_id,
        batches_df,
        selected_batch_id,
    )
    guard_work_tab_selection(work_tab, selected_project_id, selected_batch_id)
    guard_work_tab_selection(report_tab, selected_project_id, selected_batch_id)

    context = build_zscore_workbench_context(selected_batch_id)
    batch = context["batch"]
    template = context["template"]
    cv_limit = context["cv_limit"]
    input_value_type_label = context["input_value_type_label"]
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
            input_value_type_label=input_value_type_label,
            template_label=template_label,
            instrument=batch["instrument"],
            reagent=batch["reagent"],
            qc_material=batch["qc_material"],
            concentration=batch["concentration"],
            level_summary=level_summary,
            lot_no=batch["lot_no"],
            cv_limit=cv_limit,
        )

        render_section_intro(
            title="当前动作区",
            caption="左侧聚焦本次多水平录入，右侧聚焦图表控制、视图切换与最新分析，突出多水平联合判断主区。",
            badges=["多水平（Z-score法）", f"{level_count} 水平", context["overall_phase_label"], input_value_type_label],
            tone="accent",
        )
        entry_col, chart_col = st.columns([0.94, 1.24], gap="large")

        with chart_col:
            with st.container():
                render_section_intro(
                    title="图表与最新结果分析",
                    caption="在同一区查看多水平质控图、当前判读和异常备注入口。",
                    tone="accent",
                )
                phase_scope, view_mode, selected_level, y_axis_mode, standard_sd_limit = render_zscore_chart_controls(
                    selected_batch_id,
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

        with entry_col:
            with st.container():
                render_section_intro(
                    title="本次检测录入",
                    caption=f"请填写检测时间、检测人与各水平{input_value_type_label}，并结合 level 摘要判断当前阶段。",
                    tone="accent",
                )
                render_zscore_entry_section(context, selected_batch_id)

        render_section_intro(
            title="历史与次要操作区",
            caption="各水平摘要、厂家参考、维护区和导入导出统一放在主区下方，让图表控制与最新分析更集中。",
            badges=["水平摘要", "维护", "导入导出"],
            tone="muted",
        )
        _render_zscore_level_summary_cards_section(context)

        with st.container(border=True):
            render_section_intro(
                title="厂家参考值（仅作参考）",
                caption="可按水平查看或补充厂家参考值与来源备注。",
                tone="muted",
            )
            render_zscore_vendor_reference_section(context, selected_batch_id)

        lower_left, lower_right = st.columns([1.0, 1.0], gap="large")
        with lower_left:
            with st.container():
                render_section_intro(
                    title="记录维护",
                    caption="建靶期支持按单 level 点进行维护，正式期则保留只读追溯。",
                    tone="muted",
                )
                _render_zscore_maintenance_summary(context)
                render_zscore_maintenance_section(context)
        with lower_right:
            with st.container(border=True):
                render_section_intro(
                    title="导出与导入",
                    caption="导出当前批次数据与图表，并按模板导入 CSV。",
                    tone="muted",
                )
                render_zscore_export_import_section(context, selected_batch_id, chart_panel_state)

    with report_tab:
        render_zscore_monthly_report_section(selected_batch_id)
