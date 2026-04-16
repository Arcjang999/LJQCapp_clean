from __future__ import annotations

from html import escape as html_escape
from textwrap import dedent
from typing import Any

import pandas as pd
import streamlit as st

from database import delete_result, update_result
from pages.management import sync_selector_state
from services.value_type_service import (
    DEFAULT_INPUT_VALUE_TYPE,
    build_level_measurement_label,
    compute_legacy_log_value,
    get_input_value_type_label,
    normalize_input_value_type,
    validate_project_numeric_value,
)
from ui.common import (
    format_rule_code,
    format_zscore_status_label,
    render_table_html,
    summarize_note_for_table,
)
from zscore_logic import (
    build_zscore_maintenance_dialog_state,
    delete_saved_zscore_run,
    format_level_id_display,
    get_phase_label,
    get_zscore_display_sequence,
    sort_zscore_runs_for_maintenance,
    update_saved_zscore_run,
)


def bump_record_maintenance_dialog_nonce() -> int:
    next_nonce = int(st.session_state.get("record_maintenance_dialog_nonce", 0)) + 1
    st.session_state["record_maintenance_dialog_nonce"] = next_nonce
    return next_nonce


def close_record_maintenance_dialog() -> None:
    st.session_state["show_record_maintenance_dialog"] = False
    bump_record_maintenance_dialog_nonce()


