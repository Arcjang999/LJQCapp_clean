from __future__ import annotations

from dataclasses import asdict

import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st

from database import get_batch
from plotting import plot_lj_chart
from services.report_service import (
    LjMonthlyReportPackage,
    build_lj_monthly_preview_summary,
    list_lj_report_month_options,
)
from ui.common import render_compact_stat_metrics, render_section_intro


def render_lj_monthly_report_section(selected_batch_id: int) -> None:
    from ui.report_period import render_single_report
    batch = get_batch(selected_batch_id)
    months = list_lj_report_month_options(selected_batch_id)
    with st.container(border=True):
        render_section_intro(title="单水平（LJ）质控报告", caption="选择月份或起止日期，核对后生成并保存 PDF 和完整归档。", tone="accent")
        st.caption(f"{batch['project_name']} · {_build_batch_display(batch)} · 正式期")
        if not months:
            st.info("当前批次暂无正式期检测记录。进入正式期并录入结果后，可生成质控报告。")
            return
        render_single_report('lj', selected_batch_id, months=months, preview_renderer=_render_report_preview)


def _build_batch_display(batch) -> str:
    lot_no = str(batch["lot_no"] or "").strip()
    if lot_no:
        return f"质控批号 {lot_no}"
    created_at = str(batch["created_at"] or "").strip()
    if created_at:
        return f"创建于 {created_at}"
    return "当前批次"


def _render_report_preview(package: LjMonthlyReportPackage) -> None:
    report = package.report
    st.markdown("**报告预览**")
    st.caption(
        f"报告期间：{report.report_period_label}｜生成时间：{report.generated_at}"
    )
    render_compact_stat_metrics([(label.replace('月度', '期间'), value) for label, value in build_lj_monthly_preview_summary(report)])
    pending = getattr(report.statistics, 'pending_count', getattr(report.statistics, 'undetermined_count', 0))
    if pending:
        st.info(f'所选期间有 {pending} 次检测待判读，请核对适用均值和标准差。')

    st.markdown("**期间质控概况**")
    st.write(report.overview_text)

    basic_info_df = pd.DataFrame(
        [
            ("实验室名称", report.basic_info.lab_name),
            ("科室名称", report.basic_info.department_name),
            ("检验员", report.basic_info.qc_owner_name),
            ("复核员", report.basic_info.reviewer_name),
            ("方法", report.basic_info.method_label),
            ("检测方法", report.basic_info.detection_method),
            ("单位", report.basic_info.unit_symbol),
            ("报告期间", report.report_period_label),
            ("输入值类型", report.basic_info.input_value_type_label),
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

    chart_figure = plot_lj_chart(
        qc_df=package.formal_df.copy(),
        stats=package.stats,
        title=report.chart_title,
        view_mode="正式质控图",
        y_axis_mode="标准视图",
        standard_sd_limit=4.0,
        y_axis_label=report.chart_axis_label,
    )
    st.pyplot(chart_figure, width="stretch")
    plt.close(chart_figure)

    st.markdown("**异常/失控记录表**")
    abnormal_rows = [asdict(record) for record in report.abnormal_records]
    if not abnormal_rows:
        st.info("所选期间未发现警告或失控记录。")
    else:
        abnormal_df = pd.DataFrame(abnormal_rows).rename(
            columns={
                "test_time": "检测时间",
                "sequence": "检测序号",
                "value": "结果值",
                "status": "状态",
                "rule_hits": "触发规则",
                "manual_note": "手动备注",
            }
        )
        abnormal_df["结果值"] = abnormal_df["结果值"].map(lambda value: f"{float(value):.4f}")
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
