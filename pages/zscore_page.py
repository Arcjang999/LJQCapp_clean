from __future__ import annotations

from services.terminology_service import display_generated_text

import pandas as pd
import streamlit as st

from plotting import close_figure
from pages.zscore_config_section import prepare_zscore_v12_project_batch_context, render_zscore_v12_configuration_selection
from pages.zscore_report_section import render_zscore_monthly_report_section
from pages.zscore_sections import (
    build_zscore_workbench_context,
    format_zscore_level_display,
    render_zscore_chart_analysis_section,
    render_zscore_chart_controls,
    render_zscore_entry_section,
    render_zscore_rule_records_overview_section,
    render_zscore_vendor_reference_section,
)
from ui.common import (
    TEXT,
    format_optional_float,
    format_zscore_template_display_name,
)
from zscore_logic import (
    PHASE_FORMAL_QC,
    calculate_formal_realtime_stats,
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
            "结束日期早于开始日期，请重新选择日期后查看统计。",
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


def _render_zscore_level_summary_compact_section(context: dict[str, object]) -> None:
    realtime_profiles, range_text, start_date, end_date, has_formal_data, range_warning = _build_filtered_realtime_profiles(context)
    batch_id = int(context["batch"]["id"])
    level_label_map = context["level_label_map"]
    level_target_profiles = context["level_target_profiles"]
    required_level_ids = context["required_level_ids"]
    building_summary = _build_level_building_summary(context)

    rows: list[dict[str, object]] = []
    for level_id in required_level_ids:
        profile = level_target_profiles[level_id]
        realtime_profile = realtime_profiles.get(level_id, {})
        summary_item = building_summary.get(level_id, {})
        display_label, _ = format_zscore_level_display(level_id, level_label_map)
        building_mean = profile.get("final_target_mean") if profile.get("is_ready") else profile.get("provisional_mean")
        building_sd = profile.get("final_target_sd") if profile.get("is_ready") else profile.get("provisional_sd")
        building_cv = profile.get("final_target_cv") if profile.get("is_ready") else profile.get("provisional_cv")
        rows.append(
            {
                "水平": display_label,
                "参数建立点": int(summary_item.get("effective_n", 0) or 0),
                "禁用": int(summary_item.get("disabled_n", 0) or 0),
                "阶段": display_generated_text(str(profile.get("phase_label") or "-")),
                "当前均值": format_optional_float(building_mean),
                "当前 SD": format_optional_float(building_sd),
                "设定变异系数（%）": format_optional_float(building_cv if profile.get("is_ready") else None, digits=2, suffix="%"),
                "建立期实测变异系数（%）": format_optional_float(profile.get("provisional_cv"), digits=2, suffix="%"),
                "实时均值": format_optional_float(realtime_profile.get("realtime_mean")),
                "实时SD": format_optional_float(realtime_profile.get("realtime_sd")),
                "实测变异系数（%）": format_optional_float(realtime_profile.get("realtime_cv"), digits=2, suffix="%"),
            }
        )

    with st.container(border=True):
        st.markdown("**各水平统计摘要**")
        if range_text is not None:
            st.caption(f"正式期实时统计范围：{range_text}，仅包含正式期内在控数据。")
        else:
            st.info("当前批次还没有正式期数据，正式期实时统计暂为空。")

        st.dataframe(
            pd.DataFrame(rows),
            hide_index=True,
            width="stretch",
            height=112 + max(len(rows), 1) * 36,
        )

        if has_formal_data:
            with st.expander("正式期实时统计时间范围", expanded=False):
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


def render_zscore_page() -> None:
    st.subheader("多水平法")
    projects_df, selected_project_id, batches_df, selected_batch_id, issues = prepare_zscore_v12_project_batch_context()
    if selected_batch_id is None:
        render_zscore_v12_configuration_selection(
            st.container(), projects_df, selected_project_id, batches_df, selected_batch_id, issues,
        )
        from ui.record_actions import render_pending_record_dialog
        from ui.data_transfer import render_pending_transfer_dialog
        from ui.zscore_reference_dialog import render_pending_zscore_reference_dialog
        if st.session_state.get('qc_record_dialog'):
            render_pending_record_dialog('zscore', None)
        elif st.session_state.get('qc_transfer_dialog'):
            render_pending_transfer_dialog('zscore')
        else:
            render_pending_zscore_reference_dialog()
        return
    tab_names = [TEXT["current_batch"], "质控报告"]
    if st.session_state.get('zscore_workbench_tabs') in ('Z-score 月报', '月度报告'):
        st.session_state['zscore_workbench_tabs'] = '质控报告'
    if st.session_state.get('zscore_workbench_tabs') not in tab_names:
        st.session_state['zscore_workbench_tabs'] = TEXT['current_batch']
    work_tab, report_tab = st.tabs(tab_names, key='zscore_workbench_tabs', on_change='rerun')
    context = build_zscore_workbench_context(selected_batch_id)
    batch = context["batch"]
    template = context["template"]
    input_value_type_label = context["input_value_type_label"]
    level_count = int(context["level_count"])
    required_n = int(context["required_n"])

    with work_tab:
        st.caption(f"{batch['project_name']} · {batch['instrument']} · 质控批号 {batch['lot_no']} · "
                   f"{level_count} 水平 · {context['overall_phase_label']} · {input_value_type_label}")
        with st.expander('批次资料与质量目标', expanded=False):
            st.write(f"质控品：{batch['qc_material']}｜试剂：{batch['reagent']}｜浓度：{batch['concentration']}")
            batch_details = dict(batch)
            st.caption(f"单位：{batch_details.get('unit_symbol') or '-'} · "
                       f"检测方法：{batch_details.get('method_name') or '-'} · "
                       f"批号效期：{batch_details.get('v11_expiry_date') or '-'}")
            st.caption(f"规则组合：{format_zscore_template_display_name(template)}｜参数建立点数：{required_n}")
            from ui.quality_targets import render_batch_quality
            render_batch_quality("zscore", selected_batch_id)
        entry_col, chart_col = st.columns([0.94, 1.24], gap="large")
        with chart_col:
            st.markdown('**图表与最新结果分析**')
            phase_scope, view_mode, selected_level, y_axis_mode, standard_sd_limit = render_zscore_chart_controls(
                selected_batch_id, template, context["default_phase_scope"], context["level_label_map"],
            )
            chart_panel_state = render_zscore_chart_analysis_section(
                context, phase_scope, view_mode, selected_level, y_axis_mode, standard_sd_limit,
            )
        with entry_col:
            st.markdown('**本次检测录入**')
            render_zscore_entry_section(context, selected_batch_id)
            _render_zscore_level_summary_compact_section(context)
        from ui.data_transfer import render_transfer_toolbar
        render_transfer_toolbar('zscore', context, selected_batch_id, chart_panel_state)
        render_zscore_rule_records_overview_section(context)
        render_zscore_vendor_reference_section(context, selected_batch_id)
        close_figure(chart_panel_state.get("figure"))
    with report_tab:
        render_zscore_monthly_report_section(selected_batch_id)
    # A page run can render only one native dialog, including other tab actions.
    from ui.record_actions import render_pending_record_dialog
    from ui.zscore_reference_dialog import render_pending_zscore_reference_dialog
    if not st.session_state.get('qc_record_dialog') and not st.session_state.get('qc_transfer_dialog'):
        render_pending_zscore_reference_dialog()
    elif st.session_state.get('qc_record_dialog') and not st.session_state.get('qc_transfer_dialog'):
        render_pending_record_dialog('zscore', selected_batch_id)
