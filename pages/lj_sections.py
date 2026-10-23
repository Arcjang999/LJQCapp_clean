from __future__ import annotations

from services.cv_service import calculate_cv_percent
from services.lot_lifecycle_service import save_lj_result_manual_note

from datetime import datetime

import pandas as pd
import streamlit as st

from database import add_result, get_batch, get_results
from plotting import plot_lj_chart
from qc_logic import calculate_realtime_stats, calculate_target_building_cv_hint, persist_lj_batch_outlier_snapshot
from services.outlier_service import (
    DEFAULT_GRUBBS_ALPHA,
    get_current_outlier_status_label,
)
from services.profiling import profile_timer
from services.value_type_service import (
    get_input_value_type_label,
    normalize_input_value_type,
    parse_project_input_value,
)
from ui.common import build_chart_control_title, build_operator_options, format_rule_code, format_rule_description, get_saved_batch_cv_limit, render_compact_stat_metrics, render_cv_limit_hint, render_rule_summary_metrics, render_standard_view_help, render_status_panel

def get_latest_status_context(qc_df: pd.DataFrame) -> tuple[str, str]:
    if qc_df.empty:
        return "\u6682\u65e0\u6570\u636e", "\u6682\u65e0\u68c0\u6d4b\u7ed3\u679c\uff0c\u8bf7\u5148\u5f55\u5165\u8d28\u63a7\u6570\u636e\u3002"

    latest_row = qc_df.sort_values(["test_time", "id"]).iloc[-1]
    return str(latest_row.get("status", "")), str(latest_row.get("analysis_prompt", "")).strip()

def get_latest_result_panel_content(qc_df: pd.DataFrame, fallback_message: str) -> tuple[str, str]:
    if qc_df.empty:
        return "\u65e0", "\u6682\u65e0\u5206\u6790\u63d0\u793a\u3002"

    latest_row = qc_df.sort_values(["test_time", "id"]).iloc[-1]
    rule_hits = str(latest_row.get("rule_hits", "")).strip() or "\u65e0"
    prompt = str(latest_row.get("analysis_prompt", "")).strip() or fallback_message
    compact_prompt = prompt.splitlines()[0].strip() if prompt else "\u6682\u65e0\u5206\u6790\u63d0\u793a\u3002"
    return rule_hits, compact_prompt

def get_latest_qc_row(qc_df: pd.DataFrame) -> pd.Series | None:
    if qc_df.empty:
        return None
    latest_df = qc_df.sort_values(["test_time", "id"])
    if latest_df.empty:
        return None
    return latest_df.iloc[-1]


def resolve_lj_latest_analysis_mode(stats: dict[str, object]) -> str:
    return "formal" if bool(stats.get("has_formal_started")) else "building"


def build_lj_building_outlier_panel_data(
    stats: dict[str, object],
    latest_source_text: str,
) -> dict[str, object]:
    suspect_row = stats.get("current_suspect_row")
    suspect_details: dict[str, object] | None = None
    if suspect_row is not None:
        suspect_details = {
            "sequence": int(suspect_row.get("sequence", 0) or 0),
            "test_time": pd.Timestamp(suspect_row["test_time"]).strftime("%Y-%m-%d %H:%M"),
            "grubbs_statistic": suspect_row.get("grubbs_statistic"),
            "grubbs_threshold": suspect_row.get("grubbs_threshold"),
            "alpha": DEFAULT_GRUBBS_ALPHA,
            "status_label": get_current_outlier_status_label(
                is_building_included=suspect_row.get("is_building_included", 1),
                is_suspect=suspect_row.get("is_outlier_suspect", 0),
            ),
        }
    return {
        "phase_label": "参数建立期",
        "effective_building_count": int(stats.get("effective_building_count", 0) or 0),
        "disabled_building_count": int(stats.get("disabled_building_count", 0) or 0),
        "mean": stats.get("mean"),
        "sd": stats.get("sd"),
        "cv": stats.get("cv"),
        "grubbs_ready": int(stats.get("effective_building_count", 0) or 0) >= 3,
        "suspect_details": suspect_details,
        "source_text": latest_source_text,
        "target_ready": bool(stats.get("target_ready")),
    }


def _format_lj_stat_text(value: object, digits: int = 4, suffix: str = "") -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value):.{digits}f}{suffix}"