@st.dialog("检测记录维护", width="large", on_dismiss=close_record_maintenance_dialog)
def render_record_maintenance_dialog(
    qc_df: pd.DataFrame,
    input_value_type: str = DEFAULT_INPUT_VALUE_TYPE,
) -> None:
    notice_message = st.session_state.pop("record_maintenance_notice", "")
    if notice_message:
        st.success(notice_message)

    normalized_input_value_type = normalize_input_value_type(input_value_type)
    input_value_type_label = get_input_value_type_label(normalized_input_value_type)
    st.caption(f"在此可以选择检测记录进行修改或删除，当前项目的输入值类型为 {input_value_type_label}。")
    if qc_df.empty:
        st.info("当前批次暂无检测记录可维护。")
        if st.button("关闭", key="close_record_dialog_empty", width="stretch"):
            close_record_maintenance_dialog()
            st.rerun()
        return

    maintenance_df = qc_df.sort_values(["test_time", "id"], ascending=[False, False]).reset_index(drop=True)
    result_labels, result_options = build_result_select_options(maintenance_df)
    sync_selector_state(
        selector_key="result_selector",
        selected_id_key="selected_result_id",
        options_map=result_options,
        placeholder=result_labels[0],
    )
    selected_result_label = st.selectbox(
        "选择需要编辑或删除的检测记录",
        options=result_labels,
        key="result_selector",
    )
    new_result_id = result_options[selected_result_label]
    current_result_id = st.session_state.get("selected_result_id")
    if new_result_id != current_result_id:
        st.session_state["selected_result_id"] = new_result_id
        bump_record_maintenance_dialog_nonce()
        st.rerun()

    selected_result_id = st.session_state.get("selected_result_id")
    if selected_result_id is not None:
        selected_rows = maintenance_df[maintenance_df["id"] == selected_result_id]
        if not selected_rows.empty:
            selected_result = selected_rows.iloc[0]
            dialog_nonce = int(st.session_state.get("record_maintenance_dialog_nonce", 0))
            confirm_delete_key = f"confirm_delete_result_{dialog_nonce}_{int(selected_result_id)}"
            maintenance_left, maintenance_right = st.columns([1.25, 0.75], gap="large")

            with maintenance_left:
                st.caption(
                    f"当前选中：序号 {int(selected_result['sequence'])} | "
                    f"状态 {selected_result['status']} | 触发规则 "
                    f"{selected_result['rule_hits'] or '无'}"
                )
                with st.form("edit_result_form"):
                    edit_test_time = st.datetime_input(
                        "检测时间",
                        value=pd.Timestamp(selected_result["test_time"]).to_pydatetime(),
                    )
                    edit_operator = st.text_input(
                        "检测人",
                        value=str(selected_result["operator"]),
                    )
                    edit_value = st.number_input(
                        input_value_type_label,
                        value=float(selected_result["value"]),
                        format="%.4f",
                    )
                    edit_reagent_changed = st.checkbox(
                        "本次为试剂批号变更点",
                        value=bool(int(selected_result["reagent_lot_changed"])),
                    )
                    edit_manual_note = st.text_area(
                        "手动备注（可选）",
                        value=str(selected_result.get("manual_note", "") or ""),
                        height=88,
                    )
                    edit_submitted = st.form_submit_button(
                        "保存记录修改",
                        width="stretch",
                    )

                    if edit_submitted:
                        validation_errors: list[str] = []
                        cleaned_operator = edit_operator.strip()

                        if edit_test_time is None:
                            validation_errors.append("请填写检测时间。")
                        if not cleaned_operator:
                            validation_errors.append("请填写检测人，不能为空。")
                        value_error = validate_project_numeric_value(
                            edit_value,
                            normalized_input_value_type,
                            field_label=input_value_type_label,
                        )
                        if value_error is not None:
                            validation_errors.append(value_error)

                        if validation_errors:
                            st.error("\n".join(validation_errors))
                        else:
                            update_result(
                                result_id=int(selected_result_id),
                                test_time=edit_test_time.strftime("%Y-%m-%d %H:%M:%S"),
                                operator=cleaned_operator,
                                value=float(edit_value),
                                log_value=compute_legacy_log_value(
                                    float(edit_value),
                                    normalized_input_value_type,
                                ),
                                reagent_lot_changed=int(edit_reagent_changed),
                                manual_note=str(edit_manual_note or "").strip(),
                            )
                            close_record_maintenance_dialog()
                            st.success("检测记录已更新。")
                            st.rerun()

            with maintenance_right:
                st.caption("删除后会重新计算后续序号、建靶/正式阶段以及 Westgard 判定。")
                confirm_delete = st.checkbox(
                    "我确认删除这条检测记录",
                    key=confirm_delete_key,
                )
                if st.button(
                    "删除所选记录",
                    key="delete_record_dialog_button",
                    width="stretch",
                    disabled=not confirm_delete,
                ):
                    delete_result(int(selected_result_id))
                    st.session_state["selected_result_id"] = None
                    bump_record_maintenance_dialog_nonce()
                    st.session_state["show_record_maintenance_dialog"] = True
                    st.session_state["record_maintenance_notice"] = "检测记录已删除。"
                    st.rerun()

    st.divider()
    if st.button("关闭", key="close_record_dialog", width="stretch"):
        close_record_maintenance_dialog()
        st.rerun()


def bump_zscore_record_maintenance_dialog_nonce() -> int:
    next_nonce = int(st.session_state.get("zscore_record_maintenance_dialog_nonce", 0)) + 1
    st.session_state["zscore_record_maintenance_dialog_nonce"] = next_nonce
    return next_nonce


def close_zscore_record_maintenance_dialog() -> None:
    st.session_state["show_zscore_record_maintenance_dialog"] = False
    bump_zscore_record_maintenance_dialog_nonce()


