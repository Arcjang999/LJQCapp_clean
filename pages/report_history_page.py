from __future__ import annotations

from collections import defaultdict
from textwrap import dedent

import pandas as pd
import streamlit as st

from services.report_service import (
    LJ_METHOD_LABEL,
    REPORT_TYPE_LJ_MONTHLY,
    REPORT_TYPE_ZSCORE_MONTHLY,
    ReportHistoryRecord,
    ZSCORE_METHOD_LABEL,
    build_report_history_statistics_summary,
    filter_report_history_records,
    list_report_history_records,
    regenerate_report_from_history,
)
from ui.common import (
    render_compact_stat_metrics,
    render_html_block,
    render_section_intro,
    render_workbench_context_bar,
)


REGENERATION_STATE_PREFIX = "report_history_regenerated_"


def render_report_history_page() -> None:
    action_column, _ = st.columns([0.22, 0.78], gap="small")
    with action_column:
        if st.button("返回当前页面", key="close_report_history_page", use_container_width=True):
            st.session_state["show_report_history_page"] = False
            st.rerun()

    records = list_report_history_records()
    render_section_intro(
        title="报告历史",
        caption="统一查看 LJ 与 Z-score 月报记录，可按项目、方法学、批次和月份筛选。",
        eyebrow="全局入口",
        badges=["项目筛选", "摘要查看", "按当前数据重新生成"],
        tone="accent",
    )
    render_workbench_context_bar(
        title="历史记录概览",
        caption="按项目名称快速定位，再结合方法学、批次和生成时间确认目标报告。",
        items=[
            ("历史报告数", len(records)),
            ("涉及项目数", len({record.project_name for record in records})),
            ("单水平（LJ法）", sum(1 for record in records if record.report_type == REPORT_TYPE_LJ_MONTHLY)),
            ("多水平（Z-score法）", sum(1 for record in records if record.report_type == REPORT_TYPE_ZSCORE_MONTHLY)),
        ],
        badges=["快照摘要", "筛选定位", "当前数据重生成"],
    )

    if not records:
        st.info("当前还没有可展示的月度报告历史。请先在 LJ 或 Z-score 月报入口生成至少一份报告。")
        return

    with st.container():
        render_section_intro(
            title="筛选条件",
            caption="支持组合筛选项目名称、方法学、批次和报告月份。",
            badges=["可组合筛选", "组内按时间倒序"],
            tone="muted",
        )
        project_query, method_label, batch_query, report_month = _render_filters(records)

    filtered_records = filter_report_history_records(
        records,
        project_query=project_query,
        method_label=method_label,
        batch_query=batch_query,
        report_month=report_month,
    )
    if not filtered_records:
        st.info("当前筛选条件下没有匹配的历史记录，请调整项目名称、方法学、批次或报告月份条件。")
        return

    for project_name, project_records in _group_records_by_project(filtered_records):
        with st.container():
            render_section_intro(
                title=project_name,
                caption=f"共 {len(project_records)} 份历史记录，组内按生成时间倒序显示。",
                badges=[
                    f"LJ {sum(1 for item in project_records if item.report_type == REPORT_TYPE_LJ_MONTHLY)}",
                    f"Z-score {sum(1 for item in project_records if item.report_type == REPORT_TYPE_ZSCORE_MONTHLY)}",
                ],
                tone="default",
            )
            for record in project_records:
                _render_report_history_card(record)


def _render_filters(records: list[ReportHistoryRecord]) -> tuple[str, str, str, str]:
    method_options = ["全部", LJ_METHOD_LABEL, ZSCORE_METHOD_LABEL]
    available_methods = {record.method_label for record in records}
    method_options = [option for option in method_options if option == "全部" or option in available_methods]

    month_options = ["全部", *sorted({record.report_month for record in records if record.report_month}, reverse=True)]
    month_label_map = {record.report_month: record.report_month_label for record in records}

    filter_columns = st.columns(4, gap="small")
    with filter_columns[0]:
        project_query = st.text_input(
            "项目名称筛选",
            key="report_history_project_query",
            placeholder="输入项目名称关键字",
        )
    with filter_columns[1]:
        method_label = st.selectbox(
            "方法学筛选",
            options=method_options,
            index=0,
            key="report_history_method_filter",
        )
    with filter_columns[2]:
        batch_query = st.text_input(
            "批次筛选",
            key="report_history_batch_query",
            placeholder="输入批次关键字",
        )
    with filter_columns[3]:
        report_month = st.selectbox(
            "报告月份筛选",
            options=month_options,
            index=0,
            key="report_history_month_filter",
            format_func=lambda value: "全部月份" if value == "全部" else month_label_map.get(value, value),
        )

    return (
        str(project_query or "").strip(),
        "" if method_label == "全部" else str(method_label or "").strip(),
        str(batch_query or "").strip(),
        "" if report_month == "全部" else str(report_month or "").strip(),
    )


