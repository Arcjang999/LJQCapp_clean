"""One operation at a time for workbench imports and data/chart downloads."""
from __future__ import annotations

from hashlib import sha256
import json
import logging
import sqlite3
from uuid import uuid4

import pandas as pd
import streamlit as st

from import_review import (
    build_lj_building_template_dataframe,
    build_review_issues_dataframe,
    build_zscore_building_template_dataframe,
    review_lj_building_import_csv,
    review_lj_formal_import_csv,
    review_zscore_building_import_csv,
    review_zscore_formal_import_csv,
)
from services.export_utils import dataframe_to_csv_bytes, dataframe_to_xlsx_bytes
from services.lot_lifecycle_service import import_reviewed_results, review_import_lots
from ui.common import build_safe_export_name, render_import_review_summary


DIALOG_KEY = "qc_transfer_dialog"
PHASE_LABELS = {"building": "建靶期", "formal": "正式期"}
_OTHER_DIALOG_KEYS = ("qc_record_dialog", "zscore_reference_dialog", "qc_report_dialog", "qc_monthly_report_dialog")


def _ready(method, context):
    return bool(context["stats"].get("target_ready")) if method == "lj" else bool(context["formal_rules_enabled"])


def _building_locked(method, context):
    if method == "lj":
        return _ready(method, context)
    from zscore_logic import PHASE_TARGET_BUILDING
    return context["overall_phase"] != PHASE_TARGET_BUILDING


def _fresh_import_context(method, batch_id):
    """Read current phase/records without the workbench's rendering side effects."""
    from database import get_batch, get_results, read_snapshot
    with read_snapshot():
        if method == "lj":
            from qc_logic import calculate_qc_results
            from services.lot_lifecycle_service import target_profile
            batch = get_batch(batch_id)
            results = get_results(batch_id, include_manual_note=True)
            qc_df, stats = calculate_qc_results(results, int(batch["target_n"]))
            if target_profile("lj", batch_id):
                stats["target_ready"] = True
            return dict(batch=batch, results_df=results, qc_df=qc_df, stats=stats,
                        input_value_type=batch["input_value_type"])
        from zscore_logic import (
            determine_zscore_phase, get_zscore_level_targets, get_zscore_runs,
            get_effective_building_run_count, resolve_zscore_batch_context, should_enable_formal_rules,
        )
        resolved = resolve_zscore_batch_context(batch_id)
        targets = get_zscore_level_targets(batch_id, resolved["template_id"], required_n=resolved["required_n"])
        return dict(resolved, input_value_type=resolved["batch"]["input_value_type"],
                    history_runs=get_zscore_runs(batch_id, resolved["template_id"]),
                    effective_building_count=get_effective_building_run_count(targets, resolved["required_level_ids"]),
                    overall_phase=determine_zscore_phase(targets, resolved["required_level_ids"]),
                    formal_rules_enabled=should_enable_formal_rules(targets, resolved["required_level_ids"]))


def _import_source_signature(method, context):
    records = context["results_df"].to_json(orient="split", date_format="iso") if method == "lj" else context["history_runs"]
    payload = dict(batch=dict(context["batch"]), ready=_ready(method, context), records=records)
    if method == "zscore":
        payload.update(required_n=context["required_n"], template_id=context["template_id"],
                       level_ids=context["required_level_ids"], phase=context["overall_phase"])
    return sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def review_data_import(method, context, batch_id, phase, data):
    """Keep both original file validation and frozen actual-lot selections."""
    if phase not in PHASE_LABELS or method not in ("lj", "zscore"):
        raise ValueError("请选择当前批次支持的导入阶段。")
    if phase == "building" and _building_locked(method, context):
        raise ValueError("当前批次已完成建靶，不能继续导入建靶期数据。")
    if method == "lj":
        args = dict(file_bytes=data, existing_results_df=context["results_df"],
                    target_n=int(context["batch"]["target_n"]), input_value_type=context["input_value_type"])
        review = (review_lj_building_import_csv(**args) if phase == "building" else
                  review_lj_formal_import_csv(**args, target_ready=_ready(method, context)))
    else:
        from zscore_logic import PHASE_FORMAL_QC, PHASE_TARGET_BUILDING
        runs = context["history_runs"]
        building = [run for run in runs if run.get("phase") == PHASE_TARGET_BUILDING]
        existing = building if phase == "building" else runs
        args = dict(file_bytes=data, existing_results_df=pd.DataFrame({"test_time": [run.get("test_time") for run in existing]}),
                    level_count=int(context["level_count"]), input_value_type=context["input_value_type"])
        review = (review_zscore_building_import_csv(**args, target_n=int(context["required_n"]),
                    existing_effective_count=int(context["effective_building_count"])) if phase == "building" else
                  review_zscore_formal_import_csv(**args, target_ready=_ready(method, context),
                    existing_formal_count=sum(run.get("phase") == PHASE_FORMAL_QC for run in runs)))
    return review_import_lots(review, method, batch_id)


