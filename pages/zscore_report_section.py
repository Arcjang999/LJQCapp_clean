from __future__ import annotations

from dataclasses import asdict

import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st

from database import get_zscore_batch
from services.report_service import (
    ZScoreMonthlyReportPackage,
    build_zscore_monthly_preview_summary,
    list_zscore_report_month_options,
)
from ui.common import render_compact_stat_metrics, render_section_intro
from zscore_logic import format_level_id_display
from zscore_plotting import plot_zscore_single_level


def render_zscore_monthly_report_section(selected_batch_id: int) -> None:
    from ui.report_period import render_single_report
    batch = get_zscore_batch(selected_batch_id)
    months = list_zscore_report_month_options(selected_batch_id)
    with st.container(border=True):
        render_section_intro(title="多水平法质控报告", caption="选择日期，核对报告后生成PDF。报告、处理记录和附件一同保存，可分别下载。", tone="accent")
        st.caption(f"{batch['project_name']} · {_build_batch_display(batch)} · 正式期")
        if not months:
            st.info("当前批次暂无正式期检测记录。进入正式期并录入结果后，可生成质控报告。")
            return
        render_single_report('zscore', selected_batch_id, months=months, preview_renderer=_render_report_preview)


def _build_batch_display(batch) -> str:
    lot_no = str(batch["lot_no"] or "").strip()
    if lot_no:
        return f"质控批号 {lot_no}"
    created_at = str(batch["created_at"] or "").strip()
    if created_at:
        return f"创建于 {created_at}"
    return "当前批次"


def _render_report_preview(package: ZScoreMonthlyReportPackage) -> None:
    report = package.report
    st.markdown("**报告预览**")
    st.caption(
        f"报告期间：{report.report_period_label}｜生成时间：{report.generated_at}"
    )
    render_compact_stat_metrics([(label.replace('月度', '期间'), value) for label, value in build_zscore_monthly_preview_summary(report)])
    pending = getattr(report.statistics, 'pending_count', getattr(report.statistics, 'undetermined_count', 0))
    if pending:
        st.info(f'所选期间有 {pending} 次检测尚无有效质控结论，请到批次设置核对这些检测使用的均值和SD。')

    st.markdown("**期间质控概况**")
    st.write(report.overview_text)

    basic_info_df = pd.DataFrame(
        [
            ("实验室名称", report.basic_info.lab_name),
            ("科室名称", report.basic_info.department_name),
            ("检验员", report.basic_info.qc_owner_name),
            ("复核员", report.basic_info.reviewer_name),
            ("质控方法", report.basic_info.method_label),
            ("报告期间", report.report_period_label),
            ("输入值类型", report.basic_info.input_value_type_label),
            ("单位", report.basic_info.unit_symbol),
            ("检测方法", report.basic_info.detection_method),
            ("水平数", report.basic_info.level_count_label),
            ("各水平说明", report.basic_info.level_summary),
            ("当前规则组合", report.basic_info.template_label),
            ("质控品批号", report.basic_info.lot_no),
            ("仪器", report.basic_info.instrument),
            ("试剂", report.basic_info.reagent),
            ("质控品", report.basic_info.qc_material),
            ("浓度", report.basic_info.concentration),
            ("当前均值和标准差来源", report.basic_info.target_source_label.removeprefix("新版配置：")),
            ("来源说明", report.basic_info.target_source_detail),
        ],
        columns=["项目", "内容"],
    )
    st.dataframe(basic_info_df, hide_index=True, width="stretch")

    st.markdown("**各水平期间图**")
    level_label_map = {item.level_id: item.level_label for item in report.level_statistics}
    for level_id in package.active_levels:
        level_label = level_label_map.get(level_id, level_id)
        st.caption(level_label)
        chart_figure = plot_zscore_single_level(
            plot_df=package.monthly_plot_df.copy(),
            level_id=level_id,
            title=f"{format_level_id_display(level_id)} · 期间质控图",
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
                "期间正式期记录数": item.monthly_count,
                "期间均值": _format_optional_number(item.monthly_mean),
                "期间 SD": _format_level_stat_text(item.monthly_count, item.monthly_sd, "sd"),
                "实测变异系数（%）": _format_level_stat_text(item.monthly_count, item.monthly_cv, "cv"),
                "当前设定均值": _format_optional_number(item.target_mean),
                "当前设定 SD": _format_optional_number(item.target_sd),
                "当前 允许不精密度（CV）": _format_optional_percent(item.cv_limit),
            }
        )
    st.dataframe(pd.DataFrame(level_rows), hide_index=True, width="stretch")

    st.markdown("**异常/失控汇总表**")
    st.caption("请以“本次检测结论”判断整次检测是否在控；各水平的规则提示用于核对判定原因。")
    abnormal_rows = [asdict(record) for record in report.abnormal_records]
    if not abnormal_rows:
        st.info("所选期间未发现警告或失控检测记录。")
    else:
        abnormal_df = pd.DataFrame(abnormal_rows).rename(
            columns={
                "test_time": "检测时间",
                "run_sequence": "检测序号",
                "run_conclusion": "本次检测结论",
                "rule_hits": "触发规则",
                "level_evidence": "各水平触发规则及检测值",
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

    if report.handling_summaries:
        from services.out_of_control_report_service import handling_summary_paragraphs
        st.markdown("**失控处理摘要**")
        for paragraph in handling_summary_paragraphs(report.handling_summaries):
            st.write(paragraph)

    st.markdown("**异常说明**")
    st.write(report.abnormal_summary_text)

    st.markdown("**期间结论**")
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