def _get_lj_building_record_current_status_label(row: pd.Series | dict[str, object]) -> str:
    return get_current_outlier_status_label(
        is_building_included=row.get("is_building_included", 1),
        is_suspect=row.get("is_outlier_suspect", 0),
    )


def render_lj_building_outlier_panel(
    stats: dict[str, object],
    latest_source_text: str,
) -> None:
    panel_data = build_lj_building_outlier_panel_data(stats, latest_source_text)
    st.markdown("**参数建立期离群值判断**")
    render_compact_stat_metrics(
        [
            ("当前阶段", str(panel_data["phase_label"])),
            ("当前有效建立点数", str(panel_data["effective_building_count"])),
            ("当前已禁用点数", str(panel_data["disabled_building_count"])),
            ("当前建靶均值", _format_lj_stat_text(panel_data["mean"])),
            ("当前建靶SD", _format_lj_stat_text(panel_data["sd"])),
            ("实测变异系数（%）", _format_lj_stat_text(panel_data["cv"], digits=2, suffix="%")),
        ]
    )
    st.caption(str(panel_data["source_text"]))
    if not bool(panel_data["grubbs_ready"]):
        st.info("当前点数不足，暂不进行格拉布斯法判断。")
    elif panel_data["suspect_details"] is None:
        if bool(panel_data["target_ready"]):
            st.success("当前未发现疑似离群参数建立点，参数建立统计已可用于进入正式期。")
        else:
            st.success("当前未发现疑似离群参数建立点。")
    else:
        suspect_details = panel_data["suspect_details"]
        st.warning(
            "发现疑似离群参数建立点："
            f"序号 #{suspect_details['sequence']} | "
            f"时间 {suspect_details['test_time']} | "
            f"G={float(suspect_details['grubbs_statistic'] or 0.0):.4f} | "
            f"G临界值={float(suspect_details['grubbs_threshold'] or 0.0):.4f} | "
            f"alpha={float(suspect_details['alpha']):.2f} | "
            f"状态={suspect_details['status_label']}"
        )
    st.caption("如需保留、禁用或恢复，在下方“检测记录”中选中记录，再点击相应操作。")


def render_lj_formal_westgard_panel(
    latest_status: str,
    latest_compact_message: str,
    latest_rule_hits: str,
    latest_source_text: str,
) -> None:
    st.markdown("**Westgard 判读**")
    render_status_panel(
        latest_status,
        latest_compact_message,
        latest_rule_hits,
        source_text=latest_source_text,
        phase_text="正式期",
    )

def render_lj_abnormal_note_quick_entry(latest_row: pd.Series | None) -> None:
    if latest_row is None:
        return
    if str(latest_row.get("status", "") or "") not in {"\u8b66\u544a", "\u5931\u63a7"}:
        return

    result_id = int(latest_row["id"])
    from ui.out_of_control import render_abnormal_entry
    render_abnormal_entry('lj_result', result_id,
        warning=str(latest_row.get('status')) == '警告', key='latest')
    current_note = str(latest_row.get("manual_note", "") or "")
    st.caption("在下方填写异常备注并保存，备注会保留在这条检测记录中。")
    with st.form(f"lj_abnormal_note_form_{result_id}"):
        manual_note = st.text_area(
            "\u5f02\u5e38\u5907\u6ce8\uff08\u53ef\u9009\uff09",
            value=current_note,
            height=88,
            key=f"lj_abnormal_note_{result_id}",
        )
        submitted = st.form_submit_button("\u4fdd\u5b58\u5f53\u524d\u5f02\u5e38\u5907\u6ce8", width="stretch")

        if submitted:
            try:
                save_lj_result_manual_note(result_id, manual_note)
            except ValueError as exc:
                st.error(str(exc))
            else:
                st.rerun()



def build_monthly_export_dataframe(
    qc_df: pd.DataFrame,
    start_date,
    end_date,
) -> pd.DataFrame:
    if qc_df.empty:
        return qc_df.copy()

    formal_df = qc_df[qc_df["phase"] == "\u6b63\u5f0f\u6570\u636e"].copy()
    if formal_df.empty:
        return formal_df

    from services.report_period_service import resolve_report_period, filter_frame_to_period
    period = resolve_report_period(start_date=start_date, end_date=end_date)
    filtered_df = filter_frame_to_period(formal_df, period)
    if filtered_df.empty:
        return filtered_df

    filtered_df = filtered_df.sort_values(["test_time", "id"]).reset_index(drop=True)
    filtered_df["sequence"] = filtered_df.index + 1
    return filtered_df