def commit_data_import(method, batch_id, phase, review, source_signature):
    """Phase/freshness and the entire file commit share the original transaction."""
    from database import atomic_write
    if phase not in PHASE_LABELS or method not in ("lj", "zscore"):
        raise ValueError("请选择当前批次支持的导入阶段。")
    if not review or review["summary"]["has_blocking_errors"] or not review["normalized_rows"]:
        raise ValueError("请先审查文件，修正问题并核对预览后再导入。")
    with atomic_write():
        current = _fresh_import_context(method, batch_id)
        if _import_source_signature(method, current) != source_signature:
            raise ValueError("当前批次的数据或设置已变化，请重新审查文件。")
        if phase == "building" and _building_locked(method, current):
            raise ValueError("当前批次已完成建靶，不能继续导入建靶期数据。")
        if phase == "formal" and not _ready(method, current):
            raise ValueError("请先完成当前批次建靶，再导入正式期数据。")
        kwargs = dict(template_id=current["template_id"], required_n=current["required_n"]) if method == "zscore" else {}
        return import_reviewed_results(method, batch_id, review["normalized_rows"], **kwargs)


def build_data_export(method, context, phase, *, start_date=None, end_date=None):
    """Reuse phase exports; the all-data file keeps explicit phases and whole runs."""
    if phase not in ("all", "building", "formal"):
        raise ValueError("请选择要导出的检测阶段。")
    phases = ("building", "formal") if phase == "all" else (phase,)
    if method == "lj":
        from database import export_batch_results_for_phase
        frames = [export_batch_results_for_phase(context["batch"], context["qc_df"], value) for value in phases]
    else:
        from pages.zscore_sections import build_zscore_phase_export_dataframe
        frames = [build_zscore_phase_export_dataframe(context["history_runs"], context["required_level_ids"], value,
                                                     context["input_value_type"]) for value in phases]
    populated = [frame for frame in frames if not frame.empty]
    result = pd.concat(populated, ignore_index=True, sort=False) if populated else frames[0].copy()
    if "阶段" not in result.columns:
        raise ValueError("检测阶段信息缺失，暂不能导出，请重新打开当前批次。")
    if not result.empty:
        result = result.sort_values(["检测时间", "检测序号"], kind="stable").reset_index(drop=True)
    if start_date is not None or end_date is not None:
        from services.report_period_service import resolve_report_period, filter_frame_to_period
        period = resolve_report_period(start_date=start_date, end_date=end_date)
        result = filter_frame_to_period(result, period, time_column='检测时间').reset_index(drop=True)
    return result


def _file_prefix(context, batch_id):
    batch = dict(context["batch"])
    project = build_safe_export_name(batch.get("project_name"), "项目")
    lot = build_safe_export_name(batch.get("lot_no"), "批次")
    return f"{project}_{lot}_{batch_id}"