@st.dialog("检测记录维护", width="large", on_dismiss=close_zscore_record_maintenance_dialog)
def render_zscore_record_maintenance_dialog(
    saved_runs: list[dict[str, Any]],
    batch_context: dict[str, Any],
) -> None:
    notice_message = str(st.session_state.pop("zscore_record_maintenance_notice", "") or "")
    if notice_message:
        st.success(notice_message)

    input_value_type = normalize_input_value_type(batch_context["batch"]["input_value_type"])
    input_value_type_label = get_input_value_type_label(input_value_type)
    st.caption(
        f"在此查看当前批次已保存的检测记录。未锁定记录仍可维护检测时间、检测人和各水平{input_value_type_label}；建靶期记录在正式期后仅支持查看。"
    )
    if not saved_runs:
        st.info("当前批次暂无已保存的检测记录可维护。")
        if st.button("关闭", key="close_zscore_record_dialog_empty", width="stretch"):
            close_zscore_record_maintenance_dialog()
            st.rerun()
        return

    template = batch_context["template"]
    level_label_map = dict(batch_context["level_label_map"])
    maintenance_runs = sort_zscore_runs_for_maintenance(saved_runs)

    st.dataframe(
        build_zscore_record_maintenance_dataframe(maintenance_runs, level_label_map),
        hide_index=True,
        width="stretch",
    )

    run_labels, run_options = build_zscore_run_select_options(maintenance_runs, level_label_map)
    sync_selector_state(
        selector_key="zscore_run_selector",
        selected_id_key="selected_zscore_run_id",
        options_map=run_options,
        placeholder=run_labels[0],
    )
    selected_run_label = st.selectbox(
        "选择需要查看或维护的检测记录",
        options=run_labels,
        key="zscore_run_selector",
    )
    selected_run_id = run_options[selected_run_label]
    current_run_id = st.session_state.get("selected_zscore_run_id")
    if selected_run_id != current_run_id:
        st.session_state["selected_zscore_run_id"] = selected_run_id
        bump_zscore_record_maintenance_dialog_nonce()
        st.rerun()

    if selected_run_id is None:
        st.info("请选择一条已保存的检测记录。")
    else:
        selected_run = next(
            (run for run in maintenance_runs if int(run["run_id"]) == int(selected_run_id)),
            None,
        )
        if selected_run is not None:
            dialog_nonce = int(st.session_state.get("zscore_record_maintenance_dialog_nonce", 0))
            confirm_delete_key = f"confirm_delete_zscore_run_{dialog_nonce}_{int(selected_run_id)}"
            delete_button_key = f"delete_zscore_run_button_{dialog_nonce}_{int(selected_run_id)}"
            is_locked_for_maintenance = bool(selected_run.get("is_locked_for_maintenance"))
            sequence_number = get_zscore_display_sequence(selected_run)
            maintenance_left, maintenance_right = st.columns([1.25, 0.75], gap="large")

            with maintenance_left:
                st.caption(
                    f"当前选中：第 {sequence_number} 次检测 | "
                    f"{pd.Timestamp(selected_run['test_time']).strftime('%Y-%m-%d %H:%M:%S')} | "
                    f"阶段 {selected_run.get('phase_label')} | 判定 {format_zscore_status_label(selected_run.get('run_status'))} | "
                    f"触发规则 {format_zscore_rule_hits(selected_run.get('rule_hits_run', []))}"
                )
                if is_locked_for_maintenance:
                    st.info("该建靶期检测记录已锁定为只读，可查看，但不能修改或删除。")
                    readonly_prefix = f"readonly_zscore_run_{dialog_nonce}_{int(selected_run_id)}"
                    st.datetime_input(
                        "检测时间",
                        value=pd.Timestamp(selected_run["test_time"]).to_pydatetime(),
                        disabled=True,
                        key=f"{readonly_prefix}_test_time",
                    )
                    st.text_input(
                        "检测人",
                        value=str(selected_run.get("operator", "") or ""),
                        disabled=True,
                        key=f"{readonly_prefix}_operator",
                    )
                    st.text_area(
                        "手动备注",
                        value=str(selected_run.get("manual_note", "") or ""),
                        height=88,
                        disabled=True,
                        key=f"{readonly_prefix}_manual_note",
                    )
                    st.markdown(f"**各水平{input_value_type_label}**")
                    level_result_map = {
                        str(level_result.get("level_id")): level_result
                        for level_result in selected_run.get("level_results", [])
                    }
                    for level_id in template["level_ids"]:
                        display_label, level_caption = format_zscore_level_display(level_id, level_label_map)
                        current_level_result = level_result_map.get(level_id, {})
                        field_label = build_level_measurement_label(display_label, input_value_type)
                        st.number_input(
                            field_label,
                            value=float(current_level_result.get("raw_value") or 0.0),
                            format="%.4f",
                            key=f"{readonly_prefix}_{level_id.replace(' ', '_')}",
                            disabled=True,
                        )
                        if level_caption:
                            st.caption(level_caption)
                else:
                    with st.form(f"edit_zscore_run_form_{dialog_nonce}_{int(selected_run_id)}"):
                        edit_test_time = st.datetime_input(
                            "检测时间",
                            value=pd.Timestamp(selected_run["test_time"]).to_pydatetime(),
                        )
                        edit_operator = st.text_input(
                            "检测人",
                            value=str(selected_run.get("operator", "") or ""),
                        )
                        edit_manual_note = st.text_area(
                            "手动备注（可选）",
                            value=str(selected_run.get("manual_note", "") or ""),
                            height=88,
                        )
                        st.markdown(f"**各水平{input_value_type_label}**")
                        edited_level_values: dict[str, float] = {}
                        level_result_map = {
                            str(level_result.get("level_id")): level_result
                            for level_result in selected_run.get("level_results", [])
                        }
                        for level_id in template["level_ids"]:
                            display_label, level_caption = format_zscore_level_display(level_id, level_label_map)
                            current_level_result = level_result_map.get(level_id, {})
                            field_label = build_level_measurement_label(display_label, input_value_type)
                            edited_level_values[level_id] = st.number_input(
                                field_label,
                                value=float(current_level_result.get("raw_value") or 0.0),
                                format="%.4f",
                                key=f"edit_zscore_run_{dialog_nonce}_{int(selected_run_id)}_{level_id.replace(' ', '_')}",
                            )
                            if level_caption:
                                st.caption(level_caption)
                        edit_submitted = st.form_submit_button(
                            "保存记录修改",
                            width="stretch",
                        )

                        if edit_submitted:
                            validation_errors: list[str] = []
                            cleaned_operator = edit_operator.strip()
                            updated_level_results: list[dict[str, Any]] = []
                            cleaned_manual_note = str(edit_manual_note or "").strip()

                            if edit_test_time is None:
                                validation_errors.append("请填写检测时间。")
                            if not cleaned_operator:
                                validation_errors.append("请填写检测人，不能为空。")

                            for level_id in template["level_ids"]:
                                display_level = format_zscore_level_display(level_id, level_label_map)[0]
                                raw_value = edited_level_values[level_id]
                                field_label = build_level_measurement_label(display_level, input_value_type)
                                value_error = validate_project_numeric_value(
                                    raw_value,
                                    input_value_type,
                                    field_label=field_label,
                                )
                                if value_error is not None:
                                    validation_errors.append(value_error)
                                    continue
                                updated_level_results.append(
                                    {
                                        "level_id": level_id,
                                        "raw_value": float(raw_value),
                                        "log_value": compute_legacy_log_value(float(raw_value), input_value_type),
                                    }
                                )

                            if validation_errors:
                                st.error("\n".join(dict.fromkeys(validation_errors)))
                            else:
                                try:
                                    rebuild_state = update_saved_zscore_run(
                                        run_id=int(selected_run_id),
                                        test_time=edit_test_time,
                                        operator=cleaned_operator,
                                        level_results=updated_level_results,
                                        manual_note=cleaned_manual_note,
                                    )
                                except ValueError as exc:
                                    st.error(str(exc))
                                else:
                                    dialog_state = build_zscore_maintenance_dialog_state(
                                        action="update",
                                        available_runs=rebuild_state.get("runs", []),
                                        preferred_run_id=int(selected_run_id),
                                    )
                                    st.session_state["show_zscore_record_maintenance_dialog"] = bool(
                                        dialog_state["keep_dialog_open"]
                                    )
                                    st.session_state["selected_zscore_run_id"] = dialog_state["selected_run_id"]
                                    st.session_state["zscore_record_maintenance_notice"] = dialog_state["dialog_notice"]
                                    bump_zscore_record_maintenance_dialog_nonce()
                                    st.rerun()

            with maintenance_right:
                if is_locked_for_maintenance:
                    st.caption("该记录为建靶期历史记录，删除和编辑均已禁用。")
                else:
                    st.caption("删除后会同步重算当前批次统计和图表数据。")
                    confirm_delete = st.checkbox(
                        "我确认删除这条检测记录",
                        key=confirm_delete_key,
                    )
                    if st.button(
                        "删除所选记录",
                        key=delete_button_key,
                        width="stretch",
                        disabled=not confirm_delete,
                    ):
                        try:
                            rebuild_state = delete_saved_zscore_run(int(selected_run_id))
                        except ValueError as exc:
                            st.error(str(exc))
                        else:
                            dialog_state = build_zscore_maintenance_dialog_state(
                                action="delete",
                                available_runs=rebuild_state.get("runs", []),
                                preferred_run_id=int(selected_run_id),
                            )
                            st.session_state["show_zscore_record_maintenance_dialog"] = bool(
                                dialog_state["keep_dialog_open"]
                            )
                            st.session_state["selected_zscore_run_id"] = dialog_state["selected_run_id"]
                            st.session_state["zscore_record_maintenance_notice"] = dialog_state["dialog_notice"]
                            bump_zscore_record_maintenance_dialog_nonce()
                            st.rerun()

    st.divider()
    if st.button("关闭", key="close_zscore_record_dialog", width="stretch"):
        close_zscore_record_maintenance_dialog()
        st.rerun()