def build_monthly_chart_title(batch, start_date, end_date) -> str:
    return (
        f"质控图 - \u6279\u6b21 {batch['id']} - {batch['instrument']} - {batch['reagent']} - "
        f"{batch['qc_material']} - {batch['concentration']}\n"
        f"{str(start_date)[:10]} 至 {str(end_date)[:10]}"
    )


def _dataframe_signature(dataframe: pd.DataFrame) -> tuple:
    if dataframe.empty:
        return (0, None, None)
    max_id = int(dataframe["id"].max()) if "id" in dataframe.columns else len(dataframe)
    max_time = str(pd.to_datetime(dataframe["test_time"]).max()) if "test_time" in dataframe.columns else ""
    return (len(dataframe), max_id, max_time)


def _clear_stale_download_payload(state_key: str, signature: tuple) -> dict[str, object] | None:
    payload = st.session_state.get(state_key)
    if payload is None:
        return None
    if payload.get("signature") != signature:
        st.session_state.pop(state_key, None)
        return None
    return payload


def _ensure_download_payload(
    state_key: str,
    signature: tuple,
    build_bytes,
    *,
    disabled: bool = False,
) -> dict[str, object] | None:
    if disabled:
        st.session_state.pop(state_key, None)
        return None

    payload = _clear_stale_download_payload(state_key, signature)
    if payload is None:
        payload = {
            "signature": signature,
            "data": build_bytes(),
        }
        st.session_state[state_key] = payload
    return payload


def build_lj_workbench_context(selected_batch_id: int) -> dict[str, object]:
    with profile_timer("build_lj_workbench_context", batch_id=selected_batch_id):
        batch = get_batch(selected_batch_id)
        results_df = get_results(selected_batch_id, include_manual_note=True)
        qc_df, stats = persist_lj_batch_outlier_snapshot(
            selected_batch_id,
            batch=batch,
            results_df=results_df,
        )
        from services.lot_lifecycle_service import target_profile
        confirmed=target_profile('lj',selected_batch_id)
        if confirmed:
            level=confirmed['levels'][0]
            stats.update(target_ready=True,mean=level['mean'],sd=level['sd'],cv=calculate_cv_percent(level['mean'], level['sd']),
                target_profile_id=confirmed['id'],message='控制参数已确认，可录入正式质控结果。')
        latest_status, latest_status_message = get_latest_status_context(qc_df)
        latest_rule_hits, latest_compact_message = get_latest_result_panel_content(qc_df, latest_status_message)
        return {
            "batch": batch,
            "input_value_type": normalize_input_value_type(batch["input_value_type"]),
            "input_value_type_label": get_input_value_type_label(batch["input_value_type"]),
            "cv_limit": get_saved_batch_cv_limit(batch),
            "results_df": results_df,
            "qc_df": qc_df,
            "stats": stats,
            "building_cv_hint": calculate_target_building_cv_hint(
                results_df,
                int(batch["target_n"]),
                qc_df=qc_df,
                stats=stats,
            ),
            "latest_status": latest_status,
            "latest_status_message": latest_status_message,
            "latest_rule_hits": latest_rule_hits,
            "latest_compact_message": latest_compact_message,
            "latest_row": get_latest_qc_row(qc_df),
            "operator_options": build_operator_options(results_df),
        }


def _build_lj_chart_title(batch, view_mode: str) -> str:
    batch_dict = dict(batch)
    lot_no = str(batch_dict.get("lot_no", "") or "").strip()
    batch_label = f"质控批号 {lot_no}" if lot_no else "当前批次"
    return (
        f"{view_mode} - {batch_label} - {batch['instrument']} - "
        f"{batch['reagent']} - {batch['qc_material']} - {batch['concentration']}"
    )


