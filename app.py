from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from html import escape as html_escape
from io import BytesIO
import math
from string import ascii_uppercase
from textwrap import dedent
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from database import (
    add_result,
    create_batch,
    create_project,
    create_zscore_batch,
    create_zscore_project,
    delete_result,
    export_batch_results,
    get_batch,
    get_project,
    get_results,
    get_zscore_batch,
    get_zscore_project,
    init_db,
    list_batches,
    list_projects,
    list_zscore_batches,
    list_zscore_projects,
    update_result,
    update_batch,
    update_project,
)
from plotting import figure_to_png_bytes, plot_lj_chart
from qc_logic import calculate_qc_results, calculate_realtime_stats, format_stats_message
from zscore_logic import (
    PHASE_FORMAL_QC,
    PHASE_TARGET_BUILDING,
    build_zscore_maintenance_dialog_state,
    build_zscore_batch_summary_items,
    build_zscore_plot_dataframe as build_zscore_plot_dataframe_logic,
    create_zscore_run,
    delete_saved_zscore_run,
    determine_zscore_phase,
    format_level_id_display,
    format_zscore_level_label_summary,
    get_building_stat_run_ids,
    get_zscore_display_sequence,
    get_zscore_level_label_map,
    get_zscore_level_targets,
    get_phase_label,
    get_zscore_runs,
    resolve_zscore_batch_context,
    sort_zscore_runs_for_maintenance,
    should_enable_formal_rules,
    update_saved_zscore_run,
    upsert_zscore_level_target,
)
from zscore_plotting import plot_zscore_overlay, plot_zscore_single_level


PAGE_TITLE = "实验室室内质控管理工具"
TEXT = {
    "app_title": "实验室室内质控管理工具",
    "manage": "\u9879\u76ee\u4e0e\u6279\u6b21\u7ba1\u7406",
    "current_batch": "\u5f53\u524d\u6279\u6b21",
    "no_project": "\u5f53\u524d\u8fd8\u6ca1\u6709\u9879\u76ee\uff0c\u8bf7\u5148\u521b\u5efa\u9879\u76ee\u3002",
    "choose_project": "\u8bf7\u5148\u9009\u62e9\u9879\u76ee\u3002",
    "choose_batch": "\u8bf7\u5148\u9009\u62e9\u6279\u6b21\u3002",
    "no_batch": "\u5f53\u524d\u9879\u76ee\u4e0b\u8fd8\u6ca1\u6709\u6279\u6b21\uff0c\u8bf7\u5148\u521b\u5efa\u6279\u6b21\u3002",
    "fill_project": "\u8bf7\u586b\u5199\u9879\u76ee\u540d\u79f0\u3002",
    "fill_batch": "\u8bf7\u5b8c\u6574\u586b\u5199\u6279\u6b21\u4fe1\u606f\u3002",
}

DISPLAY_COLUMN_LABELS = {
    "id": "\u7f16\u53f7",
    "name": "\u9879\u76ee\u540d\u79f0",
    "created_at": "\u521b\u5efa\u65f6\u95f4",
    "project_id": "\u9879\u76ee\u7f16\u53f7",
    "project_name": "\u9879\u76ee\u540d\u79f0",
    "instrument": "\u4eea\u5668",
    "reagent": "\u8bd5\u5242",
    "qc_material": "\u8d28\u63a7\u54c1",
    "concentration": "\u6d53\u5ea6",
    "lot_no": "\u8d28\u63a7\u54c1\u6279\u53f7",
    "target_n": "\u5efa\u9776\u6240\u9700\u6b21\u6570",
    "level_1_label": "水平 1 说明",
    "level_2_label": "水平 2 说明",
    "level_3_label": "水平 3 说明",
    "level_count": "水平数",
}

ZSCORE_TEMPLATE_DISPLAY_NAMES = {
    "2_level_classic": "两水平经典多规则组合",
    "3_level_threes": "三水平多规则组合",
    "2-level classic": "两水平经典多规则组合",
    "3-level threes": "三水平多规则组合",
}

RULE_DISPLAY_NAMES = {
    "10_x": "10x",
    "12_x": "12x",
}

RULE_DESCRIPTION_MAP = {
    "1_2s": "单次结果超过均值 ±2SD，提示警告。",
    "1_3s": "单次结果超过均值 ±3SD，判定失控。",
    "2_2s": "连续 2 次结果位于均值同侧且均超过 2SD，判定失控。",
    "R_4s": "连续 2 次结果差值超过 4SD，提示随机误差风险。",
    "4_1s": "连续 4 次结果位于均值同侧且均超过 1SD，提示系统误差风险。",
    "10x": "连续 10 次结果位于均值同侧，提示系统偏移风险。",
    "10_x": "连续 10 次结果位于均值同侧，提示系统偏移风险。",
    "2of3_2s": "同一次多水平结果中有 2 个水平位于均值同侧且超过 2SD，判定失控。",
    "3_1s": "同一次三水平结果均位于均值同侧且超过 1SD，提示系统误差风险。",
    "12x": "连续 12 次结果位于均值同侧，提示系统偏移风险。",
    "12_x": "连续 12 次结果位于均值同侧，提示系统偏移风险。",
}

ZSCORE_STATUS_LABELS = {
    "accept": "在控",
    "warning": "警告",
    "reject": "失控",
    "pending": "待判读",
    PHASE_TARGET_BUILDING: "建靶期观察",
}

ERROR_TYPE_LABELS = {
    "random": "随机误差风险",
    "systematic": "系统误差风险",
    "shift": "系统偏移风险",
    "trend": "趋势性漂移风险",
    "mixed": "混合误差风险",
    "not_applicable": "建靶阶段不适用",
    "unknown": "待进一步判断",
}

ZSCORE_PHASE_VIEW_OPTIONS = {
    "building": "建靶期图",
    "formal": "正式质控图",
    "all": "全图",
}


ZSCORE_Y_AXIS_OPTIONS = ["标准视图", "全范围视图"]

st.set_page_config(page_title=PAGE_TITLE, layout="wide")
init_db()