def build_chart_png(method, context, chart_state, *, start_date=None, end_date=None):
    """Always draw a new figure: the workbench closes its own figure after rendering."""
    from plotting import figure_to_png_bytes, plot_lj_chart
    selected_dates = start_date is not None or end_date is not None
    if selected_dates:
        from services.report_period_service import resolve_report_period
        period = resolve_report_period(start_date=start_date, end_date=end_date)
        start_date, end_date = period.start_date, period.end_date
    if method == "lj":
        from pages.lj_sections import _build_lj_chart_title, build_monthly_chart_title, build_monthly_export_dataframe
        frame = (build_monthly_export_dataframe(context["qc_df"], start_date, end_date)
                 if selected_dates else context["qc_df"])
        if frame.empty:
            raise ValueError("所选范围内没有可导出的质控数据。")
        view = "正式质控图" if selected_dates else chart_state.get("view_mode", "全图")
        title = build_monthly_chart_title(context["batch"], start_date, end_date) if selected_dates else _build_lj_chart_title(context["batch"], view)
        figure = plot_lj_chart(qc_df=frame, stats=context["stats"], title=title, view_mode=view,
            y_axis_mode=chart_state.get("y_axis_mode", "标准视图"),
            standard_sd_limit=float(chart_state.get("standard_sd_limit", 4.0)), y_axis_label=context["input_value_type_label"])
    else:
        from pages.zscore_sections import build_zscore_monthly_export_plot_dataframe
        from zscore_plotting import plot_zscore_overlay, plot_zscore_single_level
        frame = (build_zscore_monthly_export_plot_dataframe(context["plot_df"], start_date, end_date)
                 if selected_dates else context["plot_df"])
        if frame.empty:
            raise ValueError("所选范围内没有可导出的质控数据。")
        phase = "formal" if selected_dates else chart_state.get("phase_scope", "all")
        label = chart_state.get("current_view_label", "多水平质控图")
        title = f"正式期｜{label}｜{start_date} 至 {end_date}" if selected_dates else f"{dict(building='参数建立期图', formal='正式质控图', all='全图')[phase]} | {label}"
        args = dict(plot_df=frame, title=title, phase_scope=phase, y_axis_mode=chart_state.get("y_axis_mode", "标准视图"),
                    standard_sd_limit=float(chart_state.get("standard_sd_limit", 4.0)), y_axis_label=context["input_value_type_label"])
        figure = (plot_zscore_single_level(**args, level_id=chart_state["selected_level"])
                  if chart_state.get("view_mode") == "单水平视图" else
                  plot_zscore_overlay(**args, active_levels=context["required_level_ids"]))
    if selected_dates:
        from services.report_pdf_layout import tidy_report_chart_ticks
        tidy_report_chart_ticks(figure)
    return figure_to_png_bytes(figure, close=True)


def build_chart_export(method, context, chart_state, *, start_date=None, end_date=None):
    """Keep an entire trend and add readable segments when the range is dense."""
    png = build_chart_png(method, context, chart_state, start_date=start_date, end_date=end_date)
    result = dict(data=png, mime='image/png', extension='png', detail_count=0)
    if start_date is None and end_date is None:
        return result
    from services.report_period_service import resolve_report_period, filter_frame_to_period
    from services.report_pdf_layout import split_report_chart_frames
    from zscore_logic import PHASE_FORMAL_QC
    period = resolve_report_period(start_date=start_date, end_date=end_date)
    field = 'qc_df' if method == 'lj' else 'plot_df'
    phase = '正式数据' if method == 'lj' else PHASE_FORMAL_QC
    frame = filter_frame_to_period(context[field], period)
    frame = frame[frame['phase'] == phase]
    count = len(frame) if method == 'lj' else frame['run_id'].nunique()
    result['detection_count'] = int(count)
    segments = split_report_chart_frames(method, frame)
    if len(segments) <= 1:
        return result
    from io import BytesIO, StringIO
    from zipfile import ZipFile, ZIP_DEFLATED
    import csv
    output, listing = BytesIO(), StringIO()
    writer = csv.writer(listing)
    writer.writerow(['文件', '检测次数', '开始检测时间', '结束检测时间'])
    with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        archive.writestr('质控图_总览.png', png)
        for number, segment in enumerate(segments, 1):
            subcontext = dict(context)
            subcontext[field] = segment
            part = build_chart_png(method, subcontext, chart_state, start_date=start_date, end_date=end_date)
            name = f'质控图_分图_{number:03d}.png'
            archive.writestr(name, part)
            count = len(segment) if method == 'lj' else segment['run_id'].nunique()
            writer.writerow([name, count, str(segment['test_time'].min()), str(segment['test_time'].max())])
        archive.writestr('图表清单.csv', listing.getvalue().encode('utf-8-sig'))
    return dict(data=output.getvalue(), mime='application/zip', extension='zip', detail_count=len(segments),
                detection_count=int(len(frame) if method == 'lj' else frame['run_id'].nunique()))


def _reset_upload(state, phase=None):
    if phase is not None:
        state["phase"] = phase
    state.update(upload=b"", upload_name="", review=None, source_signature=None,
                 widget_had_upload=False, upload_nonce=int(state.get("upload_nonce", 0)) + 1)