def _get_lj_chart_state(batch) -> dict[str, object]:
    view_options = ["参数建立图", "正式质控图", "全部数据图"]
    view_mode = st.session_state.get("chart_view_mode", "全部数据图")
    if view_mode not in view_options:
        view_mode = view_options[2]

    y_axis_options = ["标准视图", "全范围视图"]
    y_axis_mode = st.session_state.get("chart_y_axis_mode", "标准视图")
    if y_axis_mode not in y_axis_options:
        y_axis_mode = y_axis_options[0]

    standard_sd_limit = float(st.session_state.get("chart_standard_sd_limit", 4.0))
    chart_title = _build_lj_chart_title(batch, view_mode)
    return {
        "view_mode": view_mode,
        "view_options": view_options,
        "y_axis_mode": y_axis_mode,
        "y_axis_options": y_axis_options,
        "standard_sd_limit": standard_sd_limit,
        "chart_title": chart_title,
    }


def render_lj_entry_and_stats_section(
    context: dict[str, object],
    selected_batch_id: int,
) -> None:
    from pages.lot_lifecycle_section import (
        render_result_lot_choice, is_batch_writable, render_result_lot_preparation, restore_result_lot_entry_draft,
    )
    if not is_batch_writable("lj",selected_batch_id):
        return
    restore_result_lot_entry_draft('lj', selected_batch_id)
    batch = context["batch"]
    input_value_type = context["input_value_type"]
    input_value_type_label = context["input_value_type_label"]
    operator_options = context["operator_options"]
    stats = context["stats"]
    building_cv_hint = context["building_cv_hint"]
    cv_limit = context["cv_limit"]
    results_df = context["results_df"]
    qc_df = context["qc_df"]

    if st.session_state.get("entry_batch_id") != selected_batch_id:
        st.session_state["entry_batch_id"] = selected_batch_id
        st.session_state["entry_operator"] = operator_options[0] if operator_options else ""
        st.session_state["entry_value"] = ""
        st.session_state["entry_reagent_changed"] = False
        st.session_state["entry_test_time"] = datetime.now()
    if "entry_test_time" not in st.session_state:
        st.session_state["entry_test_time"] = datetime.now()
    if "entry_operator" not in st.session_state:
        st.session_state["entry_operator"] = ""
    if "entry_value" not in st.session_state:
        st.session_state["entry_value"] = ""
    if "entry_reagent_changed" not in st.session_state:
        st.session_state["entry_reagent_changed"] = False

    if st.session_state.get("reset_entry_form", False):
        last_operator = st.session_state.get("entry_operator", "")
        st.session_state["entry_operator"] = last_operator.strip()
        st.session_state["entry_value"] = ""
        st.session_state["entry_reagent_changed"] = False
        st.session_state["entry_test_time"] = datetime.now()
        st.session_state["reset_entry_form"] = False

    with st.container(border=True):
        st.markdown("**本次结果录入**")
        test_time = st.datetime_input(
            "检测时间",
            key="entry_test_time",
        )
        operator = st.selectbox(
            "检测人",
            options=operator_options,
            index=None,
            key="entry_operator",
            accept_new_options=True,
            placeholder="可选择历史姓名，也可直接输入新姓名",
        )
        value_text = st.text_input(
            input_value_type_label,
            key="entry_value",
            placeholder="例如：123.4567",
        )
        parsed_value, log_value, value_error = parse_project_input_value(
            value_text,
            input_value_type,
            field_label=input_value_type_label,
        )
        reagent_lot_changed = st.checkbox(
            "本次为试剂批号变更点",
            key="entry_reagent_changed",
        )

        lot_selection = render_result_lot_choice("lj",selected_batch_id,test_time,"lj_entry")
        render_result_lot_preparation('lj', selected_batch_id, 'lj_entry')

        if st.button("保存检测结果", type="primary", width="stretch"):
            validation_errors: list[str] = []
            cleaned_operator = (operator or "").strip()

            if test_time is None:
                validation_errors.append("请填写检测时间。")
            if not cleaned_operator:
                validation_errors.append("请填写检测人，不能为空。")
            if parsed_value is None:
                validation_errors.append(value_error or f"{input_value_type_label}必须为有效数字。")

            if validation_errors:
                st.error("\n".join(validation_errors))
            else:
                try:
                    add_result(
                        batch_id=selected_batch_id,
                        test_time=test_time.strftime("%Y-%m-%d %H:%M:%S"),
                        operator=cleaned_operator,
                        value=float(parsed_value),
                        log_value=log_value,
                        reagent_lot_changed=int(reagent_lot_changed),
                        manual_note="",
                        lot_selection=lot_selection,
                    )
                except ValueError as exc:
                    st.error(str(exc))
                    return
                st.success("检测结果已保存。")
                st.session_state["reset_entry_form"] = True
                st.rerun()

    with st.container(border=True):
        confirmed_target = bool(stats.get("target_profile_id"))
        st.markdown("**当前控制参数**" if confirmed_target else "**参数建立统计**")
        render_compact_stat_metrics(
            [
                ("总记录数", f"{stats.get('building_total_count', 0)}"),
                ("生效参数建立点", f"{stats.get('effective_building_count', 0)}"),
                ("已禁用点", f"{stats.get('disabled_building_count', 0)}"),
                ("设定均值" if confirmed_target else "均值", "-" if stats["mean"] is None else f"{stats['mean']:.4f}"),
                ("SD", "-" if stats["sd"] is None else f"{stats['sd']:.4f}"),
                ("设定变异系数（%）" if confirmed_target else "实测变异系数（%）", "-" if stats["cv"] is None else f"{stats['cv']:.2f}%"),
            ]
        )
        st.caption(
            "后续检测使用已确认的均值和SD进行判读。" if confirmed_target else "参数建立进度："
            + (
                "已完成，后续结果自动进行 Westgard 判定。"
                if stats.get("target_ready")
                else f"尚需继续录入至少 {int(batch['target_n'])} 次结果。"
            )
        )
        if cv_limit is not None:
            st.caption(f"当前批次已保存允许不精密度（CV）：≤ {cv_limit:.2f}%")
            render_cv_limit_hint(
                stats.get("cv") if confirmed_target else building_cv_hint.get("cv"),
                cv_limit,
                "当前控制参数" if confirmed_target else "当前累计均值和标准差建立",
            )

    with st.expander("实时统计", expanded=False):
        sorted_results = results_df.sort_values(["test_time", "id"]).reset_index(drop=True)
        if sorted_results.empty:
            st.info("暂无数据，无法计算实时统计。")
            return

        sorted_results["sequence"] = sorted_results.index + 1
        formal_results = sorted_results[sorted_results["sequence"] > int(batch["target_n"])].copy()
        default_start = formal_results["test_time"].min() if not formal_results.empty else sorted_results["test_time"].min()
        default_end = sorted_results["test_time"].max()

        date_cols = st.columns(2)
        realtime_start = date_cols[0].date_input(
            "开始日期",
            value=default_start.date(),
            key="realtime_start",
        )
        realtime_end = date_cols[1].date_input(
            "结束日期",
            value=default_end.date(),
            key="realtime_end",
        )
        st.caption("按日期统计，结束日期包含当日全部记录。")
        end_timestamp = pd.Timestamp(realtime_end) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
        realtime_stats, realtime_message = calculate_realtime_stats(
            results_df=results_df,
            target_n=int(batch["target_n"]),
            start_time=pd.Timestamp(realtime_start),
            end_time=end_timestamp,
            qc_df=qc_df,
        )
        render_compact_stat_metrics(
            [
                ("实时均值", "-" if realtime_stats["mean"] is None else f"{realtime_stats['mean']:.4f}"),
                ("实时 SD", "-" if realtime_stats["sd"] is None else f"{realtime_stats['sd']:.4f}"),
                ("实测变异系数（%）", "-" if realtime_stats["cv"] is None else f"{realtime_stats['cv']:.2f}%"),
            ]
        )
        if realtime_message:
            st.info(realtime_message)
        st.caption(
            "统计说明：实时统计仅基于当前批次中判定为“在控”的正式数据计算，"
            "警告和失控结果不参与计算。"
        )