st.markdown(
    """
    <style>
    div.stButton > button[kind="primary"] {
        background: #184d8d;
        border: 1px solid #184d8d;
        color: #ffffff;
        font-weight: 600;
    }
    div.stButton > button[kind="primary"]:hover {
        background: #123d70;
        border-color: #123d70;
        color: #ffffff;
    }
    div.stButton > button[kind="primary"]:focus {
        box-shadow: 0 0 0 0.2rem rgba(24, 77, 141, 0.18);
    }
    .top-feedback-bar {
        display: flex;
        justify-content: flex-end;
        align-items: center;
        margin: -0.2rem 0 0.25rem 0;
    }
    .top-feedback-link {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        min-height: 34px;
        padding: 0 14px;
        border-radius: 9px;
        background: #184d8d;
        border: 1px solid #184d8d;
        color: #ffffff !important;
        text-decoration: none !important;
        font-size: 13px;
        font-weight: 600;
        line-height: 1;
        transition: background-color 0.18s ease, border-color 0.18s ease;
    }
    .top-feedback-link:hover {
        background: #123d70;
        border-color: #123d70;
        color: #ffffff !important;
    }
    .top-feedback-link:visited,
    .top-feedback-link:focus,
    .top-feedback-link:active {
        color: #ffffff !important;
        text-decoration: none !important;
    }
    .compact-summary-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(112px, 1fr));
        gap: 10px;
        margin-top: 6px;
    }
    .compact-summary-card {
        border: 1px solid #d9dde7;
        border-radius: 10px;
        background: #f8fafc;
        padding: 10px 12px;
    }
    .compact-summary-label {
        font-size: 12px;
        color: #5e6b7d;
        line-height: 1.35;
    }
    .compact-summary-value {
        margin-top: 4px;
        font-size: 22px;
        font-weight: 700;
        color: #1f2d3d;
        line-height: 1.1;
    }
    .stat-card-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(110px, 1fr));
        gap: 8px;
        margin-top: 4px;
    }
    .stat-card {
        border: 1px solid #dde3ec;
        border-radius: 10px;
        background: #fbfcfe;
        padding: 8px 10px;
    }
    .stat-card-label {
        font-size: 11px;
        color: #6b778a;
        line-height: 1.3;
    }
    .stat-card-value {
        margin-top: 3px;
        font-size: 18px;
        font-weight: 700;
        color: #1f2d3d;
        line-height: 1.1;
    }
    .batch-summary-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(118px, 1fr));
        gap: 8px;
        margin-top: 4px;
        margin-bottom: 2px;
    }
    .batch-summary-item {
        border: 1px solid #e2e7ef;
        border-radius: 8px;
        background: #fcfdff;
        padding: 5px 8px;
    }
    .batch-summary-label {
        font-size: 10.5px;
        color: #6a7688;
        line-height: 1.2;
    }
    .batch-summary-value {
        margin-top: 3px;
        font-size: 15px;
        font-weight: 700;
        color: #233246;
        line-height: 1.2;
        word-break: break-word;
    }
    .zscore-summary-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(112px, 1fr));
        gap: 6px;
        margin: 2px 0 10px 0;
        align-items: stretch;
    }
    .zscore-summary-item {
        border: 1px solid #dde5ef;
        border-radius: 8px;
        background: #f8fbff;
        padding: 4px 7px;
        min-height: 46px;
    }
    .zscore-summary-label {
        font-size: 10px;
        color: #6c788a;
        line-height: 1.15;
    }
    .zscore-summary-value {
        margin-top: 2px;
        font-size: 13px;
        font-weight: 700;
        color: #233246;
        line-height: 1.15;
        word-break: break-word;
    }
    @media (max-width: 1680px) {
        .batch-summary-grid {
            grid-template-columns: repeat(auto-fit, minmax(108px, 1fr));
            gap: 7px;
        }
        .compact-summary-grid {
            grid-template-columns: repeat(auto-fit, minmax(104px, 1fr));
            gap: 8px;
        }
        .zscore-summary-grid {
            grid-template-columns: repeat(auto-fit, minmax(104px, 1fr));
        }
    }
    @media (max-width: 1280px) {
        .batch-summary-grid {
            grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
        }
        .stat-card-grid {
            grid-template-columns: repeat(auto-fit, minmax(96px, 1fr));
        }
        .compact-summary-grid {
            grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
        }
        .zscore-summary-grid {
            grid-template-columns: repeat(auto-fit, minmax(132px, 1fr));
        }
    }
    .welcome-chip-row {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
        margin-top: 12px;
    }
    .welcome-chip {
        border: 1px solid #d7e1ec;
        border-radius: 999px;
        background: rgba(255, 255, 255, 0.85);
        padding: 6px 12px;
        font-size: 12px;
        font-weight: 600;
        color: #2f4e6e;
        line-height: 1.2;
    }
    .main-entry-card {
        border: 1px solid #dce5ef;
        border-radius: 16px;
        background: linear-gradient(180deg, #ffffff 0%, #f8fbff 100%);
        padding: 16px 16px 14px 16px;
        min-height: 236px;
        box-shadow: 0 4px 14px rgba(26, 59, 96, 0.05);
    }
    .main-entry-card-title {
        font-size: 22px;
        font-weight: 800;
        color: #1b3553;
        line-height: 1.2;
    }
    .main-entry-card-caption {
        margin-top: 8px;
        font-size: 13px;
        color: #48627d;
        line-height: 1.6;
    }
    .main-entry-card-list {
        margin-top: 10px;
        padding-left: 18px;
        color: #34495f;
        font-size: 13px;
        line-height: 1.7;
    }
    .main-highlight-box {
        border: 1px solid #dbe4ef;
        border-radius: 14px;
        background: #f8fbff;
        padding: 14px 16px;
        margin: 10px 0 0 0;
    }
    .main-highlight-title {
        font-size: 13px;
        font-weight: 700;
        color: #24476d;
        margin-bottom: 8px;
    }
    .main-highlight-body {
        font-size: 13px;
        line-height: 1.7;
        color: #44586d;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def _clean_selector_label_part(value, fallback: str) -> str:
    cleaned_value = " ".join(str(value or "").split()).strip()
    return cleaned_value or fallback


def build_project_label(row: pd.Series) -> str:
    row = dict(row)
    project_name = _clean_selector_label_part(row.get("name"), "\u672a\u547d\u540d\u9879\u76ee")
    return f"\u9879\u76ee {row['id']} | {project_name}"


def build_batch_label(row: pd.Series) -> str:
    row = dict(row)
    instrument = _clean_selector_label_part(row.get("instrument"), "\u672a\u586b\u5199\u4eea\u5668")
    reagent = _clean_selector_label_part(row.get("reagent"), "\u672a\u586b\u5199\u8bd5\u5242")
    qc_material = _clean_selector_label_part(row.get("qc_material"), "\u672a\u586b\u5199\u8d28\u63a7\u54c1")
    concentration = _clean_selector_label_part(row.get("concentration"), "\u672a\u586b\u5199\u6d53\u5ea6")
    lot_no = _clean_selector_label_part(row.get("lot_no"), "\u672a\u586b\u5199")
    return (
        f"\u6279\u6b21 {row['id']} | {instrument} | {reagent} | "
        f"{qc_material} | {concentration} | \u8d28\u63a7\u6279\u53f7 {lot_no}"
    )


def build_zscore_project_label(row: pd.Series) -> str:
    row = dict(row)
    project_name = _clean_selector_label_part(row.get("name"), "未命名项目")
    level_count = int(row.get("level_count", 0) or 0)
    return f"项目 {row['id']} | {project_name} | {level_count} 水平"


def build_zscore_batch_label(row: pd.Series) -> str:
    row = dict(row)
    instrument = _clean_selector_label_part(row.get("instrument"), "未填写仪器")
    reagent = _clean_selector_label_part(row.get("reagent"), "未填写试剂")
    lot_no = _clean_selector_label_part(row.get("lot_no"), "未填写")
    level_count = int(row.get("level_count", 0) or 0)
    return (
        f"批次 {row['id']} | {level_count} 水平 | {instrument} | "
        f"{reagent} | 质控批号 {lot_no}"
    )


def format_zscore_template_display_name(template_or_label: Any) -> str:
    if isinstance(template_or_label, dict):
        template_key = str(template_or_label.get("template_id") or template_or_label.get("label") or "").strip()
        fallback = str(template_or_label.get("label") or template_key)
    else:
        template_key = str(template_or_label or "").strip()
        fallback = template_key
    return ZSCORE_TEMPLATE_DISPLAY_NAMES.get(template_key, ZSCORE_TEMPLATE_DISPLAY_NAMES.get(fallback, fallback or "规则组合"))


def format_rule_code(rule_id: str) -> str:
    normalized_rule_id = str(rule_id or "").strip()
    return RULE_DISPLAY_NAMES.get(normalized_rule_id, normalized_rule_id)


def format_rule_description(rule_id: str) -> str:
    normalized_rule_id = str(rule_id or "").strip()
    return RULE_DESCRIPTION_MAP.get(normalized_rule_id, "当前版本已启用该规则，请结合本页判读说明使用。")


def format_zscore_status_label(status: Any) -> str:
    normalized_status = str(status or "").strip()
    return ZSCORE_STATUS_LABELS.get(normalized_status, normalized_status or "状态未知")


def format_error_type_label(error_type_hint: Any) -> str:
    normalized_hint = str(error_type_hint or "").strip()
    return ERROR_TYPE_LABELS.get(normalized_hint, normalized_hint or "待进一步判断")


def format_datetime_column(dataframe: pd.DataFrame, column_name: str) -> pd.DataFrame:
    formatted = dataframe.copy()
    if not formatted.empty and column_name in formatted.columns:
        formatted[column_name] = pd.to_datetime(formatted[column_name]).dt.strftime("%Y-%m-%d %H:%M:%S")
    return formatted


def localize_dataframe_columns(dataframe: pd.DataFrame) -> pd.DataFrame:
    return dataframe.rename(columns={column: DISPLAY_COLUMN_LABELS.get(column, column) for column in dataframe.columns})


def render_html_block(html: str) -> None:
    html_content = dedent(html).strip()
    if hasattr(st, "html"):
        st.html(html_content)
    else:
        st.markdown(html_content, unsafe_allow_html=True)


def render_table_html(html: str, row_count: int) -> None:
    html_content = dedent(html).strip()
    if hasattr(st, "html"):
        st.html(html_content)
        return

    estimated_height = min(max(220, 92 + (row_count + 1) * 42), 920)
    components.html(html_content, height=estimated_height, scrolling=row_count > 10)


def build_safe_export_name(text: str | None, fallback: str) -> str:
    cleaned_text = str(text or "").strip()
    safe_characters: list[str] = []
    unsafe_characters = set('<>:"/\\|?*')
    for character in cleaned_text:
        if character.isspace():
            safe_characters.append("_")
        elif character.isalnum() or character in ("-", "_"):
            safe_characters.append(character)
        elif character in unsafe_characters:
            safe_characters.append("_")
        else:
            safe_characters.append("_")

    safe_text = "".join(safe_characters).strip("_")
    return safe_text or fallback


def switch_top_level_method(target_method: str) -> None:
    st.session_state["top_level_method_selector"] = target_method
    st.rerun()


def prepare_display_records(qc_df: pd.DataFrame) -> pd.DataFrame:
    display_df = qc_df.copy()
    if display_df.empty:
        return display_df

    display_df = format_datetime_column(display_df, "test_time")
    display_df["value"] = display_df["value"].map(lambda value: f"{float(value):.4f}")
    if "log_value" in display_df.columns:
        display_df["log_value"] = display_df["log_value"].map(
            lambda value: "" if pd.isna(value) else f"{float(value):.6f}"
        )
    display_df["z"] = display_df["z"].map(lambda value: "" if pd.isna(value) else f"{float(value):.3f}")
    if "analysis_prompt" in display_df.columns:
        display_df["analysis_prompt"] = display_df["analysis_prompt"].fillna("")
    if "rule_hits" in display_df.columns:
        display_df["rule_hits"] = display_df["rule_hits"].fillna("")
    if "error_type" in display_df.columns:
        display_df["error_type"] = display_df["error_type"].fillna("\u65e0")
    if "reagent_lot_changed" in display_df.columns:
        display_df["reagent_lot_changed"] = display_df["reagent_lot_changed"].map(
            lambda flag: "\u662f" if int(flag) == 1 else "\u5426"
        )

    preferred_columns = [
        "sequence",
        "test_time",
        "operator",
        "value",
        "log_value",
        "reagent_lot_changed",
        "z",
        "status",
        "rule_hits",
        "error_type",
        "analysis_prompt",
        "phase",
    ]
    column_mapping = {
        "sequence": "\u68c0\u6d4b\u5e8f\u53f7",
        "test_time": "\u68c0\u6d4b\u65f6\u95f4",
        "operator": "\u68c0\u6d4b\u4eba",
        "value": "\u68c0\u6d4b\u503c",
        "log_value": "log\u503c",
        "reagent_lot_changed": "\u8bd5\u5242\u6279\u53f7\u53d8\u66f4",
        "z": "Z\u503c",
        "status": "\u5224\u5b9a\u7ed3\u679c",
        "rule_hits": "\u89e6\u53d1\u89c4\u5219",
        "error_type": "\u8bef\u5dee\u7c7b\u578b",
        "analysis_prompt": "\u5206\u6790\u63d0\u793a",
        "phase": "\u9636\u6bb5",
    }
    ordered_columns = [column for column in preferred_columns if column in display_df.columns]
    return display_df[ordered_columns].rename(columns=column_mapping)


def ensure_selected_project(projects_df: pd.DataFrame) -> int | None:
    if projects_df.empty:
        st.session_state["selected_project_id"] = None
        st.session_state["project_selector"] = "\u8bf7\u9009\u62e9\u9879\u76ee"
        return None

    valid_ids = set(projects_df["id"].astype(int).tolist())
    current_id = st.session_state.get("selected_project_id")
    if current_id is not None and current_id not in valid_ids:
        st.session_state["selected_project_id"] = None
        st.session_state["project_selector"] = "\u8bf7\u9009\u62e9\u9879\u76ee"
        return None
    return None if current_id is None else int(current_id)


def ensure_selected_batch(batches_df: pd.DataFrame) -> int | None:
    if batches_df.empty:
        st.session_state["selected_batch_id"] = None
        st.session_state["batch_selector"] = "\u8bf7\u9009\u62e9\u6279\u6b21"
        return None

    valid_ids = set(batches_df["id"].astype(int).tolist())
    current_id = st.session_state.get("selected_batch_id")
    if current_id is not None and current_id not in valid_ids:
        st.session_state["selected_batch_id"] = None
        st.session_state["batch_selector"] = "\u8bf7\u9009\u62e9\u6279\u6b21"
        return None
    return None if current_id is None else int(current_id)


def guard_work_tab_selection(
    work_tab,
    selected_project_id: int | None,
    selected_batch_id: int | None,
) -> None:
    if selected_project_id is None:
        with work_tab:
            st.info(TEXT["choose_project"])
        st.stop()

    if selected_batch_id is None:
        with work_tab:
            st.info(TEXT["choose_batch"])
        st.stop()


def prepare_project_batch_context() -> tuple[pd.DataFrame, int | None, pd.DataFrame, int | None]:
    projects_df = list_projects()
    selected_project_id = ensure_selected_project(projects_df)
    batches_df = list_batches(selected_project_id) if selected_project_id is not None else pd.DataFrame()
    selected_batch_id = ensure_selected_batch(batches_df)
    return projects_df, selected_project_id, batches_df, selected_batch_id


def ensure_selected_zscore_project(projects_df: pd.DataFrame) -> int | None:
    if projects_df.empty:
        st.session_state["zscore_selected_project_id"] = None
        st.session_state["zscore_project_selector"] = "请选择 Z-score 项目"
        return None

    valid_ids = set(projects_df["id"].astype(int).tolist())
    current_id = st.session_state.get("zscore_selected_project_id")
    if current_id is not None and current_id not in valid_ids:
        st.session_state["zscore_selected_project_id"] = None
        st.session_state["zscore_project_selector"] = "请选择 Z-score 项目"
        return None
    return None if current_id is None else int(current_id)


def ensure_selected_zscore_batch(batches_df: pd.DataFrame) -> int | None:
    if batches_df.empty:
        st.session_state["zscore_selected_batch_id"] = None
        st.session_state["zscore_batch_selector"] = "请选择 Z-score 批次"
        return None

    valid_ids = set(batches_df["id"].astype(int).tolist())
    current_id = st.session_state.get("zscore_selected_batch_id")
    if current_id is not None and current_id not in valid_ids:
        st.session_state["zscore_selected_batch_id"] = None
        st.session_state["zscore_batch_selector"] = "请选择 Z-score 批次"
        return None
    return None if current_id is None else int(current_id)


def prepare_zscore_project_batch_context() -> tuple[pd.DataFrame, int | None, pd.DataFrame, int | None]:
    projects_df = list_zscore_projects()
    selected_project_id = ensure_selected_zscore_project(projects_df)
    batches_df = list_zscore_batches(selected_project_id) if selected_project_id is not None else pd.DataFrame()
    selected_batch_id = ensure_selected_zscore_batch(batches_df)
    return projects_df, selected_project_id, batches_df, selected_batch_id


def render_project_batch_management(
    manage_tab,
    projects_df: pd.DataFrame,
    selected_project_id: int | None,
    batches_df: pd.DataFrame,
    selected_batch_id: int | None,
) -> None:
    with manage_tab:
        top_left, top_right = st.columns([1, 1.4])

        with top_left:
            st.subheader("新建项目")
            with st.form("create_project_form", clear_on_submit=True):
                project_name = st.text_input("项目名称")
                project_submitted = st.form_submit_button("创建项目", width="stretch")

                if project_submitted:
                    if not project_name.strip():
                        st.error(TEXT["fill_project"])
                    else:
                        try:
                            project_id = create_project(project_name.strip())
                        except ValueError as exc:
                            st.error(str(exc))
                        else:
                            st.session_state["selected_project_id"] = project_id
                            st.success(f"项目 {project_id} 已创建。")
                            st.rerun()

            st.subheader("项目列表与选择")
            if projects_df.empty:
                st.info(TEXT["no_project"])
            else:
                project_labels, project_options = build_project_select_options(projects_df)
                sync_selector_state(
                    selector_key="project_selector",
                    selected_id_key="selected_project_id",
                    options_map=project_options,
                    placeholder=project_labels[0],
                )
                selected_project_label = st.selectbox(
                    "选择项目",
                    options=project_labels,
                    key="project_selector",
                )
                new_project_id = project_options[selected_project_label]
                if new_project_id != selected_project_id:
                    st.session_state["selected_project_id"] = new_project_id
                    st.session_state["selected_batch_id"] = None
                    st.session_state["batch_selector"] = "请选择批次"
                    st.rerun()

                project_table = localize_dataframe_columns(format_datetime_column(projects_df, "created_at"))
                st.dataframe(project_table, width="stretch", hide_index=True)

                if selected_project_id is not None:
                    current_project = get_project(selected_project_id)
                    with st.expander("编辑当前项目"):
                        with st.form("edit_project_form"):
                            edit_project_name = st.text_input(
                                "项目名称",
                                value=current_project["name"],
                            )
                            edit_project_submitted = st.form_submit_button(
                                "保存项目修改",
                                width="stretch",
                            )
                            if edit_project_submitted:
                                cleaned_name = edit_project_name.strip()
                                if not cleaned_name:
                                    st.error(TEXT["fill_project"])
                                else:
                                    try:
                                        update_project(selected_project_id, cleaned_name)
                                    except ValueError as exc:
                                        st.error(str(exc))
                                    else:
                                        st.success("项目名称已更新。")
                                        st.rerun()

        with top_right:
            st.subheader("新建批次")
            if selected_project_id is None:
                st.info(TEXT["choose_project"])
            else:
                current_project = get_project(selected_project_id)
                st.caption(f"当前批次将归属于项目：{current_project['name']}")
                with st.form("create_batch_form", clear_on_submit=True):
                    instrument = st.text_input("仪器")
                    reagent = st.text_input("试剂")
                    qc_material = st.text_input("质控品")
                    concentration = st.text_input("浓度")
                    lot_no = st.text_input("质控品批号")
                    target_n = st.selectbox(
                        "建靶所需次数",
                        options=list(range(5, 21)),
                        index=15,
                    )
                    create_submitted = st.form_submit_button("创建批次", width="stretch")

                    if create_submitted:
                        fields = [instrument, reagent, qc_material, concentration, lot_no]
                        if any(not field.strip() for field in fields):
                            st.error(TEXT["fill_batch"])
                        else:
                            try:
                                batch_id = create_batch(
                                    project_id=selected_project_id,
                                    instrument=instrument.strip(),
                                    reagent=reagent.strip(),
                                    qc_material=qc_material.strip(),
                                    concentration=concentration.strip(),
                                    lot_no=lot_no.strip(),
                                    target_n=int(target_n),
                                )
                            except ValueError as exc:
                                st.error(str(exc))
                            else:
                                st.session_state["selected_batch_id"] = batch_id
                                st.success(f"批次 {batch_id} 已创建。")
                                st.rerun()

            st.subheader("批次列表与选择")
            if selected_project_id is None:
                st.info(TEXT["choose_project"])
            elif batches_df.empty:
                st.info(TEXT["no_batch"])
            else:
                batch_labels, batch_options = build_batch_select_options(batches_df)
                sync_selector_state(
                    selector_key="batch_selector",
                    selected_id_key="selected_batch_id",
                    options_map=batch_options,
                    placeholder=batch_labels[0],
                )
                selected_batch_label = st.selectbox(
                    "选择批次",
                    options=batch_labels,
                    key="batch_selector",
                )
                new_batch_id = batch_options[selected_batch_label]
                if new_batch_id != selected_batch_id:
                    st.session_state["selected_batch_id"] = new_batch_id
                    st.rerun()

                batch_table = localize_dataframe_columns(format_datetime_column(batches_df, "created_at"))
                st.dataframe(batch_table, width="stretch", hide_index=True)

                if selected_batch_id is not None:
                    current_batch = get_batch(selected_batch_id)
                    with st.expander("编辑当前批次"):
                        st.markdown("**批次固定信息**")
                        st.text(f"仪器：{current_batch['instrument']}")
                        st.text(f"试剂：{current_batch['reagent']}")
                        st.text(f"质控品：{current_batch['qc_material']}")
                        st.text(f"浓度：{current_batch['concentration']}")
                        st.text(f"建靶所需次数：{current_batch['target_n']}")
                        st.markdown("**可编辑信息**")
                        with st.form("edit_batch_form"):
                            edit_lot_no = st.text_input(
                                "质控品批号",
                                value=current_batch["lot_no"],
                            )
                            edit_batch_submitted = st.form_submit_button(
                                "保存批次修改",
                                width="stretch",
                            )
                            if edit_batch_submitted:
                                if not edit_lot_no.strip():
                                    st.error("请填写质控品批号。")
                                else:
                                    update_batch(selected_batch_id, edit_lot_no.strip())
                                    st.success("批次质控品批号已更新。")
                                    st.rerun()


def render_zscore_project_batch_management(
    manage_tab,
    projects_df: pd.DataFrame,
    selected_project_id: int | None,
    batches_df: pd.DataFrame,
    selected_batch_id: int | None,
) -> None:
    with manage_tab:
        top_left, top_right = st.columns([1, 1.4])

        with top_left:
            st.subheader("新建 Z-score 项目")
            with st.form("create_zscore_project_form", clear_on_submit=True):
                project_name = st.text_input("项目名称")
                level_count = st.radio(
                    "项目水平数",
                    options=[2, 3],
                    horizontal=True,
                )
                project_submitted = st.form_submit_button("创建 Z-score 项目", width="stretch")

                if project_submitted:
                    if not project_name.strip():
                        st.error(TEXT["fill_project"])
                    else:
                        try:
                            project_id = create_zscore_project(project_name.strip(), int(level_count))
                        except ValueError as exc:
                            st.error(str(exc))
                        else:
                            st.session_state["zscore_selected_project_id"] = project_id
                            st.session_state["zscore_selected_batch_id"] = None
                            st.success(f"Z-score 项目 {project_id} 已创建。")
                            st.rerun()

            st.subheader("Z-score 项目列表与选择")
            if projects_df.empty:
                st.info("当前还没有 Z-score 项目，请先创建项目并确定双水平或三水平流程。")
            else:
                project_labels, project_options = build_zscore_project_select_options(projects_df)
                sync_selector_state(
                    selector_key="zscore_project_selector",
                    selected_id_key="zscore_selected_project_id",
                    options_map=project_options,
                    placeholder=project_labels[0],
                )
                selected_project_label = st.selectbox(
                    "选择 Z-score 项目",
                    options=project_labels,
                    key="zscore_project_selector",
                )
                new_project_id = project_options[selected_project_label]
                if new_project_id != selected_project_id:
                    st.session_state["zscore_selected_project_id"] = new_project_id
                    st.session_state["zscore_selected_batch_id"] = None
                    st.session_state["zscore_batch_selector"] = "请选择 Z-score 批次"
                    st.rerun()

                project_table = localize_dataframe_columns(format_datetime_column(projects_df, "created_at"))
                st.dataframe(project_table, width="stretch", hide_index=True)

                if selected_project_id is not None:
                    current_project = get_zscore_project(selected_project_id)
                    with st.expander("当前 Z-score 项目配置"):
                        st.text(f"项目名称：{current_project['name']}")
                        st.text(f"水平数：{int(current_project['level_count'])} 水平")
                        with st.form("edit_zscore_project_form"):
                            edit_project_name = st.text_input(
                                "项目名称",
                                value=current_project["name"],
                            )
                            edit_project_submitted = st.form_submit_button("保存项目修改", width="stretch")
                            if edit_project_submitted:
                                cleaned_name = edit_project_name.strip()
                                if not cleaned_name:
                                    st.error(TEXT["fill_project"])
                                else:
                                    try:
                                        update_project(selected_project_id, cleaned_name)
                                    except ValueError as exc:
                                        st.error(str(exc))
                                    else:
                                        st.success("项目名称已更新，水平数保持不变。")
                                        st.rerun()

        with top_right:
            st.subheader("新建 Z-score 批次")
            if selected_project_id is None:
                st.info("请先选择 Z-score 项目。")
            else:
                current_project = get_zscore_project(selected_project_id)
                project_level_count = int(current_project["level_count"])
                st.caption(
                    f"当前批次将归属于项目：{current_project['name']}｜固定为 {project_level_count} 水平。"
                )
                with st.form("create_zscore_batch_form", clear_on_submit=True):
                    instrument = st.text_input("仪器")
                    reagent = st.text_input("试剂")
                    qc_material = st.text_input("质控品")
                    concentration = st.text_input("浓度")
                    lot_no = st.text_input("质控品批号")
                    st.markdown("**各水平名称与说明**")
                    level_1_label = st.text_input("水平 1 说明", placeholder="例如：低值质控")
                    level_2_label = st.text_input("水平 2 说明", placeholder="例如：中值或高值质控")
                    level_3_label = None
                    if project_level_count == 3:
                        level_3_label = st.text_input("水平 3 说明", placeholder="例如：高值质控")
                    target_n = st.selectbox(
                        "建靶所需次数",
                        options=list(range(5, 21)),
                        index=15,
                    )
                    create_submitted = st.form_submit_button("创建 Z-score 批次", width="stretch")

                    if create_submitted:
                        fields = [instrument, reagent, qc_material, concentration, lot_no]
                        if any(not field.strip() for field in fields):
                            st.error(TEXT["fill_batch"])
                        else:
                            try:
                                batch_id = create_zscore_batch(
                                    project_id=selected_project_id,
                                    instrument=instrument.strip(),
                                    reagent=reagent.strip(),
                                    qc_material=qc_material.strip(),
                                    concentration=concentration.strip(),
                                    lot_no=lot_no.strip(),
                                    target_n=int(target_n),
                                    level_1_label=level_1_label,
                                    level_2_label=level_2_label,
                                    level_3_label=level_3_label,
                                )
                            except ValueError as exc:
                                st.error(str(exc))
                            else:
                                st.session_state["zscore_selected_batch_id"] = batch_id
                                st.success(f"Z-score 批次 {batch_id} 已创建。")
                                st.rerun()

            st.subheader("Z-score 批次列表与选择")
            if selected_project_id is None:
                st.info("请先选择 Z-score 项目。")
            elif batches_df.empty:
                st.info("当前项目下还没有 Z-score 批次，请先创建批次。")
            else:
                batch_labels, batch_options = build_zscore_batch_select_options(batches_df)
                sync_selector_state(
                    selector_key="zscore_batch_selector",
                    selected_id_key="zscore_selected_batch_id",
                    options_map=batch_options,
                    placeholder=batch_labels[0],
                )
                selected_batch_label = st.selectbox(
                    "选择 Z-score 批次",
                    options=batch_labels,
                    key="zscore_batch_selector",
                )
                new_batch_id = batch_options[selected_batch_label]
                if new_batch_id != selected_batch_id:
                    st.session_state["zscore_selected_batch_id"] = new_batch_id
                    st.rerun()

                batch_table = localize_dataframe_columns(format_datetime_column(batches_df, "created_at"))
                st.dataframe(batch_table, width="stretch", hide_index=True)

                if selected_batch_id is not None:
                    current_batch = get_zscore_batch(selected_batch_id)
                    with st.expander("当前 Z-score 批次配置"):
                        current_level_ids = list(resolve_zscore_batch_context(selected_batch_id)["required_level_ids"])
                        st.text(f"项目：{current_batch['project_name']}")
                        st.text(f"批次：{current_batch['id']}")
                        st.text(f"水平数：{int(current_batch['level_count'])} 水平")
                        st.text(f"水平说明：{format_zscore_level_label_summary(current_batch, current_level_ids)}")
                        st.text(f"建靶所需次数：{current_batch['target_n']}")
                        with st.form("edit_zscore_batch_form"):
                            edit_lot_no = st.text_input(
                                "质控品批号",
                                value=current_batch["lot_no"],
                            )
                            edit_batch_submitted = st.form_submit_button("保存批次修改", width="stretch")
                            if edit_batch_submitted:
                                if not edit_lot_no.strip():
                                    st.error("请填写质控品批号。")
                                else:
                                    update_batch(selected_batch_id, edit_lot_no.strip())
                                    st.success("批次质控品批号已更新，水平数保持不变。")
                                    st.rerun()


def compute_log10_display(value: float) -> tuple[str, float | None]:
    if math.isclose(value, 0.0, abs_tol=1e-12):
        return "", None
    if value < 0:
        return "", None
    log_value = math.log10(value)
    return f"{log_value:.6f}", log_value


def parse_numeric_input(raw_value: str | None) -> tuple[float | None, str, float | None]:
    text = (raw_value or "").strip()
    if not text:
        return None, "", None

    try:
        numeric_value = float(text)
    except ValueError:
        return None, "", None

    if not math.isfinite(numeric_value) or numeric_value <= 0:
        return None, "", None

    log_display, log_value = compute_log10_display(numeric_value)
    return numeric_value, log_display, log_value


def build_log10_hint(raw_value: str | None) -> str:
    text = (raw_value or "").strip()
    if not text:
        return "\u8bf7\u8f93\u5165\u6b63\u6570\uff0clog10 \u4f1a\u5728\u8f93\u5165\u8fc7\u7a0b\u4e2d\u5b9e\u65f6\u8ba1\u7b97\u3002"
    try:
        numeric_value = float(text)
    except ValueError:
        return "\u5f53\u524d\u4e0d\u662f\u6709\u6548\u6570\u5b57\uff0clog10 \u5df2\u6e05\u7a7a\u3002"

    if not math.isfinite(numeric_value):
        return "\u5f53\u524d\u4e0d\u662f\u6709\u6548\u6570\u5b57\uff0clog10 \u5df2\u6e05\u7a7a\u3002"
    if math.isclose(numeric_value, 0.0, abs_tol=1e-12):
        return "\u68c0\u6d4b\u503c\u4e3a 0 \u65f6\u65e0\u6cd5\u8ba1\u7b97 log10\uff0c\u7ed3\u679c\u5df2\u6e05\u7a7a\u3002"
    if numeric_value < 0:
        return "\u68c0\u6d4b\u503c\u4e3a\u8d1f\u6570\u65f6\u65e0\u6cd5\u8ba1\u7b97 log10\uff0c\u7ed3\u679c\u5df2\u6e05\u7a7a\u3002"
    return "\u5df2\u6309\u5f53\u524d\u8f93\u5165\u5b9e\u65f6\u8ba1\u7b97 log10\u3002"


def build_operator_options(results_df: pd.DataFrame) -> list[str]:
    if results_df.empty or "operator" not in results_df.columns:
        return []

    operators: list[str] = []
    for operator in reversed(results_df["operator"].fillna("").astype(str).tolist()):
        cleaned = operator.strip()
        if cleaned and cleaned not in operators:
            operators.append(cleaned)
    return operators


def build_zscore_operator_options(history_runs: list[dict[str, Any]]) -> list[str]:
    operators: list[str] = []
    for run in reversed(history_runs):
        cleaned = str(run.get("operator", "") or "").strip()
        if cleaned and cleaned not in operators:
            operators.append(cleaned)
    return operators


def format_optional_float(value: Any, digits: int = 4, suffix: str = "") -> str:
    try:
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return "-"
        numeric = float(value)
    except (TypeError, ValueError):
        return "-"
    if not math.isfinite(numeric):
        return "-"
    return f"{numeric:.{digits}f}{suffix}"


def format_optional_input_value(value: Any, digits: int = 4) -> str:
    try:
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return ""
        numeric = float(value)
    except (TypeError, ValueError):
        return ""
    if not math.isfinite(numeric):
        return ""
    return f"{numeric:.{digits}f}"


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


def render_status_panel(status: str, message: str, rule_hits: str = "\u65e0") -> None:
    palette = {
        "\u7b26\u5408\u8d28\u63a7": {
            "background": "#edf8ef",
            "border": "#59a14f",
            "text": "#1d5f2a",
            "badge": "#59a14f",
        },
        "\u8b66\u544a": {
            "background": "#fff6db",
            "border": "#edc948",
            "text": "#785b00",
            "badge": "#c89b00",
        },
        "\u5931\u63a7": {
            "background": "#fdeaea",
            "border": "#e15759",
            "text": "#8f1f28",
            "badge": "#c23b3d",
        },
    }
    style = palette.get(
        status,
        {
            "background": "#f3f6fb",
            "border": "#7a8ca5",
            "text": "#31445a",
            "badge": "#58708f",
        },
    )
    compact_message = (message or "\u6682\u65e0\u5206\u6790\u63d0\u793a\u3002").splitlines()[0].strip()
    html = dedent(
        f"""
        <div style="
            background:{style['background']};
            border:1px solid {style['border']};
            border-left:5px solid {style['border']};
            border-radius:10px;
            padding:10px 12px;
            margin:2px 0 6px 0;
        ">
            <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:6px;">
                <span style="
                    background:{style['badge']};
                    color:#ffffff;
                    font-size:12px;
                    font-weight:700;
                    border-radius:999px;
                    padding:3px 9px;
                ">{html_escape(status or '状态未知')}</span>
                <span style="color:{style['text']};font-size:12px;font-weight:600;">
                    触发规则：{html_escape(rule_hits or '无')}
                </span>
            </div>
            <div style="
                color:{style['text']};
                line-height:1.45;
                word-break:break-word;
                font-size:13px;
            ">{html_escape(compact_message)}</div>
        </div>
        """
    ).strip()
    render_html_block(html)


def render_standard_view_help(standard_sd_limit: float) -> None:
    st.caption(
        (
            f"标准视图按均值 ± {standard_sd_limit:g}SD 聚焦主要波动区间，超界点会用边界标记和原始值提示；"
            "切换到全范围视图可查看真实完整范围。"
        )
    )


def build_chart_control_summary(chart_view_mode: str, chart_y_axis_mode: str, standard_sd_limit: float) -> str:
    if chart_y_axis_mode == "\u6807\u51c6\u89c6\u56fe":
        range_text = f"\u00b1{standard_sd_limit:g}SD"
    else:
        range_text = "\u5168\u8303\u56f4"
    return f"\u5f53\u524d\uff1a{chart_view_mode}\uff5c{chart_y_axis_mode}\uff5c{range_text}"


def build_chart_control_title(chart_view_mode: str, chart_y_axis_mode: str, standard_sd_limit: float) -> str:
    return (
        "\u56fe\u8868\u63a7\u5236\uff08\u70b9\u51fb\u5c55\u5f00\uff09\uff5c"
        + build_chart_control_summary(chart_view_mode, chart_y_axis_mode, standard_sd_limit).replace("\u5f53\u524d\uff1a", "")
    )


def render_live_log10_panel(value_text: str, field_label: str, value_element_id: str, hint_element_id: str) -> None:
    _, log_display, _ = parse_numeric_input(value_text)
    hint_text = build_log10_hint(value_text)
    html = dedent(
        f"""
        <div style="
            border:1px solid #d6dbe4;
            border-radius:8px;
            background:#f8fafc;
            padding:10px 12px;
            margin:0 0 10px 0;
        ">
            <div style="font-size:12px;color:#5b677a;margin-bottom:4px;">log值（log10）</div>
            <div id="{value_element_id}" style="
                font-size:15px;
                font-weight:600;
                color:#1f2d3d;
                min-height:22px;
            ">{html_escape(log_display)}</div>
            <div id="{hint_element_id}" style="
                margin-top:6px;
                color:#66768a;
                font-size:12px;
                line-height:1.5;
            ">{html_escape(hint_text)}</div>
        </div>
        """
    ).strip()
    render_html_block(html)

    script = f"""
    <script>
    const fieldLabel = {field_label!r};
    const valueId = {value_element_id!r};
    const hintId = {hint_element_id!r};

    function computeLog10(raw) {{
      const text = (raw || '').trim();
      if (!text) {{
        return {{
          value: '',
          hint: '请输入正数，log10 会在输入过程中实时计算。'
        }};
      }}

      const numberValue = Number(text);
      if (!Number.isFinite(numberValue)) {{
        return {{
          value: '',
          hint: '当前不是有效数字，log10 已清空。'
        }};
      }}
      if (numberValue === 0) {{
        return {{
          value: '',
          hint: '检测值为 0 时无法计算 log10，结果已清空。'
        }};
      }}
      if (numberValue < 0) {{
        return {{
          value: '',
          hint: '检测值为负数时无法计算 log10，结果已清空。'
        }};
      }}

      return {{
        value: Math.log10(numberValue).toFixed(6),
        hint: '已按当前输入实时计算 log10。'
      }};
    }}

    function bind() {{
      const parentDoc = window.parent.document;
      const input = parentDoc.querySelector(`input[aria-label="${{fieldLabel}}"]`);
      const valueNode = parentDoc.getElementById(valueId);
      const hintNode = parentDoc.getElementById(hintId);
      if (!input || !valueNode || !hintNode) {{
        return false;
      }}

      const render = () => {{
        const result = computeLog10(input.value);
        valueNode.textContent = result.value;
        hintNode.textContent = result.hint;
      }};

      render();
      if (!input.dataset.log10Bound) {{
        input.addEventListener('input', render);
        input.addEventListener('change', render);
        input.dataset.log10Bound = '1';
      }}
      return true;
    }}

    let attempts = 0;
    const timer = setInterval(() => {{
      attempts += 1;
      if (bind() || attempts > 30) {{
        clearInterval(timer);
      }}
    }}, 300);
    </script>
    """
    components.html(script, height=0)


def render_rule_summary_metrics(summary: dict) -> None:
    metric_items = [
        ("1_2s", summary.get("1_2s", 0)),
        ("1_3s", summary.get("1_3s", 0)),
        ("2_2s", summary.get("2_2s", 0)),
        ("R_4s", summary.get("R_4s", 0)),
        ("4_1s", summary.get("4_1s", 0)),
        ("10x", summary.get("10x", 0)),
        ("\u8b66\u544a\u6b21\u6570", summary.get("warning_count", 0)),
        ("\u5931\u63a7\u6b21\u6570", summary.get("out_of_control_count", 0)),
    ]
    cards = []
    for label, value in metric_items:
        cards.append(
            dedent(
                f"""
                <div class="compact-summary-card">
                    <div class="compact-summary-label">{html_escape(str(label))}</div>
                    <div class="compact-summary-value">{html_escape(str(value))}</div>
                </div>
                """
            ).strip()
        )

    summary_html = dedent(
        f"""
        <div class="compact-summary-grid">
            {''.join(cards)}
        </div>
        """
    ).strip()
    render_html_block(summary_html)


def render_compact_stat_metrics(metrics: list[tuple[str, str]]) -> None:
    cards = []
    for label, value in metrics:
        cards.append(
            dedent(
                f"""
                <div class="stat-card">
                    <div class="stat-card-label">{html_escape(str(label))}</div>
                    <div class="stat-card-value">{html_escape(str(value))}</div>
                </div>
                """
            ).strip()
        )

    stats_html = dedent(
        f"""
        <div class="stat-card-grid">
            {''.join(cards)}
        </div>
        """
    ).strip()
    render_html_block(stats_html)


def render_zscore_level_input_block(
    level_label: str,
    value_key: str,
    value_element_id: str,
    hint_element_id: str,
    level_caption: str | None = None,
) -> None:
    st.markdown(f"**{level_label}**")
    if level_caption:
        st.caption(level_caption)
    field_label = f"{level_label} 检测值（支持实时 log10）"
    value_text = st.text_input(
        field_label,
        key=value_key,
        placeholder="例如：123.4567",
    )
    render_live_log10_panel(
        value_text=value_text,
        field_label=field_label,
        value_element_id=value_element_id,
        hint_element_id=hint_element_id,
    )


def format_zscore_level_display(level_id: str, level_label_map: dict[str, str]) -> tuple[str, str | None]:
    default_level_label = format_level_id_display(level_id)
    level_label = str(level_label_map.get(level_id, level_id) or level_id).strip() or level_id
    if level_label == level_id:
        return default_level_label, None
    return level_label, default_level_label


def build_zscore_current_level_results(template: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str], bool]:
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
        raw_value, _, log_value = parse_numeric_input(raw_text)
        has_any_input = has_any_input or bool(raw_text.strip())
        if raw_text.strip() and raw_value is None:
            validation_errors.append(f"{format_level_id_display(level_id)}的检测值必须为有效正数。")

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


def format_zscore_rule_hits(rule_hits: list[dict[str, Any]]) -> str:
    if not rule_hits:
        return "无"
    ordered_rule_ids = list(dict.fromkeys(hit["rule_id"] for hit in rule_hits))
    return "、".join(format_rule_code(rule_id) for rule_id in ordered_rule_ids)


def build_zscore_chart_control_title(template: dict[str, Any], view_mode: str, selected_level: str) -> str:
    scope_text = format_level_id_display(selected_level) if view_mode == "单水平视图" else "全部水平"
    return f"图表控制（点击展开）｜{format_zscore_template_display_name(template)}｜{view_mode}｜{scope_text}"


def render_zscore_chart_controls(
    templates: dict[str, dict[str, Any]],
    initial_template_id: str,
) -> tuple[str, dict[str, Any], str, str]:
    template = templates[initial_template_id]
    view_mode = st.session_state.get("zscore_view_mode", "单水平视图")
    if view_mode not in {"单水平视图", "合并视图"}:
        view_mode = "单水平视图"
    selected_level = st.session_state.get("zscore_selected_level", template["level_ids"][0])
    if selected_level not in template["level_ids"]:
        selected_level = template["level_ids"][0]
        st.session_state["zscore_selected_level"] = selected_level

    with st.expander(
        build_zscore_chart_control_title(template, view_mode, selected_level),
        expanded=False,
    ):
        control_col1, control_col2 = st.columns([1.05, 1.15], gap="large")
        template_id = control_col1.selectbox(
            "规则模板",
            options=list(templates.keys()),
            index=list(templates.keys()).index(initial_template_id),
            key="zscore_rule_template",
            format_func=lambda option: templates[option]["label"],
        )
        view_mode = control_col2.radio(
            "视图模式",
            options=["单水平视图", "合并视图"],
            horizontal=True,
            key="zscore_view_mode",
        )
        template = templates[template_id]
        if st.session_state.get("zscore_selected_level") not in template["level_ids"]:
            st.session_state["zscore_selected_level"] = template["level_ids"][0]
        if view_mode == "单水平视图":
            selected_level = st.radio(
                "选择显示水平",
                options=template["level_ids"],
                horizontal=True,
                key="zscore_selected_level",
                format_func=format_level_id_display,
            )
        else:
            selected_level = st.session_state.get("zscore_selected_level", template["level_ids"][0])
    return template_id, template, view_mode, selected_level


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

    palette = {
        "accept": {"background": "#edf8ef", "border": "#59a14f", "text": "#1d5f2a", "badge": "#59a14f"},
        "warning": {"background": "#fff6db", "border": "#edc948", "text": "#785b00", "badge": "#c89b00"},
        "reject": {"background": "#fdeaea", "border": "#e15759", "text": "#8f1f28", "badge": "#c23b3d"},
        "pending": {"background": "#f3f6fb", "border": "#7a8ca5", "text": "#31445a", "badge": "#58708f"},
        PHASE_TARGET_BUILDING: {
            "background": "#eef4fb",
            "border": "#4e79a7",
            "text": "#24476d",
            "badge": "#4e79a7",
        },
    }
    is_building_phase = (not formal_rules_enabled) or latest_run.get("phase") != PHASE_FORMAL_QC
    status = PHASE_TARGET_BUILDING if is_building_phase else str(latest_run.get("run_status", "pending"))
    style = palette.get(status, palette["pending"])
    source_text = (
        "当前输入预览"
        if latest_run.get("is_preview")
        else f"最近已保存检测序号 #{get_zscore_display_sequence(latest_run)}"
    )
    phase_label = str(latest_run.get("phase_label") or get_phase_label(latest_run.get("phase", overall_phase)))
    badge_text = phase_label if is_building_phase else format_zscore_status_label(status)
    html = dedent(
        f"""
        <div style="
            background:{style['background']};
            border:1px solid {style['border']};
            border-left:5px solid {style['border']};
            border-radius:10px;
            padding:10px 12px;
            margin:2px 0 6px 0;
        ">
            <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:8px;">
                <span style="
                    background:{style['badge']};
                    color:#ffffff;
                    font-size:12px;
                    font-weight:700;
                    border-radius:999px;
                    padding:3px 9px;
                ">{html_escape(badge_text)}</span>
                <span style="color:{style['text']};font-size:12px;font-weight:600;">
                    {html_escape(source_text)}
                </span>
            </div>
            <div style="color:{style['text']};font-size:13px;line-height:1.55;">
                <div><strong>当前阶段：</strong>{html_escape(phase_label)}</div>
                {
                    '<div><strong>正式规则判读：</strong>当前仅用于累计靶值与观察趋势，暂不输出正式警告或失控结论。</div>'
                    '<div style="margin-top:6px;">本次结果纳入建靶观察，不作为正式质控结论。</div>'
                    if is_building_phase
                    else f"<div><strong>触发规则：</strong>{html_escape(format_zscore_rule_hits(latest_run.get('rule_hits_run', [])))}</div>"
                    f"<div><strong>误差类型提示：</strong>{html_escape(format_error_type_label(latest_run.get('error_type_hint', 'unknown')))}</div>"
                    f"<div style=\"margin-top:6px;\">{html_escape(str(latest_run.get('analysis_prompt', '暂无分析提示。')))}</div>"
                }
            </div>
        </div>
        """
    ).strip()
    render_html_block(html)


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


def render_zscore_vendor_reference_editor(
    batch_id: int,
    template_id: str,
    level_id: str,
    level_display_name: str,
    required_n: int,
    profile: dict[str, Any],
) -> None:
    with st.expander("厂家参考值（仅作参考）", expanded=False):
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


def render_zscore_chart_controls(
    template: dict[str, Any],
    default_phase_scope: str,
    level_label_map: dict[str, str],
) -> tuple[str, str, str, str, float]:
    phase_scope = st.session_state.get("zscore_phase_scope", default_phase_scope)
    if phase_scope not in ZSCORE_PHASE_VIEW_OPTIONS:
        phase_scope = default_phase_scope
        st.session_state["zscore_phase_scope"] = phase_scope

    view_mode = st.session_state.get("zscore_view_mode", "单水平视图")
    if view_mode not in {"单水平视图", "合并视图"}:
        view_mode = "单水平视图"
        st.session_state["zscore_view_mode"] = view_mode

    y_axis_mode = st.session_state.get("zscore_y_axis_mode", ZSCORE_Y_AXIS_OPTIONS[0])
    if y_axis_mode not in ZSCORE_Y_AXIS_OPTIONS:
        y_axis_mode = ZSCORE_Y_AXIS_OPTIONS[0]
        st.session_state["zscore_y_axis_mode"] = y_axis_mode

    standard_sd_limit = float(st.session_state.get("zscore_standard_sd_limit", 4.0) or 4.0)
    if standard_sd_limit <= 0:
        standard_sd_limit = 4.0
        st.session_state["zscore_standard_sd_limit"] = standard_sd_limit

    selected_level = st.session_state.get("zscore_selected_level", template["level_ids"][0])
    if selected_level not in template["level_ids"]:
        selected_level = template["level_ids"][0]
        st.session_state["zscore_selected_level"] = selected_level

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
        control_col1, control_col2 = st.columns([1.05, 1.15], gap="large")
        phase_scope = control_col1.radio(
            "数据范围视图",
            options=list(ZSCORE_PHASE_VIEW_OPTIONS.keys()),
            index=list(ZSCORE_PHASE_VIEW_OPTIONS.keys()).index(phase_scope),
            format_func=lambda option: ZSCORE_PHASE_VIEW_OPTIONS[option],
            horizontal=True,
            key="zscore_phase_scope",
        )
        view_mode = control_col2.radio(
            "图形呈现方式",
            options=["单水平视图", "合并视图"],
            horizontal=True,
            key="zscore_view_mode",
        )
        y_axis_mode = st.radio(
            "Y 轴范围",
            options=ZSCORE_Y_AXIS_OPTIONS,
            horizontal=True,
            key="zscore_y_axis_mode",
        )
        if y_axis_mode == "标准视图":
            standard_sd_limit = float(
                st.slider(
                    "标准视图范围（均值 ± nSD）",
                    min_value=2.0,
                    max_value=6.0,
                    value=float(st.session_state.get("zscore_standard_sd_limit", standard_sd_limit)),
                    step=1.0,
                    key="zscore_standard_sd_limit",
                )
            )
            render_standard_view_help(standard_sd_limit)
        else:
            standard_sd_limit = float(st.session_state.get("zscore_standard_sd_limit", standard_sd_limit))
        if st.session_state.get("zscore_selected_level") not in template["level_ids"]:
            st.session_state["zscore_selected_level"] = template["level_ids"][0]
        if view_mode == "单水平视图":
            selected_level = st.radio(
                "选择显示水平",
                options=template["level_ids"],
                horizontal=True,
                key="zscore_selected_level",
                format_func=lambda option: format_zscore_level_display(option, level_label_map)[0],
            )
        else:
            selected_level = st.session_state.get("zscore_selected_level", template["level_ids"][0])
    return phase_scope, view_mode, selected_level, y_axis_mode, standard_sd_limit


def sync_zscore_workbench_state(
    batch_id: int,
    template: dict[str, Any],
    default_phase_scope: str,
) -> None:
    if st.session_state.get("zscore_workbench_batch_id") != batch_id:
        st.session_state["zscore_workbench_batch_id"] = batch_id
        st.session_state["zscore_phase_scope"] = default_phase_scope
        st.session_state["zscore_selected_level"] = template["level_ids"][0]
        if st.session_state.get("zscore_view_mode") not in {"单水平视图", "合并视图"}:
            st.session_state["zscore_view_mode"] = "单水平视图"
        if st.session_state.get("zscore_y_axis_mode") not in ZSCORE_Y_AXIS_OPTIONS:
            st.session_state["zscore_y_axis_mode"] = ZSCORE_Y_AXIS_OPTIONS[0]
        if float(st.session_state.get("zscore_standard_sd_limit", 4.0) or 4.0) <= 0:
            st.session_state["zscore_standard_sd_limit"] = 4.0
        return

    if st.session_state.get("zscore_phase_scope") not in ZSCORE_PHASE_VIEW_OPTIONS:
        st.session_state["zscore_phase_scope"] = default_phase_scope
    if st.session_state.get("zscore_selected_level") not in template["level_ids"]:
        st.session_state["zscore_selected_level"] = template["level_ids"][0]
    if st.session_state.get("zscore_view_mode") not in {"单水平视图", "合并视图"}:
        st.session_state["zscore_view_mode"] = "单水平视图"
    if st.session_state.get("zscore_y_axis_mode") not in ZSCORE_Y_AXIS_OPTIONS:
        st.session_state["zscore_y_axis_mode"] = ZSCORE_Y_AXIS_OPTIONS[0]
    if float(st.session_state.get("zscore_standard_sd_limit", 4.0) or 4.0) <= 0:
        st.session_state["zscore_standard_sd_limit"] = 4.0


def render_batch_summary_row(batch) -> None:
    summary_items = [
        ("\u4eea\u5668", batch["instrument"]),
        ("\u8bd5\u5242", batch["reagent"]),
        ("\u8d28\u63a7\u54c1", batch["qc_material"]),
        ("\u6d53\u5ea6", batch["concentration"]),
        ("\u8d28\u63a7\u54c1\u6279\u53f7", batch["lot_no"]),
        ("\u5efa\u9776\u6240\u9700\u6b21\u6570", batch["target_n"]),
    ]
    cards = []
    for label, value in summary_items:
        cards.append(
            dedent(
                f"""
                <div class="batch-summary-item">
                    <div class="batch-summary-label">{html_escape(str(label))}</div>
                    <div class="batch-summary-value">{html_escape(str(value))}</div>
                </div>
                """
            ).strip()
        )

    summary_html = dedent(
        f"""
        <div class="batch-summary-grid">
            {''.join(cards)}
        </div>
        """
    ).strip()
    render_html_block(summary_html)


def render_zscore_batch_summary_row(
    batch,
    phase_label: str,
    formal_rules_enabled: bool,
    template_label: str,
    level_ids: list[str],
) -> None:
    summary_items = build_zscore_batch_summary_items(
        batch=batch,
        phase_label=phase_label,
        formal_rules_enabled=formal_rules_enabled,
        template_label=template_label,
        level_ids=level_ids,
    )
    cards = []
    for label, value in summary_items:
        cards.append(
            dedent(
                f"""
                <div class="zscore-summary-item">
                    <div class="zscore-summary-label">{html_escape(str(label))}</div>
                    <div class="zscore-summary-value">{html_escape(str(value))}</div>
                </div>
                """
            ).strip()
        )

    summary_html = dedent(
        f"""
        <div class="zscore-summary-grid">
            {''.join(cards)}
        </div>
        """
    ).strip()
    render_html_block(summary_html)


def bump_record_maintenance_dialog_nonce() -> int:
    next_nonce = int(st.session_state.get("record_maintenance_dialog_nonce", 0)) + 1
    st.session_state["record_maintenance_dialog_nonce"] = next_nonce
    return next_nonce


def close_record_maintenance_dialog() -> None:
    st.session_state["show_record_maintenance_dialog"] = False
    bump_record_maintenance_dialog_nonce()


@st.dialog("\u68c0\u6d4b\u8bb0\u5f55\u7ef4\u62a4", width="large", on_dismiss=close_record_maintenance_dialog)
def render_record_maintenance_dialog(qc_df: pd.DataFrame) -> None:
    notice_message = st.session_state.pop("record_maintenance_notice", "")
    if notice_message:
        st.success(notice_message)

    st.caption(
        "\u5728\u6b64\u53ef\u4ee5\u9009\u62e9\u68c0\u6d4b\u8bb0\u5f55\u8fdb\u884c\u4fee\u6539\u6216\u5220\u9664\uff0c"
        "\u64cd\u4f5c\u540e\u4f1a\u81ea\u52a8\u5237\u65b0\u4e3b\u9875\u9762\u6570\u636e\u3002"
    )
    if qc_df.empty:
        st.info("\u5f53\u524d\u6279\u6b21\u6682\u65e0\u68c0\u6d4b\u8bb0\u5f55\u53ef\u7ef4\u62a4\u3002")
        if st.button("\u5173\u95ed", key="close_record_dialog_empty", width="stretch"):
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
        "\u9009\u62e9\u9700\u8981\u7f16\u8f91\u6216\u5220\u9664\u7684\u68c0\u6d4b\u8bb0\u5f55",
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
            current_log_display, _ = compute_log10_display(float(selected_result["value"]))
            dialog_nonce = int(st.session_state.get("record_maintenance_dialog_nonce", 0))
            confirm_delete_key = f"confirm_delete_result_{dialog_nonce}_{int(selected_result_id)}"
            maintenance_left, maintenance_right = st.columns([1.25, 0.75], gap="large")

            with maintenance_left:
                st.caption(
                    f"\u5f53\u524d\u9009\u4e2d\uff1a\u5e8f\u53f7 {int(selected_result['sequence'])} | "
                    f"\u72b6\u6001 {selected_result['status']} | \u89e6\u53d1\u89c4\u5219 "
                    f"{selected_result['rule_hits'] or '\u65e0'}"
                )
                with st.form("edit_result_form"):
                    edit_test_time = st.datetime_input(
                        "\u68c0\u6d4b\u65f6\u95f4",
                        value=pd.Timestamp(selected_result["test_time"]).to_pydatetime(),
                    )
                    edit_operator = st.text_input(
                        "\u68c0\u6d4b\u4eba",
                        value=str(selected_result["operator"]),
                    )
                    edit_value = st.number_input(
                        "\u68c0\u6d4b\u503c",
                        value=float(selected_result["value"]),
                        format="%.4f",
                    )
                    edit_log_display, edit_log_value = compute_log10_display(float(edit_value))
                    st.text_input(
                        "log\u503c\uff08log10\uff09",
                        value=edit_log_display or current_log_display,
                        disabled=True,
                    )
                    edit_reagent_changed = st.checkbox(
                        "\u672c\u6b21\u4e3a\u8bd5\u5242\u6279\u53f7\u53d8\u66f4\u70b9",
                        value=bool(int(selected_result["reagent_lot_changed"])),
                    )
                    edit_submitted = st.form_submit_button(
                        "\u4fdd\u5b58\u8bb0\u5f55\u4fee\u6539",
                        width="stretch",
                    )

                    if edit_submitted:
                        validation_errors: list[str] = []
                        cleaned_operator = edit_operator.strip()

                        if edit_test_time is None:
                            validation_errors.append("\u8bf7\u586b\u5199\u68c0\u6d4b\u65f6\u95f4\u3002")
                        if not cleaned_operator:
                            validation_errors.append("\u8bf7\u586b\u5199\u68c0\u6d4b\u4eba\uff0c\u4e0d\u80fd\u4e3a\u7a7a\u3002")
                        if not isinstance(edit_value, (int, float)) or not math.isfinite(float(edit_value)):
                            validation_errors.append("\u68c0\u6d4b\u503c\u5fc5\u987b\u4e3a\u6709\u6548\u6570\u5b57\u3002")
                        elif float(edit_value) <= 0:
                            validation_errors.append(
                                "\u68c0\u6d4b\u503c\u5fc5\u987b\u5927\u4e8e 0\uff0c\u624d\u80fd\u4fdd\u5b58\u5e76\u8ba1\u7b97 log\u503c\u3002"
                            )

                        if validation_errors:
                            st.error("\n".join(validation_errors))
                        else:
                            update_result(
                                result_id=int(selected_result_id),
                                test_time=edit_test_time.strftime("%Y-%m-%d %H:%M:%S"),
                                operator=cleaned_operator,
                                value=float(edit_value),
                                log_value=edit_log_value,
                                reagent_lot_changed=int(edit_reagent_changed),
                            )
                            close_record_maintenance_dialog()
                            st.success("\u68c0\u6d4b\u8bb0\u5f55\u5df2\u66f4\u65b0\u3002")
                            st.rerun()

            with maintenance_right:
                st.caption(
                    "\u5220\u9664\u540e\u4f1a\u91cd\u65b0\u8ba1\u7b97\u540e\u7eed\u5e8f\u53f7\u3001"
                    "\u5efa\u9776/\u6b63\u5f0f\u9636\u6bb5\u4ee5\u53ca Westgard \u5224\u5b9a\u3002"
                )
                confirm_delete = st.checkbox(
                    "\u6211\u786e\u8ba4\u5220\u9664\u8fd9\u6761\u68c0\u6d4b\u8bb0\u5f55",
                    key=confirm_delete_key,
                )
                if st.button(
                    "\u5220\u9664\u6240\u9009\u8bb0\u5f55",
                    key="delete_record_dialog_button",
                    width="stretch",
                    disabled=not confirm_delete,
                ):
                    delete_result(int(selected_result_id))
                    st.session_state["selected_result_id"] = None
                    bump_record_maintenance_dialog_nonce()
                    st.session_state["show_record_maintenance_dialog"] = True
                    st.session_state["record_maintenance_notice"] = "\u68c0\u6d4b\u8bb0\u5f55\u5df2\u5220\u9664\u3002"
                    st.rerun()

    st.divider()
    if st.button("\u5173\u95ed", key="close_record_dialog", width="stretch"):
        close_record_maintenance_dialog()
        st.rerun()


def bump_zscore_record_maintenance_dialog_nonce() -> int:
    next_nonce = int(st.session_state.get("zscore_record_maintenance_dialog_nonce", 0)) + 1
    st.session_state["zscore_record_maintenance_dialog_nonce"] = next_nonce
    return next_nonce


def close_zscore_record_maintenance_dialog() -> None:
    st.session_state["show_zscore_record_maintenance_dialog"] = False
    bump_zscore_record_maintenance_dialog_nonce()


@st.dialog("Z-score 记录维护", width="large", on_dismiss=close_zscore_record_maintenance_dialog)
def render_zscore_record_maintenance_dialog(
    saved_runs: list[dict[str, Any]],
    batch_context: dict[str, Any],
) -> None:
    notice_message = str(st.session_state.pop("zscore_record_maintenance_notice", "") or "")
    if notice_message:
        st.success(notice_message)

    st.caption("在此查看当前批次已保存的多水平检测记录，并维护检测时间、检测人和各水平原始值；保存或删除后会自动整批次重算。")
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
        "选择需要编辑或删除的检测记录",
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
                    f"当前选中：检测序号 {sequence_number} | "
                    f"{pd.Timestamp(selected_run['test_time']).strftime('%Y-%m-%d %H:%M:%S')} | "
                    f"阶段 {selected_run.get('phase_label')} | 判定 {format_zscore_status_label(selected_run.get('run_status'))} | "
                    f"触发规则 {format_zscore_rule_hits(selected_run.get('rule_hits_run', []))}"
                )
                if is_locked_for_maintenance:
                    st.info("建靶期数据在正式期启用后已锁定，只可查看，不可编辑或删除。")

                with st.form(f"edit_zscore_run_form_{dialog_nonce}_{int(selected_run_id)}"):
                    edit_test_time = st.datetime_input(
                        "检测时间",
                        value=pd.Timestamp(selected_run["test_time"]).to_pydatetime(),
                        disabled=is_locked_for_maintenance,
                    )
                    edit_operator = st.text_input(
                        "检测人",
                        value=str(selected_run.get("operator", "") or ""),
                        disabled=is_locked_for_maintenance,
                    )
                    st.markdown("**多水平原始值**")
                    edited_level_values: dict[str, float] = {}
                    level_result_map = {
                        str(level_result.get("level_id")): level_result
                        for level_result in selected_run.get("level_results", [])
                    }
                    for level_id in template["level_ids"]:
                        display_label, level_caption = format_zscore_level_display(level_id, level_label_map)
                        current_level_result = level_result_map.get(level_id, {})
                        edited_level_values[level_id] = st.number_input(
                            display_label,
                            value=float(current_level_result.get("raw_value") or 0.0),
                            format="%.4f",
                            key=f"edit_zscore_run_{dialog_nonce}_{int(selected_run_id)}_{level_id.replace(' ', '_')}",
                            disabled=is_locked_for_maintenance,
                        )
                        if level_caption:
                            st.caption(level_caption)
                    edit_submitted = st.form_submit_button(
                        "保存记录修改",
                        width="stretch",
                        disabled=is_locked_for_maintenance,
                    )

                    if edit_submitted:
                        validation_errors: list[str] = []
                        cleaned_operator = edit_operator.strip()
                        updated_level_results: list[dict[str, Any]] = []

                        if edit_test_time is None:
                            validation_errors.append("请填写检测时间。")
                        if not cleaned_operator:
                            validation_errors.append("请填写检测人，不能为空。")

                        for level_id in template["level_ids"]:
                            display_level = format_zscore_level_display(level_id, level_label_map)[0]
                            raw_value = edited_level_values[level_id]
                            if not isinstance(raw_value, (int, float)) or not math.isfinite(float(raw_value)):
                                validation_errors.append(f"{display_level}的原始值必须为有效数字。")
                                continue
                            if float(raw_value) <= 0:
                                validation_errors.append(f"{display_level}的原始值必须大于 0。")
                                continue
                            updated_level_results.append(
                                {
                                    "level_id": level_id,
                                    "raw_value": float(raw_value),
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
                st.caption("删除后会同步重算当前批次的建靶统计、正式靶值、正式期实时统计、阶段判定、结果判读和图表基础数据。")
                confirm_delete = st.checkbox(
                    "我确认删除这条检测记录",
                    key=confirm_delete_key,
                    disabled=is_locked_for_maintenance,
                )
                if st.button(
                    "删除所选记录",
                    key=delete_button_key,
                    width="stretch",
                    disabled=is_locked_for_maintenance or not confirm_delete,
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
        st.info("\u5f53\u524d\u6279\u6b21\u6682\u65e0\u68c0\u6d4b\u8bb0\u5f55\u3002")
        return

    html_rows: list[str] = []
    for _, row in display_df.iterrows():
        row_cells: list[str] = []
        for column_name in display_df.columns:
            value = "" if pd.isna(row[column_name]) else str(row[column_name])
            cell_text = html_escape(value).replace("\n", "<br>")
            row_cells.append(
                f'<td title="{html_escape(value)}">{cell_text}</td>'
            )
        html_rows.append("<tr>" + "".join(row_cells) + "</tr>")

    headers = "".join(f"<th>{html_escape(str(column))}</th>" for column in display_df.columns)
    records_html = dedent(
        f"""
        <div class="qc-records-wrapper">
            <style>
            .qc-records-wrapper {{
                width: 100%;
                overflow-x: auto;
            }}
            .qc-records-table {{
                width: 100%;
                border-collapse: collapse;
                table-layout: fixed;
                font-size: 13px;
            }}
            .qc-records-table th,
            .qc-records-table td {{
                border: 1px solid #d9dde7;
                padding: 8px 10px;
                vertical-align: top;
                white-space: normal;
                word-break: break-word;
                line-height: 1.55;
            }}
            .qc-records-table th {{
                background: #f2f5fa;
                font-weight: 700;
                color: #223045;
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


def build_project_select_options(projects_df: pd.DataFrame) -> tuple[list[str], dict[str, int | None]]:
    option_map = {"\u8bf7\u9009\u62e9\u9879\u76ee": None}
    for _, row in projects_df.iterrows():
        option_map[build_project_label(row)] = int(row["id"])
    return list(option_map.keys()), option_map


def build_batch_select_options(batches_df: pd.DataFrame) -> tuple[list[str], dict[str, int | None]]:
    option_map = {"\u8bf7\u9009\u62e9\u6279\u6b21": None}
    for _, row in batches_df.iterrows():
        option_map[build_batch_label(row)] = int(row["id"])
    return list(option_map.keys()), option_map


def build_zscore_project_select_options(projects_df: pd.DataFrame) -> tuple[list[str], dict[str, int | None]]:
    option_map = {"请选择 Z-score 项目": None}
    for _, row in projects_df.iterrows():
        option_map[build_zscore_project_label(row)] = int(row["id"])
    return list(option_map.keys()), option_map


def build_zscore_batch_select_options(batches_df: pd.DataFrame) -> tuple[list[str], dict[str, int | None]]:
    option_map = {"请选择 Z-score 批次": None}
    for _, row in batches_df.iterrows():
        option_map[build_zscore_batch_label(row)] = int(row["id"])
    return list(option_map.keys()), option_map


def build_result_label(row: pd.Series) -> str:
    test_time = pd.Timestamp(row["test_time"]).strftime("%Y-%m-%d %H:%M:%S")
    return (
        f"\u68c0\u6d4b\u5e8f\u53f7 {int(row['sequence'])} | "
        f"{test_time} | {float(row['value']):.4f} | {row['operator']}"
    )


def build_result_select_options(results_df: pd.DataFrame) -> tuple[list[str], dict[str, int | None]]:
    option_map = {"\u8bf7\u9009\u62e9\u68c0\u6d4b\u8bb0\u5f55": None}
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
    return f"序号 {test_sequence} | {test_time} | {run.get('operator', '')} | {level_summary}"


def build_zscore_run_select_options(
    saved_runs: list[dict[str, Any]],
    level_label_map: dict[str, str],
) -> tuple[list[str], dict[str, int | None]]:
    option_map = {"请选择需要维护的检测记录": None}
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
                "维护状态": "已锁定" if bool(run.get("is_locked_for_maintenance")) else "可维护",
                "水平摘要": build_zscore_run_level_summary(run.get("level_results", []), level_label_map),
            }
        )
    return pd.DataFrame(rows)


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

    start_timestamp = pd.Timestamp(start_date)
    end_timestamp = pd.Timestamp(end_date) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
    filtered_df = formal_df[
        (formal_df["test_time"] >= start_timestamp) & (formal_df["test_time"] <= end_timestamp)
    ].copy()
    if filtered_df.empty:
        return filtered_df

    filtered_df = filtered_df.sort_values(["test_time", "id"]).reset_index(drop=True)
    filtered_df["sequence"] = filtered_df.index + 1
    return filtered_df


def build_monthly_chart_title(batch, start_date, end_date) -> str:
    return (
        f"\u6708\u5ea6\u8d28\u63a7\u56fe - \u6279\u6b21 {batch['id']} - {batch['instrument']} - {batch['reagent']} - "
        f"{batch['qc_material']} - {batch['concentration']}\n"
        f"{start_date.strftime('%Y-%m-%d')} \u81f3 {end_date.strftime('%Y-%m-%d')}"
    )


def sync_selector_state(
    selector_key: str,
    selected_id_key: str,
    options_map: dict[str, int | None],
    placeholder: str,
) -> None:
    current_id = st.session_state.get(selected_id_key)
    valid_ids = {value for value in options_map.values() if value is not None}
    if current_id is not None and current_id not in valid_ids:
        st.session_state[selected_id_key] = None

    current_label = st.session_state.get(selector_key)
    if current_label not in options_map:
        if current_id in valid_ids:
            for label, value in options_map.items():
                if value == current_id:
                    st.session_state[selector_key] = label
                    return
        st.session_state[selector_key] = placeholder
        return

    if current_label == placeholder and current_id in valid_ids:
        for label, value in options_map.items():
            if value == current_id:
                st.session_state[selector_key] = label
                return

    if current_label == placeholder:
        st.session_state[selected_id_key] = None


def render_lj_page(
    work_tab,
    selected_batch_id: int,
) -> None:
    batch = get_batch(selected_batch_id)
    results_df = get_results(selected_batch_id)
    qc_df, stats = calculate_qc_results(results_df, int(batch["target_n"]))
    latest_status, latest_status_message = get_latest_status_context(qc_df)
    latest_rule_hits, latest_compact_message = get_latest_result_panel_content(qc_df, latest_status_message)
    operator_options = build_operator_options(results_df)

    view_mode = st.session_state.get("chart_view_mode", "\u5168\u90e8\u6570\u636e\u56fe")
    view_options = ["\u5efa\u9776\u56fe", "\u6b63\u5f0f\u8d28\u63a7\u56fe", "\u5168\u90e8\u6570\u636e\u56fe"]
    if view_mode not in view_options:
        view_mode = view_options[2]

    y_axis_mode = st.session_state.get("chart_y_axis_mode", "\u6807\u51c6\u89c6\u56fe")
    y_axis_options = ["\u6807\u51c6\u89c6\u56fe", "\u5168\u8303\u56f4\u89c6\u56fe"]
    if y_axis_mode not in y_axis_options:
        y_axis_mode = y_axis_options[0]

    standard_sd_limit = float(st.session_state.get("chart_standard_sd_limit", 4.0))

    chart_title = (
        f"{view_mode} - \u6279\u6b21 {batch['id']} - {batch['instrument']} - "
        f"{batch['reagent']} - {batch['qc_material']} - {batch['concentration']}"
    )

    with work_tab:
        st.caption(f"\u5f53\u524d\u9879\u76ee\uff1a{batch['project_name']}")
        with st.container():
            render_batch_summary_row(batch)

        st.divider()
        entry_col, chart_col = st.columns([1.0, 1.18], gap="large")

        with entry_col:
            st.subheader("\u5f55\u5165\u4e0e\u7edf\u8ba1")
            st.caption("\u5de6\u4fa7\u4e13\u6ce8\u6570\u636e\u5f55\u5165\u548c\u5173\u952e\u7edf\u8ba1\uff0c\u51cf\u5c11\u9996\u5c4f\u4fe1\u606f\u62e5\u6324\u3002")
            st.markdown("**\u6279\u6b21\u6570\u636e\u5f55\u5165**")
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

            test_time = st.datetime_input(
                "\u68c0\u6d4b\u65f6\u95f4",
                key="entry_test_time",
            )
            operator = st.selectbox(
                "\u68c0\u6d4b\u4eba",
                options=operator_options,
                index=None,
                key="entry_operator",
                accept_new_options=True,
                placeholder="\u53ef\u9009\u62e9\u5386\u53f2\u59d3\u540d\uff0c\u4e5f\u53ef\u76f4\u63a5\u8f93\u5165\u65b0\u59d3\u540d",
            )
            value_text = st.text_input(
                "\u68c0\u6d4b\u503c\uff08\u652f\u6301\u5b9e\u65f6 log10\uff09",
                key="entry_value",
                placeholder="\u4f8b\u5982\uff1a123.4567",
            )
            parsed_value, log_display, log_value = parse_numeric_input(value_text)
            render_live_log10_panel(
                value_text=value_text,
                field_label="\u68c0\u6d4b\u503c\uff08\u652f\u6301\u5b9e\u65f6 log10\uff09",
                value_element_id="entry-log10-value",
                hint_element_id="entry-log10-hint",
            )
            reagent_lot_changed = st.checkbox(
                "\u672c\u6b21\u4e3a\u8bd5\u5242\u6279\u53f7\u53d8\u66f4\u70b9",
                key="entry_reagent_changed",
            )

            if st.button("\u4fdd\u5b58\u68c0\u6d4b\u7ed3\u679c", type="primary", width="stretch"):
                validation_errors: list[str] = []
                cleaned_operator = (operator or "").strip()

                if test_time is None:
                    validation_errors.append("\u8bf7\u586b\u5199\u68c0\u6d4b\u65f6\u95f4\u3002")
                if not cleaned_operator:
                    validation_errors.append("\u8bf7\u586b\u5199\u68c0\u6d4b\u4eba\uff0c\u4e0d\u80fd\u4e3a\u7a7a\u3002")
                if parsed_value is None:
                    validation_errors.append("\u68c0\u6d4b\u503c\u5fc5\u987b\u4e3a\u6709\u6548\u6570\u5b57\u3002")

                if validation_errors:
                    st.error("\n".join(validation_errors))
                else:
                    add_result(
                        batch_id=selected_batch_id,
                        test_time=test_time.strftime("%Y-%m-%d %H:%M:%S"),
                        operator=cleaned_operator,
                        value=float(parsed_value),
                        log_value=log_value,
                        reagent_lot_changed=int(reagent_lot_changed),
                    )
                    st.success("\u68c0\u6d4b\u7ed3\u679c\u5df2\u4fdd\u5b58\u3002")
                    st.session_state["reset_entry_form"] = True
                    st.rerun()

            st.divider()
            st.markdown("**\u5efa\u9776\u7edf\u8ba1**")
            render_compact_stat_metrics(
                [
                    ("均值", "-" if stats["mean"] is None else f"{stats['mean']:.4f}"),
                    ("SD", "-" if stats["sd"] is None else f"{stats['sd']:.4f}"),
                    ("CV%", "-" if stats["cv"] is None else f"{stats['cv']:.2f}%"),
                ]
            )
            st.caption(
                "\u5efa\u9776\u8fdb\u5ea6\uff1a"
                + (
                    "\u5df2\u5b8c\u6210\uff0c\u540e\u7eed\u7ed3\u679c\u81ea\u52a8\u8fdb\u884c Westgard \u5224\u5b9a\u3002"
                    if stats.get("target_ready")
                    else f"\u5c1a\u9700\u7ee7\u7eed\u5f55\u5165\u81f3\u5c11 {int(batch['target_n'])} \u6b21\u7ed3\u679c\u3002"
                )
            )

            st.divider()
            st.markdown("**\u5b9e\u65f6\u7edf\u8ba1**")
            sorted_results = results_df.sort_values(["test_time", "id"]).reset_index(drop=True)
            if sorted_results.empty:
                st.info("\u6682\u65e0\u6570\u636e\uff0c\u65e0\u6cd5\u8ba1\u7b97\u5b9e\u65f6\u7edf\u8ba1\u3002")
            else:
                sorted_results["sequence"] = sorted_results.index + 1
                formal_results = sorted_results[sorted_results["sequence"] > int(batch["target_n"])].copy()
                default_start = formal_results["test_time"].min() if not formal_results.empty else sorted_results["test_time"].min()
                default_end = sorted_results["test_time"].max()

                date_cols = st.columns(2)
                realtime_start = date_cols[0].date_input(
                    "\u5f00\u59cb\u65e5\u671f",
                    value=default_start.date(),
                    key="realtime_start",
                )
                realtime_end = date_cols[1].date_input(
                    "\u7ed3\u675f\u65e5\u671f",
                    value=default_end.date(),
                    key="realtime_end",
                )
                st.caption("\u6309\u65e5\u671f\u7edf\u8ba1\uff0c\u7ed3\u675f\u65e5\u671f\u5305\u542b\u5f53\u65e5\u5168\u90e8\u8bb0\u5f55\u3002")
                end_timestamp = pd.Timestamp(realtime_end) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
                realtime_stats, realtime_message = calculate_realtime_stats(
                    results_df=results_df,
                    target_n=int(batch["target_n"]),
                    start_time=pd.Timestamp(realtime_start),
                    end_time=end_timestamp,
                )
                render_compact_stat_metrics(
                    [
                        ("\u5b9e\u65f6\u5747\u503c", "-" if realtime_stats["mean"] is None else f"{realtime_stats['mean']:.4f}"),
                        ("\u5b9e\u65f6 SD", "-" if realtime_stats["sd"] is None else f"{realtime_stats['sd']:.4f}"),
                        ("\u5b9e\u65f6 CV%", "-" if realtime_stats["cv"] is None else f"{realtime_stats['cv']:.2f}%"),
                    ]
                )
                if realtime_message:
                    st.info(realtime_message)
                st.caption(
                    "\u7edf\u8ba1\u53e3\u5f84\uff1a\u5b9e\u65f6\u7edf\u8ba1\u4ec5\u57fa\u4e8e\u5f53\u524d\u6279\u6b21\u4e2d\u5224\u5b9a\u4e3a\u201c\u5728\u63a7\u201d\u7684\u6b63\u5f0f\u6570\u636e\u8ba1\u7b97\uff0c"
                    "\u5df2\u81ea\u52a8\u6392\u9664\u8b66\u544a\u548c\u5931\u63a7\u7ed3\u679c\uff1b"
                    "\u5f53\u68c0\u6d4b\u8bb0\u5f55\u88ab\u4fee\u6539\u6216\u5220\u9664\u540e\uff0c\u5b9e\u65f6\u5747\u503c / SD / CV% \u4f1a\u968f\u4e4b\u81ea\u52a8\u53d8\u5316\u3002"
                )

        with chart_col:
            st.subheader("\u56fe\u8868\u4e0e\u5224\u8bfb")
            chart_view_mode = view_mode
            chart_y_axis_mode = y_axis_mode
            chart_standard_sd_limit = float(st.session_state.get("chart_standard_sd_limit", standard_sd_limit))
            with st.expander(
                build_chart_control_title(chart_view_mode, chart_y_axis_mode, chart_standard_sd_limit),
                expanded=False,
            ):
                view_selector_col, y_axis_selector_col = st.columns([1.2, 1.1])
                chart_view_mode = view_selector_col.radio(
                    "\u56fe\u5f62\u89c6\u56fe",
                    options=view_options,
                    horizontal=True,
                    index=view_options.index(view_mode),
                )
                chart_y_axis_mode = y_axis_selector_col.radio(
                    "Y \u8f74\u8303\u56f4",
                    options=y_axis_options,
                    horizontal=True,
                    index=y_axis_options.index(y_axis_mode),
                )
                st.session_state["chart_view_mode"] = chart_view_mode
                st.session_state["chart_y_axis_mode"] = chart_y_axis_mode
                if chart_y_axis_mode == "\u6807\u51c6\u89c6\u56fe":
                    chart_standard_sd_limit = st.slider(
                        "\u6807\u51c6\u89c6\u56fe\u8303\u56f4\uff08\u5747\u503c \u00b1 nSD\uff09",
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
            st.markdown("**LJ\u56fe**")
            figure = plot_lj_chart(
                qc_df=qc_df,
                stats=stats,
                title=(
                    f"{chart_view_mode} - \u6279\u6b21 {batch['id']} - {batch['instrument']} - "
                    f"{batch['reagent']} - {batch['qc_material']} - {batch['concentration']}"
                ),
                view_mode=chart_view_mode,
                y_axis_mode=chart_y_axis_mode,
                standard_sd_limit=chart_standard_sd_limit,
            )
            st.pyplot(figure, clear_figure=False, width="stretch")
            st.markdown("**\u6700\u65b0\u7ed3\u679c\u5206\u6790**")
            render_status_panel(
                latest_status,
                latest_compact_message,
                latest_rule_hits,
            )

        st.divider()
        st.markdown("**\u672c\u6279\u6b21\u89c4\u5219\u6c47\u603b**")
        render_rule_summary_metrics(stats.get("rule_summary", {}))

        with st.expander("Westgard\u89c4\u5219\u8bf4\u660e\uff08\u70b9\u51fb\u5c55\u5f00\uff09", expanded=False):
            st.caption(
                "\u4ee5\u4e0b\u8bf4\u660e\u5bf9\u5e94\u5f53\u524d\u7248\u672c\u7684 Westgard \u5224\u8bfb\u53e3\u5f84\uff1b"
                "\u5efa\u9776\u671f\u4ec5\u7528\u4e8e\u53c2\u8003\uff0c\u6b63\u5f0f\u8d28\u63a7\u671f\u624d\u8f93\u51fa\u89c4\u5219\u7ed3\u8bba\u3002"
            )
            for rule_id in ["1_2s", "1_3s", "2_2s", "R_4s", "4_1s", "10x"]:
                st.markdown(f"- `{format_rule_code(rule_id)}`\uff1a{format_rule_description(rule_id)}")

        st.divider()
        with st.expander("\u5f53\u524d\u6279\u6b21\u68c0\u6d4b\u8bb0\u5f55\uff08\u70b9\u51fb\u6298\u53e0/\u5c55\u5f00\uff09", expanded=True):
            st.caption("\u5f53\u524d\u6279\u6b21\u7684\u5b8c\u6574\u68c0\u6d4b\u8bb0\u5f55\u3001Westgard \u89e6\u53d1\u89c4\u5219\u548c\u5206\u6790\u63d0\u793a\u90fd\u5728\u6b64\u67e5\u770b\u3002")
            display_df = prepare_display_records(qc_df)
            render_records_table(display_df)

        st.divider()
        maintenance_col, export_col = st.columns([0.9, 1.1], gap="large")
        with maintenance_col:
            st.subheader("\u68c0\u6d4b\u8bb0\u5f55\u7ef4\u62a4")
            st.caption("\u4e3b\u9875\u53ea\u4fdd\u7559\u7ef4\u62a4\u5165\u53e3\uff0c\u70b9\u51fb\u540e\u5728\u5f39\u7a97\u4e2d\u4fee\u6539\u6216\u5220\u9664\u68c0\u6d4b\u8bb0\u5f55\u3002")
            open_maintenance_disabled = qc_df.empty
            if st.button(
                "\u6253\u5f00\u68c0\u6d4b\u8bb0\u5f55\u7ef4\u62a4",
                key="open_record_maintenance_dialog",
                width="stretch",
                disabled=open_maintenance_disabled,
            ):
                bump_record_maintenance_dialog_nonce()
                st.session_state["show_record_maintenance_dialog"] = True
            if open_maintenance_disabled:
                st.info("\u5f53\u524d\u6279\u6b21\u6682\u65e0\u68c0\u6d4b\u8bb0\u5f55\u53ef\u7ef4\u62a4\u3002")
            if st.session_state.get("show_record_maintenance_dialog", False):
                render_record_maintenance_dialog(qc_df)

        with export_col:
            st.subheader("\u5bfc\u51fa")
            export_df = export_batch_results(batch, qc_df)
            csv_bytes = export_df.to_csv(index=False).encode("utf-8-sig")
            xlsx_bytes = dataframe_to_xlsx_bytes(export_df)
            png_bytes = figure_to_png_bytes(figure)
            project_name_fragment = build_safe_export_name(
                batch["project_name"] if "project_name" in batch.keys() else None,
                "project",
            )
            lot_no_fragment = build_safe_export_name(
                batch["lot_no"] if "lot_no" in batch.keys() else None,
                f"batch_{batch['id']}",
            )

            st.markdown("**\u5f53\u524d\u6279\u6b21\u5bfc\u51fa**")
            export_format = st.radio(
                "\u5bfc\u51fa\u6570\u636e\u683c\u5f0f",
                options=["Excel (.xlsx)", "CSV (.csv)"],
                horizontal=True,
                key="export_format",
            )
            export_button_cols = st.columns(2)
            export_button_cols[0].download_button(
                label="\u5bfc\u51fa\u5f53\u524d\u6279\u6b21\u6570\u636e",
                data=xlsx_bytes if export_format == "Excel (.xlsx)" else csv_bytes,
                file_name=(
                    f"{project_name_fragment}_batch_{batch['id']}_{lot_no_fragment}_results.xlsx"
                    if export_format == "Excel (.xlsx)"
                    else f"{project_name_fragment}_batch_{batch['id']}_{lot_no_fragment}_results.csv"
                ),
                mime=(
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    if export_format == "Excel (.xlsx)"
                    else "text/csv"
                ),
                width="stretch",
            )
            export_button_cols[1].download_button(
                label="\u5bfc\u51fa\u5f53\u524d LJ \u56fe PNG",
                data=png_bytes,
                file_name=(
                    f"{project_name_fragment}_batch_{batch['id']}_{lot_no_fragment}_"
                    f"{build_safe_export_name(chart_view_mode, 'chart')}.png"
                ),
                mime="image/png",
                width="stretch",
            )

            st.divider()
            st.markdown("**\u6708\u5ea6\u8d28\u63a7\u56fe\u5bfc\u51fa**")
            st.caption("\u4ec5\u5bfc\u51fa\u6b63\u5f0f\u6570\u636e\uff0c\u65e5\u671f\u8303\u56f4\u6700\u957f 30 \u5929\u3002")
            formal_qc_df = qc_df[qc_df["phase"] == "\u6b63\u5f0f\u6570\u636e"].copy() if "phase" in qc_df.columns else pd.DataFrame()
            if formal_qc_df.empty:
                st.info("\u5f53\u524d\u6279\u6b21\u8fd8\u6ca1\u6709\u6b63\u5f0f\u8d28\u63a7\u6570\u636e\u3002")
            else:
                default_monthly_start = formal_qc_df["test_time"].min().date()
                default_monthly_end = formal_qc_df["test_time"].max().date()
                monthly_col_start, monthly_col_end = st.columns(2)
                monthly_start = monthly_col_start.date_input(
                    "\u5f00\u59cb\u65e5\u671f",
                    value=default_monthly_start,
                    key="monthly_export_start",
                )
                monthly_end = monthly_col_end.date_input(
                    "\u7ed3\u675f\u65e5\u671f",
                    value=default_monthly_end,
                    key="monthly_export_end",
                )

                monthly_error = ""
                day_span = (pd.Timestamp(monthly_end).date() - pd.Timestamp(monthly_start).date()).days + 1
                if monthly_end < monthly_start:
                    monthly_error = "\u7ed3\u675f\u65e5\u671f\u4e0d\u80fd\u65e9\u4e8e\u5f00\u59cb\u65e5\u671f\u3002"
                elif day_span > 30:
                    monthly_error = "\u6708\u5ea6\u8d28\u63a7\u56fe\u5bfc\u51fa\u8303\u56f4\u6700\u957f\u4e3a30\u5929\uff0c\u8bf7\u91cd\u65b0\u9009\u62e9\u65e5\u671f\u8303\u56f4\u3002"

                monthly_png_bytes = None
                monthly_file_name = None
                if monthly_error:
                    st.warning(monthly_error)
                else:
                    monthly_qc_df = build_monthly_export_dataframe(
                        qc_df=qc_df,
                        start_date=monthly_start,
                        end_date=monthly_end,
                    )
                    if monthly_qc_df.empty:
                        st.info("\u6240\u9009\u65e5\u671f\u8303\u56f4\u5185\u6ca1\u6709\u6b63\u5f0f\u8d28\u63a7\u6570\u636e\uff0c\u65e0\u6cd5\u5bfc\u51fa\u6708\u5ea6\u8d28\u63a7\u56fe\u3002")
                    else:
                        monthly_title = build_monthly_chart_title(
                            batch=batch,
                            start_date=monthly_start,
                            end_date=monthly_end,
                        )
                        monthly_figure = plot_lj_chart(
                            qc_df=monthly_qc_df,
                            stats=stats,
                            title=monthly_title,
                            view_mode="\u6b63\u5f0f\u8d28\u63a7\u56fe",
                            y_axis_mode=chart_y_axis_mode,
                            standard_sd_limit=float(st.session_state.get("chart_standard_sd_limit", 4.0)),
                        )
                        monthly_png_bytes = figure_to_png_bytes(monthly_figure)
                        monthly_file_name = (
                            f"{project_name_fragment}_batch_{batch['id']}_{lot_no_fragment}_monthly_qc_"
                            f"{monthly_start.strftime('%Y-%m-%d')}_to_{monthly_end.strftime('%Y-%m-%d')}.png"
                        )

                st.download_button(
                    label="\u5bfc\u51fa\u6708\u5ea6\u8d28\u63a7\u56fe PNG",
                    data=monthly_png_bytes if monthly_png_bytes is not None else b"",
                    file_name=(
                        monthly_file_name
                        or f"{project_name_fragment}_batch_{batch['id']}_{lot_no_fragment}_monthly_qc.png"
                    ),
                    mime="image/png",
                    width="stretch",
                    disabled=monthly_png_bytes is None,
                )


def render_main_entry_page() -> None:
    chips = "".join(
        f'<div class="welcome-chip">{html_escape(label)}</div>'
        for label in [
            "LJ 曲线",
            "多水平 Z-score",
            "项目与批次管理",
            "建靶与正式质控",
            "图表分析与记录维护",
        ]
    )
    hero_html = dedent(
        f"""
        <div style="
            border:1px solid #d9e2ee;
            border-radius:18px;
            padding:20px 22px;
            background:linear-gradient(135deg, #f7fbff 0%, #eef5fb 55%, #f9fbfd 100%);
            margin:4px 0 14px 0;
        ">
            <div style="font-size:28px;font-weight:800;color:#1c3553;line-height:1.2;">
                实验室室内质控管理工具
            </div>
            <div style="margin-top:8px;font-size:14px;font-weight:600;color:#36587f;">
                用于实验室室内质控的 LJ 与多水平 Z-score 管理与判读工具
            </div>
            <div style="margin-top:10px;font-size:14px;line-height:1.7;color:#4e6076;">
                当前版本支持 LJ 曲线、多水平 Z-score、项目与批次管理、建靶与正式质控、
                图表查看、结果分析和记录维护，可作为内部试用、演示与小范围部署的交付基线。
            </div>
            <div class="welcome-chip-row">{chips}</div>
        </div>
        """
    ).strip()
    render_html_block(hero_html)

    render_html_block(
        dedent(
            """
            <div class="main-highlight-box">
                <div class="main-highlight-title">从哪里开始</div>
                <div class="main-highlight-body">
                    首次使用建议先选择 <strong>LJ</strong> 或 <strong>Z-score</strong> 页面，按“先建项目、再建批次、再录入结果”的顺序开始。
                    如果只是想了解方法差异和当前版本边界，可先阅读下方方法说明与使用教程。
                </div>
            </div>
            """
        ).strip()
    )

    st.divider()
    st.markdown("**功能入口与方法说明**")
    method_cards = [
        (
            "LJ",
            "适用于单水平 LJ 曲线质控。",
            [
                "支持项目与批次管理、建靶、正式质控与 Westgard 判读。",
                "支持标准视图 / 全范围视图、规则汇总、最新结果分析与记录维护。",
                "适合常规单水平室内质控流程。",
            ],
            "进入 LJ 页面",
            "LJ",
        ),
        (
            "Z-score",
            "适用于双水平 / 三水平多水平 IQC。",
            [
                "支持项目级水平数配置与批次级水平说明。",
                "支持建靶期 / 正式质控期、多水平检测记录录入、图表与结果分析。",
                "支持正式期记录维护，以及删除或编辑后的整批次重算。",
            ],
            "进入 Z-score 页面",
            "Z-score",
        ),
        (
            "Instant",
            "当前为预留页面。",
            [
                "本版本尚未接入正式业务逻辑。",
                "页面仅保留模块定位说明，不参与当前 LJ 与 Z-score 主流程。",
                "如需可用功能，请优先进入 LJ 或 Z-score 页面。",
            ],
            "查看 Instant 说明",
            "Instant",
        ),
    ]
    method_cols = st.columns(3, gap="large")
    for column, (title, caption, bullets, button_label, target_method) in zip(method_cols, method_cards):
        with column:
            bullet_html = "".join(f"<li>{html_escape(item)}</li>" for item in bullets)
            render_html_block(
                dedent(
                    f"""
                    <div class="main-entry-card">
                        <div class="main-entry-card-title">{html_escape(title)}</div>
                        <div class="main-entry-card-caption">{html_escape(caption)}</div>
                        <ul class="main-entry-card-list">{bullet_html}</ul>
                    </div>
                    """
                ).strip()
            )
            if st.button(button_label, key=f"main_jump_{target_method}", width="stretch"):
                switch_top_level_method(target_method)

    st.divider()
    st.markdown("**快速开始**")
    quick_start_col1, quick_start_col2 = st.columns(2, gap="large")
    with quick_start_col1:
        st.markdown("**LJ 快速开始**")
        st.markdown(
            "1. 新建 LJ 项目。\n"
            "2. 新建批次，并确认建靶所需次数。\n"
            "3. 录入检测结果，累计建靶数据。\n"
            "4. 建靶完成后自动进入正式质控，并开始 Westgard 判读。\n"
            "5. 在图表区查看趋势、规则汇总与最新结果分析；如需修正历史数据，可进入记录维护。"
        )
        if st.button("从 LJ 开始", key="main_quickstart_lj", width="stretch"):
            switch_top_level_method("LJ")
    with quick_start_col2:
        st.markdown("**Z-score 快速开始**")
        st.markdown(
            "1. 新建 Z-score 项目，并选择双水平或三水平。\n"
            "2. 新建批次并配置各水平名称或说明。\n"
            "3. 录入多水平检测记录，完成建靶。\n"
            "4. 建靶完成后进入正式质控，查看单水平图、合并图和最新结果分析。\n"
            "5. 如需修正正式期检测记录，可通过记录维护入口编辑或删除，系统会自动整批次重算。"
        )
        if st.button("从 Z-score 开始", key="main_quickstart_zscore", width="stretch"):
            switch_top_level_method("Z-score")

    st.divider()
    st.markdown("**使用说明与版本边界**")
    with st.expander("LJ 使用说明", expanded=False):
        st.markdown(
            "- 适用场景：单水平室内质控、LJ 曲线查看与 Westgard 规则判读。\n"
            "- 基本概念：项目用于区分分析物或方法，批次用于承载同一组质控材料、批号和建靶参数。\n"
            "- 建靶所需次数：以批次中的“建靶所需次数”为准；完成前仅累计统计，不启用正式规则判读。\n"
            "- 正式质控后：可在图表区查看建靶图、正式质控图、全部数据图，以及标准视图 / 全范围视图。\n"
            "- 记录维护：可修改或删除历史检测记录；删除后会重算后续检测序号、阶段和判读结果。"
        )

    with st.expander("Z-score 使用说明", expanded=False):
        st.markdown(
            "- 适用场景：双水平 / 三水平多水平 IQC 管理与判读。\n"
            "- 项目级水平数配置：决定该项目固定采用双水平或三水平流程；创建后批次会自动继承。\n"
            "- 批次级水平说明：用于给默认的水平 1 / 水平 2 / 水平 3 添加业务名称，便于录入与维护。\n"
            "- 建靶期与正式质控期：建靶期用于累计实验室正式靶值；只有全部水平达到建靶条件后，才进入正式质控期。\n"
            "- 图表理解：单水平图用于查看单个水平趋势；合并图用于对比多个水平；数据范围可切换为建靶期图、正式质控图或全图。\n"
            "- 厂家参考值：仅供参考，不直接替代实验室正式靶值；当前版本仅支持手工录入。\n"
            "- 正式期实时统计：只基于正式期在控数据计算，警告和失控结果不纳入统计。"
        )

    with st.expander("常见说明 / 注意事项", expanded=False):
        st.markdown(
            "- 建靶期不启用正式规则判读。\n"
            "- Z-score 批次进入正式质控后，建靶期检测记录会被锁定，只可查看，不可再维护。\n"
            "- 删除 Z-score 检测记录后会触发整批次重算，包括建靶统计、正式靶值、正式期实时统计和图表基础数据。\n"
            "- 检测序号是业务序号，与数据库内部编号不同。\n"
            "- 厂家参考值当前仅支持手工录入，不支持 COA 自动解析。"
        )

    with st.expander("当前版本说明", expanded=False):
        st.caption("当前版本已具备主流程使用能力，但仍以内部试用、演示和小范围部署为主要交付场景。")
        support_col, limit_col = st.columns(2, gap="large")
        with support_col:
            st.markdown("**已支持**")
            st.markdown(
                "- LJ 主流程\n"
                "- Z-score 双水平 / 三水平主流程\n"
                "- 多水平检测记录持久化\n"
                "- 建靶 / 正式质控\n"
                "- 图表查看、结果分析与记录维护\n"
                "- LJ 导出与月度质控图导出"
            )
        with limit_col:
            st.markdown("**暂未支持**")
            st.markdown(
                "- 批量导入\n"
                "- Z-score 导出\n"
                "- COA 解析\n"
                "- peer-group 数据\n"
                "- target freeze / re-establish 等高级流程\n"
                "- Instant 正式业务功能"
            )

    st.info("可直接点击上方按钮进入 LJ、Z-score 或 Instant 页面；也可以使用顶部“功能入口”切换。")


def render_zscore_placeholder_page() -> None:
    st.subheader("Z-score")
    st.caption("Z-score 页面用于多水平 IQC 流程管理；项目创建时固定双水平或三水平，批次自动继承。")
    projects_df, selected_project_id, batches_df, selected_batch_id = prepare_zscore_project_batch_context()
    manage_tab, work_tab = st.tabs([TEXT["manage"], TEXT["current_batch"]])
    render_zscore_project_batch_management(
        manage_tab,
        projects_df,
        selected_project_id,
        batches_df,
        selected_batch_id,
    )

    guard_work_tab_selection(work_tab, selected_project_id, selected_batch_id)

    batch_context = resolve_zscore_batch_context(selected_batch_id)
    batch = batch_context["batch"]
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

    level_count = int(batch_context["level_count"])
    template_id = str(batch_context["template_id"])
    template = batch_context["template"]
    required_level_ids = list(batch_context["required_level_ids"])
    level_label_map = dict(batch_context["level_label_map"])

    with work_tab:
        entry_col, chart_col = st.columns([1.0, 1.18], gap="large")

        history_runs = get_zscore_runs(selected_batch_id, template_id)
        operator_options = build_zscore_operator_options(history_runs)
        required_n = int(batch_context["required_n"])
        level_target_profiles = get_zscore_level_targets(selected_batch_id, template_id, required_n=required_n)
        overall_phase = determine_zscore_phase(level_target_profiles, required_level_ids)
        overall_phase_label = get_phase_label(overall_phase)
        formal_rules_enabled = should_enable_formal_rules(level_target_profiles, required_level_ids)
        default_phase_scope = "building" if overall_phase == PHASE_TARGET_BUILDING else "formal"
        sync_zscore_workbench_state(selected_batch_id, template, default_phase_scope)

        with st.container():
            render_zscore_batch_summary_row(
                batch,
                overall_phase_label,
                formal_rules_enabled,
                format_zscore_template_display_name(template),
                required_level_ids,
            )

        with chart_col:
            st.subheader("图表与判读")
            phase_scope, view_mode, selected_level, y_axis_mode, standard_sd_limit = render_zscore_chart_controls(
                template,
                default_phase_scope,
                level_label_map,
            )

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

        current_level_results, input_errors, _ = build_zscore_current_level_results(template)

        notice_message = str(st.session_state.pop("zscore_notice", "") or "")
        with entry_col:
            st.subheader("录入与统计")
            if notice_message:
                st.success(notice_message)
            st.caption(
                f"当前项目固定为 {level_count} 水平｜当前采用 {format_zscore_template_display_name(template)}｜"
                f"各水平累计达到 {required_n} 次且形成有效 SD 后，系统才进入正式质控。"
            )

            st.markdown("**本次数据录入**")
            st.datetime_input("检测时间", key="zscore_entry_test_time")
            st.text_input(
                "检测人",
                key="zscore_entry_operator",
                placeholder="请输入本次检测人",
            )
            if operator_options:
                st.caption(f"最近使用的检测人：{', '.join(operator_options[:5])}")

            st.divider()
            st.markdown("**多水平结果录入**")
            level_render_config = {
                "Level 1": ("zscore_level1_value", "zscore-level1-log10-value", "zscore-level1-log10-hint"),
                "Level 2": ("zscore_level2_value", "zscore-level2-log10-value", "zscore-level2-log10-hint"),
                "Level 3": ("zscore_level3_value", "zscore-level3-log10-value", "zscore-level3-log10-hint"),
            }
            for level_id in template["level_ids"]:
                display_label, level_caption = format_zscore_level_display(level_id, level_label_map)
                value_key, value_element_id, hint_element_id = level_render_config[level_id]
                render_zscore_level_input_block(
                    level_label=display_label,
                    value_key=value_key,
                    value_element_id=value_element_id,
                    hint_element_id=hint_element_id,
                    level_caption=level_caption,
                )

            if st.button("保存本次检测结果", type="primary", width="stretch"):
                validation_errors = list(input_errors)
                cleaned_operator = str(st.session_state.get("zscore_entry_operator", "") or "").strip()
                if st.session_state.get("zscore_entry_test_time") is None:
                    validation_errors.append("请填写检测时间。")
                if not cleaned_operator:
                    validation_errors.append("请填写检测人。")
                for level_result in current_level_results:
                    if level_result["raw_value"] is None:
                        validation_errors.append(
                            f"{format_level_id_display(level_result['level_id'])}的检测值必须为有效正数。"
                        )

                if validation_errors:
                    st.error("\n".join(dict.fromkeys(validation_errors)))
                else:
                    try:
                        create_zscore_run(
                            batch_id=selected_batch_id,
                            test_time=st.session_state["zscore_entry_test_time"],
                            operator=cleaned_operator,
                            level_results=current_level_results,
                            template_id=template_id,
                            required_n=required_n,
                        )
                    except ValueError as exc:
                        st.error(str(exc))
                    else:
                        st.session_state["zscore_notice"] = "Z-score 检测记录已保存。"
                        st.session_state["zscore_reset_entry_form"] = True
                        st.rerun()

            st.divider()
            st.markdown("**各水平建靶与正式统计**")
            stat_cols = st.columns(len(required_level_ids), gap="large")
            for stat_col, level_id in zip(stat_cols, required_level_ids):
                profile = level_target_profiles[level_id]
                display_label, level_caption = format_zscore_level_display(level_id, level_label_map)
                with stat_col:
                    st.markdown(f"**{display_label}**")
                    if level_caption:
                        st.caption(level_caption)
                    render_compact_stat_metrics(
                        [
                            ("已收集", f"{profile['collected_n']}"),
                            ("建靶要求", f"{profile['required_n']} 次"),
                            ("已达建靶条件", "是" if profile["is_ready"] else "否"),
                            ("阶段", profile["phase_label"]),
                        ]
                    )
                    render_zscore_profile_stat_line(
                        "厂家参考（仅参考）",
                        profile.get("vendor_reference_mean"),
                        profile.get("vendor_reference_sd"),
                        profile.get("vendor_reference_cv"),
                    )
                    if profile.get("vendor_reference_source_note"):
                        st.caption(f"来源备注：{profile['vendor_reference_source_note']}")
                    render_zscore_profile_stat_line(
                        "建靶统计",
                        profile.get("provisional_mean"),
                        profile.get("provisional_sd"),
                        profile.get("provisional_cv"),
                    )
                    render_zscore_profile_stat_line(
                        "正式靶值",
                        profile.get("final_target_mean"),
                        profile.get("final_target_sd"),
                        profile.get("final_target_cv"),
                    )
                    render_zscore_profile_stat_line(
                        "正式期实时统计",
                        profile.get("realtime_mean"),
                        profile.get("realtime_sd"),
                        profile.get("realtime_cv"),
                    )
                    render_zscore_vendor_reference_editor(
                        batch_id=selected_batch_id,
                        template_id=template_id,
                        level_id=level_id,
                        level_display_name=display_label,
                        required_n=required_n,
                        profile=profile,
                    )

        plot_df = build_zscore_plot_dataframe(history_runs, None, display_phase=None)
        building_run_ids = get_building_stat_run_ids(history_runs)
        building_history_runs = [
            run for run in history_runs if int(run.get("run_id") or 0) in building_run_ids
        ]
        formal_history_runs = [run for run in history_runs if str(run.get("phase")) == PHASE_FORMAL_QC]
        if phase_scope == "building":
            latest_run = building_history_runs[-1] if building_history_runs else None
        elif phase_scope == "formal":
            latest_run = formal_history_runs[-1] if formal_history_runs else None
        else:
            latest_run = history_runs[-1] if history_runs else None
        if phase_scope == "building" and latest_run is not None:
            latest_run = dict(latest_run)
            latest_run["phase"] = PHASE_TARGET_BUILDING
            latest_run["phase_label"] = get_phase_label(PHASE_TARGET_BUILDING)
            latest_run["run_status"] = PHASE_TARGET_BUILDING
            latest_run["rule_hits_run"] = []
            latest_run["error_type_hint"] = "not_applicable"
            latest_run["analysis_prompt"] = "当前视图仅显示纳入建靶统计的观察点，不输出正式质控结论。"

        with chart_col:
            phase_title = {
                "building": "建靶期图",
                "formal": "正式质控图",
                "all": "全图",
            }[phase_scope]
            if view_mode == "单水平视图":
                figure = plot_zscore_single_level(
                    plot_df=plot_df,
                    level_id=selected_level,
                    title=f"{phase_title}｜{format_zscore_level_display(selected_level, level_label_map)[0]}",
                    phase_scope=phase_scope,
                    y_axis_mode=y_axis_mode,
                    standard_sd_limit=standard_sd_limit,
                )
            else:
                figure = plot_zscore_overlay(
                    plot_df=plot_df,
                    title=f"{phase_title}｜{format_zscore_template_display_name(template)}",
                    active_levels=required_level_ids,
                    phase_scope=phase_scope,
                    y_axis_mode=y_axis_mode,
                    standard_sd_limit=standard_sd_limit,
                )
            st.pyplot(figure, clear_figure=False, width="stretch")
            render_zscore_latest_analysis_panel(latest_run, overall_phase, formal_rules_enabled)
            render_zscore_rules_config_expander(template, overall_phase, formal_rules_enabled)

        st.divider()
        st.subheader("记录维护")
        st.caption("主工作区保持录入与判读；如需修正历史检测记录，请在弹窗中编辑或删除，系统会按当前批次全量重算。")
        open_zscore_maintenance_disabled = not history_runs
        if st.button(
            "打开 Z-score 记录维护",
            key="open_zscore_record_maintenance_dialog",
            width="stretch",
            disabled=open_zscore_maintenance_disabled,
        ):
            bump_zscore_record_maintenance_dialog_nonce()
            st.session_state["show_zscore_record_maintenance_dialog"] = True
        if open_zscore_maintenance_disabled:
            st.info("当前批次暂无已保存的检测记录可维护。")
        if st.session_state.get("show_zscore_record_maintenance_dialog", False):
            render_zscore_record_maintenance_dialog(history_runs, batch_context)

def render_instant_placeholder_page() -> None:
    st.subheader("Instant")
    st.caption("Instant 页面目前作为预留入口保留。当前版本暂未接入正式业务流程。")

    status_col, guide_col = st.columns(2, gap="large")
    with status_col:
        st.markdown("**当前版本状态**")
        st.markdown(
            "- 本页当前仅用于说明模块定位。\n"
            "- 暂不提供正式数据录入、规则判读、图表输出或导出能力。\n"
            "- 该页面不会影响 LJ 与 Z-score 现有主流程。"
        )
    with guide_col:
        st.markdown("**建议使用路径**")
        st.markdown(
            "- 如需立即开展单水平室内质控，请进入 LJ 页面。\n"
            "- 如需开展双水平 / 三水平多水平 IQC，请进入 Z-score 页面。\n"
            "- Instant 后续若接入正式功能，会在此页面补充明确说明。"
        )

    st.info("当前可用的质控流程请从顶部“功能入口”进入 LJ 或 Z-score 页面。")


def render_lj_mode() -> None:
    projects_df, selected_project_id, batches_df, selected_batch_id = prepare_project_batch_context()
    manage_tab, work_tab = st.tabs([TEXT["manage"], TEXT["current_batch"]])
    render_project_batch_management(
        manage_tab,
        projects_df,
        selected_project_id,
        batches_df,
        selected_batch_id,
    )

    guard_work_tab_selection(work_tab, selected_project_id, selected_batch_id)
    render_lj_page(work_tab, selected_batch_id)


def render_page_chrome() -> None:
    render_html_block(
        dedent(
            """
            <div class="top-feedback-bar">
                <a
                    class="top-feedback-link"
                    href="https://docs.qq.com/sheet/DY3V4b0FqS3psbkdK?tab=BB08J2"
                    target="_blank"
                    rel="noopener noreferrer"
                >
                    \u95ee\u9898\u53cd\u9988
                </a>
            </div>
            """
        ).strip()
    )
    st.title(TEXT["app_title"])
    st.caption("当前版本适用于内部试用、演示与小范围部署；如需反馈问题，可使用右上角“问题反馈”。")


render_page_chrome()
selected_method = st.radio(
    "功能入口",
    options=["首页", "LJ", "Z-score", "Instant"],
    horizontal=True,
    key="top_level_method_selector",
)

if selected_method in {"首页", "Main"}:
    render_main_entry_page()
elif selected_method == "LJ":
    render_lj_mode()
elif selected_method == "Z-score":
    render_zscore_placeholder_page()
else:
    render_instant_placeholder_page()
