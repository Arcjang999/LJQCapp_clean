from __future__ import annotations

from dataclasses import asdict

import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st

from database import get_zscore_batch
from services.report_service import (
    ZSCORE_METHOD_LABEL,
    ZScoreMonthlyReportPackage,
    build_zscore_monthly_preview_summary,
    build_zscore_monthly_report_package,
    build_zscore_monthly_report_pdf,
    list_zscore_report_month_options,
    save_zscore_monthly_report_snapshot,
)
from ui.common import render_compact_stat_metrics, render_section_intro, render_workbench_context_bar
from zscore_plotting import plot_zscore_single_level


def render_zscore_monthly_report_section(selected_batch_id: int) -> None:
    batch = get_zscore_batch(selected_batch_id)
    report_scope = f"zscore_monthly_report_{selected_batch_id}"
    preview_key = f"{report_scope}_preview"
    available_months = list_zscore_report_month_options(selected_batch_id)

    with st.container(border=True):
        render_section_intro(
            title="多水平（Z-score法）月度质控报告",
            caption="基于当前批次生成多水平（Z-score法）月度质控报告。",
            tone="accent",
        )
        render_workbench_context_bar(
            title="Z-score 月报当前选择",
            caption="项目与批次沿用当前 Z-score 工作台选择；报告统计仅包含所选月份内的正式期检测记录。",
            items=[
                ("方法", ZSCORE_METHOD_LABEL),
                ("项目名称", batch["project_name"]),
                ("批次标识", _build_batch_display(batch)),
                ("报告支持范围", "仅支持 Z-score 多水平月报"),
            ],
            badges=[ZSCORE_METHOD_LABEL, "仅支持 Z-score 月报"],
        )

        if not available_months:
            st.info("当前批次暂无可选检测月份，请先录入数据后再生成月度报告。")
            st.session_state.pop(preview_key, None)
            return

        selected_month = st.selectbox(
            "报告月份",
            options=available_months,
            index=0,
            key=f"{report_scope}_month",
            format_func=_format_report_month_option,
        )

        generate_clicked = st.button(
            "生成月度报告",
            key=f"{report_scope}_generate",
            width="stretch",
        )
        if generate_clicked:
            try:
                package = build_zscore_monthly_report_package(selected_batch_id, selected_month)
            except ValueError as exc:
                st.session_state.pop(preview_key, None)
                st.warning(str(exc))
            else:
                pdf_bytes = build_zscore_monthly_report_pdf(package)
                snapshot_id = save_zscore_monthly_report_snapshot(package)
                st.session_state[preview_key] = {
                    "batch_id": selected_batch_id,
                    "report_month": selected_month,
                    "package": package,
                    "pdf_bytes": pdf_bytes,
                    "file_name": package.report.file_name,
                    "snapshot_id": snapshot_id,
                }
                st.success("已生成多水平（Z-score法）月度质控报告，可预览并下载 PDF。")

        preview_state = st.session_state.get(preview_key)
        if not _preview_matches(preview_state, selected_batch_id, selected_month):
            return

        package = preview_state["package"]
        if not isinstance(package, ZScoreMonthlyReportPackage):
            return

        st.divider()
        _render_report_preview(package)
        st.download_button(
            label="下载 PDF",
            data=preview_state["pdf_bytes"],
            file_name=preview_state["file_name"],
            mime="application/pdf",
            key=f"{report_scope}_download",
            width="stretch",
        )
        st.caption(f"已保存报告快照，编号：{preview_state['snapshot_id']}")


def _preview_matches(
    preview_state: dict[str, object] | None,
    selected_batch_id: int,
    selected_month: str,
) -> bool:
    if not isinstance(preview_state, dict):
        return False
    return (
        int(preview_state.get("batch_id", -1)) == int(selected_batch_id)
        and str(preview_state.get("report_month", "")) == str(selected_month)
    )


def _build_batch_display(batch) -> str:
    lot_no = str(batch["lot_no"] or "").strip()
    if lot_no:
        return f"质控批号 {lot_no}"
    created_at = str(batch["created_at"] or "").strip()
    if created_at:
        return f"创建于 {created_at}"
    return "当前批次"


def _format_report_month_option(value: str) -> str:
    period = pd.Period(str(value), freq="M")
    return f"{period.year}年{period.month:02d}月"


