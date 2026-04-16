from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
from html import escape as html_escape
from io import BytesIO
import math
from string import ascii_uppercase
from textwrap import dedent
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

import pandas as pd
import streamlit as st

from import_review import (
    build_review_issues_dataframe,
    build_zscore_building_template_dataframe,
    review_zscore_building_import_csv,
    review_zscore_formal_import_csv,
)
from plotting import figure_to_png_bytes
from pages.management import (
    guard_work_tab_selection as guard_work_tab_selection_impl,
    prepare_zscore_project_batch_context as prepare_zscore_project_batch_context_impl,
    render_zscore_project_batch_management as render_zscore_project_batch_management_impl,
)
from services.value_type_service import (
    build_level_measurement_label,
    get_input_value_type_label,
    normalize_input_value_type,
    parse_project_input_value,
    validate_project_numeric_value,
)
from ui.common import (
    TEXT,
    ZSCORE_PHASE_VIEW_OPTIONS,
    ZSCORE_Y_AXIS_OPTIONS,
    build_safe_export_name,
    build_zscore_operator_options,
    format_error_type_label,
    format_optional_float,
    format_optional_input_value,
    format_rule_code,
    format_rule_description,
    format_zscore_status_label,
    format_zscore_template_display_name,
    get_saved_batch_cv_limit,
    parse_numeric_input,
    render_compact_stat_metrics,
    render_cv_limit_hint,
    render_html_block,
    render_import_review_summary,
    render_latest_analysis_card,
    render_standard_view_help,
)
from ui.dialogs import (
    bump_zscore_record_maintenance_dialog_nonce as bump_zscore_record_maintenance_dialog_nonce_impl,
    render_zscore_record_maintenance_dialog as render_zscore_record_maintenance_dialog_impl,
)
from services.outlier_service import (
    DEFAULT_GRUBBS_ALPHA,
    get_outlier_manual_status_label,
    get_outlier_status_label,
)
from zscore_logic import (
    PHASE_FORMAL_QC,
    PHASE_TARGET_BUILDING,
    build_zscore_plot_dataframe as build_zscore_plot_dataframe_logic,
    create_zscore_run,
    disable_zscore_level_result,
    determine_zscore_phase,
    format_level_id_display,
    get_phase_label,
    get_zscore_display_sequence,
    get_zscore_level_targets,
    get_zscore_runs,
    keep_zscore_level_result,
    resolve_zscore_batch_context,
    restore_zscore_level_result,
    should_enable_formal_rules,
    update_saved_zscore_run_manual_note,
    upsert_zscore_level_target,
)
from zscore_plotting import plot_zscore_overlay, plot_zscore_single_level

