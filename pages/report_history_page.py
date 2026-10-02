from __future__ import annotations

from collections import defaultdict
from textwrap import dedent

import pandas as pd
import streamlit as st
from services.search_service import SEARCH_HELP

from services.report_service import (
    LJ_METHOD_LABEL,
    REPORT_TYPE_LJ_MONTHLY,
    REPORT_TYPE_ZSCORE_MONTHLY,
    REPORT_TYPE_EVENT,
    ReportHistoryRecord,
    ZSCORE_METHOD_LABEL,
    build_report_history_statistics_summary,
    filter_report_history_records,
    list_report_history_records,
    get_report_history_record,
    regenerate_report_from_history,
    read_report_history_pdf,
)
from services.project_config_service import QC_METHOD_LABELS, QC_METHOD_LEGACY_LABELS
from ui.common import (
    render_compact_stat_metrics,
    render_html_block,
    render_section_intro,
    render_workbench_context_bar,
)


REGENERATION_STATE_PREFIX = "report_history_regenerated_"


def _display_method_label(value: str) -> str:
    return QC_METHOD_LABELS.get(QC_METHOD_LEGACY_LABELS.get(value, ''), value)


def render_report_history_page() -> None:
    action_column, _ = st.columns([0.22, 0.78], gap="small")
    with action_column:
        if st.button("返回当前页面", key="close_report_history_page", use_container_width=True):
            st.session_state["show_report_history_page"] = False
            destination = st.session_state.pop('report_history_return_page', None)
            st.session_state.pop('report_history_selected_export_id', None)
            if destination:
                from ui.common import open_global_page
                open_global_page(destination)
            st.rerun()

    if st.button("批量月报与月度汇总", key="history_batch_monthly"):
        from ui.common import open_global_page
        open_global_page("show_batch_monthly_reports_page")

    records = list_report_history_records()
    selected_export = st.session_state.get('report_history_selected_export_id')
    if selected_export is not None:
        st.subheader('所选报告')
        try:
            selected_record = get_report_history_record(int(selected_export))
        except ValueError as exc:
            st.error(str(exc))
        else:
            _render_report_history_card(selected_record)
        if st.button('显示全部报告历史', key='report_history_clear_selected'):
            st.session_state.pop('report_history_selected_export_id', None)
            st.rerun()
        return
    from ui.common import render_module_header
    render_module_header(
        title="报告历史",
        caption="查看月度质控报告和失控处理报告。可下载当时保存的原报告，也可按当前资料生成新报告。",
        eyebrow="报告管理",
        tone="reports",
    )
    render_workbench_context_bar(
        title="历史记录概览",
        caption="按项目名称查找，再结合质控方法、批次和生成时间确认报告。",
        items=[
            ("历史报告数", len(records)),
            ("涉及项目数", len({record.project_name for record in records})),
            ("单水平（LJ）", sum(1 for record in records if record.report_type == REPORT_TYPE_LJ_MONTHLY)),
            ("多水平法", sum(1 for record in records if record.report_type == REPORT_TYPE_ZSCORE_MONTHLY)),
            ("失控处理报告", sum(1 for record in records if record.report_type == REPORT_TYPE_EVENT)),
        ],
    )

    if not records:
        st.info("当前还没有报告历史，可从月报或失控处理记录生成报告。")
        return

    with st.container():
        render_section_intro(
            title="筛选条件",
            caption="可按项目名称、质控方法、批次和报告月份筛选。",
            tone="muted",
        )
        project_query, method_label, batch_query, report_month = _render_filters(records)
        type_col, instrument_col = st.columns(2)
        type_labels = {"": "全部报告", REPORT_TYPE_LJ_MONTHLY: "单水平月报", REPORT_TYPE_ZSCORE_MONTHLY: "多水平月报", REPORT_TYPE_EVENT: "失控处理报告"}
        report_type = type_col.selectbox("报告类型", list(type_labels), format_func=type_labels.get,
                                        key="report_history_type_filter")
        instrument_query = instrument_col.text_input("仪器筛选", key="report_history_instrument_query", help=SEARCH_HELP)

    filtered_records = filter_report_history_records(
        records,
        project_query=project_query,
        method_label=method_label,
        batch_query=batch_query,
        report_month=report_month,
        report_type=report_type,
        instrument_query=instrument_query,
    )
    if not filtered_records:
        st.info("当前筛选条件下没有匹配的历史记录，请调整项目名称、质控方法、批次或报告月份。")
        return

    for project_name, project_records in _group_records_by_project(filtered_records):
        with st.container():
            render_section_intro(
                title=project_name,
                caption=f"共 {len(project_records)} 份历史记录，组内按生成时间倒序显示。",
                badges=[
                    f"单水平（LJ） {sum(1 for item in project_records if item.report_type == REPORT_TYPE_LJ_MONTHLY)}",
                    f"多水平法 {sum(1 for item in project_records if item.report_type == REPORT_TYPE_ZSCORE_MONTHLY)}",
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
            key="report_history_project_query", help=SEARCH_HELP,
            placeholder="输入项目名称关键字",
        )
    with filter_columns[1]:
        method_label = st.selectbox(
            "质控方法筛选",
            options=method_options,
            index=0,
            format_func=_display_method_label,
            key="report_history_method_filter",
        )
    with filter_columns[2]:
        batch_query = st.text_input(
            "批次筛选",
            key="report_history_batch_query", help=SEARCH_HELP,
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
            <span class="main-entry-card-tag">{_display_method_label(record.method_label)}</span>
            <span class="main-entry-card-tag">{record.batch_label}</span>
            <span class="main-entry-card-tag">{record.report_month_label}</span>
            <span class="main-entry-card-tag">{record.generated_at_label}</span>
            <span class="main-entry-card-tag">{record.input_value_type_label}</span>
        </div>
        """
    ).strip()
    render_html_block(html)


def _render_report_history_card(record: ReportHistoryRecord) -> None:
    if record.report_type == REPORT_TYPE_EVENT:
        _render_event_report_card(record)
        return
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
                    ("质控方法", _display_method_label(record.method_label)),
                    ("生成时间", record.generated_at_label),
                ]
            )

        _render_record_meta_row(record)
        st.write(record.summary_text or "暂无摘要说明。")
        st.caption(f"导出文件：{export_identifier}")

        with st.expander("查看摘要", expanded=False):
            detail_rows = pd.DataFrame(
                [
                    ("项目名称", record.project_name),
                    ("质控方法", _display_method_label(record.method_label)),
                    ("批次标识", record.batch_label),
                    ("报告月份", record.report_month_label),
                    ("报告期间", record.report_period_label),
                    ("输入值类型", record.input_value_type_label),
                    ("生成时间", record.generated_at_label),
                    ("导出文件", export_identifier),
                ],
                columns=["项目", "内容"],
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

        _render_archived_download(record)
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
            st.caption("这份新报告使用当前检测数据和设置，内容可能与原报告不同；原报告仍可下载。")
            with action_right:
                st.download_button(
                    label="下载重新生成的 PDF",
                    data=regeneration_state["pdf_bytes"],
                    file_name=regeneration_state["file_name"],
                    mime="application/pdf",
                    key=f"report_history_download_{record.export_id}",
                    use_container_width=True,
                )
            st.caption(f"已新增报告记录 #{regeneration_state['snapshot_id']}")
        else:
            with action_right:
                st.button(
                    "下载重新生成的 PDF",
                    key=f"report_history_download_placeholder_{record.export_id}",
                    disabled=True,
                    use_container_width=True,
                )


def _render_archived_download(record: ReportHistoryRecord) -> None:
    try:
        data = read_report_history_pdf(record)
    except ValueError as exc:
        st.caption(str(exc))
    else:
        st.download_button("下载已保存的原 PDF", data, file_name=record.file_name, mime="application/pdf",
                           key=f"report_history_original_{record.export_id}", use_container_width=True)


def _render_event_report_card(record: ReportHistoryRecord) -> None:
    from ui.out_of_control import navigate_event
    package = record.summary_json
    event = package["event"]
    origin = event["origin_snapshot"]
    with st.container(border=True):
        st.markdown(f"**{record.project_name} · 失控处理报告**")
        st.caption(f"报告编号：{package['event_report_no']}｜生成时间：{record.generated_at_label}")
        st.write(record.summary_text)
        st.caption(f"仪器：{package['instrument_name']}｜原检测：{record.report_period_label}｜实际质控批号：{record.batch_label}")
        with st.expander("查看生成报告时的处理记录"):
            content = event.get("content") or {}
            st.caption(f"本报告采用第 {package['revision_no']} 版处理记录。后续补充或更正处理内容，不会改变这份报告。")
            for key, label in (("cause_analysis", "原因分析"), ("corrective_action", "纠正措施"),
                               ("effect_description", "处理效果"), ("handler_text", "处理人"),
                               ("confirmer_text", "确认人"), ("confirmed_at", "确认时间")):
                st.markdown(f"**{label}**")
                st.write(content.get(key) or "未记录")
        _render_archived_download(record)
        if st.button("查看当前处理记录", key=f"report_history_event_{record.export_id}"):
            navigate_event(event["source_type"], event["source_id"], event_id=event["event_id"])
            st.rerun()
        if st.button("按当前处理版本生成报告", key=f"report_history_event_regenerate_{record.export_id}"):
            try:
                result = regenerate_report_from_history(record)
            except ValueError as exc:
                st.warning(str(exc))
            else:
                st.download_button("下载当前处理版本报告", result.pdf_bytes, file_name=result.file_name,
                                   mime="application/pdf", key=f"report_history_event_new_{record.export_id}")
                st.caption("处理记录未修改时，下载的是此前保存的报告。补充或更正处理记录后，可生成新报告。")
