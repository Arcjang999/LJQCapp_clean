from __future__ import annotations

from datetime import datetime

import pandas as pd
import streamlit as st

from pages.instant_config_section import (
    prepare_instant_v12_project_batch_context,
    render_instant_v12_configuration_selection,
)
from plotting import plot_instant_chart
from services.instant_service import (
    INSTANT_TRANSFER_READY_COUNT,
    build_instant_transfer_preview,
    build_instant_workbench_context,
    confirm_instant_transfer_to_lj,
    get_instant_manual_status_label,
    save_instant_result,
)
from services.value_type_service import (
    get_input_value_type_label,
    parse_project_input_value,
)
from ui.common import (
    format_optional_float,
    render_compact_stat_metrics,
    render_latest_analysis_card,
)
from ui.chart_display import workbench_chart_columns


def _clean_instant_display_part(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _join_instant_display_parts(parts: list[str]) -> str:
    return " | ".join(part for part in parts if part)


def _build_instant_batch_summary(batch: dict[str, object] | pd.Series | object) -> str:
    batch_dict = dict(batch)
    parts = []
    lot_no = _clean_instant_display_part(batch_dict.get("lot_no"))
    instrument = _clean_instant_display_part(batch_dict.get("instrument"))
    reagent = _clean_instant_display_part(batch_dict.get("reagent"))
    qc_material = _clean_instant_display_part(batch_dict.get("qc_material"))
    concentration = _clean_instant_display_part(batch_dict.get("concentration"))
    if lot_no:
        parts.append(f"质控批号 {lot_no}")
    if instrument:
        parts.append(instrument)
    if reagent:
        parts.append(reagent)
    if qc_material:
        parts.append(qc_material)
    if concentration:
        parts.append(concentration)
    return _join_instant_display_parts(parts) or "当前批次"


def _format_instant_datetime_text(value: object) -> str:
    cleaned_value = _clean_instant_display_part(value)
    if not cleaned_value:
        return "-"
    try:
        return pd.to_datetime(cleaned_value).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return cleaned_value


def _navigate_to_lj_batch(project_id: int | None, batch_id: int | None) -> None:
    if project_id is None or batch_id is None:
        return
    from pages.main_page import LJ_ENTRY_LABEL
    st.session_state["pending_top_level_method"] = LJ_ENTRY_LABEL
    st.session_state["pending_lj_project_id"] = int(project_id)
    st.session_state["pending_lj_batch_id"] = int(batch_id)
    st.session_state["pending_navigation_source"] = "instant_transfer"


def _close_instant_transfer_dialog() -> None:
    st.session_state["show_instant_transfer_dialog"] = False
    st.session_state.pop('instant_transfer_dialog_batch_id', None)


@st.dialog("确认转入 LJ 法", width="large", on_dismiss=_close_instant_transfer_dialog)
def _render_instant_transfer_dialog(batch_id: int) -> None:
    preview = build_instant_transfer_preview(batch_id)
    batch = preview["batch"]
    summary = preview["summary"]
    transfer_state = preview["transfer_state"]
    input_value_type_label = get_input_value_type_label(batch["input_value_type"])
    target_project_action = str(transfer_state.get("target_project_action", "") or "")
    target_project_summary = (
        f"{transfer_state['target_project_name']}（自动创建）"
        if target_project_action == "create"
        else str(transfer_state["target_project_name"])
    )
    target_batch_summary = str(transfer_state.get("target_batch_lot_no") or "").strip() or "系统将新建 LJ 批次"

    st.caption(
        "确认后会新建一个LJ批次，并带入本批次的有效检测值。"
        "今后请在新LJ批次继续录入；原即时法记录保留供查看。"
    )

    if transfer_state.get("is_transferred"):
        st.success("本批次已转入LJ法。请到下方显示的LJ项目和批次继续检测。")
        render_compact_stat_metrics(
            [
                ("当前状态", "已转入 LJ 法"),
                ("转入时间", _format_instant_datetime_text(transfer_state.get("transferred_at"))),
                ("去向 LJ 项目", str(transfer_state.get("transferred_to_lj_project_name") or "-")),
                ("去向 LJ 批次", str(transfer_state.get("transferred_to_lj_batch_display") or "-")),
            ]
        )
        if st.button("关闭", key=f"close_instant_transfer_dialog_{batch_id}", width="stretch"):
            _close_instant_transfer_dialog()
            st.rerun()
        return

    blockers = list(transfer_state.get("blockers", []))
    if blockers:
        st.warning("当前批次暂不可确认转入 LJ 法。")
        st.markdown("\n".join(f"- {reason}" for reason in blockers))
    render_compact_stat_metrics(
        [
            ("源即时法项目", str(batch["project_name"])),
            ("源即时法批次", _build_instant_batch_summary(batch)),
            ("输入值类型", input_value_type_label),
            ("总记录数", str(summary["total_count"])),
            ("有效点数", str(summary["effective_count"])),
            ("禁用点数", str(summary["disabled_count"])),
        ]
    )
    render_compact_stat_metrics(
        [
            ("均值", format_optional_float(summary["mean"])),
            ("SD", format_optional_float(summary["sd"])),
            ("实测变异系数（%）", format_optional_float(summary["cv"], digits=2, suffix="%")),
            ("目标 LJ 项目", target_project_summary),
            ("将新建的 LJ 批次", f"质控品批号：{target_batch_summary}"),
            ("参数建立有效点数", str(INSTANT_TRANSFER_READY_COUNT)),
        ]
    )
    st.markdown(
        "\n".join(
            [
                "- 前 20 个有效点将作为 LJ 参数建立数据。",
                "- 第 21 个及之后的有效点将作为 LJ 正式期数据。",
                "- 今后在新LJ批次继续录入；原即时法记录保留供查看。",
            ]
        )
    )

    action_col, cancel_col = st.columns([1, 1])
    if action_col.button(
        "确认转入 LJ 法",
        key=f"confirm_instant_transfer_dialog_{batch_id}",
        type="primary",
        disabled=bool(blockers),
        width="stretch",
    ):
        try:
            result = confirm_instant_transfer_to_lj(batch_id)
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.session_state["instant_transfer_notice"] = (
                f"即时法批次“{result['source_batch_lot_no']}”已转入 LJ 法，"
                f"已生成“由即时法转入”的 LJ 批次“{result['target_batch_lot_no']}”。"
            )
            st.session_state["instant_transfer_target_project_id"] = result["target_project_id"]
            st.session_state["instant_transfer_target_batch_id"] = result["target_batch_id"]
            _close_instant_transfer_dialog()
            st.rerun()
    if cancel_col.button(
        "取消",
        key=f"cancel_instant_transfer_dialog_{batch_id}",
        width="stretch",
    ):
        _close_instant_transfer_dialog()
        st.rerun()


def _render_instant_entry_and_summary_section(
    context: dict[str, object],
    selected_batch_id: int,
) -> None:
    from pages.lot_lifecycle_section import (
        render_result_lot_choice, render_result_lot_preparation, restore_result_lot_entry_draft,
    )
    restore_result_lot_entry_draft('instant', selected_batch_id)
    batch = context["batch"]
    input_value_type = context["input_value_type"]
    input_value_type_label = context["input_value_type_label"]
    operator_options = context["operator_options"]
    summary = context["summary"]
    transfer_state = context["transfer_state"]
    is_transferred = bool(transfer_state.get("is_transferred"))

    transfer_notice = st.session_state.pop("instant_transfer_notice", "")
    if transfer_notice:
        st.success(transfer_notice)

    if not is_transferred:
        st.markdown("**结果录入**")
    if not is_transferred and context.get("is_writable", True):
        if st.session_state.get("instant_entry_batch_id") != selected_batch_id:
            st.session_state["instant_entry_batch_id"] = selected_batch_id
            st.session_state["instant_entry_operator"] = operator_options[0] if operator_options else ""
            st.session_state["instant_entry_value"] = ""
            st.session_state["instant_entry_test_time"] = datetime.now()
        if st.session_state.get("instant_reset_entry_form", False):
            last_operator = str(st.session_state.get("instant_entry_operator", "") or "").strip()
            st.session_state["instant_entry_operator"] = last_operator
            st.session_state["instant_entry_value"] = ""
            st.session_state["instant_entry_test_time"] = datetime.now()
            st.session_state["instant_reset_entry_form"] = False

        st.caption("填写检测时间、检测人和本次检测值。")
        test_time = st.datetime_input(
            "检测时间",
            key="instant_entry_test_time",
        )
        operator = st.selectbox(
            "检测人",
            options=operator_options,
            index=None,
            key="instant_entry_operator",
            accept_new_options=True,
            placeholder="可选择历史姓名，也可直接输入新姓名",
        )
        value_text = st.text_input(
            input_value_type_label,
            key="instant_entry_value",
            placeholder="例如：123.4567",
        )
        parsed_value, log_value, value_error = parse_project_input_value(
            value_text,
            input_value_type,
            field_label=input_value_type_label,
        )
        lot_selection=render_result_lot_choice("instant",selected_batch_id,test_time,"instant_entry")
        render_result_lot_preparation('instant', selected_batch_id, 'instant_entry')
        if st.button("保存检测结果", key="instant_entry_save_button", type="primary", width="stretch"):
            validation_errors: list[str] = []
            cleaned_operator = str(operator or "").strip()
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
                    save_instant_result(
                        batch_id=selected_batch_id,
                        test_time=test_time.strftime("%Y-%m-%d %H:%M:%S"),
                        operator=cleaned_operator,
                        value=float(parsed_value),
                        log_value=log_value,
                        lot_selection=lot_selection,
                    )
                except ValueError as exc:
                    st.error(str(exc))
                    return
                st.success(f"即时法结果已保存到批次“{batch['lot_no']}”。")
                st.session_state["instant_reset_entry_form"] = True
                st.rerun()

    st.markdown("**累计统计**")
    render_compact_stat_metrics(
        [
            ("均值", format_optional_float(summary["mean"])),
            ("SD", format_optional_float(summary["sd"])),
            ("实测变异系数（%）", format_optional_float(summary["cv"], digits=2, suffix="%")),
        ]
    )
    st.caption("统计说明：均值、SD、CV 仅基于当前有效点计算；疑似离群点在未手工禁用前仍计入有效统计。")


def _render_instant_transfer_section(context: dict[str, object]) -> None:
    summary = context["summary"]
    transfer_state = context["transfer_state"]
    is_transferred = bool(transfer_state.get("is_transferred"))

    if is_transferred:
        st.success("已转入 LJ 法；原记录仅供查询，请在对应 LJ 批次继续录入。")
        if st.button(
            "前往对应 LJ 批次",
            key="instant_go_to_transferred_lj_batch",
            type="primary",
            width="stretch",
        ):
            _navigate_to_lj_batch(
                transfer_state.get("transferred_to_lj_project_id"),
                transfer_state.get("transferred_to_lj_batch_id"),
            )
            st.rerun()
        return

    progress, action = st.columns([3, 1], vertical_alignment="center")
    effective_count = int(summary["effective_count"])
    pending_count = int(summary.get("pending_outlier_review_count", 0))
    progress.caption(
        f"有效点 {effective_count}/{INSTANT_TRANSFER_READY_COUNT} · "
        f"已禁用 {summary['disabled_count']} · 待核对疑似离群 {pending_count}"
    )
    if action.button(
        "确认转入 LJ 法",
        key="open_instant_transfer_dialog",
        type="primary",
        disabled=(not bool(transfer_state.get("eligible"))
                  or bool(st.session_state.get("qc_record_dialog")
                          or st.session_state.get("qc_transfer_dialog"))),
        width="stretch",
    ):
        st.session_state["show_instant_transfer_dialog"] = True
        st.session_state['instant_transfer_dialog_batch_id'] = int(context['batch']['id'])
        st.rerun()
    blockers = list(transfer_state.get("blockers", []))
    if blockers:
        st.info("暂时不能转入 LJ 法，请先完成：\n\n" + "\n".join(f"- {reason}" for reason in blockers))
    if st.session_state.get("qc_record_dialog") or st.session_state.get("qc_transfer_dialog"):
        st.info("请先完成或取消当前打开的记录、导入或导出窗口，再点击“确认转入 LJ 法”。")


def _resolve_instant_tone_key(status: str) -> str | None:
    if status == "疑似离群":
        return "warning"
    if status == "警告":
        return "warning"
    if status == "有效点":
        return "accept"
    if status == "继续累计":
        return "参数建立期"
    return None


def _render_instant_si_method_explanation(summary: dict[str, object]) -> None:
    method_label = str(summary.get("instant_method_label", "即刻法 SI 值判定"))
    formula = str(
        summary.get(
            "instant_method_formula",
            "SI上限 = (X最大值 - x̄) / s；SI下限 = (x̄ - X最小值) / s",
        )
    )
    with st.expander("即刻法 SI 值说明", expanded=False):
        st.caption("用于前 20 个有效点内的即时质控提示，系统只提示，不自动剔除。")
        st.markdown(
            "\n".join(
                [
                    f"- 当前采用：`{method_label}`。",
                    "- 使用同一批质控品中尚未禁用的检测值进行判定。",
                    "- 起判条件：有效点数 `n < 3` 时不判定，`n >= 3` 时开始判定。",
                    "- 使用范围：即刻法 SI 表仅用于前 20 个有效点，超过后应转入 LJ 法。",
                    f"- 公式：`{formula}`，其中 `s` 使用样本标准差。",
                    "- 判定阈值：SI上限和 SI下限均不超过 n2s 为在控；超过 n2s 但不超过 n3s 为警告；超过 n3s 为疑似离群。",
                    "- 疑似离群结果由您核对。可在“检测记录”中选中一条，再点保留、禁用或恢复。",
                ]
            )
        )


def _render_instant_chart_analysis_section(context: dict[str, object]) -> object:
    analysis_df = context["analysis_df"]
    summary = context["summary"]
    latest_row = context["latest_row"]
    input_value_type_label = context["input_value_type_label"]
    figure = plot_instant_chart(
        analysis_df,
        summary,
        title="即时法趋势图",
        y_axis_label=input_value_type_label,
    )

    st.markdown("**即时法质控图**")
    st.pyplot(figure, clear_figure=False, width="stretch")

    st.divider()
    st.markdown("**最新判定区**")
    st.caption("在此查看当前批次的最新判定。")
    latest_source_text = (
        f"最近记录 #{int(latest_row['sequence'])}"
        if latest_row is not None and "sequence" in latest_row
        else "当前批次最近记录"
    )
    render_latest_analysis_card(
        status_label=str(summary["latest_status"]),
        summary_text=str(summary["latest_message"]),
        meta_items=list(summary.get("latest_meta", [])),
        source_text=latest_source_text,
        tone_key=_resolve_instant_tone_key(str(summary["latest_status"])),
    )
    return figure


def _build_instant_display_dataframe(
    analysis_df: pd.DataFrame,
    input_value_type_label: str,
) -> pd.DataFrame:
    display_df = analysis_df.copy()
    if display_df.empty:
        return display_df

    display_df["test_time"] = pd.to_datetime(display_df["test_time"]).dt.strftime("%Y-%m-%d %H:%M:%S")
    display_df["value"] = display_df["value"].map(lambda value: f"{float(value):.4f}")
    display_df["effective_sequence"] = display_df["effective_sequence"].map(
        lambda value: "" if pd.isna(value) else str(int(value))
    )
    display_df["is_outlier_suspect"] = display_df["is_outlier_suspect"].map(lambda flag: "是" if int(flag) == 1 else "否")
    for column_name in ["si_upper", "si_lower"]:
        display_df[column_name] = display_df[column_name].map(
            lambda value: "-" if value is None or pd.isna(value) else f"{float(value):.4f}"
        )
    for column_name in ["si_n2s", "si_n3s"]:
        display_df[column_name] = display_df[column_name].map(
            lambda value: "-" if value is None or pd.isna(value) else f"{float(value):.2f}"
        )
    display_df["si_trigger_side"] = display_df["si_trigger_side"].map(
        lambda value: {"max": "最大值", "min": "最小值"}.get(str(value or ""), "-")
    )
    display_df["grubbs_statistic"] = display_df["grubbs_statistic"].map(
        lambda value: "-" if value is None or pd.isna(value) else f"{float(value):.4f}"
    )
    display_df["grubbs_threshold"] = display_df["grubbs_threshold"].map(
        lambda value: "-" if value is None or pd.isna(value) else f"{float(value):.4f}"
    )
    display_df["manual_status"] = display_df["manual_status"].map(get_instant_manual_status_label)
    ordered_columns = [
        "sequence",
        "effective_sequence",
        "test_time",
        "operator",
        "value",
        "status",
        "manual_status",
        "is_outlier_suspect",
        "si_upper",
        "si_lower",
        "si_n2s",
        "si_n3s",
        "si_trigger_side",
    ]
    return display_df[ordered_columns].rename(
        columns={
            "sequence": "记录序号",
            "effective_sequence": "有效序号",
            "test_time": "检测时间",
            "operator": "检测人",
            "value": input_value_type_label,
            "status": "状态",
            "manual_status": "手工处理状态",
            "is_outlier_suspect": "疑似离群",
            "si_upper": "SI上限",
            "si_lower": "SI下限",
            "si_n2s": "n2s",
            "si_n3s": "n3s",
            "si_trigger_side": "触发方向",
        }
    )


def _render_instant_records_section(context: dict[str, object]) -> None:
    from ui.record_workspace import render_record_selection
    from ui.record_actions import render_record_toolbar

    analysis_df = context["analysis_df"].sort_values(['test_time', 'id'], ascending=False)
    batch_id = int(context["batch"]["id"])
    st.markdown("**检测记录**")
    filter_key = f'instant_{batch_id}_filter'
    widget_key = filter_key + '_widget'
    if widget_key not in st.session_state:
        st.session_state[widget_key] = st.session_state.get(filter_key, '全部')
    def remember_filter():
        st.session_state[filter_key] = st.session_state[widget_key]
    scope = st.selectbox('记录范围', ['全部', '有效点', '待核对', '已禁用'], key=widget_key, on_change=remember_filter)
    st.session_state[filter_key] = scope
    if scope == '有效点':
        analysis_df = analysis_df.loc[analysis_df.is_effective == 1]
    elif scope == '待核对':
        analysis_df = analysis_df.loc[(analysis_df.is_outlier_suspect == 1) & (analysis_df.manual_status == 'pending_review')]
    elif scope == '已禁用':
        analysis_df = analysis_df.loc[analysis_df.is_effective == 0]
    display = _build_instant_display_dataframe(analysis_df, context["input_value_type_label"])
    if not display.empty:
        display = display[["记录序号", "有效序号", "检测时间", "检测人",
                           context["input_value_type_label"], "状态", "手工处理状态", "疑似离群"]]
    selected_id = render_record_selection(
        display, analysis_df["id"].astype(int).tolist() if not analysis_df.empty else [],
        scope=f"instant_{batch_id}",
    )
    selected = analysis_df.loc[analysis_df["id"] == selected_id] if selected_id is not None else pd.DataFrame()
    row = selected.iloc[0] if not selected.empty else None
    render_record_toolbar(
        "instant", batch_id, selected_id,
        allow_edit=False,
        allow_status=row is not None and bool(context.get("is_writable")) and not bool(context["transfer_state"].get("is_transferred")),
        included=bool(row["is_effective"]) if row is not None else None,
        abnormal=(bool(row["is_outlier_suspect"]) and str(row["manual_status"]) == "pending_review") if row is not None else False,
    )


def _render_instant_context(context: dict[str, object]) -> None:
    batch = context["batch"]
    summary = context["summary"]
    transfer_state = context["transfer_state"]
    st.caption(
        f"{batch['project_name']} · {_build_instant_batch_summary(batch)} · "
        f"{context['input_value_type_label']} · 共 {summary['total_count']} 条记录"
    )
    with st.expander("查看批次资料"):
        items = [
            ("检验项目", batch["project_name"]),
            ("仪器", batch["instrument"]),
            ("试剂", batch["reagent"]),
            ("质控品", batch["qc_material"]),
            ("浓度", batch["concentration"]),
            ("质控品批号", batch["lot_no"]),
            ("质控水平", batch["level_name"] or "-"),
            ("单位", batch["unit_symbol"] or "-"),
            ("检测方法", batch["method_name"] or "-"),
            ("批次名称", batch["v11_config_name"] or "-"),
            ("批号效期", batch["v11_expiry_date"] or "-"),
            ("输入值类型", context["input_value_type_label"]),
        ]
        if transfer_state.get("is_transferred"):
            items.extend([
                ("转入时间", _format_instant_datetime_text(transfer_state.get("transferred_at"))),
                ("转入有效点数", str(transfer_state.get("transferred_effective_count") or 0)),
                ("去向 LJ 项目", transfer_state.get("transferred_to_lj_project_name") or "-"),
                ("去向 LJ 批次", transfer_state.get("transferred_to_lj_batch_display") or "-"),
            ])
        st.dataframe(pd.DataFrame(items, columns=["项目", "内容"]), hide_index=True, width="stretch")


def render_instant_page() -> None:
    from pages.lot_lifecycle_section import is_batch_writable
    from ui.record_actions import render_pending_record_dialog

    st.subheader("即时法")
    projects_df, selected_project_id, batches_df, selected_batch_id, issues = prepare_instant_v12_project_batch_context()
    if selected_project_id is None or selected_batch_id is None:
        render_instant_v12_configuration_selection(
            st.container(), projects_df, selected_project_id, batches_df, selected_batch_id, issues,
        )
        if st.session_state.get('qc_record_dialog'):
            render_pending_record_dialog('instant', None)
        elif st.session_state.get('show_instant_transfer_dialog') and st.session_state.get('instant_transfer_dialog_batch_id'):
            _render_instant_transfer_dialog(st.session_state['instant_transfer_dialog_batch_id'])
        return

    context = build_instant_workbench_context(selected_batch_id)
    transfer_state = context["transfer_state"]
    context["is_writable"] = not bool(transfer_state.get("is_transferred")) and is_batch_writable("instant", selected_batch_id)
    _render_instant_context(context)
    _render_instant_transfer_section(context)

    from ui.quality_targets import render_batch_quality
    render_batch_quality("instant", selected_batch_id)
    entry_col, chart_col = workbench_chart_columns("instant")
    with entry_col:
        _render_instant_entry_and_summary_section(context, selected_batch_id)
    with chart_col:
        _render_instant_chart_analysis_section(context)

    _render_instant_records_section(context)
    _render_instant_si_method_explanation(context["summary"])

    if st.session_state.get("show_instant_transfer_dialog"):
        _render_instant_transfer_dialog(st.session_state.get('instant_transfer_dialog_batch_id', selected_batch_id))
    else:
        render_pending_record_dialog("instant", selected_batch_id)