def get_latest_zscore_run_for_display(runs: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not runs:
        return None
    return max(
        runs,
        key=lambda run: (
            int(run.get("test_sequence") or run.get("run_id") or 0),
            pd.Timestamp(run.get("test_time")),
            int(run.get("run_id") or 0),
        ),
    )

def render_zscore_level_input_block(
    level_label: str,
    input_value_type: str,
    value_key: str,
    level_caption: str | None = None,
) -> None:
    st.markdown(f"**{level_label}**")
    if level_caption:
        st.caption(level_caption)
    st.text_input(
        build_level_measurement_label(level_label, input_value_type),
        key=value_key,
        placeholder="例如：123.4567",
    )

def format_zscore_level_display(level_id: str, level_label_map: dict[str, str]) -> tuple[str, str | None]:
    default_level_label = format_level_id_display(level_id)
    level_label = str(level_label_map.get(level_id, level_id) or level_id).strip() or level_id
    if level_label == level_id:
        return default_level_label, None
    return level_label, default_level_label

def build_zscore_current_level_results(
    template: dict[str, Any],
    input_value_type: str,
    level_label_map: dict[str, str],
) -> tuple[list[dict[str, Any]], list[str], bool]:
    key_map = {
        "Level 1": "zscore_level1_value",
        "Level 2": "zscore_level2_value",
        "Level 3": "zscore_level3_value",
    }
    level_results: list[dict[str, Any]] = []
    validation_errors: list[str] = []
    has_any_input = False
    for level_id in template["level_ids"]:
        value_key = key_map[level_id]
        raw_text = str(st.session_state.get(value_key, "") or "")
        display_label = format_zscore_level_display(level_id, level_label_map)[0]
        field_label = build_level_measurement_label(display_label, input_value_type)
        raw_value, log_value, value_error = parse_project_input_value(
            raw_text,
            input_value_type,
            field_label=field_label,
        )
        has_any_input = has_any_input or bool(raw_text.strip())
        if raw_text.strip() and raw_value is None:
            validation_errors.append(value_error or f"{field_label}必须为有效数字。")

        target_info = template["default_targets"][level_id]
        level_results.append(
            {
                "level_id": level_id,
                "raw_value": raw_value,
                "log_value": log_value,
                "target_mean": target_info["target_mean"],
                "target_sd": target_info["target_sd"],
            }
        )
    return level_results, validation_errors, has_any_input

def build_zscore_plot_dataframe(
    saved_runs: list[dict[str, Any]],
    draft_run: dict[str, Any] | None = None,
    display_phase: str | None = None,
) -> pd.DataFrame:
    return build_zscore_plot_dataframe_logic(
        saved_runs=saved_runs,
        draft_run=draft_run,
        display_phase=display_phase,
    )

def render_zscore_latest_analysis_panel(
    latest_run: dict[str, Any] | None,
    overall_phase: str,
    formal_rules_enabled: bool,
) -> None:
    st.markdown("**最新结果分析**")
    if latest_run is None:
        if overall_phase == PHASE_FORMAL_QC:
            st.info("建靶已完成，正式规则已启用。请录入首条正式质控检测记录。")
        else:
            st.info("当前处于建靶期，请先录入检测记录，用于累计实验室靶值并观察多水平趋势。")
        return

    is_building_phase = (not formal_rules_enabled) or latest_run.get("phase") != PHASE_FORMAL_QC
    status = PHASE_TARGET_BUILDING if is_building_phase else str(latest_run.get("run_status", "pending"))
    source_text = (
        "当前输入预览"
        if latest_run.get("is_preview")
        else f"最近已保存检测序号 #{get_zscore_display_sequence(latest_run)}"
    )
    phase_label = str(latest_run.get("phase_label") or get_phase_label(latest_run.get("phase", overall_phase)))
    badge_text = phase_label if is_building_phase else format_zscore_status_label(status)
    trigger_rule_text = (
        "建靶期不启用正式规则"
        if is_building_phase
        else format_zscore_rule_hits(latest_run.get("rule_hits_run", []))
    )
    summary_text = (
        "本次结果纳入建靶观察，不作为正式质控结论。"
        if is_building_phase
        else str(latest_run.get("analysis_prompt", "") or "暂无分析提示。").splitlines()[0].strip()
    )
    render_latest_analysis_card(
        status_label=badge_text,
        summary_text=summary_text,
        meta_items=[
            ("当前阶段", phase_label),
            ("触发规则", trigger_rule_text),
            ("误差类型", format_error_type_label(latest_run.get("error_type_hint", "unknown"))),
        ],
        source_text=source_text,
        tone_key=status,
    )

def render_zscore_abnormal_note_quick_entry(latest_run: dict[str, Any] | None) -> None:
    if latest_run is None or latest_run.get("is_preview"):
        return
    if str(latest_run.get("phase")) != PHASE_FORMAL_QC:
        return
    if str(latest_run.get("run_status", "") or "") not in {"warning", "reject"}:
        return

    run_id = int(latest_run["run_id"])
    current_note = str(latest_run.get("manual_note", "") or "")
    st.caption("当前异常记录可直接补充备注，并写回同一条检测记录。")
    with st.form(f"zscore_abnormal_note_form_{run_id}"):
        manual_note = st.text_area(
            "\u5f02\u5e38\u5907\u6ce8\uff08\u53ef\u9009\uff09",
            value=current_note,
            height=88,
            key=f"zscore_abnormal_note_{run_id}",
        )
        submitted = st.form_submit_button("\u4fdd\u5b58\u5f53\u524d\u5f02\u5e38\u5907\u6ce8", width="stretch")

        if submitted:
            update_saved_zscore_run_manual_note(run_id, str(manual_note or "").strip())
            st.session_state["zscore_notice"] = "Z-score \u624b\u52a8\u5907\u6ce8\u5df2\u4fdd\u5b58\u3002"
            st.rerun()

def render_zscore_rules_config_expander(
    template: dict[str, Any],
    overall_phase: str,
    formal_rules_enabled: bool,
) -> None:
    with st.expander("规则说明与判读口径（点击展开）", expanded=False):
        template_display_name = format_zscore_template_display_name(template)
        st.caption(template["note"])
        st.markdown(f"- 当前规则组合：`{template_display_name}`")
        st.markdown(f"- 当前阶段：`{get_phase_label(overall_phase)}`")
        st.markdown(f"- 正式规则已启用：`{'是' if formal_rules_enabled else '否'}`")
        if not formal_rules_enabled:
            st.info("当前仍处于建靶期，以下规则说明仅供进入正式质控期后的判读参考。")
        for rule_id in template["rule_ids"]:
            st.markdown(f"- `{format_rule_code(rule_id)}`：{format_rule_description(rule_id)}")

def render_zscore_profile_stat_line(label: str, mean_value: Any, sd_value: Any, cv_value: Any) -> None:
    st.caption(
        f"{label}："
        f"均值 {format_optional_float(mean_value)} | "
        f"SD {format_optional_float(sd_value)} | "
        f"CV% {format_optional_float(cv_value, digits=2, suffix='%')}"
    )

def render_zscore_vendor_reference_editor_body(
    batch_id: int,
    template_id: str,
    level_id: str,
    level_display_name: str,
    required_n: int,
    profile: dict[str, Any],
) -> None:
    mean_default = format_optional_input_value(profile.get("vendor_reference_mean"))
    sd_default = format_optional_input_value(profile.get("vendor_reference_sd"))
    source_default = str(profile.get("vendor_reference_source_note", "") or "")
    state_prefix = f"zscore_vendor_{batch_id}_{template_id}_{level_id.replace(' ', '_')}"
    form_key = f"{state_prefix}_form"
    with st.form(form_key):
        mean_text = st.text_input("参考均值", value=mean_default, key=f"{state_prefix}_mean")
        sd_text = st.text_input("参考标准差", value=sd_default, key=f"{state_prefix}_sd")
        source_note = st.text_input("来源备注（可选）", value=source_default, key=f"{state_prefix}_source")
        vendor_mean, _, _ = parse_numeric_input(mean_text)
        vendor_sd, _, _ = parse_numeric_input(sd_text)
        vendor_cv = None
        if vendor_mean is not None and vendor_sd is not None and not math.isclose(vendor_mean, 0.0, abs_tol=1e-12):
            vendor_cv = vendor_sd / vendor_mean * 100
        st.caption(f"参考 CV%：{format_optional_float(vendor_cv, digits=2, suffix='%')}")
        submitted = st.form_submit_button("保存厂家参考值", width="stretch")

        if submitted:
            validation_errors: list[str] = []
            if mean_text.strip() and vendor_mean is None:
                validation_errors.append("厂家参考均值必须为有效正数。")
            if sd_text.strip() and vendor_sd is None:
                validation_errors.append("厂家参考标准差必须为有效正数。")

            if validation_errors:
                st.error("\n".join(validation_errors))
            else:
                upsert_zscore_level_target(
                    batch_id=batch_id,
                    level_id=level_id,
                    vendor_reference_mean=vendor_mean,
                    vendor_reference_sd=vendor_sd,
                    vendor_reference_source_note=source_note.strip() or None,
                    required_n=int(required_n),
                )
                st.session_state["zscore_notice"] = f"{level_display_name}的厂家参考值已保存。"
                st.rerun()

def render_zscore_vendor_reference_editor(
    batch_id: int,
    template_id: str,
    level_id: str,
    level_display_name: str,
    required_n: int,
    profile: dict[str, Any],
) -> None:
    with st.expander("厂家参考值（仅作参考）", expanded=False):
        render_zscore_vendor_reference_editor_body(
            batch_id=batch_id,
            template_id=template_id,
            level_id=level_id,
            level_display_name=level_display_name,
            required_n=required_n,
            profile=profile,
        )

def format_zscore_rule_hits(rule_hits: list[dict[str, Any]]) -> str:
    if not rule_hits:
        return "无"
    ordered_rule_ids = list(dict.fromkeys(hit["rule_id"] for hit in rule_hits))
    return "、".join(format_rule_code(rule_id) for rule_id in ordered_rule_ids)

def build_zscore_chart_control_title(
    template: dict[str, Any],
    phase_scope: str,
    view_mode: str,
    selected_level: str,
    level_label_map: dict[str, str],
    y_axis_mode: str,
    standard_sd_limit: float,
) -> str:
    scope_text = format_zscore_level_display(selected_level, level_label_map)[0] if view_mode == "单水平视图" else "全部水平"
    phase_scope_label = ZSCORE_PHASE_VIEW_OPTIONS.get(phase_scope, "全图")
    template_display_name = format_zscore_template_display_name(template)
    if y_axis_mode == "标准视图":
        range_text = f"±{standard_sd_limit:g}SD"
    else:
        range_text = "全范围"
    return f"图表控制（点击展开）｜{template_display_name}｜{phase_scope_label}｜{view_mode}｜{scope_text}｜{range_text}"

def _remember_zscore_chart_control_state(batch_id: int) -> None:
    st.session_state["zscore_chart_state_batch_id"] = int(batch_id)
    for session_key in [
        "zscore_phase_scope",
        "zscore_view_mode",
        "zscore_selected_level",
        "zscore_y_axis_mode",
        "zscore_standard_sd_limit",
    ]:
        if session_key in st.session_state:
            st.session_state[f"{session_key}__saved"] = st.session_state[session_key]


def _sync_zscore_chart_control_widget_state(
    template: dict[str, Any],
    default_phase_scope: str,
    *,
    force: bool = False,
) -> None:
    phase_scope_options = list(ZSCORE_PHASE_VIEW_OPTIONS.keys())
    phase_scope_value = st.session_state.get("zscore_phase_scope", default_phase_scope)
    if phase_scope_value not in phase_scope_options:
        phase_scope_value = default_phase_scope
        st.session_state["zscore_phase_scope"] = phase_scope_value
    if force or st.session_state.get("zscore_phase_scope_widget") not in phase_scope_options:
        st.session_state["zscore_phase_scope_widget"] = phase_scope_value

    valid_view_modes = {"单水平视图", "合并视图"}
    view_mode_value = st.session_state.get("zscore_view_mode", "单水平视图")
    if view_mode_value not in valid_view_modes:
        view_mode_value = "单水平视图"
        st.session_state["zscore_view_mode"] = view_mode_value
    view_mode_widget_value = st.session_state.get("zscore_view_mode_widget")
    view_mode_changed = bool(st.session_state.pop("zscore_view_mode_changed", False))
    should_restore_overlay_view = (
        not view_mode_changed
        and view_mode_value == "合并视图"
        and view_mode_widget_value == "单水平视图"
        and st.session_state.get("zscore_selected_level_widget") not in template["level_ids"]
    )
    if force or view_mode_widget_value not in valid_view_modes or should_restore_overlay_view:
        st.session_state["zscore_view_mode_widget"] = view_mode_value

    y_axis_mode_value = st.session_state.get("zscore_y_axis_mode", ZSCORE_Y_AXIS_OPTIONS[0])
    if y_axis_mode_value not in ZSCORE_Y_AXIS_OPTIONS:
        y_axis_mode_value = ZSCORE_Y_AXIS_OPTIONS[0]
        st.session_state["zscore_y_axis_mode"] = y_axis_mode_value
    if force or st.session_state.get("zscore_y_axis_mode_widget") not in ZSCORE_Y_AXIS_OPTIONS:
        st.session_state["zscore_y_axis_mode_widget"] = y_axis_mode_value

    try:
        standard_sd_limit_value = float(st.session_state.get("zscore_standard_sd_limit", 4.0) or 4.0)
    except (TypeError, ValueError):
        standard_sd_limit_value = 4.0
    if standard_sd_limit_value <= 0:
        standard_sd_limit_value = 4.0
        st.session_state["zscore_standard_sd_limit"] = standard_sd_limit_value
    widget_sd_limit = st.session_state.get("zscore_standard_sd_limit_widget")
    if force or widget_sd_limit is None or float(widget_sd_limit or 0.0) <= 0:
        st.session_state["zscore_standard_sd_limit_widget"] = standard_sd_limit_value

    selected_level_value = st.session_state.get("zscore_selected_level", template["level_ids"][0])
    if selected_level_value not in template["level_ids"]:
        selected_level_value = template["level_ids"][0]
        st.session_state["zscore_selected_level"] = selected_level_value
    if force or st.session_state.get("zscore_selected_level_widget") not in template["level_ids"]:
        st.session_state["zscore_selected_level_widget"] = selected_level_value


def _capture_zscore_chart_control_state_from_widgets(template: dict[str, Any]) -> None:
    phase_scope_widget_value = st.session_state.get("zscore_phase_scope_widget")
    if phase_scope_widget_value in ZSCORE_PHASE_VIEW_OPTIONS:
        st.session_state["zscore_phase_scope"] = phase_scope_widget_value

    view_mode_widget_value = st.session_state.get("zscore_view_mode_widget")
    if view_mode_widget_value in {"单水平视图", "合并视图"}:
        st.session_state["zscore_view_mode"] = view_mode_widget_value

    selected_level_widget_value = st.session_state.get("zscore_selected_level_widget")
    if selected_level_widget_value in template["level_ids"]:
        st.session_state["zscore_selected_level"] = selected_level_widget_value

    y_axis_mode_widget_value = st.session_state.get("zscore_y_axis_mode_widget")
    if y_axis_mode_widget_value in ZSCORE_Y_AXIS_OPTIONS:
        st.session_state["zscore_y_axis_mode"] = y_axis_mode_widget_value

    try:
        standard_sd_limit_widget_value = float(st.session_state.get("zscore_standard_sd_limit_widget", 0.0) or 0.0)
    except (TypeError, ValueError):
        standard_sd_limit_widget_value = 0.0
    if standard_sd_limit_widget_value > 0:
        st.session_state["zscore_standard_sd_limit"] = standard_sd_limit_widget_value


def _mark_zscore_view_mode_changed() -> None:
    st.session_state["zscore_view_mode_changed"] = True


def _snapshot_zscore_chart_control_state_for_restore(
    batch_id: int,
    template: dict[str, Any],
    default_phase_scope: str,
    phase_scope: str,
    view_mode: str,
    selected_level: str,
    y_axis_mode: str,
    standard_sd_limit: float,
) -> None:
    st.session_state["zscore_chart_restore_batch_id"] = int(batch_id)

    phase_scope_value = phase_scope
    if phase_scope_value not in ZSCORE_PHASE_VIEW_OPTIONS:
        phase_scope_value = default_phase_scope
    st.session_state["zscore_phase_scope__restore"] = phase_scope_value

    view_mode_value = view_mode
    if view_mode_value not in {"单水平视图", "合并视图"}:
        view_mode_value = "单水平视图"
    st.session_state["zscore_view_mode__restore"] = view_mode_value

    selected_level_value = selected_level
    if selected_level_value not in template["level_ids"]:
        selected_level_value = template["level_ids"][0]
    st.session_state["zscore_selected_level__restore"] = selected_level_value

    y_axis_mode_value = y_axis_mode
    if y_axis_mode_value not in ZSCORE_Y_AXIS_OPTIONS:
        y_axis_mode_value = ZSCORE_Y_AXIS_OPTIONS[0]
    st.session_state["zscore_y_axis_mode__restore"] = y_axis_mode_value

    try:
        standard_sd_limit_value = float(standard_sd_limit or 4.0)
    except (TypeError, ValueError):
        standard_sd_limit_value = 4.0
    if standard_sd_limit_value <= 0:
        standard_sd_limit_value = 4.0
    st.session_state["zscore_standard_sd_limit__restore"] = standard_sd_limit_value


def _restore_zscore_chart_control_state_if_requested(
    batch_id: int,
    template: dict[str, Any],
    default_phase_scope: str,
) -> bool:
    valid_view_modes = {"单水平视图", "合并视图"}
    restore_requested = bool(st.session_state.get("zscore_restore_chart_controls", False))
    st.session_state["zscore_restore_chart_controls"] = False
    if not restore_requested:
        return False

    saved_batch_id = st.session_state.pop("zscore_chart_restore_batch_id", None)
    if saved_batch_id is None:
        saved_batch_id = st.session_state.get("zscore_chart_state_batch_id")
    if saved_batch_id is None or int(saved_batch_id) != int(batch_id):
        return False

    restored_phase_scope = st.session_state.pop("zscore_phase_scope__restore", None)
    if restored_phase_scope is None:
        restored_phase_scope = st.session_state.get("zscore_phase_scope__saved")
    if restored_phase_scope not in ZSCORE_PHASE_VIEW_OPTIONS:
        restored_phase_scope = default_phase_scope
    st.session_state["zscore_phase_scope"] = restored_phase_scope

    restored_view_mode = st.session_state.pop("zscore_view_mode__restore", None)
    if restored_view_mode is None:
        restored_view_mode = st.session_state.get("zscore_view_mode__saved")
    if restored_view_mode not in valid_view_modes:
        restored_view_mode = "单水平视图"
    st.session_state["zscore_view_mode"] = restored_view_mode

    restored_selected_level = st.session_state.pop("zscore_selected_level__restore", None)
    if restored_selected_level is None:
        restored_selected_level = st.session_state.get("zscore_selected_level__saved")
    if restored_selected_level not in template["level_ids"]:
        restored_selected_level = template["level_ids"][0]
    st.session_state["zscore_selected_level"] = restored_selected_level

    restored_y_axis_mode = st.session_state.pop("zscore_y_axis_mode__restore", None)
    if restored_y_axis_mode is None:
        restored_y_axis_mode = st.session_state.get("zscore_y_axis_mode__saved")
    if restored_y_axis_mode not in ZSCORE_Y_AXIS_OPTIONS:
        restored_y_axis_mode = ZSCORE_Y_AXIS_OPTIONS[0]
    st.session_state["zscore_y_axis_mode"] = restored_y_axis_mode

    try:
        restored_sd_limit = float(st.session_state.pop("zscore_standard_sd_limit__restore", None) or 0.0)
    except (TypeError, ValueError):
        restored_sd_limit = 0.0
    if restored_sd_limit <= 0:
        try:
            restored_sd_limit = float(st.session_state.get("zscore_standard_sd_limit__saved", 4.0) or 4.0)
        except (TypeError, ValueError):
            restored_sd_limit = 4.0
    if restored_sd_limit <= 0:
        restored_sd_limit = 4.0
    st.session_state["zscore_standard_sd_limit"] = restored_sd_limit
    st.session_state["zscore_chart_controls_force_sync"] = True
    return True


def _apply_saved_zscore_chart_control_state(
    template: dict[str, Any],
    default_phase_scope: str,
) -> None:
    phase_scope_value = st.session_state.get("zscore_phase_scope__saved", st.session_state.get("zscore_phase_scope"))
    if phase_scope_value not in ZSCORE_PHASE_VIEW_OPTIONS:
        phase_scope_value = default_phase_scope
    st.session_state["zscore_phase_scope"] = phase_scope_value

    view_mode_value = st.session_state.get("zscore_view_mode__saved", st.session_state.get("zscore_view_mode"))
    if view_mode_value not in {"单水平视图", "合并视图"}:
        view_mode_value = "单水平视图"
    st.session_state["zscore_view_mode"] = view_mode_value

    selected_level_value = st.session_state.get(
        "zscore_selected_level__saved",
        st.session_state.get("zscore_selected_level"),
    )
    if selected_level_value not in template["level_ids"]:
        selected_level_value = template["level_ids"][0]
    st.session_state["zscore_selected_level"] = selected_level_value

    y_axis_mode_value = st.session_state.get("zscore_y_axis_mode__saved", st.session_state.get("zscore_y_axis_mode"))
    if y_axis_mode_value not in ZSCORE_Y_AXIS_OPTIONS:
        y_axis_mode_value = ZSCORE_Y_AXIS_OPTIONS[0]
    st.session_state["zscore_y_axis_mode"] = y_axis_mode_value

    try:
        standard_sd_limit_value = float(
            st.session_state.get("zscore_standard_sd_limit__saved", st.session_state.get("zscore_standard_sd_limit", 4.0))
            or 4.0
        )
    except (TypeError, ValueError):
        standard_sd_limit_value = 4.0
    if standard_sd_limit_value <= 0:
        standard_sd_limit_value = 4.0
    st.session_state["zscore_standard_sd_limit"] = standard_sd_limit_value


def render_zscore_chart_controls(
    batch_id: int,
    template: dict[str, Any],
    default_phase_scope: str,
    level_label_map: dict[str, str],
) -> tuple[str, str, str, str, float]:
    phase_scope_options = list(ZSCORE_PHASE_VIEW_OPTIONS.keys())
    if st.session_state.get("zscore_phase_scope") not in phase_scope_options:
        st.session_state["zscore_phase_scope"] = default_phase_scope
    if st.session_state.get("zscore_view_mode") not in {"单水平视图", "合并视图"}:
        st.session_state["zscore_view_mode"] = "单水平视图"
    if st.session_state.get("zscore_y_axis_mode") not in ZSCORE_Y_AXIS_OPTIONS:
        st.session_state["zscore_y_axis_mode"] = ZSCORE_Y_AXIS_OPTIONS[0]
    y_axis_mode = str(st.session_state["zscore_y_axis_mode"])
    try:
        standard_sd_limit = float(st.session_state.get("zscore_standard_sd_limit", 4.0) or 4.0)
    except (TypeError, ValueError):
        standard_sd_limit = 4.0
    if standard_sd_limit <= 0:
        standard_sd_limit = 4.0
    st.session_state["zscore_standard_sd_limit"] = standard_sd_limit
    if st.session_state.get("zscore_selected_level") not in template["level_ids"]:
        st.session_state["zscore_selected_level"] = template["level_ids"][0]
    selected_level = str(st.session_state["zscore_selected_level"])

    force_widget_sync = bool(st.session_state.pop("zscore_chart_controls_force_sync", False))
    _sync_zscore_chart_control_widget_state(
        template,
        default_phase_scope,
        force=force_widget_sync,
    )

    with st.container(border=True):
        st.markdown("**图表控制**")
        st.caption("常用选项直接显示，更多设置可在展开后调整。")
        control_col1, control_col2 = st.columns([1.05, 1.15], gap="large")
        phase_scope = control_col1.radio(
            "数据范围",
            options=phase_scope_options,
            format_func=lambda option: ZSCORE_PHASE_VIEW_OPTIONS[option],
            horizontal=True,
            key="zscore_phase_scope_widget",
        )
        view_mode = control_col2.radio(
            "视图模式",
            options=["单水平视图", "合并视图"],
            horizontal=True,
            key="zscore_view_mode_widget",
            on_change=_mark_zscore_view_mode_changed,
        )
        if view_mode == "单水平视图":
            selected_level = st.radio(
                "当前水平",
                options=template["level_ids"],
                horizontal=True,
                key="zscore_selected_level_widget",
                format_func=lambda option: format_zscore_level_display(option, level_label_map)[0],
            )
        else:
            selected_level = str(st.session_state.get("zscore_selected_level", template["level_ids"][0]))

        with st.expander(
            build_zscore_chart_control_title(
                template,
                phase_scope,
                view_mode,
                selected_level,
                level_label_map,
                y_axis_mode,
                standard_sd_limit,
            ),
            expanded=False,
        ):
            y_axis_mode = st.radio(
                "Y 轴范围",
                options=ZSCORE_Y_AXIS_OPTIONS,
                horizontal=True,
                key="zscore_y_axis_mode_widget",
            )
            if y_axis_mode == "标准视图":
                standard_sd_limit = float(
                    st.slider(
                        "标准视图范围（均值 ± nSD）",
                        min_value=2.0,
                        max_value=6.0,
                        step=1.0,
                        key="zscore_standard_sd_limit_widget",
                    )
                )
                render_standard_view_help(standard_sd_limit)
            else:
                standard_sd_limit = float(st.session_state.get("zscore_standard_sd_limit", standard_sd_limit))
    st.session_state["zscore_phase_scope"] = phase_scope
    st.session_state["zscore_view_mode"] = view_mode
    st.session_state["zscore_selected_level"] = selected_level
    st.session_state["zscore_y_axis_mode"] = y_axis_mode
    st.session_state["zscore_standard_sd_limit"] = standard_sd_limit
    _remember_zscore_chart_control_state(batch_id)
    return phase_scope, view_mode, selected_level, y_axis_mode, standard_sd_limit

def sync_zscore_workbench_state(
    batch_id: int,
    template: dict[str, Any],
    default_phase_scope: str,
) -> None:
    restored_chart_state = _restore_zscore_chart_control_state_if_requested(
        batch_id,
        template,
        default_phase_scope,
    )
    should_force_widget_sync = restored_chart_state
    if st.session_state.get("zscore_workbench_batch_id") != batch_id:
        st.session_state["zscore_workbench_batch_id"] = batch_id
        if not restored_chart_state:
            st.session_state["zscore_phase_scope"] = default_phase_scope
            st.session_state["zscore_selected_level"] = template["level_ids"][0]
            should_force_widget_sync = True
        if st.session_state.get("zscore_view_mode") not in {"单水平视图", "合并视图"}:
            st.session_state["zscore_view_mode"] = "单水平视图"
            should_force_widget_sync = True
        if st.session_state.get("zscore_y_axis_mode") not in ZSCORE_Y_AXIS_OPTIONS:
            st.session_state["zscore_y_axis_mode"] = ZSCORE_Y_AXIS_OPTIONS[0]
            should_force_widget_sync = True
        if float(st.session_state.get("zscore_standard_sd_limit", 4.0) or 4.0) <= 0:
            st.session_state["zscore_standard_sd_limit"] = 4.0
            should_force_widget_sync = True
        if should_force_widget_sync:
            st.session_state["zscore_chart_controls_force_sync"] = True
        return

    if st.session_state.get("zscore_phase_scope") not in ZSCORE_PHASE_VIEW_OPTIONS:
        st.session_state["zscore_phase_scope"] = default_phase_scope
        should_force_widget_sync = True
    if st.session_state.get("zscore_selected_level") not in template["level_ids"]:
        st.session_state["zscore_selected_level"] = template["level_ids"][0]
        should_force_widget_sync = True
    if st.session_state.get("zscore_view_mode") not in {"单水平视图", "合并视图"}:
        st.session_state["zscore_view_mode"] = "单水平视图"
        should_force_widget_sync = True
    if st.session_state.get("zscore_y_axis_mode") not in ZSCORE_Y_AXIS_OPTIONS:
        st.session_state["zscore_y_axis_mode"] = ZSCORE_Y_AXIS_OPTIONS[0]
        should_force_widget_sync = True
    if float(st.session_state.get("zscore_standard_sd_limit", 4.0) or 4.0) <= 0:
        st.session_state["zscore_standard_sd_limit"] = 4.0
        should_force_widget_sync = True
    if should_force_widget_sync:
        st.session_state["zscore_chart_controls_force_sync"] = True

def _excel_column_name(index: int) -> str:
    result = ""
    current = index
    while current > 0:
        current, remainder = divmod(current - 1, 26)
        result = ascii_uppercase[remainder] + result
    return result

def dataframe_to_xlsx_bytes(dataframe: pd.DataFrame) -> bytes:
    output = BytesIO()
    rows = [list(dataframe.columns)] + dataframe.fillna("").astype(object).values.tolist()
    shared_strings: list[str] = []
    shared_lookup: dict[str, int] = {}
    worksheet_rows: list[str] = []

    for row_index, row_values in enumerate(rows, start=1):
        cells: list[str] = []
        for column_index, value in enumerate(row_values, start=1):
            cell_reference = f"{_excel_column_name(column_index)}{row_index}"
            if isinstance(value, bool):
                cell_value = "1" if value else "0"
                cells.append(f'<c r="{cell_reference}" t="b"><v>{cell_value}</v></c>')
                continue

            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if math.isfinite(float(value)):
                    cells.append(f'<c r="{cell_reference}"><v>{value}</v></c>')
                    continue

            text = str(value)
            if text not in shared_lookup:
                shared_lookup[text] = len(shared_strings)
                shared_strings.append(text)
            shared_index = shared_lookup[text]
            cells.append(f'<c r="{cell_reference}" t="s"><v>{shared_index}</v></c>')

        worksheet_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')

    shared_xml_items = "".join(
        f"<si><t>{html_escape(text)}</t></si>" for text in shared_strings
    )
    worksheet_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
        <sheetData>{''.join(worksheet_rows)}</sheetData>
    </worksheet>
    """
    shared_strings_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="{len(shared_strings)}" uniqueCount="{len(shared_strings)}">
        {shared_xml_items}
    </sst>
    """
    workbook_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
      xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
      <sheets>
        <sheet name="质控数据" sheetId="1" r:id="rId1"/>
      </sheets>
    </workbook>
    """
    workbook_rels_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
      <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
      <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
      <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>
    </Relationships>
    """
    root_rels_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
      <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
      <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
      <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
    </Relationships>
    """
    content_types_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
      <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
      <Default Extension="xml" ContentType="application/xml"/>
      <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
      <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
      <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
      <Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>
      <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
      <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
    </Types>
    """
    styles_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
      <fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>
      <fills count="1"><fill><patternFill patternType="none"/></fill></fills>
      <borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
      <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
      <cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>
      <cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
    </styleSheet>
    """
    core_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
      xmlns:dc="http://purl.org/dc/elements/1.1/"
      xmlns:dcterms="http://purl.org/dc/terms/"
      xmlns:dcmitype="http://purl.org/dc/dcmitype/"
      xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
      <dc:creator>LJQCApp</dc:creator>
      <cp:lastModifiedBy>LJQCApp</cp:lastModifiedBy>
      <dcterms:created xsi:type="dcterms:W3CDTF">2026-03-24T00:00:00Z</dcterms:created>
      <dcterms:modified xsi:type="dcterms:W3CDTF">2026-03-24T00:00:00Z</dcterms:modified>
    </cp:coreProperties>
    """
    app_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
      xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
      <Application>LJQCApp</Application>
    </Properties>
    """

    with ZipFile(output, "w", ZIP_DEFLATED) as workbook:
        workbook.writestr("[Content_Types].xml", content_types_xml)
        workbook.writestr("_rels/.rels", root_rels_xml)
        workbook.writestr("docProps/core.xml", core_xml)
        workbook.writestr("docProps/app.xml", app_xml)
        workbook.writestr("xl/workbook.xml", workbook_xml)
        workbook.writestr("xl/_rels/workbook.xml.rels", workbook_rels_xml)
        workbook.writestr("xl/styles.xml", styles_xml)
        workbook.writestr("xl/sharedStrings.xml", shared_strings_xml)
        workbook.writestr("xl/worksheets/sheet1.xml", worksheet_xml)

    return output.getvalue()

def build_zscore_monthly_export_plot_dataframe(
    plot_df: pd.DataFrame,
    start_date,
    end_date,
) -> pd.DataFrame:
    if plot_df.empty:
        return plot_df.copy()
    if "phase" not in plot_df.columns or "test_time" not in plot_df.columns:
        return pd.DataFrame(columns=plot_df.columns)

    formal_df = plot_df[plot_df["phase"] == PHASE_FORMAL_QC].copy()
    if formal_df.empty:
        return formal_df

    start_timestamp = pd.Timestamp(start_date)
    end_timestamp = pd.Timestamp(end_date) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
    filtered_df = formal_df[
        (formal_df["test_time"] >= start_timestamp) & (formal_df["test_time"] <= end_timestamp)
    ].copy()
    if filtered_df.empty:
        return filtered_df

    run_axis_df = (
        filtered_df[["run_id", "run_index", "test_sequence", "test_time"]]
        .drop_duplicates()
        .sort_values(["test_time", "run_index", "run_id"])
        .reset_index(drop=True)
    )
    run_axis_df["monthly_run_index"] = run_axis_df.index + 1
    run_index_map = run_axis_df.set_index("run_id")["monthly_run_index"].to_dict()
    filtered_df["run_index"] = filtered_df["run_id"].map(run_index_map).astype(int)
    filtered_df["test_sequence"] = filtered_df["run_index"]
    filtered_df["plot_phase"] = PHASE_FORMAL_QC
    return filtered_df.sort_values(["run_index", "level_id"]).reset_index(drop=True)

def _format_zscore_export_datetime(value: Any) -> str:
    timestamp = pd.to_datetime(value, errors="coerce")
    if pd.isna(timestamp):
        return ""
    return pd.Timestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")

def _format_zscore_export_numeric(value: Any, digits: int = 4) -> float | None:
    if value is None or pd.isna(value):
        return None
    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric_value):
        return None
    return round(numeric_value, digits)

def _build_zscore_export_level_prefix(level_id: str) -> str:
    normalized_level_id = str(level_id or "").strip()
    if normalized_level_id.startswith("Level "):
        suffix = normalized_level_id.removeprefix("Level ").strip()
        return f"Level {suffix}" if suffix else "Level"
    if normalized_level_id.startswith("Level"):
        suffix = normalized_level_id.removeprefix("Level").strip()
        return f"Level {suffix}" if suffix else "Level"
    return normalized_level_id or "Level"

def build_zscore_phase_export_dataframe(
    saved_runs: list[dict[str, Any]],
    required_level_ids: list[str],
    phase_scope: str,
    input_value_type: str,
) -> pd.DataFrame:
    if phase_scope not in {"building", "formal"}:
        raise ValueError(f"Unsupported Z-score export phase: {phase_scope}")

    normalized_input_value_type = normalize_input_value_type(input_value_type)
    if phase_scope == "building":
        export_runs = [
            run
            for run in saved_runs
            if str(run.get("phase") or "") == PHASE_TARGET_BUILDING
        ]
        export_columns = ["检测序号", "检测时间", "检测人"]
        for level_id in required_level_ids:
            export_columns.append(
                build_level_measurement_label(
                    _build_zscore_export_level_prefix(level_id),
                    normalized_input_value_type,
                )
            )
        export_columns.extend(["备注", "阶段"])
    else:
        export_runs = [
            run for run in saved_runs if str(run.get("phase") or "") == PHASE_FORMAL_QC
        ]
        export_columns = ["检测序号", "检测时间", "检测人"]
        for level_id in required_level_ids:
            level_prefix = build_level_measurement_label(
                _build_zscore_export_level_prefix(level_id),
                normalized_input_value_type,
            )
            export_columns.extend(
                [
                    level_prefix,
                    f"{level_prefix} Z值",
                    f"{level_prefix} 状态",
                ]
            )
        export_columns.extend(
            [
                "run-level 判定结果",
                "触发规则",
                "误差类型",
                "分析提示",
                "备注",
                "阶段",
            ]
        )

    rows: list[dict[str, Any]] = []
    for run in export_runs:
        level_results_by_id = {
            str(level_result.get("level_id")): level_result
            for level_result in run.get("level_results", [])
        }
        row: dict[str, Any] = {
            "检测序号": get_zscore_display_sequence(run),
            "检测时间": _format_zscore_export_datetime(run.get("test_time")),
            "检测人": str(run.get("operator", "") or ""),
        }
        for level_id in required_level_ids:
            level_prefix = build_level_measurement_label(
                _build_zscore_export_level_prefix(level_id),
                normalized_input_value_type,
            )
            level_result = level_results_by_id.get(level_id, {})
            row[level_prefix] = _format_zscore_export_numeric(level_result.get("raw_value"))
            if phase_scope == "formal":
                row[f"{level_prefix} Z值"] = _format_zscore_export_numeric(level_result.get("zscore"))
                row[f"{level_prefix} 状态"] = (
                    format_zscore_status_label(level_result.get("status", "pending"))
                    if level_result
                    else ""
                )
        if phase_scope == "formal":
            row["run-level 判定结果"] = format_zscore_status_label(run.get("run_status", "pending"))
            row["触发规则"] = format_zscore_rule_hits(run.get("rule_hits_run", []))
            row["误差类型"] = format_error_type_label(run.get("error_type_hint", "unknown"))
            row["分析提示"] = str(run.get("analysis_prompt", "") or "")
        row["备注"] = str(run.get("manual_note", "") or "")
        row["阶段"] = str(run.get("phase_label") or get_phase_label(run.get("phase")))
        rows.append(row)

    return pd.DataFrame(rows, columns=export_columns)

def _ensure_zscore_workbench_session_defaults() -> None:
    if "zscore_entry_test_time" not in st.session_state:
        st.session_state["zscore_entry_test_time"] = datetime.now()
    if "zscore_entry_operator" not in st.session_state:
        st.session_state["zscore_entry_operator"] = ""
    if "zscore_level1_value" not in st.session_state:
        st.session_state["zscore_level1_value"] = ""
    if "zscore_level2_value" not in st.session_state:
        st.session_state["zscore_level2_value"] = ""
    if "zscore_level3_value" not in st.session_state:
        st.session_state["zscore_level3_value"] = ""
    if "zscore_view_mode" not in st.session_state:
        st.session_state["zscore_view_mode"] = "单水平视图"
    if "zscore_phase_scope" not in st.session_state:
        st.session_state["zscore_phase_scope"] = "building"
    if "zscore_selected_level" not in st.session_state:
        st.session_state["zscore_selected_level"] = "Level 1"
    if "zscore_reset_entry_form" not in st.session_state:
        st.session_state["zscore_reset_entry_form"] = False


def build_zscore_workbench_context(selected_batch_id: int) -> dict[str, object]:
    _ensure_zscore_workbench_session_defaults()
    batch_context = resolve_zscore_batch_context(selected_batch_id)
    batch = batch_context["batch"]
    input_value_type = normalize_input_value_type(batch["input_value_type"])
    cv_limit = get_saved_batch_cv_limit(batch)
    level_count = int(batch_context["level_count"])
    template_id = str(batch_context["template_id"])
    template = batch_context["template"]
    required_level_ids = list(batch_context["required_level_ids"])
    level_label_map = dict(batch_context["level_label_map"])
    history_runs = get_zscore_runs(selected_batch_id, template_id)
    operator_options = build_zscore_operator_options(history_runs)
    required_n = int(batch_context["required_n"])
    level_target_profiles = get_zscore_level_targets(selected_batch_id, template_id, required_n=required_n)
    overall_phase = determine_zscore_phase(level_target_profiles, required_level_ids)
    overall_phase_label = get_phase_label(overall_phase)
    formal_rules_enabled = should_enable_formal_rules(level_target_profiles, required_level_ids)
    default_phase_scope = "building" if overall_phase == PHASE_TARGET_BUILDING else "formal"
    sync_zscore_workbench_state(selected_batch_id, template, default_phase_scope)
    return {
        "batch_context": batch_context,
        "batch": batch,
        "input_value_type": input_value_type,
        "input_value_type_label": get_input_value_type_label(input_value_type),
        "cv_limit": cv_limit,
        "level_count": level_count,
        "template_id": template_id,
        "template": template,
        "required_level_ids": required_level_ids,
        "level_label_map": level_label_map,
        "history_runs": history_runs,
        "operator_options": operator_options,
        "required_n": required_n,
        "level_target_profiles": level_target_profiles,
        "overall_phase": overall_phase,
        "overall_phase_label": overall_phase_label,
        "formal_rules_enabled": formal_rules_enabled,
        "default_phase_scope": default_phase_scope,
        "plot_df": build_zscore_plot_dataframe(history_runs, None, display_phase=None),
        "latest_run": get_latest_zscore_run_for_display(history_runs),
    }


def render_zscore_entry_section(
    context: dict[str, object],
    selected_batch_id: int,
) -> None:
    template = context["template"]
    input_value_type = context["input_value_type"]
    input_value_type_label = context["input_value_type_label"]
    operator_options = context["operator_options"]
    level_count = context["level_count"]
    cv_limit = context["cv_limit"]
    required_n = context["required_n"]
    level_label_map = context["level_label_map"]
    level_target_profiles = context["level_target_profiles"]
    required_level_ids = context["required_level_ids"]
    template_id = context["template_id"]

    if st.session_state.get("zscore_entry_batch_id") != selected_batch_id:
        st.session_state["zscore_entry_batch_id"] = selected_batch_id
        st.session_state["zscore_entry_operator"] = operator_options[0] if operator_options else ""
        st.session_state["zscore_level1_value"] = ""
        st.session_state["zscore_level2_value"] = ""
        st.session_state["zscore_level3_value"] = ""
        st.session_state["zscore_entry_test_time"] = datetime.now()
    if st.session_state.get("zscore_reset_entry_form", False):
        st.session_state["zscore_entry_operator"] = str(st.session_state.get("zscore_entry_operator", "") or "").strip()
        st.session_state["zscore_level1_value"] = ""
        st.session_state["zscore_level2_value"] = ""
        st.session_state["zscore_level3_value"] = ""
        st.session_state["zscore_entry_test_time"] = datetime.now()
        st.session_state["zscore_reset_entry_form"] = False

    notice_message = str(st.session_state.pop("zscore_notice", "") or "")

    if notice_message:
        st.success(notice_message)
    st.caption(
        f"当前项目固定为 {level_count} 水平，输入值类型为 {input_value_type_label}，规则组合为 {format_zscore_template_display_name(template)}。"
        "完成录入后可查看图表与最新结果分析。"
    )
    if cv_limit is not None:
        st.caption(f"当前批次已保存 CV 要求：≤ {cv_limit:.2f}%")

    with st.container(border=True):
        st.markdown("**录入信息**")
        level_render_config = {
            "Level 1": "zscore_level1_value",
            "Level 2": "zscore_level2_value",
            "Level 3": "zscore_level3_value",
        }
        current_phase_scope = str(st.session_state.get("zscore_phase_scope", context["default_phase_scope"]))
        current_view_mode = str(st.session_state.get("zscore_view_mode", "单水平视图"))
        current_selected_level = str(st.session_state.get("zscore_selected_level", template["level_ids"][0]))
        current_y_axis_mode = str(st.session_state.get("zscore_y_axis_mode", ZSCORE_Y_AXIS_OPTIONS[0]))
        try:
            current_standard_sd_limit = float(st.session_state.get("zscore_standard_sd_limit", 4.0) or 4.0)
        except (TypeError, ValueError):
            current_standard_sd_limit = 4.0
        # Keep entry widgets inside a form so Tab focus changes do not trigger reruns.
        with st.form("zscore_entry_form"):
            test_time = st.datetime_input("检测时间", key="zscore_entry_test_time")
            operator = st.selectbox(
                "检测人",
                options=operator_options,
                index=None,
                key="zscore_entry_operator",
                accept_new_options=True,
                placeholder="可选择历史姓名，也可直接输入新姓名",
            )
            for level_id in template["level_ids"]:
                display_label, level_caption = format_zscore_level_display(level_id, level_label_map)
                value_key = level_render_config[level_id]
                render_zscore_level_input_block(
                    level_label=display_label,
                    input_value_type=input_value_type,
                    value_key=value_key,
                    level_caption=level_caption,
                )
            submitted = st.form_submit_button(
                "保存本次检测",
                type="primary",
                width="stretch",
                on_click=_snapshot_zscore_chart_control_state_for_restore,
                args=(
                    selected_batch_id,
                    template,
                    str(context["default_phase_scope"]),
                    current_phase_scope,
                    current_view_mode,
                    current_selected_level,
                    current_y_axis_mode,
                    current_standard_sd_limit,
                ),
            )

        if submitted:
            current_level_results, input_errors, _ = build_zscore_current_level_results(
                template,
                input_value_type,
                level_label_map,
            )
            validation_errors = list(input_errors)
            cleaned_operator = str(operator or "").strip()
            if test_time is None:
                validation_errors.append("请填写检测时间。")
            if not cleaned_operator:
                validation_errors.append("请填写检测人。")
            for level_result in current_level_results:
                if level_result["raw_value"] is None:
                    display_label = format_zscore_level_display(level_result["level_id"], level_label_map)[0]
                    validation_errors.append(
                        f"{build_level_measurement_label(display_label, input_value_type)}不能为空。"
                    )

            if validation_errors:
                st.error("\n".join(dict.fromkeys(validation_errors)))
            else:
                try:
                    create_zscore_run(
                        batch_id=selected_batch_id,
                        test_time=test_time,
                        operator=cleaned_operator,
                        level_results=current_level_results,
                        template_id=template_id,
                        required_n=required_n,
                        manual_note="",
                    )
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    _apply_saved_zscore_chart_control_state(
                        template,
                        str(context["default_phase_scope"]),
                    )
                    st.session_state["zscore_notice"] = "Z-score 检测结果已保存。"
                    st.session_state["zscore_reset_entry_form"] = True
                    st.session_state["zscore_restore_chart_controls"] = True
                    st.session_state["zscore_chart_controls_force_sync"] = True
                    st.rerun()

def render_zscore_level_summary_section(
    context: dict[str, object],
    selected_batch_id: int,
) -> None:
    del selected_batch_id
    level_label_map = context["level_label_map"]
    level_target_profiles = context["level_target_profiles"]
    required_level_ids = context["required_level_ids"]
    cv_limit = context["cv_limit"]

    st.subheader("各水平统计摘要")
    st.caption("各水平的建靶进度、正式靶值与实时统计集中展示在这里。")
    stat_cols = st.columns(len(required_level_ids), gap="large")
    for stat_col, level_id in zip(stat_cols, required_level_ids):
        profile = level_target_profiles[level_id]
        display_label, level_caption = format_zscore_level_display(level_id, level_label_map)
        with stat_col:
            with st.container(border=True):
                st.markdown(f"**{display_label}**")
                if level_caption:
                    st.caption(level_caption)
                render_compact_stat_metrics(
                    [
                        ("已收集", f"{profile['collected_n']}"),
                        ("建靶要求", f"{profile['required_n']} 次"),
                        ("已达建靶条件", "是" if profile["is_ready"] else "否"),
                        ("当前阶段", profile["phase_label"]),
                    ]
                )
                render_zscore_profile_stat_line(
                    "建靶统计",
                    profile.get("provisional_mean"),
                    profile.get("provisional_sd"),
                    profile.get("provisional_cv"),
                )
                render_cv_limit_hint(
                    profile.get("provisional_cv"),
                    cv_limit,
                    f"{display_label} 建靶",
                )
                render_zscore_profile_stat_line(
                    "正式靶值",
                    profile.get("final_target_mean"),
                    profile.get("final_target_sd"),
                    profile.get("final_target_cv"),
                )
                render_zscore_profile_stat_line(
                    "实时统计",
                    profile.get("realtime_mean"),
                    profile.get("realtime_sd"),
                    profile.get("realtime_cv"),
                )

def render_zscore_vendor_reference_section(
    context: dict[str, object],
    selected_batch_id: int,
) -> None:
    template_id = context["template_id"]
    required_n = context["required_n"]
    level_label_map = context["level_label_map"]
    level_target_profiles = context["level_target_profiles"]
    required_level_ids = context["required_level_ids"]

    with st.expander("查看各水平参考值与来源备注", expanded=False):
        vendor_cols = st.columns(len(required_level_ids), gap="large")
        for vendor_col, level_id in zip(vendor_cols, required_level_ids):
            profile = level_target_profiles[level_id]
            display_label, level_caption = format_zscore_level_display(level_id, level_label_map)
            with vendor_col:
                with st.container(border=True):
                    st.markdown(f"**{display_label}**")
                    if level_caption:
                        st.caption(level_caption)
                    render_zscore_profile_stat_line(
                        "当前厂家参考",
                        profile.get("vendor_reference_mean"),
                        profile.get("vendor_reference_sd"),
                        profile.get("vendor_reference_cv"),
                    )
                    if profile.get("vendor_reference_source_note"):
                        st.caption(f"来源备注：{profile['vendor_reference_source_note']}")
                    render_zscore_vendor_reference_editor_body(
                        batch_id=selected_batch_id,
                        template_id=template_id,
                        level_id=level_id,
                        level_display_name=display_label,
                        required_n=required_n,
                        profile=profile,
                    )


def render_zscore_chart_analysis_section(
    context: dict[str, object],
    phase_scope: str,
    view_mode: str,
    selected_level: str,
    y_axis_mode: str,
    standard_sd_limit: float,
) -> dict[str, object]:
    plot_df = context["plot_df"]
    latest_run = context["latest_run"]
    overall_phase = context["overall_phase"]
    formal_rules_enabled = context["formal_rules_enabled"]
    batch = context["batch"]
    input_value_type_label = context["input_value_type_label"]
    required_level_ids = context["required_level_ids"]
    template = context["template"]
    level_label_map = context["level_label_map"]

    phase_title = {
        "building": "建靶期图",
        "formal": "正式质控图",
        "all": "全图",
    }[phase_scope]
    if view_mode == "单水平视图":
        figure = plot_zscore_single_level(
            plot_df=plot_df,
            level_id=selected_level,
            title=f"{phase_title} | {format_zscore_level_display(selected_level, level_label_map)[0]}",
            phase_scope=phase_scope,
            y_axis_mode=y_axis_mode,
            standard_sd_limit=standard_sd_limit,
            y_axis_label=input_value_type_label,
        )
    else:
        figure = plot_zscore_overlay(
            plot_df=plot_df,
            title=f"{phase_title} | {format_zscore_template_display_name(template)}",
            active_levels=required_level_ids,
            phase_scope=phase_scope,
            y_axis_mode=y_axis_mode,
            standard_sd_limit=standard_sd_limit,
            y_axis_label=input_value_type_label,
        )
    with st.container(border=True):
        st.markdown("**质控图**")
        st.pyplot(figure, clear_figure=False, width="stretch")
    with st.container(border=True):
        render_zscore_latest_analysis_panel(latest_run, overall_phase, formal_rules_enabled)
    render_zscore_abnormal_note_quick_entry(latest_run)
    render_zscore_rules_config_expander(template, overall_phase, formal_rules_enabled)

    project_name_fragment = build_safe_export_name(
        batch["project_name"] if "project_name" in batch.keys() else None,
        "project",
    )
    lot_no_fragment = build_safe_export_name(
        batch["lot_no"] if "lot_no" in batch.keys() else None,
        f"batch_{batch['id']}",
    )
    phase_scope_fragment = build_safe_export_name(
        ZSCORE_PHASE_VIEW_OPTIONS.get(phase_scope, phase_scope),
        "all",
    )
    template_display_name = format_zscore_template_display_name(template)
    selected_level_display, _ = format_zscore_level_display(selected_level, level_label_map)
    current_view_label = selected_level_display if view_mode == "单水平视图" else template_display_name
    current_view_fragment = build_safe_export_name(current_view_label, "chart")
    return {
        "figure": figure,
        "current_png_bytes": figure_to_png_bytes(figure),
        "project_name_fragment": project_name_fragment,
        "lot_no_fragment": lot_no_fragment,
        "phase_scope_fragment": phase_scope_fragment,
        "current_view_label": current_view_label,
        "current_view_fragment": current_view_fragment,
        "phase_scope": phase_scope,
        "view_mode": view_mode,
        "selected_level": selected_level,
        "y_axis_mode": y_axis_mode,
        "standard_sd_limit": standard_sd_limit,
    }


def render_zscore_maintenance_section(context: dict[str, object]) -> None:
    history_runs = context["history_runs"]
    batch_context = context["batch_context"]
    level_label_map = dict(batch_context["level_label_map"])
    formal_rules_enabled = bool(context["formal_rules_enabled"])
    building_runs = [
        run for run in history_runs if str(run.get("phase") or "") == PHASE_TARGET_BUILDING
    ]

    notice_message = str(st.session_state.pop("zscore_outlier_notice", "") or "")
    if notice_message:
        st.success(notice_message)

    with st.container(border=True):
        if not building_runs:
            st.info("当前批次暂无建靶期 level 点可维护。")
        else:
            ordered_runs = sorted(
                building_runs,
                key=lambda run: (
                    get_zscore_display_sequence(run),
                    int(run.get("run_id") or 0),
                ),
                reverse=True,
            )
            run_options: dict[str, int] = {}
            run_labels: list[str] = []
            for run in ordered_runs:
                label = (
                    f"run #{get_zscore_display_sequence(run)} | "
                    f"{pd.Timestamp(run['test_time']).strftime('%Y-%m-%d %H:%M')}"
                )
                run_labels.append(label)
                run_options[label] = int(run["run_id"])

            selected_run_label = st.selectbox(
                "选择建靶期 run",
                options=run_labels,
                key="zscore_outlier_run_selector",
            )
            selected_run_id = run_options[selected_run_label]
            selected_run = next(
                run for run in ordered_runs if int(run["run_id"]) == int(selected_run_id)
            )
            if formal_rules_enabled:
                st.info("正式期启用后，Z-score 建靶期单 level 离群值状态将锁定，不再允许保留、禁用或恢复。")

            for level_result in selected_run.get("level_results", []):
                display_label, level_caption = format_zscore_level_display(
                    level_result["level_id"],
                    level_label_map,
                )
                statistic_text = ""
                if level_result.get("grubbs_statistic") is not None:
                    statistic_text = f"{float(level_result['grubbs_statistic']):.4f}"
                threshold_text = ""
                if level_result.get("grubbs_threshold") is not None:
                    threshold_text = f"{float(level_result['grubbs_threshold']):.4f}"
                with st.container(border=True):
                    st.markdown(f"**{display_label}**")
                    if level_caption:
                        st.caption(level_caption)
                    st.caption(
                        f"所属 run：#{get_zscore_display_sequence(selected_run)} | "
                        f"所属 level：{display_label} | "
                        f"状态：{get_outlier_status_label(level_result.get('outlier_status'))} | "
                        f"手工处理：{get_outlier_manual_status_label(level_result.get('manual_status'))} | "
                        f"G={statistic_text} | "
                        f"G临界值={threshold_text} | "
                        f"alpha={DEFAULT_GRUBBS_ALPHA:.2f}"
                    )
                    if int(level_result.get("is_outlier_suspect", 0) or 0) == 1:
                        st.warning(
                            f"run #{get_zscore_display_sequence(selected_run)} 的 {display_label} 当前为疑似离群点。"
                        )
                    action_cols = st.columns(3)
                    keep_disabled = formal_rules_enabled
                    disable_disabled = formal_rules_enabled or int(level_result.get("is_building_included", 1) or 0) == 0
                    restore_disabled = formal_rules_enabled or int(level_result.get("is_building_included", 1) or 0) == 1
                    if action_cols[0].button(
                        "保留",
                        key=f"zscore_keep_{level_result['id']}",
                        width="stretch",
                        disabled=keep_disabled,
                    ):
                        keep_zscore_level_result(int(level_result["id"]))
                        st.session_state["zscore_outlier_notice"] = f"{display_label} 已标记为保留，并已重算建靶统计。"
                        st.rerun()
                    if action_cols[1].button(
                        "禁用",
                        key=f"zscore_disable_{level_result['id']}",
                        width="stretch",
                        disabled=disable_disabled,
                    ):
                        disable_zscore_level_result(int(level_result["id"]))
                        st.session_state["zscore_outlier_notice"] = f"{display_label} 已禁用，并已重算建靶统计。"
                        st.rerun()
                    if action_cols[2].button(
                        "恢复",
                        key=f"zscore_restore_{level_result['id']}",
                        width="stretch",
                        disabled=restore_disabled,
                    ):
                        restore_zscore_level_result(int(level_result["id"]))
                        st.session_state["zscore_outlier_notice"] = f"{display_label} 已恢复，并已重算建靶统计。"
                        st.rerun()

        if st.button(
            "打开记录维护",
            key="open_zscore_record_maintenance_dialog",
            width="stretch",
            disabled=not history_runs,
        ):
            bump_zscore_record_maintenance_dialog_nonce_impl()
            st.session_state["show_zscore_record_maintenance_dialog"] = True
        if not history_runs:
            st.info("当前批次暂无已保存的检测记录可维护。")
    if st.session_state.get("show_zscore_record_maintenance_dialog", False):
        render_zscore_record_maintenance_dialog_impl(history_runs, batch_context)


def render_zscore_export_import_section(
    context: dict[str, object],
    selected_batch_id: int,
    chart_panel_state: dict[str, object],
) -> None:
    batch = context["batch"]
    history_runs = context["history_runs"]
    input_value_type = context["input_value_type"]
    input_value_type_label = context["input_value_type_label"]
    level_count = context["level_count"]
    required_level_ids = context["required_level_ids"]
    required_n = context["required_n"]
    overall_phase = context["overall_phase"]
    formal_rules_enabled = context["formal_rules_enabled"]
    plot_df = context["plot_df"]
    template_id = context["template_id"]

    project_name_fragment = chart_panel_state["project_name_fragment"]
    lot_no_fragment = chart_panel_state["lot_no_fragment"]
    phase_scope_fragment = chart_panel_state["phase_scope_fragment"]
    current_view_label = chart_panel_state["current_view_label"]
    current_view_fragment = chart_panel_state["current_view_fragment"]
    phase_scope = chart_panel_state["phase_scope"]
    view_mode = chart_panel_state["view_mode"]
    selected_level = chart_panel_state["selected_level"]
    y_axis_mode = chart_panel_state["y_axis_mode"]
    standard_sd_limit = chart_panel_state["standard_sd_limit"]

    zscore_building_template_df = build_zscore_building_template_dataframe(
        level_count,
        input_value_type=input_value_type,
    )
    zscore_building_template_csv_bytes = zscore_building_template_df.to_csv(index=False).encode("utf-8-sig")
    zscore_building_import_scope = f"zscore_building_import_{selected_batch_id}"
    zscore_building_import_review_state_key = f"{zscore_building_import_scope}_review"
    zscore_building_import_success_key = f"{zscore_building_import_scope}_success"
    zscore_building_import_uploader_nonce_key = f"{zscore_building_import_scope}_uploader_nonce"
    zscore_building_import_uploader_nonce = int(
        st.session_state.get(zscore_building_import_uploader_nonce_key, 0)
    )
    zscore_building_import_uploader_key = (
        f"{zscore_building_import_scope}_file_{zscore_building_import_uploader_nonce}"
    )
    zscore_building_import_disabled = overall_phase != PHASE_TARGET_BUILDING
    existing_building_runs = [
        run
        for run in history_runs
        if str(run.get("phase") or "") == PHASE_TARGET_BUILDING
    ]
    existing_all_runs_df = pd.DataFrame(
        {
            "test_time": [run.get("test_time") for run in history_runs],
        }
    )
    existing_building_runs_df = pd.DataFrame(
        {
            "test_time": [run.get("test_time") for run in existing_building_runs],
        }
    )
    existing_formal_runs = [
        run
        for run in history_runs
        if str(run.get("phase") or "") == PHASE_FORMAL_QC
    ]
    zscore_target_ready = bool(formal_rules_enabled)
    zscore_building_import_success_message = str(
        st.session_state.pop(zscore_building_import_success_key, "") or ""
    )
    if zscore_building_import_success_message:
        st.success(zscore_building_import_success_message)

    zscore_formal_import_scope = f"zscore_formal_import_{selected_batch_id}"
    zscore_formal_import_review_state_key = f"{zscore_formal_import_scope}_review"
    zscore_formal_import_success_key = f"{zscore_formal_import_scope}_success"
    zscore_formal_import_uploader_nonce_key = f"{zscore_formal_import_scope}_uploader_nonce"
    zscore_formal_import_uploader_nonce = int(
        st.session_state.get(zscore_formal_import_uploader_nonce_key, 0)
    )
    zscore_formal_import_uploader_key = (
        f"{zscore_formal_import_scope}_file_{zscore_formal_import_uploader_nonce}"
    )
    zscore_formal_import_success_message = str(
        st.session_state.pop(zscore_formal_import_success_key, "") or ""
    )
    if zscore_formal_import_success_message:
        st.success(zscore_formal_import_success_message)

    building_export_df = build_zscore_phase_export_dataframe(
        history_runs,
        required_level_ids,
        "building",
        input_value_type,
    )
    formal_export_df = build_zscore_phase_export_dataframe(
        history_runs,
        required_level_ids,
        "formal",
        input_value_type,
    )
    building_csv_bytes = building_export_df.to_csv(index=False).encode("utf-8-sig")
    building_xlsx_bytes = dataframe_to_xlsx_bytes(building_export_df)
    formal_csv_bytes = formal_export_df.to_csv(index=False).encode("utf-8-sig")
    formal_xlsx_bytes = dataframe_to_xlsx_bytes(formal_export_df)

    st.caption(f"导出当前批次数据与图表，并按模板导入 CSV；各水平主值列统一为“{input_value_type_label}”。")
    st.markdown("**导出**")
    st.caption("当前批次数据按每次检测展开为宽表，建靶期与正式期可分别导出。")
    zscore_export_format = st.radio(
        "导出数据格式",
        options=["Excel (.xlsx)", "CSV (.csv)"],
        horizontal=True,
        key="zscore_export_format",
    )
    zscore_data_export_cols = st.columns(2)
    zscore_data_export_cols[0].download_button(
        label="导出建靶期数据",
        data=building_xlsx_bytes if zscore_export_format == "Excel (.xlsx)" else building_csv_bytes,
        file_name=(
            f"{project_name_fragment}_batch_{batch['id']}_{lot_no_fragment}_zscore_building_runs.xlsx"
            if zscore_export_format == "Excel (.xlsx)"
            else f"{project_name_fragment}_batch_{batch['id']}_{lot_no_fragment}_zscore_building_runs.csv"
        ),
        mime=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            if zscore_export_format == "Excel (.xlsx)"
            else "text/csv"
        ),
        width="stretch",
        disabled=building_export_df.empty,
    )
    zscore_data_export_cols[1].download_button(
        label="导出正式期数据",
        data=formal_xlsx_bytes if zscore_export_format == "Excel (.xlsx)" else formal_csv_bytes,
        file_name=(
            f"{project_name_fragment}_batch_{batch['id']}_{lot_no_fragment}_zscore_formal_runs.xlsx"
            if zscore_export_format == "Excel (.xlsx)"
            else f"{project_name_fragment}_batch_{batch['id']}_{lot_no_fragment}_zscore_formal_runs.csv"
        ),
        mime=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            if zscore_export_format == "Excel (.xlsx)"
            else "text/csv"
        ),
        width="stretch",
        disabled=formal_export_df.empty,
    )

    st.markdown("**CSV 导入**")
    st.caption("建靶期与正式期模板、上传、审查、确认导入分开展示。")
    st.markdown("**建靶期 CSV 导入**")
    st.caption(
        f"先下载当前批次标准模板，再上传 CSV 审查；只有无阻断错误时，才允许确认导入当前批次建靶期{input_value_type_label}检测记录。"
    )
    st.download_button(
        label="下载建靶期 CSV 模板",
        data=zscore_building_template_csv_bytes,
        file_name=(
            f"{project_name_fragment}_batch_{batch['id']}_{lot_no_fragment}_zscore_building_import_template.csv"
        ),
        mime="text/csv",
        width="stretch",
    )
    if zscore_building_import_disabled:
        st.info("当前批次已完成建靶。当前入口仅支持建靶期 CSV 导入，不支持继续追加正式期检测记录。")
        st.session_state.pop(zscore_building_import_review_state_key, None)

    uploaded_zscore_building_csv = st.file_uploader(
        "上传建靶期 CSV",
        type=["csv"],
        key=zscore_building_import_uploader_key,
        disabled=zscore_building_import_disabled,
        help="模板会按当前批次的 2 水平 / 3 水平自动生成，当前版本仅支持 CSV。",
    )
    uploaded_zscore_building_bytes = (
        uploaded_zscore_building_csv.getvalue() if uploaded_zscore_building_csv is not None else b""
    )
    current_zscore_building_signature = (
        (
            f"{hashlib.sha256(uploaded_zscore_building_bytes).hexdigest()}:"
            f"{overall_phase}:{len(existing_building_runs)}:{required_n}:{level_count}"
        )
        if uploaded_zscore_building_bytes
        else ""
    )

    zscore_building_import_review_state = st.session_state.get(
        zscore_building_import_review_state_key
    )
    if not current_zscore_building_signature:
        st.session_state.pop(zscore_building_import_review_state_key, None)
        zscore_building_import_review_state = None
    elif (
        zscore_building_import_review_state is not None
        and zscore_building_import_review_state.get("file_signature")
        != current_zscore_building_signature
    ):
        st.session_state.pop(zscore_building_import_review_state_key, None)
        zscore_building_import_review_state = None

    zscore_import_action_cols = st.columns(2)
    review_zscore_building_clicked = zscore_import_action_cols[0].button(
        "审查 CSV",
        key=f"{zscore_building_import_scope}_review_button",
        width="stretch",
        disabled=zscore_building_import_disabled or uploaded_zscore_building_csv is None,
    )
    if review_zscore_building_clicked:
        zscore_building_import_review_state = review_zscore_building_import_csv(
            file_bytes=uploaded_zscore_building_bytes,
            existing_results_df=existing_building_runs_df,
            level_count=level_count,
            target_n=required_n,
            input_value_type=input_value_type,
        )
        zscore_building_import_review_state["file_signature"] = current_zscore_building_signature
        st.session_state[zscore_building_import_review_state_key] = (
            zscore_building_import_review_state
        )

    confirm_zscore_building_import_disabled = True
    if zscore_building_import_review_state is not None:
        zscore_review_summary = zscore_building_import_review_state["summary"]
        zscore_review_issues_df = build_review_issues_dataframe(
            zscore_building_import_review_state["issues"]
        )
        render_import_review_summary(zscore_review_summary)
        if zscore_review_summary["has_blocking_errors"]:
            st.error("审查未通过：存在阻断错误，当前整批不会导入。")
        else:
            st.success("审查通过：当前没有阻断错误，可以确认导入。")

        if zscore_review_issues_df.empty:
            st.info("本次审查未发现错误或警告。")
        else:
            st.dataframe(zscore_review_issues_df, hide_index=True, width="stretch")

        confirm_zscore_building_import_disabled = (
            zscore_building_import_disabled
            or zscore_review_summary["has_blocking_errors"]
            or not zscore_building_import_review_state["normalized_rows"]
        )

    confirm_zscore_building_import_clicked = zscore_import_action_cols[1].button(
        "确认导入建靶期数据",
        key=f"{zscore_building_import_scope}_confirm_button",
        width="stretch",
        disabled=confirm_zscore_building_import_disabled,
    )
    if (
        confirm_zscore_building_import_clicked
        and zscore_building_import_review_state is not None
    ):
        imported_row_count = 0
        try:
            for row in zscore_building_import_review_state["normalized_rows"]:
                create_zscore_run(
                    batch_id=selected_batch_id,
                    test_time=row["test_time"],
                    operator=row["operator"],
                    level_results=deepcopy(row["level_results"]),
                    template_id=template_id,
                    required_n=required_n,
                    manual_note=row["manual_note"],
                )
                imported_row_count += 1
        except ValueError as exc:
            st.error(f"导入中断：{exc}。请重新审查当前文件后再试。")
        else:
            st.session_state.pop(zscore_building_import_review_state_key, None)
            st.session_state[zscore_building_import_uploader_nonce_key] = (
                zscore_building_import_uploader_nonce + 1
            )
            st.session_state[zscore_building_import_success_key] = (
                f"已追加导入 {imported_row_count} 条建靶期检测记录，并自动更新当前建靶统计与建靶进度。"
            )
            st.rerun()

    st.markdown("**正式期 CSV 导入**")
    st.caption(
        f"先下载当前批次标准模板，再上传 CSV 审查；导入目标为当前批次正式期，只有无阻断错误时才允许确认导入当前批次{input_value_type_label}数据。"
    )
    st.download_button(
        label="下载正式期 CSV 模板",
        data=zscore_building_template_csv_bytes,
        file_name=(
            f"{project_name_fragment}_batch_{batch['id']}_{lot_no_fragment}_zscore_formal_import_template.csv"
        ),
        mime="text/csv",
        width="stretch",
    )
    if not zscore_target_ready:
        st.info("当前批次尚未完成建靶，不能导入正式期数据。你仍可先上传 CSV 做审查。")

    uploaded_zscore_formal_csv = st.file_uploader(
        "上传正式期 CSV",
        type=["csv"],
        key=zscore_formal_import_uploader_key,
        help="模板会按当前批次的 2 水平 / 3 水平自动生成，当前版本仅支持 CSV。",
    )
    uploaded_zscore_formal_bytes = (
        uploaded_zscore_formal_csv.getvalue() if uploaded_zscore_formal_csv is not None else b""
    )
    current_zscore_formal_signature = (
        (
            f"{hashlib.sha256(uploaded_zscore_formal_bytes).hexdigest()}:"
            f"{int(zscore_target_ready)}:{overall_phase}:{len(history_runs)}:{len(existing_formal_runs)}:{required_n}:{level_count}"
        )
        if uploaded_zscore_formal_bytes
        else ""
    )

    zscore_formal_import_review_state = st.session_state.get(
        zscore_formal_import_review_state_key
    )
    if not current_zscore_formal_signature:
        st.session_state.pop(zscore_formal_import_review_state_key, None)
        zscore_formal_import_review_state = None
    elif (
        zscore_formal_import_review_state is not None
        and zscore_formal_import_review_state.get("file_signature")
        != current_zscore_formal_signature
    ):
        st.session_state.pop(zscore_formal_import_review_state_key, None)
        zscore_formal_import_review_state = None

    zscore_formal_import_action_cols = st.columns(2)
    review_zscore_formal_clicked = zscore_formal_import_action_cols[0].button(
        "审查正式期 CSV",
        key=f"{zscore_formal_import_scope}_review_button",
        width="stretch",
        disabled=uploaded_zscore_formal_csv is None,
    )
    if review_zscore_formal_clicked:
        zscore_formal_import_review_state = review_zscore_formal_import_csv(
            file_bytes=uploaded_zscore_formal_bytes,
            existing_results_df=existing_all_runs_df,
            level_count=level_count,
            target_ready=zscore_target_ready,
            existing_formal_count=len(existing_formal_runs),
            input_value_type=input_value_type,
        )
        zscore_formal_import_review_state["file_signature"] = current_zscore_formal_signature
        st.session_state[zscore_formal_import_review_state_key] = (
            zscore_formal_import_review_state
        )

    confirm_zscore_formal_import_disabled = True
    if zscore_formal_import_review_state is not None:
        zscore_formal_review_summary = zscore_formal_import_review_state["summary"]
        zscore_formal_review_issues_df = build_review_issues_dataframe(
            zscore_formal_import_review_state["issues"]
        )
        render_import_review_summary(zscore_formal_review_summary)
        if zscore_formal_review_summary["has_blocking_errors"]:
            st.error("正式期审查未通过：存在阻断错误，当前整批不会导入。")
        else:
            st.success("正式期审查通过：当前没有阻断错误，可以确认导入。")

        if zscore_formal_review_issues_df.empty:
            st.info("本次正式期审查未发现错误或警告。")
        else:
            st.dataframe(zscore_formal_review_issues_df, hide_index=True, width="stretch")

        confirm_zscore_formal_import_disabled = (
            (not zscore_target_ready)
            or zscore_formal_review_summary["has_blocking_errors"]
            or not zscore_formal_import_review_state["normalized_rows"]
        )

    confirm_zscore_formal_import_clicked = zscore_formal_import_action_cols[1].button(
        "确认导入正式期数据",
        key=f"{zscore_formal_import_scope}_confirm_button",
        width="stretch",
        disabled=confirm_zscore_formal_import_disabled,
    )
    if (
        confirm_zscore_formal_import_clicked
        and zscore_formal_import_review_state is not None
    ):
        imported_formal_row_count = 0
        try:
            for row in zscore_formal_import_review_state["normalized_rows"]:
                create_zscore_run(
                    batch_id=selected_batch_id,
                    test_time=row["test_time"],
                    operator=row["operator"],
                    level_results=deepcopy(row["level_results"]),
                    template_id=template_id,
                    required_n=required_n,
                    manual_note=row["manual_note"],
                )
                imported_formal_row_count += 1
        except ValueError as exc:
            st.error(f"正式期导入中断：{exc}。请重新审查当前文件后再试。")
        else:
            st.session_state.pop(zscore_formal_import_review_state_key, None)
            st.session_state[zscore_formal_import_uploader_nonce_key] = (
                zscore_formal_import_uploader_nonce + 1
            )
            st.session_state[zscore_formal_import_success_key] = (
                f"已追加导入 {imported_formal_row_count} 条正式期检测记录，并自动刷新各水平 Z-score、判定结果、图表与最新结果分析。"
            )
            st.rerun()

    st.markdown("**图导出**")
    st.download_button(
        label="导出当前图 PNG",
        data=chart_panel_state["current_png_bytes"],
        file_name=(
            f"{project_name_fragment}_batch_{batch['id']}_{lot_no_fragment}_"
            f"zscore_current_{phase_scope_fragment}_{current_view_fragment}.png"
        ),
        mime="image/png",
        width="stretch",
    )
    st.caption("月度图固定只导正式期，日期范围最长 30 天；单水平视图导出单水平月度图，合并视图导出合并月度图。")
    formal_plot_df = plot_df[plot_df["phase"] == PHASE_FORMAL_QC].copy() if "phase" in plot_df.columns else pd.DataFrame()
    if formal_plot_df.empty:
        st.info("当前批次还没有正式质控数据。")
    else:
        default_monthly_start = formal_plot_df["test_time"].min().date()
        default_monthly_end = formal_plot_df["test_time"].max().date()
        monthly_col_start, monthly_col_end = st.columns(2)
        monthly_start = monthly_col_start.date_input(
            "开始日期",
            value=default_monthly_start,
            key="zscore_monthly_export_start",
        )
        monthly_end = monthly_col_end.date_input(
            "结束日期",
            value=default_monthly_end,
            key="zscore_monthly_export_end",
        )

        monthly_error = ""
        day_span = (pd.Timestamp(monthly_end).date() - pd.Timestamp(monthly_start).date()).days + 1
        if monthly_end < monthly_start:
            monthly_error = "结束日期不能早于开始日期。"
        elif day_span > 30:
            monthly_error = "月度质控图导出范围最长为 30 天，请重新选择日期范围。"

        monthly_png_bytes = None
        monthly_file_name = None
        if monthly_error:
            st.warning(monthly_error)
        else:
            monthly_plot_df = build_zscore_monthly_export_plot_dataframe(
                plot_df=plot_df,
                start_date=monthly_start,
                end_date=monthly_end,
            )
            if monthly_plot_df.empty:
                st.info("所选日期范围内没有正式质控数据，无法导出月度图。")
            else:
                monthly_title = (
                    f"月度质控图 - 质控批号 {batch['lot_no']} - {batch['instrument']} - {batch['reagent']} - "
                    f"{batch['qc_material']} - {batch['concentration']}\n"
                    f"正式期｜{current_view_label}｜{monthly_start.strftime('%Y-%m-%d')} 至 {monthly_end.strftime('%Y-%m-%d')}"
                )
                if view_mode == "单水平视图":
                    monthly_figure = plot_zscore_single_level(
                        plot_df=monthly_plot_df,
                        level_id=selected_level,
                        title=monthly_title,
                        phase_scope="formal",
                        y_axis_mode=y_axis_mode,
                        standard_sd_limit=standard_sd_limit,
                        y_axis_label=input_value_type_label,
                    )
                else:
                    monthly_figure = plot_zscore_overlay(
                        plot_df=monthly_plot_df,
                        title=monthly_title,
                        active_levels=required_level_ids,
                        phase_scope="formal",
                        y_axis_mode=y_axis_mode,
                        standard_sd_limit=standard_sd_limit,
                        y_axis_label=input_value_type_label,
                    )
                monthly_png_bytes = figure_to_png_bytes(monthly_figure)
                monthly_file_name = (
                    f"{project_name_fragment}_batch_{batch['id']}_{lot_no_fragment}_"
                    f"zscore_monthly_formal_{current_view_fragment}_"
                    f"{monthly_start.strftime('%Y-%m-%d')}_to_{monthly_end.strftime('%Y-%m-%d')}.png"
                )

        st.download_button(
            label="导出月度图 PNG",
            data=monthly_png_bytes if monthly_png_bytes is not None else b"",
            file_name=(
                monthly_file_name
                or f"{project_name_fragment}_batch_{batch['id']}_{lot_no_fragment}_zscore_monthly_formal.png"
            ),
            mime="image/png",
            width="stretch",
            disabled=monthly_png_bytes is None,
        )
