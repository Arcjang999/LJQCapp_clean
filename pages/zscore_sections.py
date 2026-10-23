from __future__ import annotations

from services.terminology_service import display_generated_text


from datetime import datetime
import math
from typing import Any

import pandas as pd
import streamlit as st

from services.value_type_service import (
    build_level_measurement_label,
    get_input_value_type_label,
    normalize_input_value_type,
    parse_project_input_value,
)
from ui.common import ZSCORE_PHASE_VIEW_OPTIONS, ZSCORE_Y_AXIS_OPTIONS, build_safe_export_name, build_zscore_operator_options, format_error_type_label, format_optional_float, format_optional_input_value, format_rule_code, format_rule_description, format_zscore_status_label, format_zscore_template_display_name, get_saved_batch_cv_limit, render_compact_stat_metrics, render_cv_limit_hint, render_latest_analysis_card, render_standard_view_help
from services.outlier_service import get_current_outlier_status_label, get_outlier_manual_status_label
from services.profiling import profile_timer
from zscore_logic import (
    PHASE_FORMAL_QC,
    PHASE_TARGET_BUILDING,
    build_zscore_plot_dataframe as build_zscore_plot_dataframe_logic,
    create_zscore_run,
    determine_zscore_phase,
    format_level_id_display,
    get_phase_label,
    get_zscore_display_sequence,
    get_zscore_level_targets,
    get_zscore_runs,
    resolve_zscore_batch_context,
    should_enable_formal_rules,
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
    with profile_timer(
        "pages.build_zscore_plot_dataframe",
        runs=0 if saved_runs is None else len(saved_runs),
        display_phase=display_phase,
        has_draft=bool(draft_run),
    ):
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
            st.info("控制参数已确认，正式规则已启用。请录入首条正式质控检测记录。")
        else:
            st.info("当前处于参数建立期，请先录入检测记录，用于建立均值和标准差并观察多水平趋势。")
        return

    is_building_phase = (not formal_rules_enabled) or latest_run.get("phase") != PHASE_FORMAL_QC
    status = PHASE_TARGET_BUILDING if is_building_phase else str(latest_run.get("run_status", "pending"))
    source_text = (
        "本次输入（尚未保存）"
        if latest_run.get("is_preview")
        else f"最近已保存检测序号 #{get_zscore_display_sequence(latest_run)}"
    )
    phase_label = display_generated_text(str(latest_run.get("phase_label") or get_phase_label(latest_run.get("phase", overall_phase))))
    badge_text = phase_label if is_building_phase else format_zscore_status_label(status)
    trigger_rule_text = (
        "参数建立期不启用正式规则"
        if is_building_phase
        else format_zscore_rule_hits(latest_run.get("rule_hits_run", []))
    )
    summary_text = (
        "本次结果纳入均值和标准差建立观察，不作为正式质控结论。"
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
    """Compatibility entry for a known abnormal run, without an inline editor."""
    if latest_run is None or latest_run.get('is_preview') or latest_run.get('phase') != PHASE_FORMAL_QC:
        return
    if latest_run.get('run_status') not in ('warning', 'reject'):
        return
    from ui.out_of_control import render_abnormal_entry
    render_abnormal_entry('zscore_run', int(latest_run['run_id']),
                          warning=latest_run.get('run_status') == 'warning', key='latest')


def render_zscore_rules_config_expander(
    template: dict[str, Any],
    overall_phase: str,
    formal_rules_enabled: bool,
) -> None:
    with st.expander("规则说明", expanded=False):
        template_display_name = format_zscore_template_display_name(template)
        st.caption(template["note"])
        st.markdown(f"- 当前规则组合：`{template_display_name}`")
        st.markdown(f"- 当前阶段：`{display_generated_text(get_phase_label(overall_phase))}`")
        st.markdown(f"- 正式规则已启用：`{'是' if formal_rules_enabled else '否'}`")
        if not formal_rules_enabled:
            st.info("当前仍处于参数建立期，以下规则说明仅供进入正式质控期后的判读参考。")
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
    """Keep the prior entry point while editing only in the reference dialog."""
    from ui.zscore_reference_dialog import render_zscore_reference_summary
    render_zscore_reference_summary(batch_id, template_id, required_n,
                                   {level_id: profile}, [level_id], {level_id: level_display_name})


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
    ordered_rule_ids = list(
        dict.fromkeys(
            rule_id
            for rule_id in (_extract_zscore_rule_id(hit) for hit in rule_hits)
            if rule_id
        )
    )
    if not ordered_rule_ids:
        return "无"
    return "、".join(format_rule_code(rule_id) for rule_id in ordered_rule_ids)


def format_zscore_level_rule_hits(rule_hit_ids: list[str]) -> str:
    normalized_rule_ids = [str(rule_id or "").strip() for rule_id in rule_hit_ids if str(rule_id or "").strip()]
    if not normalized_rule_ids:
        return "无"
    ordered_rule_ids = list(dict.fromkeys(normalized_rule_ids))
    return "、".join(format_rule_code(rule_id) for rule_id in ordered_rule_ids)


def get_zscore_level_current_status_label(level_result: dict[str, Any]) -> str:
    return get_current_outlier_status_label(
        is_building_included=level_result.get("is_building_included", 1),
        is_suspect=level_result.get("is_outlier_suspect", 0),
    )


def build_zscore_building_run_status_summary(run: dict[str, Any]) -> dict[str, Any]:
    level_results = list(run.get("level_results", []))
    included_flags = [
        int(level_result.get("is_building_included", 1) or 0)
        for level_result in level_results
    ]
    any_excluded = any(flag == 0 for flag in included_flags)
    all_excluded = bool(included_flags) and all(flag == 0 for flag in included_flags)
    has_inconsistent_status = any_excluded and not all_excluded
    any_active_suspect = any(
        int(level_result.get("is_building_included", 1) or 0) == 1
        and int(level_result.get("is_outlier_suspect", 0) or 0) == 1
        for level_result in level_results
    )
    if all_excluded:
        current_status_label = "已禁用"
    elif has_inconsistent_status:
        current_status_label = "状态不一致"
    elif any_active_suspect:
        current_status_label = "疑似离群"
    else:
        current_status_label = "正常"
    return {
        "current_status_label": current_status_label,
        "any_excluded": any_excluded,
        "all_excluded": all_excluded,
        "has_inconsistent_status": has_inconsistent_status,
    }


def build_zscore_building_run_evidence_dataframe(
    run: dict[str, Any],
    level_label_map: dict[str, str],
    input_value_type_label: str,
) -> pd.DataFrame:
    evidence_rows: list[dict[str, str]] = []
    for level_result in run.get("level_results", []):
        display_label, _ = format_zscore_level_display(
            str(level_result.get("level_id") or ""),
            level_label_map,
        )
        evidence_rows.append(
            {
                "水平": display_label,
                input_value_type_label: format_optional_input_value(level_result.get("raw_value")),
                "当前状态": get_zscore_level_current_status_label(level_result),
                "手工处理": get_outlier_manual_status_label(level_result.get("manual_status")),
                "参与参数建立统计": "是" if int(level_result.get("is_building_included", 1) or 0) == 1 else "否",
                "G": format_optional_float(level_result.get("grubbs_statistic"), digits=4),
                "G临界值": format_optional_float(level_result.get("grubbs_threshold"), digits=4),
                "状态": format_zscore_status_label(level_result.get("status", PHASE_TARGET_BUILDING)),
                "触发规则": format_zscore_level_rule_hits(level_result.get("rule_hits_local", [])),
            }
        )
    return pd.DataFrame(evidence_rows)


def _extract_zscore_rule_id(rule_hit: Any) -> str:
    if isinstance(rule_hit, dict):
        return str(rule_hit.get("rule_id") or "").strip()
    return str(rule_hit or "").strip()


def _build_zscore_rule_summary(
    formal_runs: list[dict[str, Any]],
    rule_ids: list[str],
) -> dict[str, int]:
    summary = {rule_id: 0 for rule_id in rule_ids}
    summary["warning_count"] = 0
    summary["reject_count"] = 0
    for run in formal_runs:
        run_status = str(run.get("run_status") or "").strip()
        if run_status == "warning":
            summary["warning_count"] += 1
        elif run_status == "reject":
            summary["reject_count"] += 1

        hit_rule_ids = {
            rule_id
            for rule_id in (
                _extract_zscore_rule_id(rule_hit)
                for rule_hit in run.get("rule_hits_run", []) or []
            )
            if rule_id
        }
        for rule_id in hit_rule_ids:
            if rule_id in summary:
                summary[rule_id] += 1
    return summary


def _format_zscore_level_result_summary(
    run: dict[str, Any],
    level_label_map: dict[str, str],
) -> str:
    summary_parts: list[str] = []
    for level_result in sorted(
        run.get("level_results", []),
        key=lambda item: str(item.get("level_id") or ""),
    ):
        level_label, _ = format_zscore_level_display(
            str(level_result.get("level_id") or ""),
            level_label_map,
        )
        value_text = format_optional_input_value(level_result.get("raw_value"))
        zscore_text = format_optional_float(level_result.get("zscore"))
        status_text = format_zscore_status_label(level_result.get("status", "pending"))
        summary_parts.append(
            f"{level_label}: 值={value_text}, Z={zscore_text}, 状态={status_text}"
        )
    return "\n".join(summary_parts)


def _build_zscore_abnormal_records_dataframe(
    formal_runs: list[dict[str, Any]],
    level_label_map: dict[str, str],
) -> pd.DataFrame:
    abnormal_rows: list[dict[str, Any]] = []
    for run in formal_runs:
        if str(run.get("run_status") or "") not in {"warning", "reject"}:
            continue
        abnormal_rows.append(
            {
                "检测序号": get_zscore_display_sequence(run),
                "检测时间": _format_zscore_export_datetime(run.get("test_time")),
                "检测人": str(run.get("operator", "") or ""),
                "本次判定结果": format_zscore_status_label(run.get("run_status", "pending")),
                "触发规则": format_zscore_rule_hits(run.get("rule_hits_run", [])),
                "误差类型": format_error_type_label(run.get("error_type_hint", "unknown")),
                "分析提示": display_generated_text(str(run.get("analysis_prompt", "") or "")),
                "手动备注": str(run.get("manual_note", "") or ""),
                "各水平结果摘要": _format_zscore_level_result_summary(run, level_label_map),
            }
        )
    return pd.DataFrame(abnormal_rows)


def _build_zscore_run_records_dataframe(
    history_runs: list[dict[str, Any]],
    required_level_ids: list[str],
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for run in history_runs:
        level_results_by_id = {
            str(level_result.get("level_id")): level_result
            for level_result in run.get("level_results", [])
        }
        row: dict[str, Any] = {
            "检测序号": get_zscore_display_sequence(run),
            "阶段": display_generated_text(str(run.get("phase_label") or get_phase_label(run.get("phase")))),
            "检测时间": _format_zscore_export_datetime(run.get("test_time")),
            "检测人": str(run.get("operator", "") or ""),
            "本次判定结果": format_zscore_status_label(run.get("run_status", "pending")),
            "触发规则": format_zscore_rule_hits(run.get("rule_hits_run", [])),
            "误差类型": format_error_type_label(run.get("error_type_hint", "unknown")),
            "分析提示": display_generated_text(str(run.get("analysis_prompt", "") or "")),
            "备注": str(run.get("manual_note", "") or ""),
        }
        for level_id in required_level_ids:
            level_prefix = _build_zscore_export_level_prefix(level_id)
            level_result = level_results_by_id.get(level_id, {})
            row[f"{level_prefix} 值"] = _format_zscore_export_numeric(level_result.get("raw_value"))
            row[f"{level_prefix} Z值"] = _format_zscore_export_numeric(level_result.get("zscore"))
            row[f"{level_prefix} 状态"] = (
                format_zscore_status_label(level_result.get("status", "pending"))
                if level_result
                else ""
            )
        rows.append(row)
    return pd.DataFrame(rows)


def filter_zscore_record_runs(history_runs: list[dict[str, Any]], record_filter: str) -> list[dict[str, Any]]:
    """One run identity drives every view and action, including filtered lists."""
    rows = [run for run in history_runs if run.get('run_id', run.get('id')) is not None]
    if record_filter == '参数建立期':
        rows = [run for run in rows if run.get('phase') == PHASE_TARGET_BUILDING]
    elif record_filter == '正式期':
        rows = [run for run in rows if run.get('phase') == PHASE_FORMAL_QC]
    elif record_filter == '警告 / 失控':
        rows = [run for run in rows if run.get('phase') == PHASE_FORMAL_QC and run.get('run_status') in ('warning', 'reject')]
    elif record_filter == '已禁用':
        rows = [run for run in rows if build_zscore_building_run_status_summary(run)['any_excluded']]
    return sorted(rows, key=lambda run: (pd.Timestamp(run['test_time']), int(run.get('run_id', run.get('id')))), reverse=True)


def render_zscore_rule_records_overview_section(context: dict[str, object]) -> None:
    from database import read_snapshot
    from services.lot_lifecycle_service import get_result_edit_availability, require_writable
    from ui.record_workspace import render_record_selection
    from ui.record_actions import render_record_toolbar
    batch_id = int(context['batch']['id'])
    history_runs = list(context['history_runs'])
    scope = f'zscore_records_{batch_id}'
    filter_key = scope + '_filter'
    widget_key = scope + '_filter_widget'
    filters = ['全部', '参数建立期', '正式期', '警告 / 失控', '已禁用']
    if widget_key not in st.session_state:
        st.session_state[widget_key] = st.session_state.get(filter_key, '全部')
    def remember_filter():
        st.session_state[filter_key] = st.session_state[widget_key]
    with st.container(border=True):
        st.markdown('**检测记录**')
        current_filter = st.selectbox('记录范围', filters, key=widget_key, on_change=remember_filter)
        st.session_state[filter_key] = current_filter
        runs = filter_zscore_record_runs(history_runs, current_filter)
        display = _build_zscore_run_records_dataframe(runs, list(context['required_level_ids']))
        if not display.empty:
            display = display.drop(columns=['分析提示', '误差类型', '备注',
                                           *[column for column in display if column.endswith((' Z值', ' 状态'))]])
            display = display.rename(columns={
                f"{_build_zscore_export_level_prefix(level_id)} 值":
                f"{format_zscore_level_display(level_id, context['level_label_map'])[0]} 值"
                for level_id in context['required_level_ids']
            })
            display['维护状态'] = [build_zscore_building_run_status_summary(run)['current_status_label']
                                  if run.get('phase') == PHASE_TARGET_BUILDING and not context['formal_rules_enabled'] else '只读' for run in runs]
        record_ids = [int(run.get('run_id', run.get('id'))) for run in runs]
        selected_id = render_record_selection(display, record_ids, scope=scope)
        selected = next((run for run in runs if int(run.get('run_id', run.get('id'))) == selected_id), None)
        try:
            with read_snapshot() as connection:
                require_writable(connection, 'zscore', batch_id, read_only=True)
            writable = True
        except ValueError:
            writable = False
        summary = build_zscore_building_run_status_summary(selected) if selected else None
        included = None if not summary or summary['has_inconsistent_status'] else not summary['all_excluded']
        allow_status = bool(selected and writable and not context['formal_rules_enabled']
                            and selected.get('phase') == PHASE_TARGET_BUILDING)
        availability = get_result_edit_availability('zscore', selected_id) if selected else {'allowed': False, 'reason': ''}
        render_record_toolbar('zscore', batch_id, selected_id,
                              allow_edit=bool(writable and availability['allowed'] and not context['formal_rules_enabled']
                                              and not (selected or {}).get('is_locked_for_maintenance')), allow_status=allow_status,
                              included=included,
                              abnormal=bool(selected and selected.get('phase') == PHASE_FORMAL_QC
                                            and selected.get('run_status') in ('warning', 'reject')))
        if selected and not availability['allowed'] and availability.get('reason'):
            st.caption(availability['reason'])
        if summary and summary['has_inconsistent_status']:
            st.caption('各水平维护状态不一致；保留、禁用或恢复会同时应用于整次检测的全部水平。')
        formal_runs = [run for run in history_runs if run.get('phase') == PHASE_FORMAL_QC]
        rule_summary = _build_zscore_rule_summary(formal_runs, list(context['template']['rule_ids']))
        st.caption(f"本批次共 {len(history_runs)} 次检测｜警告 {rule_summary['warning_count']} 次｜失控 {rule_summary['reject_count']} 次")
        with st.expander('规则汇总与说明', expanded=False):
            st.dataframe(pd.DataFrame([{'规则': format_rule_code(rule_id), '命中次数': rule_summary[rule_id],
                                      '规则说明': format_rule_description(rule_id)}
                                     for rule_id in context['template']['rule_ids']]), hide_index=True, width='stretch')


def render_zscore_record_maintenance_entry(
    history_runs: list[dict[str, Any]],
    batch_context: dict[str, Any],
    *,
    caption_text: str | None = None,
) -> None:
    """Compatibility wrapper: the record list owns selection and maintenance."""
    if caption_text:
        st.caption(caption_text)
    from ui.record_actions import render_pending_record_dialog
    render_pending_record_dialog('zscore', int(batch_context['batch']['id']))

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

    from services.report_period_service import resolve_report_period, filter_frame_to_period
    period = resolve_report_period(start_date=start_date, end_date=end_date)
    filtered_df = filter_frame_to_period(formal_df, period)
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
            "本次检测判定结果",
                "触发规则",
                "误差类型",
                "分析提示",
                "备注",
                "阶段",
            ]
        )

    export_columns.extend(["实际试剂批号","参数版本ID","上下文ID"])
    rows: list[dict[str, Any]] = []
    for run in export_runs:
        level_results_by_id = {
            str(level_result.get("level_id")): level_result
            for level_result in run.get("level_results", [])
        }
        row: dict[str, Any] = {
            "实际试剂批号":run.get("actual_reagent_lot","未记录"),"参数版本ID":run.get("target_profile_id"),"上下文ID":run.get("context_id"),
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
            row["本次检测判定结果"] = format_zscore_status_label(run.get("run_status", "pending"))
            row["触发规则"] = format_zscore_rule_hits(run.get("rule_hits_run", []))
            row["误差类型"] = format_error_type_label(run.get("error_type_hint", "unknown"))
            row["分析提示"] = display_generated_text(str(run.get("analysis_prompt", "") or ""))
        row["备注"] = str(run.get("manual_note", "") or "")
        row["阶段"] = display_generated_text(str(run.get("phase_label") or get_phase_label(run.get("phase"))))
        rows.append(row)

    return pd.DataFrame(rows, columns=export_columns)


def _dataframe_signature(dataframe: pd.DataFrame) -> tuple:
    if dataframe.empty:
        return (0, None, None)
    id_column = "run_id" if "run_id" in dataframe.columns else "id"
    max_id = int(dataframe[id_column].max()) if id_column in dataframe.columns else len(dataframe)
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
    with profile_timer("build_zscore_workbench_context", batch_id=selected_batch_id):
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
        overall_phase_label = display_generated_text(get_phase_label(overall_phase))
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
    from pages.lot_lifecycle_section import render_result_lot_choice,is_batch_writable
    if not is_batch_writable("zscore",selected_batch_id):
        return
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
        f"请填写 {level_count} 个水平的{input_value_type_label}，保存后查看质控图和检测结论。"
        f"所用规则：{format_zscore_template_display_name(template)}。"
    )
    if cv_limit is not None:
        st.caption(f"当前批次已保存允许不精密度（CV）：≤ {cv_limit:.2f}%")

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
            lot_selection=render_result_lot_choice("zscore",selected_batch_id,test_time,"zscore_entry")
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
                        lot_selection=lot_selection,
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
                        ("参数建立要求", f"{profile['required_n']} 次"),
                        ("已达参数建立条件", "是" if profile["is_ready"] else "否"),
                        ("当前阶段", display_generated_text(profile["phase_label"])),
                    ]
                )
                render_zscore_profile_stat_line(
                    "参数建立统计",
                    profile.get("provisional_mean"),
                    profile.get("provisional_sd"),
                    profile.get("provisional_cv"),
                )
                render_cv_limit_hint(
                    profile.get("provisional_cv"),
                    cv_limit,
                    f"{display_label} 均值和标准差建立",
                )
                render_zscore_profile_stat_line(
                    "正式控制参数",
                    profile.get("final_target_mean"),
                    profile.get("final_target_sd"),
                    profile.get("final_target_cv"),
                )
                render_cv_limit_hint(profile.get("final_target_cv"), cv_limit, f"{display_label} 正式控制参数")
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
    from ui.zscore_reference_dialog import render_zscore_reference_summary
    labels = {level_id: format_zscore_level_display(level_id, context['level_label_map'])[0]
              for level_id in context['required_level_ids']}
    render_zscore_reference_summary(selected_batch_id, context['template_id'], context['required_n'],
                                   context['level_target_profiles'], context['required_level_ids'], labels)


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
        "building": "参数建立期图",
        "formal": "正式质控图",
        "all": "全图",
    }[phase_scope]
    if view_mode == "单水平视图":
        figure = plot_zscore_single_level(
            plot_df=plot_df,
            level_id=selected_level,
            title=f"{phase_title} · {format_level_id_display(selected_level)}",
            phase_scope=phase_scope,
            y_axis_mode=y_axis_mode,
            standard_sd_limit=standard_sd_limit,
            y_axis_label=input_value_type_label,
        )
    else:
        figure = plot_zscore_overlay(
            plot_df=plot_df,
            title=f"{phase_title} · 多水平合并图",
            active_levels=required_level_ids,
            phase_scope=phase_scope,
            y_axis_mode=y_axis_mode,
            standard_sd_limit=standard_sd_limit,
            y_axis_label=input_value_type_label,
        )
    with st.container(border=True):
        st.markdown("**质控图**")
        if view_mode == "单水平视图":
            st.caption(format_zscore_level_display(selected_level, level_label_map)[0])
        st.pyplot(figure, clear_figure=False, width="stretch")
    with st.container(border=True):
        render_zscore_latest_analysis_panel(latest_run, overall_phase, formal_rules_enabled)

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
        "current_png_bytes": b"",
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
    """Compatibility wrapper for the unified record list and action toolbar."""
    render_zscore_rule_records_overview_section(context)


def render_zscore_export_import_section(context, selected_batch_id, figure, chart_panel_state):
    from ui.data_transfer import render_transfer_toolbar
    with profile_timer("render_zscore_export_import_section", batch_id=selected_batch_id):
        render_transfer_toolbar('zscore', context, selected_batch_id, chart_panel_state)