def _change_phase(widget_key):
    state = st.session_state.get(DIALOG_KEY)
    if state:
        _reset_upload(state, st.session_state[widget_key])


def _close_transfer(notice=None):
    state = st.session_state.pop(DIALOG_KEY, None)
    if notice and state:
        st.session_state["qc_transfer_notice"] = (state["method"], state["batch_id"], notice)
    st.rerun(scope="app")


def _cancel_import(state):
    if state.get("upload") or state.get("review"):
        state["discard"] = True
        st.rerun(scope="app")
    _close_transfer()


def _preview_rows(method, context, review):
    from services.value_type_service import build_level_measurement_label, get_measurement_label
    rows = []
    for record in review["normalized_rows"]:
        row = {"检测时间": record["test_time"], "检测人": record["operator"],
               "实际试剂批号": record.get("actual_reagent_lot") or "未记录"}
        if method == "lj":
            row[get_measurement_label(context["input_value_type"])] = record["value"]
        else:
            for level in record["level_results"]:
                row[build_level_measurement_label(level["level_id"], context["input_value_type"])] = level["raw_value"]
        row["备注"] = record.get("manual_note", "")
        rows.append(row)
    return pd.DataFrame(rows)


@st.dialog("导入数据", width="large", dismissible=False)
def _render_import_dialog(state):
    if state.get("discard"):
        st.warning("当前文件尚未导入，是否放弃本次导入？")
        keep, discard = st.columns(2)
        if keep.button("继续导入", type="primary", key="qc_transfer_keep"):
            state.update(discard=False, widget_had_upload=False)
            st.rerun(scope="app")
        if discard.button("放弃导入", key="qc_transfer_discard"):
            _close_transfer()
        return
    method, batch_id = state["method"], state["batch_id"]
    current = _fresh_import_context(method, batch_id)
    nonce = state["nonce"]
    phase_key = f"qc_transfer_phase_{nonce}"
    phase = st.radio("导入阶段", list(PHASE_LABELS), index=list(PHASE_LABELS).index(state["phase"]),
                     format_func=PHASE_LABELS.get, horizontal=True, key=phase_key,
                     on_change=_change_phase, args=(phase_key,))
    locked = phase == "building" and _building_locked(method, current)
    if locked:
        st.info("当前批次已完成建靶，请选择正式期导入。")
    elif phase == "formal" and not _ready(method, current):
        st.info("当前批次尚未完成建靶，可先审查文件，完成建靶后再确认导入正式期数据。")
    st.caption("按本次数据的用途选择导入阶段，下载当前模板填写后上传CSV或单工作表Excel。"
               "审查通过并核对预览后再确认导入；切换阶段需重新上传。未填试剂批号的行会保存为“未记录”。")
    st.caption("建靶数据与正式期数据分别导入。正式期数据请在完成建靶后选择“正式期”；"
               "“全部”检测数据导出文件用于查询和留档，不能直接作为当前阶段的导入文件。")
    template = (build_lj_building_template_dataframe(current["input_value_type"]) if method == "lj" else
                build_zscore_building_template_dataframe(current["level_count"], current["input_value_type"]))
    st.download_button("下载导入模板", dataframe_to_csv_bytes(template),
        file_name=f"{_file_prefix(current, batch_id)}_{PHASE_LABELS[phase]}导入模板.csv", mime="text/csv",
        key=f"qc_transfer_template_{nonce}", width="stretch")
    if method == "zscore":
        st.caption("每行填写一整次检测的全部水平，不能只导入其中一个水平。")
    uploaded = st.file_uploader("上传检测数据", type=["csv", "xlsx"], disabled=locked,
                               key=f"qc_transfer_upload_{nonce}_{state['upload_nonce']}")
    if uploaded is not None:
        data = uploaded.getvalue()
        if data != state.get("upload"):
            state.update(review=None, source_signature=None)
        state.update(upload=data, upload_name=uploaded.name, widget_had_upload=True)
    elif state.get("widget_had_upload"):
        _reset_upload(state)
    data = state.get("upload", b"")
    if data:
        st.caption("当前文件：" + state["upload_name"])
    signature = _import_source_signature(method, current)
    if state.get("review") and state.get("source_signature") != signature:
        state.update(review=None, source_signature=None)
        st.info("当前批次数据或设置已变化，请重新审查文件。")
    if st.button("审查并预览", disabled=locked or not data, key=f"qc_transfer_review_{nonce}", width="stretch"):
        try:
            state["review"] = review_data_import(method, current, batch_id, phase, data)
            state["source_signature"] = signature
        except ValueError as exc:
            state.update(review=None, source_signature=None)
            st.error(str(exc))
    review = state.get("review")
    if review:
        render_import_review_summary(review["summary"])
        if review["summary"]["has_blocking_errors"]:
            st.error("审查未通过，请修正问题后重新上传，本次数据尚未导入。")
        else:
            st.success("审查通过，请核对检测时间、结果和实际试剂批号。")
        issues = build_review_issues_dataframe(review["issues"])
        if not issues.empty:
            st.dataframe(issues, hide_index=True, width="stretch")
        preview = _preview_rows(method, current, review)
        if not preview.empty:
            st.dataframe(preview, hide_index=True, width="stretch")
    cancel, confirm = st.columns(2)
    if cancel.button("取消", key=f"qc_transfer_cancel_{nonce}", width="stretch"):
        _cancel_import(state)
    allowed = bool(review and not review["summary"]["has_blocking_errors"] and review["normalized_rows"]
                   and not locked and (phase == "building" or _ready(method, current)))
    if confirm.button("确认导入", type="primary", disabled=not allowed, key=f"qc_transfer_confirm_{nonce}", width="stretch"):
        try:
            count = commit_data_import(method, batch_id, phase, review, state["source_signature"])
        except ValueError as exc:
            state.update(review=None, source_signature=None)
            st.error(str(exc))
        except (sqlite3.Error, OSError):
            logging.exception('Reviewed records could not be imported')
            st.error('导入尚未保存，当前文件和预览仍保留。请检查数据保存位置后重试。')
        else:
            _close_transfer(f"已导入{count}条{PHASE_LABELS[phase]}检测记录，图表与统计已更新。")


