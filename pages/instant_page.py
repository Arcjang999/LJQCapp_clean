from __future__ import annotations

from datetime import datetime

import pandas as pd
import streamlit as st

from database import (
    count_instant_project_batches,
    create_instant_batch,
    create_instant_project,
    get_instant_batch,
    get_instant_project,
    list_instant_batches,
    list_instant_projects,
)
from pages.management import guard_work_tab_selection, sync_selector_state
from plotting import plot_instant_chart
from services.instant_service import (
    INSTANT_TRANSFER_READY_COUNT,
    build_instant_transfer_preview,
    build_instant_workbench_context,
    confirm_instant_transfer_to_lj,
    disable_instant_result,
    get_instant_manual_status_label,
    keep_instant_result,
    restore_instant_result,
    save_instant_result,
)
from services.value_type_service import (
    INPUT_VALUE_TYPE_OPTIONS,
    get_input_value_type_label,
    parse_project_input_value,
)
from ui.common import (
    TEXT,
    format_datetime_column,
    format_optional_float,
    render_compact_stat_metrics,
    render_latest_analysis_card,
    render_section_intro,
    render_workbench_context_bar,
)


def _ensure_selected_instant_project(projects_df: pd.DataFrame) -> int | None:
    if projects_df.empty:
        st.session_state["instant_selected_project_id"] = None
        st.session_state["instant_project_selector"] = "请选择即时法项目"
        return None
    valid_ids = set(projects_df["id"].astype(int).tolist())
    current_id = st.session_state.get("instant_selected_project_id")
    if current_id is not None and current_id not in valid_ids:
        st.session_state["instant_selected_project_id"] = None
        st.session_state["instant_project_selector"] = "请选择即时法项目"
        return None
    return None if current_id is None else int(current_id)


def _ensure_selected_instant_batch(batches_df: pd.DataFrame) -> int | None:
    if batches_df.empty:
        st.session_state["instant_selected_batch_id"] = None
        st.session_state["instant_batch_selector"] = "请选择即时法批次"
        return None
    valid_ids = set(batches_df["id"].astype(int).tolist())
    current_id = st.session_state.get("instant_selected_batch_id")
    if current_id is not None and current_id not in valid_ids:
        st.session_state["instant_selected_batch_id"] = None
        st.session_state["instant_batch_selector"] = "请选择即时法批次"
        return None
    return None if current_id is None else int(current_id)


def prepare_instant_project_batch_context() -> tuple[pd.DataFrame, int | None, pd.DataFrame, int | None]:
    projects_df = list_instant_projects()
    selected_project_id = _ensure_selected_instant_project(projects_df)
    batches_df = list_instant_batches(selected_project_id) if selected_project_id is not None else pd.DataFrame()
    selected_batch_id = _ensure_selected_instant_batch(batches_df)
    return projects_df, selected_project_id, batches_df, selected_batch_id