def render_records_table(display_df: pd.DataFrame) -> None:
    if display_df.empty:
        st.info("当前批次暂无检测记录。")
        return

    def resolve_column_class(column_name: str) -> str:
        if column_name in {"检测序号", "生效建靶序号", "阶段", "判定结果", "疑似离群", "参与建靶统计"}:
            return "qc-records-col-narrow"
        if column_name in {"检测时间", "检测人", "处理时间", "触发规则", "分析提示", "备注"}:
            return "qc-records-col-wide"
        return "qc-records-col-default"

    html_rows: list[str] = []
    for _, row in display_df.iterrows():
        row_cells: list[str] = []
        for column_name in display_df.columns:
            value = "" if pd.isna(row[column_name]) else str(row[column_name])
            cell_text = html_escape(value).replace("\n", "<br>")
            row_cells.append(
                f'<td class="{resolve_column_class(str(column_name))}" title="{html_escape(value)}">{cell_text}</td>'
            )
        html_rows.append("<tr>" + "".join(row_cells) + "</tr>")

    headers = "".join(
        f'<th class="{resolve_column_class(str(column))}">{html_escape(str(column))}</th>'
        for column in display_df.columns
    )
    records_html = dedent(
        f"""
        <div class="qc-records-wrapper">
            <style>
            .qc-records-wrapper {{
                width: 100%;
                overflow-x: auto;
                border: 1px solid #d9dde7;
                border-radius: 14px;
                background: #ffffff;
            }}
            .qc-records-table {{
                width: max-content;
                min-width: 100%;
                border-collapse: collapse;
                table-layout: auto;
                font-size: 13px;
            }}
            .qc-records-table th,
            .qc-records-table td {{
                min-width: 104px;
                max-width: 260px;
                border: 1px solid #d9dde7;
                padding: 9px 10px;
                vertical-align: top;
                white-space: normal;
                overflow-wrap: anywhere;
                word-break: break-word;
                line-height: 1.55;
            }}
            .qc-records-table th {{
                background: #f2f5fa;
                font-weight: 700;
                color: #223045;
                position: sticky;
                top: 0;
                z-index: 1;
            }}
            .qc-records-table .qc-records-col-narrow {{
                min-width: 86px;
                max-width: 128px;
            }}
            .qc-records-table .qc-records-col-wide {{
                min-width: 156px;
                max-width: 360px;
            }}
            .qc-records-table tbody tr:nth-child(even) {{
                background: #fbfcfe;
            }}
            </style>
            <table class="qc-records-table">
                <thead>
                    <tr>{headers}</tr>
                </thead>
                <tbody>
                    {''.join(html_rows)}
                </tbody>
            </table>
        </div>
        """
    ).strip()
    render_table_html(records_html, row_count=len(display_df))