def render_lj_chart_and_analysis_section(
    context: dict[str, object],
) -> tuple[object, dict[str, object]]:
    batch = context["batch"]
    input_value_type_label = context["input_value_type_label"]
    qc_df = context["qc_df"]
    stats = context["stats"]
    latest_status = context["latest_status"]
    latest_compact_message = context["latest_compact_message"]
    latest_rule_hits = context["latest_rule_hits"]
    latest_row = context["latest_row"]

    chart_state = _get_lj_chart_state(batch)
    view_mode = chart_state["view_mode"]
    y_axis_mode = chart_state["y_axis_mode"]
    standard_sd_limit = chart_state["standard_sd_limit"]
    latest_source_text = (
        f"最近已保存检测序号 #{int(latest_row['sequence'])}"
        if latest_row is not None and "sequence" in latest_row
        else "当前批次最近结果"
    )

    chart_view_mode = view_mode
    chart_y_axis_mode = y_axis_mode
    chart_standard_sd_limit = float(st.session_state.get("chart_standard_sd_limit", standard_sd_limit))
    with st.container(border=True):
        with st.expander(
            build_chart_control_title(chart_view_mode, chart_y_axis_mode, chart_standard_sd_limit),
            expanded=False,
        ):
            view_selector_col, y_axis_selector_col = st.columns([1.2, 1.1])
            chart_view_mode = view_selector_col.radio(
                "图形视图",
                options=chart_state["view_options"],
                horizontal=True,
                index=chart_state["view_options"].index(view_mode),
            )
            chart_y_axis_mode = y_axis_selector_col.radio(
                "Y 轴范围",
                options=chart_state["y_axis_options"],
                horizontal=True,
                index=chart_state["y_axis_options"].index(y_axis_mode),
            )
            st.session_state["chart_view_mode"] = chart_view_mode
            st.session_state["chart_y_axis_mode"] = chart_y_axis_mode
            if chart_y_axis_mode == "标准视图":
                chart_standard_sd_limit = st.slider(
                    "标准视图范围（均值 ± nSD）",
                    min_value=3.0,
                    max_value=6.0,
                    value=float(standard_sd_limit),
                    step=0.5,
                )
                st.session_state["chart_standard_sd_limit"] = chart_standard_sd_limit
                render_standard_view_help(chart_standard_sd_limit)
            else:
                chart_standard_sd_limit = float(st.session_state.get("chart_standard_sd_limit", standard_sd_limit))

            if chart_view_mode != view_mode or chart_y_axis_mode != y_axis_mode:
                st.rerun()

    chart_view_mode = st.session_state.get("chart_view_mode", view_mode)
    chart_y_axis_mode = st.session_state.get("chart_y_axis_mode", y_axis_mode)
    chart_standard_sd_limit = float(st.session_state.get("chart_standard_sd_limit", standard_sd_limit))
    with st.container(border=True):
        st.markdown("**质控图**")
        figure = plot_lj_chart(
            qc_df=qc_df,
            stats=stats,
            title=chart_view_mode,
            view_mode=chart_view_mode,
            y_axis_mode=chart_y_axis_mode,
            standard_sd_limit=chart_standard_sd_limit,
            y_axis_label=input_value_type_label,
        )
        st.pyplot(figure, clear_figure=False, width="stretch")

    with st.container(border=True):
        st.markdown("**最新结果分析**")
        analysis_mode = resolve_lj_latest_analysis_mode(stats)
        if analysis_mode == "building":
            render_lj_building_outlier_panel(stats, latest_source_text)
        else:
            render_lj_formal_westgard_panel(
                latest_status,
                latest_compact_message,
                latest_rule_hits,
                latest_source_text,
            )
    if resolve_lj_latest_analysis_mode(stats) == "formal":
        render_lj_abnormal_note_quick_entry(latest_row)
    return figure, {
        "view_mode": chart_view_mode,
        "y_axis_mode": chart_y_axis_mode,
        "standard_sd_limit": chart_standard_sd_limit,
    }