@st.dialog("导出", width="large", dismissible=False)
def _render_export_dialog(state, context):
    method, batch_id, nonce = state["method"], state["batch_id"], state["nonce"]
    kind = st.radio("导出内容", ["检测数据", "质控图"], horizontal=True, key=f"qc_transfer_kind_{nonce}")
    prefix = _file_prefix(context, batch_id)
    data, name, mime, download_label = b"", prefix, "text/csv", '下载检测数据'
    if kind == "检测数据":
        phase = st.radio("检测范围", ["all", "building", "formal"], horizontal=True,
                         format_func=lambda value: "全部" if value == "all" else PHASE_LABELS[value], key=f"qc_transfer_scope_{nonce}")
        export_format = st.radio("文件格式", ["Excel (.xlsx)", "CSV (.csv)"], horizontal=True, key=f"qc_transfer_format_{nonce}")
        frame = build_data_export(method, context, phase)
        dates = st.radio('数据日期', ['全部日期', '按月或自选日期'], horizontal=True, key=f'qc_transfer_dates_{nonce}')
        period = None
        if dates == '按月或自选日期':
            from ui.report_period import render_report_period
            times = pd.to_datetime(frame['检测时间'], errors='coerce', format='mixed').dropna()
            period = render_report_period(f'qc_transfer_data_period_{nonce}',
                months=times.dt.to_period('M').astype(str).unique().tolist())
            frame = build_data_export(method, context, phase, start_date=period.start_date, end_date=period.end_date) if period else frame.iloc[:0]
        st.caption("导出文件用于查询和留档，保留检测阶段和实际试剂批号。"
                   "重新导入时请按所需阶段下载导入模板并整理原始数据，不能直接上传全部阶段的导出文件。"
                   + ("每行是一整次检测，各水平完整列出。" if method == "zscore" else ""))
        if frame.empty:
            st.info("所选范围内暂无检测记录。")
        else:
            st.caption(f"共{len(frame)}次检测。")
            excel = export_format == "Excel (.xlsx)"
            data = dataframe_to_xlsx_bytes(frame) if excel else dataframe_to_csv_bytes(frame)
            date_label = f'_{period.key}' if period else ''
            name = f"{prefix}_{'全部' if phase == 'all' else PHASE_LABELS[phase]}检测数据{date_label}.{'xlsx' if excel else 'csv'}"
            mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if excel else "text/csv"
    else:
        download_label = '下载质控图（PNG）'
        scope = st.radio("图表范围", ["当前图", "按日期选择正式期"], horizontal=True, key=f"qc_transfer_chart_scope_{nonce}")
        start_date = end_date = None
        ready = True
        if scope == "按日期选择正式期":
            from zscore_logic import PHASE_FORMAL_QC
            frame = context["qc_df"] if method == "lj" else context["plot_df"]
            formal = frame[frame["phase"] == ("正式数据" if method == "lj" else PHASE_FORMAL_QC)] if not frame.empty else frame
            st.caption("按日期导出正式期质控图，包含起止日期当日及所选期间的全部检测。")
            if formal.empty:
                st.info("当前批次暂无正式期质控数据。")
                ready = False
            else:
                latest = pd.Timestamp(formal["test_time"].max()).date()
                earliest = pd.Timestamp(formal["test_time"].min()).date()
                from ui.report_period import render_report_period
                times = pd.to_datetime(formal['test_time'], errors='coerce', format='mixed')
                period = render_report_period(f'qc_transfer_chart_period_{nonce}',
                    months=times.dt.to_period('M').astype(str).dropna().unique().tolist(),
                    default_start=earliest, default_end=latest)
                ready = period is not None
                if period:
                    start_date, end_date = period.start_date, period.end_date
        else:
            st.caption("导出当前工作台所选视图的质控图。")
        if ready:
            try:
                chart_key = (scope, str(start_date), str(end_date))
                if state.get("png_key") != chart_key or not isinstance(state.get('png'), dict):
                    state["png"] = build_chart_export(method, context, state["chart_state"], start_date=start_date, end_date=end_date)
                    state["png_key"] = chart_key
                chart_export = state['png']
                data = chart_export['data']
                date_part = f"{start_date}_至_{end_date}" if start_date is not None else "当前图"
                name, mime = f"{prefix}_{date_part}.{chart_export['extension']}", chart_export['mime']
                download_label = '下载总览和分图（ZIP）' if chart_export['detail_count'] else '下载质控图（PNG）'
                if chart_export['detail_count']:
                    st.caption(f"所选期间共 {chart_export['detection_count']} 次检测，分为 {chart_export['detail_count']} 张明细图。下载文件包含完整总览和全部明细图。")
            except ValueError as exc:
                state.pop("png_key", None)
                state.pop("png", None)
                st.info(str(exc))
    st.download_button(download_label, data, file_name=name, mime=mime, disabled=not data,
                       key=f"qc_transfer_download_{nonce}", width="stretch")
    if st.button("关闭", key=f"qc_transfer_close_{nonce}", width="stretch"):
        _close_transfer()