def build_result_label(row: pd.Series) -> str:
    test_time = pd.Timestamp(row["test_time"]).strftime("%Y-%m-%d %H:%M:%S")
    return (
        f"检测序号 {int(row['sequence'])} | "
        f"{test_time} | {float(row['value']):.4f} | {row['operator']}"
    )


def build_result_select_options(results_df: pd.DataFrame) -> tuple[list[str], dict[str, int | None]]:
    option_map = {"请选择检测记录": None}
    for _, row in results_df.iterrows():
        option_map[build_result_label(row)] = int(row["id"])
    return list(option_map.keys()), option_map


def build_zscore_run_level_summary(
    level_results: list[dict[str, Any]],
    level_label_map: dict[str, str],
) -> str:
    summary_items: list[str] = []
    for level_result in sorted(level_results, key=lambda item: str(item.get("level_id") or "")):
        level_id = str(level_result.get("level_id") or "")
        display_label, _ = format_zscore_level_display(level_id, level_label_map)
        raw_value = level_result.get("raw_value")
        value_text = "-" if raw_value is None else f"{float(raw_value):.4f}"
        summary_items.append(f"{display_label}={value_text}")
    return " | ".join(summary_items)


def build_zscore_run_label(run: dict[str, Any], level_label_map: dict[str, str]) -> str:
    test_time = pd.Timestamp(run["test_time"]).strftime("%Y-%m-%d %H:%M:%S")
    level_summary = build_zscore_run_level_summary(run.get("level_results", []), level_label_map)
    test_sequence = get_zscore_display_sequence(run)
    return f"第 {test_sequence} 次检测 | {test_time} | {run.get('operator', '')} | {level_summary}"