def _group_records_by_project(
    records: list[ReportHistoryRecord],
) -> list[tuple[str, list[ReportHistoryRecord]]]:
    grouped_records: dict[str, list[ReportHistoryRecord]] = defaultdict(list)
    for record in records:
        grouped_records[record.project_name].append(record)
    return [
        (project_name, grouped_records[project_name])
        for project_name in sorted(grouped_records, key=lambda value: value.casefold())
    ]


def _render_record_meta_row(record: ReportHistoryRecord) -> None:
    html = dedent(
        f"""
        <div class="main-entry-card-tags" style="margin-top:4px; margin-bottom:10px;">
            <span class="main-entry-card-tag">{record.method_label}</span>
            <span class="main-entry-card-tag">{record.batch_label}</span>
            <span class="main-entry-card-tag">{record.report_month_label}</span>
            <span class="main-entry-card-tag">{record.generated_at_label}</span>
            <span class="main-entry-card-tag">{record.input_value_type_label}</span>
        </div>
        """
    ).strip()
    render_html_block(html)


def _render_report_history_card(record: ReportHistoryRecord) -> None:
    export_identifier = record.file_name or f"历史记录 #{record.export_id}"
    regeneration_state_key = f"{REGENERATION_STATE_PREFIX}{record.export_id}"

    with st.container(border=True):
        title_col, info_col = st.columns([0.68, 0.32], gap="small")
        with title_col:
            st.markdown(f"**{record.project_name}**")
            st.caption(f"报告月份：{record.report_month_label}｜批次：{record.batch_label}")
        with info_col:
            render_compact_stat_metrics(
                [
                    ("方法标签", record.method_label),
                    ("生成时间", record.generated_at_label),
                ]
            )

        _render_record_meta_row(record)
        st.write(record.summary_text or "暂无摘要说明。")
        st.caption(f"文件名 / 导出标识：{export_identifier}")

        with st.expander("查看摘要", expanded=False):
            detail_rows = pd.DataFrame(
                [
                    ("项目名称", record.project_name),
                    ("方法标签", record.method_label),
                    ("批次标识", record.batch_label),
                    ("报告月份", record.report_month_label),
                    ("报告期间", record.report_period_label),
                    ("输入值类型", record.input_value_type_label),
                    ("生成时间", record.generated_at_label),
                    ("文件名 / 导出标识", export_identifier),
                ],
                columns=["字段", "内容"],
            )
            st.dataframe(detail_rows, hide_index=True, width="stretch")

            st.markdown("**关键统计摘要**")
            render_compact_stat_metrics(build_report_history_statistics_summary(record))

            st.markdown("**备注 / 说明**")
            st.write(record.summary_text or "暂无说明。")

            if record.overview_text:
                st.markdown("**月度概况**")
                st.write(record.overview_text)

            if record.conclusion_text:
                st.markdown("**结论摘要**")
                st.write(record.conclusion_text)

        action_left, action_right = st.columns(2, gap="small")
        with action_left:
            if st.button(
                "按当前数据重新生成 PDF",
                key=f"report_history_regenerate_{record.export_id}",
                type="primary",
                use_container_width=True,
            ):
                try:
                    regeneration_result = regenerate_report_from_history(record)
                except ValueError as exc:
                    st.session_state.pop(regeneration_state_key, None)
                    st.warning(str(exc))
                else:
                    st.session_state[regeneration_state_key] = {
                        "pdf_bytes": regeneration_result.pdf_bytes,
                        "file_name": regeneration_result.file_name,
                        "snapshot_id": regeneration_result.snapshot_id,
                    }
                    st.success("已按当前数据重新生成报告，可继续下载新的 PDF。")

        regeneration_state = st.session_state.get(regeneration_state_key)
        if isinstance(regeneration_state, dict):
            st.caption("这是按当前数据重新生成的 PDF，内容可能与历史记录当时不同，并非下载历史原始旧 PDF。")
            with action_right:
                st.download_button(
                    label="下载重新生成的 PDF",
                    data=regeneration_state["pdf_bytes"],
                    file_name=regeneration_state["file_name"],
                    mime="application/pdf",
                    key=f"report_history_download_{record.export_id}",
                    use_container_width=True,
                )
            st.caption(f"本次重新生成已新增历史快照 #{regeneration_state['snapshot_id']}")
        else:
            with action_right:
                st.button(
                    "下载重新生成的 PDF",
                    key=f"report_history_download_placeholder_{record.export_id}",
                    disabled=True,
                    use_container_width=True,
                )