def render_transfer_toolbar(method, context, batch_id, chart_state):
    if method not in ("lj", "zscore"):
        raise ValueError("不支持的质控方法。")
    notice = st.session_state.get("qc_transfer_notice")
    if notice and notice[:2] == (method, batch_id):
        st.success(notice[2])
        st.session_state.pop("qc_transfer_notice", None)
    other_dialog = any(st.session_state.get(key) for key in _OTHER_DIALOG_KEYS)
    state = st.session_state.get(DIALOG_KEY)
    left, right = st.columns(2)
    actions = [left.button("导入数据", disabled=other_dialog or bool(state), key=f"qc_transfer_open_import_{method}_{batch_id}", width="stretch"),
               right.button("导出", disabled=other_dialog or bool(state), key=f"qc_transfer_open_export_{method}_{batch_id}", width="stretch")]
    if any(actions):
        state = dict(method=method, batch_id=batch_id, mode="import" if actions[0] else "export", nonce=uuid4().hex,
                     phase="formal" if _ready(method, context) else "building", discard=False,
                     export_context=context,
                     chart_state={key: value for key, value in chart_state.items() if key not in ("figure", "current_png_bytes")})
        _reset_upload(state)
        st.session_state[DIALOG_KEY] = state
    render_pending_transfer_dialog(method, context if state and state['batch_id'] == batch_id else None)


def render_pending_transfer_dialog(method, context=None):
    """Keep cancel/close available when the current selection disappears."""
    state = st.session_state.get(DIALOG_KEY)
    other_dialog = any(st.session_state.get(key) for key in _OTHER_DIALOG_KEYS)
    if state and state["method"] == method and not other_dialog:
        if state["mode"] == "import":
            _render_import_dialog(state)
        else:
            _render_export_dialog(state, context if context is not None else state['export_context'])