def build_zscore_run_select_options(
    saved_runs: list[dict[str, Any]],
    level_label_map: dict[str, str],
) -> tuple[list[str], dict[str, int | None]]:
    option_map = {"请选择检测记录": None}
    for run in saved_runs:
        option_map[build_zscore_run_label(run, level_label_map)] = int(run["run_id"])
    return list(option_map.keys()), option_map


def build_zscore_record_maintenance_dataframe(
    saved_runs: list[dict[str, Any]],
    level_label_map: dict[str, str],
) -> pd.DataFrame:
    rows = []
    for run in saved_runs:
        rows.append(
            {
                "检测序号": get_zscore_display_sequence(run),
                "检测时间": pd.Timestamp(run["test_time"]).strftime("%Y-%m-%d %H:%M:%S"),
                "检测人": str(run.get("operator", "") or ""),
                "阶段": str(run.get("phase_label") or get_phase_label(run.get("phase"))),
                "判定": format_zscore_status_label(run.get("run_status", "pending")),
                "维护状态": "建靶期只读" if bool(run.get("is_locked_for_maintenance")) else "可维护",
                "水平摘要": build_zscore_run_level_summary(run.get("level_results", []), level_label_map),
                "备注": summarize_note_for_table(run.get("manual_note", "")),
            }
        )
    return pd.DataFrame(rows)


def format_zscore_level_display(level_id: str, level_label_map: dict[str, str]) -> tuple[str, str | None]:
    default_level_label = format_level_id_display(level_id)
    level_label = str(level_label_map.get(level_id, level_id) or level_id).strip() or level_id
    if level_label == level_id:
        return default_level_label, None
    return level_label, default_level_label


def format_zscore_rule_hits(rule_hits: list[dict[str, Any]]) -> str:
    if not rule_hits:
        return "无"
    ordered_rule_ids = list(dict.fromkeys(hit["rule_id"] for hit in rule_hits))
    return "、".join(format_rule_code(rule_id) for rule_id in ordered_rule_ids)