def _clean_instant_display_part(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _format_selector_datetime(value: object) -> str:
    cleaned_value = _clean_instant_display_part(value)
    if not cleaned_value:
        return ""
    try:
        return pd.to_datetime(cleaned_value).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return cleaned_value


def _join_instant_display_parts(parts: list[str]) -> str:
    return " | ".join(part for part in parts if part)


def _build_instant_project_label(row: pd.Series) -> str:
    project_name = _clean_instant_display_part(row.get("name")) or "未命名项目"
    input_value_type_label = get_input_value_type_label(row.get("input_value_type"))
    return _join_instant_display_parts([project_name, input_value_type_label])


def _build_instant_batch_label(row: pd.Series) -> str:
    lot_no = _clean_instant_display_part(row.get("lot_no"))
    created_at = _format_selector_datetime(row.get("created_at"))
    parts = []
    if lot_no:
        parts.append(f"质控批号：{lot_no}")
    if created_at:
        parts.append(f"创建于 {created_at}")
    if not parts:
        parts.append("未命名批次")
    return _join_instant_display_parts(parts)


def _build_instant_project_select_options(projects_df: pd.DataFrame) -> tuple[list[str], dict[str, int | None]]:
    option_map = {"请选择即时法项目": None}
    for _, row in projects_df.iterrows():
        option_map[_build_instant_project_label(row)] = int(row["id"])
    return list(option_map.keys()), option_map


def _build_instant_batch_select_options(batches_df: pd.DataFrame) -> tuple[list[str], dict[str, int | None]]:
    option_map = {"请选择即时法批次": None}
    for _, row in batches_df.iterrows():
        option_map[_build_instant_batch_label(row)] = int(row["id"])
    return list(option_map.keys()), option_map


def _build_instant_project_table(projects_df: pd.DataFrame) -> pd.DataFrame:
    if projects_df.empty:
        return projects_df
    display_df = format_datetime_column(projects_df, "created_at").copy()
    display_df["name"] = display_df["name"].map(_clean_instant_display_part)
    display_df["input_value_type"] = display_df["input_value_type"].map(get_input_value_type_label)
    return display_df[["name", "input_value_type", "created_at"]].rename(
        columns={
            "name": "项目名称",
            "input_value_type": "输入值类型",
            "created_at": "创建时间",
        }
    )


def _build_instant_batch_table(batches_df: pd.DataFrame) -> pd.DataFrame:
    if batches_df.empty:
        return batches_df
    display_df = format_datetime_column(batches_df, "created_at").copy()
    for column_name in ["lot_no", "instrument", "reagent", "qc_material", "concentration"]:
        display_df[column_name] = display_df[column_name].map(_clean_instant_display_part)
    return display_df[
        ["lot_no", "instrument", "reagent", "qc_material", "concentration", "created_at"]
    ].rename(
        columns={
            "lot_no": "质控品批号",
            "instrument": "仪器",
            "reagent": "试剂",
            "qc_material": "质控品",
            "concentration": "浓度",
            "created_at": "创建时间",
        }
    )


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
    st.session_state["pending_top_level_method"] = "单水平（LJ法）"
    st.session_state["pending_lj_project_id"] = int(project_id)
    st.session_state["pending_lj_batch_id"] = int(batch_id)
    st.session_state["pending_navigation_source"] = "instant_transfer"


def _close_instant_transfer_dialog() -> None:
    st.session_state["show_instant_transfer_dialog"] = False


def _render_instant_project_batch_management(
    manage_tab,
    projects_df: pd.DataFrame,
    selected_project_id: int | None,
    batches_df: pd.DataFrame,
    selected_batch_id: int | None,
) -> None:
    with manage_tab:
        top_left, top_right = st.columns([1, 1.4])

        with top_left:
            st.subheader("新建即时法项目")
            with st.form("create_instant_project_form", clear_on_submit=True):
                project_name = st.text_input("项目名称")
                input_value_type = st.radio(
                    "输入值类型",
                    options=INPUT_VALUE_TYPE_OPTIONS,
                    format_func=get_input_value_type_label,
                    horizontal=True,
                )
                project_submitted = st.form_submit_button("创建即时法项目", width="stretch")
                if project_submitted:
                    if not project_name.strip():
                        st.error(TEXT["fill_project"])
                    else:
                        try:
                            project_id = create_instant_project(
                                project_name.strip(),
                                input_value_type=input_value_type,
                            )
                        except ValueError as exc:
                            st.error(str(exc))
                        else:
                            st.session_state["instant_selected_project_id"] = project_id
                            st.session_state["instant_selected_batch_id"] = None
                            st.success(f"即时法项目“{project_name.strip()}”已创建。")
                            st.rerun()

            st.subheader("即时法项目列表与选择")
            if projects_df.empty:
                st.info("当前还没有即时法项目，请先创建项目并确定输入值类型。")
            else:
                project_labels, project_options = _build_instant_project_select_options(projects_df)
                sync_selector_state(
                    selector_key="instant_project_selector",
                    selected_id_key="instant_selected_project_id",
                    options_map=project_options,
                    placeholder=project_labels[0],
                )
                selected_project_label = st.selectbox(
                    "选择即时法项目",
                    options=project_labels,
                    key="instant_project_selector",
                )
                new_project_id = project_options[selected_project_label]
                if new_project_id != selected_project_id:
                    st.session_state["instant_selected_project_id"] = new_project_id
                    st.session_state["instant_selected_batch_id"] = None
                    st.session_state["instant_batch_selector"] = "请选择即时法批次"
                    st.rerun()

                project_table = _build_instant_project_table(projects_df)
                st.dataframe(project_table, width="stretch", hide_index=True)
                if selected_project_id is not None:
                    current_project = get_instant_project(selected_project_id)
                    has_existing_batches = count_instant_project_batches(selected_project_id) > 0
                    st.caption(
                        "当前项目："
                        f"{current_project['name']}｜输入值类型："
                        f"{get_input_value_type_label(current_project['input_value_type'])}。"
                    )
                    if has_existing_batches:
                        st.info("当前项目下已存在批次。为保持批次录入口径一致，本阶段不提供输入值类型修改。")

        with top_right:
            st.subheader("新建即时法批次")
            if selected_project_id is None:
                st.info("请先选择即时法项目。")
            else:
                current_project = get_instant_project(selected_project_id)
                current_input_value_type_label = get_input_value_type_label(current_project["input_value_type"])
                st.caption(
                    f"当前批次将归属于项目：{current_project['name']}｜输入值类型固定为 {current_input_value_type_label}。"
                )
                with st.form("create_instant_batch_form", clear_on_submit=True):
                    instrument = st.text_input("仪器")
                    reagent = st.text_input("试剂")
                    qc_material = st.text_input("质控品")
                    concentration = st.text_input("浓度")
                    lot_no = st.text_input("质控品批号")
                    create_submitted = st.form_submit_button("创建即时法批次", width="stretch")
                    if create_submitted:
                        required_fields = [instrument, reagent, qc_material, concentration, lot_no]
                        if any(not field.strip() for field in required_fields):
                            st.error(TEXT["fill_batch"])
                        else:
                            batch_id = create_instant_batch(
                                project_id=selected_project_id,
                                instrument=instrument.strip(),
                                reagent=reagent.strip(),
                                qc_material=qc_material.strip(),
                                concentration=concentration.strip(),
                                lot_no=lot_no.strip(),
                            )
                            st.session_state["instant_selected_batch_id"] = batch_id
                            st.success(f"即时法批次“{lot_no.strip()}”已创建。")
                            st.rerun()

            st.subheader("即时法批次列表与选择")
            if selected_project_id is None:
                st.info("请先选择即时法项目。")
            elif batches_df.empty:
                st.info("当前项目下还没有即时法批次，请先创建批次。")
            else:
                batch_labels, batch_options = _build_instant_batch_select_options(batches_df)
                sync_selector_state(
                    selector_key="instant_batch_selector",
                    selected_id_key="instant_selected_batch_id",
                    options_map=batch_options,
                    placeholder=batch_labels[0],
                )
                selected_batch_label = st.selectbox(
                    "选择即时法批次",
                    options=batch_labels,
                    key="instant_batch_selector",
                )
                new_batch_id = batch_options[selected_batch_label]
                if new_batch_id != selected_batch_id:
                    st.session_state["instant_selected_batch_id"] = new_batch_id
                    st.rerun()

                batch_table = _build_instant_batch_table(batches_df)
                st.dataframe(batch_table, width="stretch", hide_index=True)
                if selected_batch_id is not None:
                    current_batch = get_instant_batch(selected_batch_id)
                    st.caption(
                        "当前批次："
                        f"{_build_instant_batch_summary(current_batch)}｜项目：{current_batch['project_name']}｜"
                        f"输入值类型：{get_input_value_type_label(current_batch['input_value_type'])}。"
                    )


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
        "确认后将把当前即时法批次中的有效点转入一个新建的 LJ 批次，"
        "并冻结当前即时法批次为只读。"
    )

    if transfer_state.get("is_transferred"):
        st.success("该即时法批次已转入 LJ 法，不能再次执行确认转入。")
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
            ("CV%", format_optional_float(summary["cv"], digits=2, suffix="%")),
            ("目标 LJ 项目", target_project_summary),
            ("将新建的 LJ 批次", f"质控品批号：{target_batch_summary}"),
            ("目标批次 target_n", str(INSTANT_TRANSFER_READY_COUNT)),
        ]
    )
    st.markdown(
        "\n".join(
            [
                "- 前 20 个有效点将作为 LJ 建靶数据。",
                "- 第 21 个及之后的有效点将作为 LJ 正式期数据。",
                "- 转入后当前即时法批次将冻结为只读，不可继续录入、维护或再次转入。",
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

    st.markdown("**结果录入区**")
    if is_transferred:
        st.info("该批次已转入 LJ 法，当前录入区已冻结为只读。")
    else:
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

        st.caption("即时法当前按单水平工作流录入检测时间、检测人和单个结果值。")
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
                save_instant_result(
                    batch_id=selected_batch_id,
                    test_time=test_time.strftime("%Y-%m-%d %H:%M:%S"),
                    operator=cleaned_operator,
                    value=float(parsed_value),
                    log_value=log_value,
                )
                st.success(f"即时法结果已保存到批次“{batch['lot_no']}”。")
                st.session_state["instant_reset_entry_form"] = True
                st.rerun()

    st.divider()
    st.markdown("**累计统计区**")
    render_compact_stat_metrics(
        [
            ("总记录数", str(summary["total_count"])),
            ("有效点数", str(summary["effective_count"])),
            ("均值", format_optional_float(summary["mean"])),
            ("SD", format_optional_float(summary["sd"])),
            ("CV%", format_optional_float(summary["cv"], digits=2, suffix="%")),
        ]
    )
    st.caption("统计口径：均值、SD、CV 仅基于当前有效点计算；疑似离群点在未手工禁用前仍计入有效统计。")


def _render_instant_transfer_section(context: dict[str, object]) -> None:
    summary = context["summary"]
    transfer_state = context["transfer_state"]
    is_transferred = bool(transfer_state.get("is_transferred"))

    if is_transferred:
        st.success("该即时法批次已转入 LJ 法，后续请在对应 LJ 批次继续流程。")
        render_compact_stat_metrics(
            [
                ("当前状态", "已转入 LJ 法"),
                ("转入时间", _format_instant_datetime_text(transfer_state.get("transferred_at"))),
                ("转入有效点数", str(transfer_state.get("transferred_effective_count") or 0)),
                ("去向 LJ 项目", str(transfer_state.get("transferred_to_lj_project_name") or "-")),
                ("去向 LJ 批次", str(transfer_state.get("transferred_to_lj_batch_display") or "-")),
            ]
        )
        st.caption("转入后当前即时法批次已冻结为只读，目标 LJ 批次会保留“由即时法转入”的来源标识。")
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

    render_compact_stat_metrics(
        [
            ("当前有效点数", str(summary["effective_count"])),
            ("达到转入阈值", "是" if summary.get("transfer_ready") else "否"),
            ("待处理疑似离群点", str(summary.get("pending_outlier_review_count", 0))),
            ("目标阈值", str(INSTANT_TRANSFER_READY_COUNT)),
        ]
    )
    if summary.get("transfer_ready"):
        st.success("已达到 20 个有效点，可确认转入 LJ 法。")
    else:
        st.caption(f"达到 {INSTANT_TRANSFER_READY_COUNT} 个有效点后，才可执行“确认转入 LJ 法”。")

    st.caption(
        "转入规则：前 20 个有效点作为 LJ 建靶数据；第 21 个及之后的有效点作为 LJ 正式期数据；"
        "转入后当前即时法批次将冻结为只读。"
    )
    if st.button(
        "确认转入 LJ 法",
        key="open_instant_transfer_dialog",
        type="primary",
        disabled=not bool(transfer_state.get("eligible")),
        width="stretch",
    ):
        st.session_state["show_instant_transfer_dialog"] = True
    blockers = list(transfer_state.get("blockers", []))
    if blockers:
        st.info("当前暂不可转入：\n" + "\n".join(f"- {reason}" for reason in blockers))


def _resolve_instant_tone_key(status: str) -> str | None:
    if status == "疑似离群":
        return "warning"
    if status == "有效点":
        return "accept"
    if status == "继续累计":
        return "建靶期"
    return None


def _render_grubbs_method_explanation(summary: dict[str, object]) -> None:
    alpha_value = float(summary.get("grubbs_alpha", 0.05) or 0.05)
    grubbs_method_label = str(summary.get("grubbs_method_label", "双侧单异常值 Grubbs 检验"))
    grubbs_formula = str(summary.get("grubbs_formula", "G = max(|xi - x̄|) / s"))
    with st.expander("格拉布斯法说明", expanded=False):
        st.caption("当前即时法采用固定口径的基础离群提示，本轮只做说明与追溯展示，不做自动剔除。")
        st.markdown(
            "\n".join(
                [
                    f"- 当前采用：`{grubbs_method_label}`。",
                    "- 判定范围：基于当前有效点集合进行检查。",
                    "- 起判条件：有效点数 `n < 3` 时不判定，`n >= 3` 时开始判定。",
                    f"- 显著性水平：`alpha = {alpha_value:.0%}`。",
                    f"- 统计量：`{grubbs_formula}`，其中 `s` 使用样本标准差。",
                    "- 当前阶段：只做单次提示，不做迭代反复剔除。",
                    "- 判定结果：超过阈值时标记为“疑似离群”。",
                    "- 处理方式：系统不自动禁用，保留 / 禁用 / 恢复由用户手工处理。",
                ]
            )
        )


def _render_instant_chart_analysis_section(context: dict[str, object]) -> object:
    batch = context["batch"]
    analysis_df = context["analysis_df"]
    summary = context["summary"]
    latest_row = context["latest_row"]
    input_value_type_label = context["input_value_type_label"]
    figure = plot_instant_chart(
        analysis_df,
        summary,
        title=(
            f"即时法趋势图 - {_build_instant_batch_summary(batch)}"
        ),
        y_axis_label=input_value_type_label,
    )

    st.markdown("**即时法质控图**")
    st.pyplot(figure, clear_figure=False, width="stretch")

    st.divider()
    st.markdown("**最新判定区**")
    st.caption("即时法页面的判定输出统一收口在这里，左侧累计统计区不再重复展示判定结果。")
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
        "grubbs_statistic",
        "grubbs_threshold",
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
            "grubbs_statistic": "Grubbs G",
            "grubbs_threshold": "G临界值",
        }
    )


def _render_instant_records_section(context: dict[str, object]) -> None:
    analysis_df = context["analysis_df"]
    input_value_type_label = context["input_value_type_label"]
    with st.expander("当前记录表（点击折叠/展开）", expanded=False):
        st.caption("当前批次历史记录、疑似离群提示状态与手工处理状态都会保留在此。")
        display_df = _build_instant_display_dataframe(analysis_df, input_value_type_label)
        st.dataframe(display_df, width="stretch", hide_index=True)


def _build_maintenance_option_label(row: pd.Series, input_value_type_label: str) -> str:
    test_time = pd.Timestamp(row["test_time"]).strftime("%Y-%m-%d %H:%M")
    value_text = f"{float(row['value']):.4f}"
    return (
        f"记录 #{int(row['sequence'])} | {test_time} | {row['operator']} | "
        f"{input_value_type_label}={value_text} | {row['status']}"
    )


def _render_instant_maintenance_section(context: dict[str, object]) -> None:
    analysis_df = context["analysis_df"]
    summary = context["summary"]
    input_value_type_label = context["input_value_type_label"]
    transfer_state = context["transfer_state"]
    render_compact_stat_metrics(
        [
            ("总记录数", str(summary["total_count"])),
            ("有效建靶点数", str(summary["effective_count"])),
            ("已禁用点数", str(summary["disabled_count"])),
        ]
    )
    if analysis_df.empty:
        st.info("当前批次暂无记录可维护。")
        return
    if bool(transfer_state.get("is_transferred")):
        st.success("该即时法批次已转入 LJ 法，记录维护入口已冻结为只读。")
        st.caption("如需继续后续质控，请前往对应 LJ 批次；即时法源批次仅保留追溯信息。")
        return

    option_map = {
        _build_maintenance_option_label(row, input_value_type_label): int(row["id"])
        for _, row in analysis_df.iterrows()
    }
    selector_key = f"instant_maintenance_selector_{int(context['batch']['id'])}"
    if st.session_state.get(selector_key) not in option_map:
        st.session_state[selector_key] = next(iter(option_map.keys()))
    selected_label = st.selectbox(
        "选择需要维护的记录",
        options=list(option_map.keys()),
        key=selector_key,
    )
    selected_result_id = option_map[selected_label]
    selected_row = analysis_df.loc[analysis_df["id"] == selected_result_id].iloc[0]

    st.caption(str(selected_row.get("analysis_prompt", "") or "当前记录暂无额外分析提示。"))
    render_compact_stat_metrics(
        [
            ("当前状态", str(selected_row["status"])),
            ("手工处理", get_instant_manual_status_label(selected_row["manual_status"])),
            ("Grubbs G", format_optional_float(selected_row["grubbs_statistic"])),
            ("G临界值", format_optional_float(selected_row["grubbs_threshold"])),
        ]
    )
    st.caption("禁用后原始记录不会删除，只是不再参与即时法统计和图表默认展示。")

    if int(selected_row["is_effective"]) == 1:
        if (
            int(selected_row.get("is_outlier_suspect", 0) or 0) == 1
            and str(selected_row.get("manual_status", "") or "") == "pending_review"
        ):
            if st.button(
                "保留该记录",
                key=f"instant_keep_result_{selected_result_id}",
                width="stretch",
            ):
                keep_instant_result(selected_result_id)
                st.success("该疑似离群记录已标记为保留，可继续参与统计与转入判断。")
                st.rerun()
        if st.button(
            "禁用该记录",
            key=f"instant_disable_result_{selected_result_id}",
            width="stretch",
        ):
            disable_instant_result(selected_result_id)
            st.success("记录已禁用，当前批次统计与离群提示已重算。")
            st.rerun()
    else:
        if st.button(
            "恢复该记录",
            key=f"instant_restore_result_{selected_result_id}",
            width="stretch",
        ):
            restore_instant_result(selected_result_id)
            st.success("记录已恢复，当前批次统计与离群提示已重算。")
            st.rerun()

    st.button(
        "更多维护动作（下一阶段开放）",
        key=f"instant_more_maintenance_{selected_result_id}",
        disabled=True,
        width="stretch",
    )


def render_instant_page() -> None:
    st.subheader("即时法")
    st.caption(
        "即时法用于短期内难以快速积累满 20 个点的单水平项目；当前阶段支持单水平录入、"
        "格拉布斯法基础提示、有效点累计统计，以及满足条件后的“确认转入 LJ 法”。"
    )
    projects_df, selected_project_id, batches_df, selected_batch_id = prepare_instant_project_batch_context()
    manage_tab, work_tab = st.tabs([TEXT["manage"], TEXT["current_batch"]])
    _render_instant_project_batch_management(
        manage_tab,
        projects_df,
        selected_project_id,
        batches_df,
        selected_batch_id,
    )
    guard_work_tab_selection(work_tab, selected_project_id, selected_batch_id)

    context = build_instant_workbench_context(selected_batch_id)
    batch = context["batch"]
    summary = context["summary"]
    transfer_state = context["transfer_state"]
    input_value_type_label = context["input_value_type_label"]
    with work_tab:
        context_items = [
            ("项目名称", batch["project_name"]),
            ("质控品批号", batch["lot_no"]),
            ("输入值类型", input_value_type_label),
            ("当前有效点数", summary["effective_count"]),
            ("总记录数", summary["total_count"]),
            ("仪器", batch["instrument"]),
            ("试剂", batch["reagent"]),
            ("质控品", batch["qc_material"]),
            ("浓度", batch["concentration"]),
        ]
        context_badges = [
            f"质控批号 {batch['lot_no']}",
            input_value_type_label,
            f"有效点 {summary['effective_count']}/{INSTANT_TRANSFER_READY_COUNT}",
        ]
        context_caption = (
            f"当前项目：{batch['project_name']}。请确认输入值类型（{input_value_type_label}）后再录入结果。"
            "达到 3 个有效点后开始格拉布斯法提示，满足条件后可执行“确认转入 LJ 法”。"
        )
        if transfer_state.get("is_transferred"):
            context_items.extend(
                [
                    ("当前状态", "已转入 LJ 法"),
                    ("转入时间", _format_instant_datetime_text(transfer_state.get("transferred_at"))),
                    ("去向 LJ 项目", transfer_state.get("transferred_to_lj_project_name") or "-"),
                    ("去向 LJ 批次", transfer_state.get("transferred_to_lj_batch_display") or "-"),
                ]
            )
            context_badges.append("已转入 LJ 法")
            context_caption = (
                f"当前项目：{batch['project_name']}。该即时法批次已转入 LJ 法并冻结为只读，"
                "请前往对应 LJ 批次继续后续质控。"
            )
        elif transfer_state.get("eligible"):
            context_badges.append("可确认转入 LJ 法")

        render_workbench_context_bar(
            title="即时法当前批次",
            caption=context_caption,
            items=context_items,
            badges=context_badges,
        )

        if st.session_state.get("show_instant_transfer_dialog"):
            _render_instant_transfer_dialog(selected_batch_id)
        render_section_intro(
            title="当前动作区",
            caption="左侧聚焦单水平录入与累计统计，右侧保留唯一的最新判定区，减少即时法页面的重复判定提示。",
            badges=["过渡方法", f"有效点 {summary['effective_count']}/{INSTANT_TRANSFER_READY_COUNT}", input_value_type_label],
            tone="accent",
        )
        entry_col, chart_col = st.columns([0.98, 1.12], gap="large")
        with entry_col:
            with st.container():
                render_section_intro(
                    title="结果录入与累计统计",
                    caption="左侧只保留结果录入和累计统计，不再混入最新判定。",
                    tone="accent",
                )
                _render_instant_entry_and_summary_section(context, selected_batch_id)
        with chart_col:
            with st.container():
                render_section_intro(
                    title="图表与最新判定",
                    caption="右侧统一承载趋势图和最新判定，是当前页面唯一的判定出口。",
                    tone="accent",
                )
                _render_instant_chart_analysis_section(context)

        render_section_intro(
            title="历史与次要操作区",
            caption="转入 LJ、记录表、维护区和判定口径说明统一下沉到主区下方，保留追溯但降低页面噪音。",
            badges=["转入 LJ", "记录回顾", "维护与说明"],
            tone="muted",
        )
        with st.container(border=True):
            render_section_intro(
                title="转入 LJ 法",
                caption="明确显示当前是否可转入 LJ 法，以及转入后对应的去向项目和批次。",
                tone="muted",
            )
            _render_instant_transfer_section(context)

        lower_left, lower_right = st.columns([1.0, 1.0], gap="large")
        with lower_left:
            with st.container(border=True):
                render_section_intro(
                    title="当前记录表",
                    caption="查看历史记录、疑似离群状态和手工处理状态。",
                    tone="muted",
                )
                _render_instant_records_section(context)
        with lower_right:
            with st.container(border=True):
                render_section_intro(
                    title="记录维护区",
                    caption="本阶段支持即时法记录禁用与恢复，已转入 LJ 后统一冻结为只读。",
                    tone="muted",
                )
                _render_instant_maintenance_section(context)

        with st.container(border=True):
            render_section_intro(
                title="判定口径说明",
                caption="格拉布斯法说明保留在下部，避免干扰主录入和最新判定。",
                tone="muted",
            )
            _render_grubbs_method_explanation(summary)
