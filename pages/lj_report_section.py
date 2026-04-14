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
    build_lj_monthly_report_package,
    build_lj_monthly_report_pdf,
    list_lj_report_month_options,
    save_lj_monthly_report_snapshot,
)
from ui.common import render_compact_stat_metrics, render_section_intro, render_workbench_context_bar


def render_lj_monthly_report_section(selected_batch_id: int) -> None:
    batch = get_batch(selected_batch_id)
    report_scope = f"lj_monthly_report_{selected_batch_id}"
    preview_key = f"{report_scope}_preview"
    available_months = list_lj_report_month_options(selected_batch_id)

    with st.container(border=True):
        render_section_intro(
            title="单水平（LJ法）月度报告",
            caption="当前入口只支持单水平（LJ法）月报，不支持 Z-score 月报。",
            tone="accent",
        )
        render_workbench_context_bar(
            title="LJ 月报当前选择",
            caption="项目与批次沿用当前 LJ 工作台的选择结果。报告主统计只基于所选月份内的正式期数据。",
            items=[
                ("方法", "单水平（LJ法）"),
                ("项目名称", batch["project_name"]),
                ("批次标识", _build_batch_display(batch)),
                ("报告支持范围", "仅支持 LJ 单水平月报"),
            ],
            badges=["单水平（LJ法）", "仅支持 LJ 月报"],
        )

        if not available_months:
            st.info("当前批次暂无可选检测月份，请先录入数据后再生成月度报告。")
            st.session_state.pop(preview_key, None)
            return

        month_key = f"{report_scope}_month"
        selected_month = st.selectbox(
            "报告月份",
            options=available_months,
            index=0,
            key=month_key,
            format_func=_format_report_month_option,
        )

        generate_clicked = st.button(
            "生成月度报告",
            key=f"{report_scope}_generate",
            width="stretch",
        )
        if generate_clicked:
            try:
                package = build_lj_monthly_report_package(selected_batch_id, selected_month)
            except ValueError as exc:
                st.session_state.pop(preview_key, None)
                st.warning(str(exc))
            else:
                pdf_bytes = build_lj_monthly_report_pdf(package)
                snapshot_id = save_lj_monthly_report_snapshot(package)
                st.session_state[preview_key] = {
                    "batch_id": selected_batch_id,
                    "report_month": selected_month,
                    "package": package,
                    "pdf_bytes": pdf_bytes,
                    "file_name": package.report.file_name,
                    "snapshot_id": snapshot_id,
                }
                st.success("已生成单水平（LJ法）月度质控报告，可预览摘要并下载 PDF。")

        preview_state = st.session_state.get(preview_key)
        if not _preview_matches(preview_state, selected_batch_id, selected_month):
            return

        package = preview_state["package"]
        if not isinstance(package, LjMonthlyReportPackage):
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
        st.caption(f"已保存最小报告快照，快照编号：{preview_state['snapshot_id']}")


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


def _render_report_preview(package: LjMonthlyReportPackage) -> None:
    report = package.report
    st.markdown("**报告预览摘要**")
    st.caption(
        f"报告月份：{report.report_month_label}｜报告期间：{report.report_period_label}｜生成时间：{report.generated_at}"
    )
    render_compact_stat_metrics(build_lj_monthly_preview_summary(report))

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
        st.info("本月未发现警告或失控记录。")
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

    st.markdown("**异常说明**")
    st.write(report.abnormal_summary_text)

    st.markdown("**月度结论**")
    st.write(report.conclusion)

    st.markdown("**声明区**")
    st.caption(report.declaration)