def _render_report_preview(package: ZScoreMonthlyReportPackage) -> None:
    report = package.report
    st.markdown("**报告预览**")
    st.caption(
        f"报告月份：{report.report_month_label}｜报告期间：{report.report_period_label}｜生成时间：{report.generated_at}"
    )
    render_compact_stat_metrics(build_zscore_monthly_preview_summary(report))

    st.markdown("**本月质控概况**")
    st.write(report.overview_text)

    basic_info_df = pd.DataFrame(
        [
            ("实验室名称", report.basic_info.lab_name),
            ("科室名称", report.basic_info.department_name),
            ("质控负责人", report.basic_info.qc_owner_name),
            ("审核人", report.basic_info.reviewer_name),
            ("方法", report.basic_info.method_label),
            ("报告月份", report.report_month_label),
            ("报告期间", report.report_period_label),
            ("输入值类型", report.basic_info.input_value_type_label),
            ("水平数", report.basic_info.level_count_label),
            ("各水平说明", report.basic_info.level_summary),
            ("当前规则组合", report.basic_info.template_label),
            ("质控品批号", report.basic_info.lot_no),
            ("仪器", report.basic_info.instrument),
            ("试剂", report.basic_info.reagent),
            ("质控品", report.basic_info.qc_material),
            ("浓度", report.basic_info.concentration),
            ("当前靶值来源", report.basic_info.target_source_label),
            ("来源说明", report.basic_info.target_source_detail),
        ],
        columns=["字段", "内容"],
    )
    st.dataframe(basic_info_df, hide_index=True, width="stretch")

    st.markdown("**各水平月度图**")
    level_label_map = {item.level_id: item.level_label for item in report.level_statistics}
    for level_id in package.active_levels:
        level_label = level_label_map.get(level_id, level_id)
        chart_figure = plot_zscore_single_level(
            plot_df=package.monthly_plot_df.copy(),
            level_id=level_id,
            title=f"{level_label} 月度图",
            phase_scope="formal",
            y_axis_mode="标准视图",
            standard_sd_limit=4.0,
            y_axis_label=report.chart_axis_label,
        )
        st.pyplot(chart_figure, width="stretch")
        plt.close(chart_figure)

    st.markdown("**各水平统计摘要**")
    level_rows = []
    for item in report.level_statistics:
        level_rows.append(
            {
                "水平": item.level_label,
                "本月正式期记录数": item.monthly_count,
                "月度均值": _format_optional_number(item.monthly_mean),
                "月度 SD": _format_level_stat_text(item.monthly_count, item.monthly_sd, "sd"),
                "月度 CV%": _format_level_stat_text(item.monthly_count, item.monthly_cv, "cv"),
                "当前目标均值": _format_optional_number(item.target_mean),
                "当前目标 SD": _format_optional_number(item.target_sd),
                "当前 CV 要求": _format_optional_percent(item.cv_limit),
            }
        )
    st.dataframe(pd.DataFrame(level_rows), hide_index=True, width="stretch")

    st.markdown("**异常/失控汇总表**")
    st.caption("本次检测结论为最终判定，各水平触发证据用于说明规则触发情况。")
    abnormal_rows = [asdict(record) for record in report.abnormal_records]
    if not abnormal_rows:
        st.info("本月未发现警告或失控检测记录。")
    else:
        abnormal_df = pd.DataFrame(abnormal_rows).rename(
            columns={
                "test_time": "检测时间",
                "run_sequence": "检测序号",
                "run_conclusion": "本次检测结论",
                "rule_hits": "触发规则",
                "level_evidence": "各水平触发证据",
                "error_type": "误差类型",
                "manual_note": "手动备注",
            }
        )
        abnormal_df["手动备注"] = abnormal_df["手动备注"].replace("", "未填写")
        st.dataframe(abnormal_df, hide_index=True, width="stretch")

    st.markdown("**原因与纠正措施**")
    if report.corrective_actions:
        for action in report.corrective_actions:
            st.markdown(f"- {action}")
    else:
        st.write(report.corrective_actions_empty_text)

    st.markdown("**异常说明**")
    st.write(report.abnormal_summary_text)

    st.markdown("**月度结论**")
    st.write(report.conclusion)

    st.markdown("**报告声明**")
    st.caption(report.declaration)


def _format_optional_number(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{float(value):.4f}"


def _format_optional_percent(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{float(value):.2f}%"


def _format_level_stat_text(monthly_count: int, value: float | None, metric: str) -> str:
    if metric in {"sd", "cv"} and monthly_count < 2:
        return "暂不计算（样本数不足）"
    if metric == "cv":
        return _format_optional_percent(value)
    return _format_optional_number(value)