def render_lj_rule_summary_section(stats: dict[str, object]) -> None:
    if not bool(stats.get("target_ready")):
        st.info("当前仍在参数建立期，上方最新分析仅显示离群值判断；Westgard 规则汇总会在正式期启用后显示。")
        return

    st.markdown("**本批次规则汇总**")
    render_rule_summary_metrics(stats.get("rule_summary", {}))

    with st.expander("Westgard 规则说明", expanded=False):
        st.caption(
            "参数建立期可参考，正式质控期会输出规则结论。"
        )
        for rule_id in ["1_2s", "1_3s", "2_2s", "R_4s", "4_1s", "10x"]:
            st.markdown(f"- `{format_rule_code(rule_id)}`：{format_rule_description(rule_id)}")


def render_lj_records_section(qc_df: pd.DataFrame, input_value_type: str, *, context=None) -> None:
    from database import read_snapshot
    from services.lot_lifecycle_service import get_result_edit_availability, require_writable
    from ui.record_workspace import render_record_selection
    from ui.record_actions import render_record_toolbar
    if context is None:
        if qc_df.empty:
            st.info("当前批次暂无检测记录。")
            return
        context = build_lj_workbench_context(int(qc_df["batch_id"].iloc[0]))
    batch_id = int(context["batch"]["id"])
    scope = f'lj_records_{batch_id}'
    filters = ['全部', '参数建立期', '正式期', '警告 / 失控', '已禁用']
    filter_key, widget_key = scope + '_filter', scope + '_filter_widget'
    if widget_key not in st.session_state:
        st.session_state[widget_key] = st.session_state.get(filter_key, '全部')
    def remember_filter():
        st.session_state[filter_key] = st.session_state[widget_key]
    with st.container(border=True):
        st.markdown('**检测记录**')
        selected_filter = st.selectbox('记录范围', filters, key=widget_key, on_change=remember_filter)
        st.session_state[filter_key] = selected_filter
        records = qc_df.copy()
        if not records.empty:
            if selected_filter == '参数建立期':
                records = records.loc[records.phase == '建靶数据']
            elif selected_filter == '正式期':
                records = records.loc[records.phase == '正式数据']
            elif selected_filter == '警告 / 失控':
                records = records.loc[(records.phase == '正式数据') & records.status.isin(['警告', '失控'])]
            elif selected_filter == '已禁用':
                records = records.loc[(records.phase == '建靶数据') & (records.is_building_included == 0)]
            records = records.sort_values(['test_time', 'id'], ascending=False)
        display = pd.DataFrame([{
            '序号': int(row.get('sequence', 0) or 0),
            '检测时间': pd.Timestamp(row['test_time']).strftime('%Y-%m-%d %H:%M'),
            '检测人': row.get('operator') or '-',
            get_input_value_type_label(input_value_type): row['value'],
            '阶段': '参数建立期' if row['phase'] == '建靶数据' else '正式期',
            '判定': _get_lj_building_record_current_status_label(row) if row['phase'] == '建靶数据' else row['status'],
            '规则': row.get('rule_hits') or '-',
        } for _, row in records.iterrows()])
        selected_id = render_record_selection(display, records['id'].astype(int).tolist(), scope=scope)
        selected = records.loc[records.id == selected_id]
        row = None if selected.empty else selected.iloc[0]
        try:
            with read_snapshot() as connection:
                require_writable(connection, 'lj', batch_id, read_only=True)
            writable = True
        except ValueError:
            writable = False
        availability = get_result_edit_availability('lj', selected_id) if row is not None else {'allowed': False, 'reason': ''}
        render_record_toolbar('lj', batch_id, selected_id,
            allow_edit=bool(writable and availability['allowed']),
            allow_status=bool(row is not None and writable and row['phase'] == '建靶数据'
                              and not context['stats'].get('has_formal_started')),
            included=bool(row.get('is_building_included', 1)) if row is not None else None,
            abnormal=bool(row is not None and row['phase'] == '正式数据' and row['status'] in ('警告', '失控')))
        if row is not None and not availability['allowed'] and availability.get('reason'):
            st.caption(availability['reason'])
        st.caption(f"本批次共 {len(qc_df)} 条记录；先选择一条记录，再查看或处理。")


def render_lj_maintenance_section(context: dict[str, object]) -> None:
    # Compatibility entry for callers that render the record workspace alone.
    from ui.record_actions import render_pending_record_dialog
    render_lj_records_section(context['qc_df'], context['input_value_type'], context=context)
    render_pending_record_dialog('lj', int(context['batch']['id']))


def render_lj_export_import_section(
    context: dict[str, object],
    selected_batch_id: int,
    figure,
    chart_state: dict[str, object],
) -> None:
    from ui.data_transfer import render_transfer_toolbar
    with profile_timer("render_lj_export_import_section", batch_id=selected_batch_id):
        render_transfer_toolbar('lj', context, selected_batch_id, chart_state)
