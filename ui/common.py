from __future__ import annotations

import math
from html import escape as html_escape
from textwrap import dedent
from typing import Any

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from zscore_logic import PHASE_TARGET_BUILDING, build_zscore_batch_summary_items

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
    "cv_limit": "CV 要求（%）",
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

APP_TITLE = TEXT["app_title"]

def inject_global_styles() -> None:
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
    st.title(APP_TITLE)
    st.caption("当前版本适用于内部试用、演示与小范围部署；如需反馈问题，可使用右上角“问题反馈”。")

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

def summarize_note_for_table(note: Any, max_length: int = 24) -> str:
    text = str(note or "")
    if not text:
        return ""
    compact_text = " ".join(segment.strip() for segment in text.replace("\r", "\n").splitlines() if segment.strip())
    if len(compact_text) <= max_length:
        return compact_text
    return compact_text[:max_length].rstrip() + "..."

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
    if "manual_note" in display_df.columns:
        display_df["manual_note"] = display_df["manual_note"].fillna("").map(summarize_note_for_table)

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
        "manual_note",
    ]
    column_mapping = {
        "sequence": "\u68c0\u6d4b\u5e8f\u53f7",
        "test_time": "\u68c0\u6d4b\u65f6\u95f4",
        "operator": "\u68c0\u6d4b\u4eba",
        "value": "\u68c0\u6d4b\u503c",
        "log_value": "log\u503c",
        "manual_note": "\u5907\u6ce8",
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

def parse_optional_cv_limit_input(raw_value: str | None) -> tuple[float | None, str | None]:
    text = str(raw_value or "").strip()
    if not text:
        return None, None

    try:
        numeric = float(text)
    except ValueError:
        return None, "CV 要求（%）必须为有效数字。"

    if not math.isfinite(numeric) or numeric <= 0:
        return None, "CV 要求（%）必须大于 0。"
    return float(numeric), None

def get_saved_batch_cv_limit(batch: Any) -> float | None:
    raw_value = None
    if isinstance(batch, dict):
        raw_value = batch.get("cv_limit")
    elif hasattr(batch, "keys") and "cv_limit" in batch.keys():
        raw_value = batch["cv_limit"]
    else:
        raw_value = getattr(batch, "cv_limit", None)

    try:
        if raw_value is None or (isinstance(raw_value, float) and math.isnan(raw_value)):
            return None
        numeric = float(raw_value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric) or numeric <= 0:
        return None
    return numeric

def render_cv_limit_hint(current_cv: Any, cv_limit: float | None, subject: str) -> None:
    if cv_limit is None:
        return

    try:
        if current_cv is None or (isinstance(current_cv, float) and math.isnan(current_cv)):
            return
        resolved_current_cv = float(current_cv)
    except (TypeError, ValueError):
        return
    if not math.isfinite(resolved_current_cv):
        return

    message = (
        f"{subject} CV%：{resolved_current_cv:.2f}% | "
        f"批次要求：≤ {cv_limit:.2f}%"
    )
    if resolved_current_cv <= cv_limit:
        st.success(f"{message}，已满足要求。")
    else:
        st.warning(f"{message}，已超出要求。")

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

def render_import_review_summary(review_summary: dict[str, Any]) -> None:
    render_compact_stat_metrics(
        [
            ("总行数", str(review_summary["total_rows"])),
            ("可导入行数", str(review_summary["importable_rows"])),
            ("错误行数", str(review_summary["error_rows"])),
            ("警告行数", str(review_summary["warning_rows"])),
        ]
    )
    file_error_count = int(review_summary.get("file_error_count", 0) or 0)
    file_warning_count = int(review_summary.get("file_warning_count", 0) or 0)
    if file_error_count > 0 or file_warning_count > 0:
        parts: list[str] = []
        if file_error_count > 0:
            parts.append(f"文件级阻断：{file_error_count} 条")
        if file_warning_count > 0:
            parts.append(f"文件级提醒：{file_warning_count} 条")
        st.caption("；".join(parts))

    row_warning_groups = list(review_summary.get("row_warning_groups") or [])
    if row_warning_groups:
        st.caption("行级警告汇总：")
        for group in row_warning_groups:
            st.markdown(f"- {group['label']}：{group['row_count']} 行")

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
