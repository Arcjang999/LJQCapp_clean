from __future__ import annotations

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
    create_zscore_run,
    determine_zscore_phase,
    get_zscore_level_targets,
    get_phase_label,
    get_zscore_runs,
    resolve_zscore_batch_context,
    should_enable_formal_rules,
    upsert_zscore_level_target,
)
from zscore_plotting import plot_zscore_overlay, plot_zscore_single_level


PAGE_TITLE = "\u5b9e\u9a8c\u5ba4\u8d28\u63a7 LJ \u66f2\u7ebf"
TEXT = {
    "app_title": "\u5b9e\u9a8c\u5ba4\u8d28\u63a7 LJ \u66f2\u7ebf\u8f6f\u4ef6",
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
    "level_count": "Level 数",
}

ZSCORE_PHASE_VIEW_OPTIONS = {
    "building": "建靶期图",
    "formal": "正式质控图",
    "all": "全图",
}


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
    @media (max-width: 1680px) {
        .batch-summary-grid {
            grid-template-columns: repeat(auto-fit, minmax(108px, 1fr));
            gap: 7px;
        }
        .compact-summary-grid {
            grid-template-columns: repeat(auto-fit, minmax(104px, 1fr));
            gap: 8px;
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
    return f"项目 {row['id']} | {project_name} | {level_count}-level"


def build_zscore_batch_label(row: pd.Series) -> str:
    row = dict(row)
    instrument = _clean_selector_label_part(row.get("instrument"), "未填写仪器")
    reagent = _clean_selector_label_part(row.get("reagent"), "未填写试剂")
    lot_no = _clean_selector_label_part(row.get("lot_no"), "未填写")
    level_count = int(row.get("level_count", 0) or 0)
    return (
        f"批次 {row['id']} | {level_count}-level | {instrument} | "
        f"{reagent} | 质控批号 {lot_no}"
    )


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
                    "项目 level_count",
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
                st.info("当前还没有 Z-score 项目，请先创建项目并确定 2-level 或 3-level。")
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
                        st.text(f"level_count：{int(current_project['level_count'])}-level")
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
                                        st.success("项目名称已更新，level_count 保持不变。")
                                        st.rerun()

        with top_right:
            st.subheader("新建 Z-score 批次")
            if selected_project_id is None:
                st.info("请先选择 Z-score 项目。")
            else:
                current_project = get_zscore_project(selected_project_id)
                project_level_count = int(current_project["level_count"])
                st.caption(
                    f"当前批次将归属于项目：{current_project['name']}｜固定为 {project_level_count}-level。"
                )
                with st.form("create_zscore_batch_form", clear_on_submit=True):
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
                        st.text(f"项目：{current_batch['project_name']}")
                        st.text(f"批次：{current_batch['id']}")
                        st.text(f"level_count：{int(current_batch['level_count'])}-level")
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
                                    st.success("批次质控品批号已更新，level_count 保持不变。")
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
            f"标准视图按 Mean ± {standard_sd_limit:g}SD 聚焦主要波动区间，超界点会用边界标记和原始值提示；"
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
) -> None:
    st.markdown(f"**{level_label}**")
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
            validation_errors.append(f"{level_id} 检测值必须为有效正数。")

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
    expected_columns = [
        "run_id",
        "run_index",
        "test_time",
        "level_id",
        "zscore",
        "status",
        "rule_hits",
        "raw_value",
        "log_value",
        "phase",
        "is_preview",
    ]
    rows: list[dict[str, Any]] = []
    for run in saved_runs:
        run_phase = str(run.get("phase", PHASE_TARGET_BUILDING))
        if display_phase and run_phase != display_phase:
            continue
        for level_result in run.get("level_results", []):
            zscore = level_result.get("zscore")
            if zscore is None:
                continue
            rows.append(
                {
                    "run_id": run.get("run_id"),
                    "run_index": run.get("run_id"),
                    "test_time": run.get("test_time"),
                    "level_id": level_result.get("level_id"),
                    "zscore": zscore,
                    "status": level_result.get("status", run.get("run_status", "accept")),
                    "rule_hits": ",".join(level_result.get("rule_hits_local", [])),
                    "raw_value": level_result.get("raw_value"),
                    "log_value": level_result.get("log_value"),
                    "phase": run_phase,
                    "is_preview": False,
                }
            )

    if draft_run and draft_run.get("run_status") != "pending":
        draft_phase = str(draft_run.get("phase", PHASE_TARGET_BUILDING))
        if display_phase and draft_phase != display_phase:
            return pd.DataFrame(rows, columns=expected_columns)
        preview_run_index = len(saved_runs) + 1
        for level_result in draft_run.get("level_results", []):
            zscore = level_result.get("zscore")
            if zscore is None:
                continue
            rows.append(
                {
                    "run_id": preview_run_index,
                    "run_index": preview_run_index,
                    "test_time": draft_run.get("test_time"),
                    "level_id": level_result.get("level_id"),
                    "zscore": zscore,
                    "status": level_result.get("status", draft_run.get("run_status", "accept")),
                    "rule_hits": ",".join(level_result.get("rule_hits_local", [])),
                    "raw_value": level_result.get("raw_value"),
                    "log_value": level_result.get("log_value"),
                    "phase": draft_phase,
                    "is_preview": True,
                }
            )

    return pd.DataFrame(rows, columns=expected_columns)


def format_zscore_rule_hits(rule_hits: list[dict[str, Any]]) -> str:
    if not rule_hits:
        return "无"
    ordered_rule_ids = list(dict.fromkeys(hit["rule_id"] for hit in rule_hits))
    return "、".join(ordered_rule_ids)


def build_zscore_chart_control_title(template: dict[str, Any], view_mode: str, selected_level: str) -> str:
    scope_text = selected_level if view_mode == "单水平视图" else "全部 Level"
    return f"图表控制（点击展开）｜{template['label']}｜{view_mode}｜{scope_text}"


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
                "Level Selector",
                options=template["level_ids"],
                horizontal=True,
                key="zscore_selected_level",
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
            st.info("建靶已完成，正式规则已启用。请录入首个正式质控 run。")
        else:
            st.info("当前阶段为建靶中。请先录入 run，用于累计靶值与观察趋势。")
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
    source_text = "当前输入预览" if latest_run.get("is_preview") else f"最近已保存 run #{latest_run.get('run_id', '-')}"
    phase_label = str(latest_run.get("phase_label") or get_phase_label(latest_run.get("phase", overall_phase)))
    badge_text = phase_label if is_building_phase else status
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
                    '<div><strong>正式规则：</strong>当前仅用于累计靶值与观察趋势，暂不进行正式规则判读。</div>'
                    '<div style="margin-top:6px;">当前阶段为建靶中，本次结果只作为建靶观察，不输出正式 warning / reject。</div>'
                    if is_building_phase
                    else f"<div><strong>触发规则：</strong>{html_escape(format_zscore_rule_hits(latest_run.get('rule_hits_run', [])))}</div>"
                    f"<div><strong>误差类型提示：</strong>{html_escape(str(latest_run.get('error_type_hint', 'unknown')))}</div>"
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
    with st.expander("规则模板说明（点击展开）", expanded=False):
        st.caption(template["note"])
        st.markdown(f"- 当前模板：`{template['label']}`")
        st.markdown(f"- 当前阶段：`{get_phase_label(overall_phase)}`")
        st.markdown(f"- 正式规则启用：`{'是' if formal_rules_enabled else '否'}`")
        if formal_rules_enabled:
            st.markdown(f"- 当前启用规则：`{', '.join(template['rule_ids'])}`")
        else:
            st.markdown(f"- 模板规则预览：`{', '.join(template['rule_ids'])}`")
            st.caption("当前处于建靶中，以下规则仅作为正式质控期启用前的模板预览。")
        for rule in template["rules"]:
            prefix = "正式期规则预览" if not formal_rules_enabled else "规则"
            st.markdown(f"- {prefix} `{rule['rule_id']}` | `{rule['severity']}` | `{rule['scope']}`")


def render_zscore_profile_stat_line(label: str, mean_value: Any, sd_value: Any, cv_value: Any) -> None:
    st.caption(
        f"{label}："
        f"Mean {format_optional_float(mean_value)} | "
        f"SD {format_optional_float(sd_value)} | "
        f"CV {format_optional_float(cv_value, digits=2, suffix='%')}"
    )


def render_zscore_vendor_reference_editor(
    batch_id: int,
    template_id: str,
    level_id: str,
    required_n: int,
    profile: dict[str, Any],
) -> None:
    with st.expander("厂家参考值（仅供参考）", expanded=False):
        mean_default = format_optional_input_value(profile.get("vendor_reference_mean"))
        sd_default = format_optional_input_value(profile.get("vendor_reference_sd"))
        source_default = str(profile.get("vendor_reference_source_note", "") or "")
        state_prefix = f"zscore_vendor_{batch_id}_{template_id}_{level_id.replace(' ', '_')}"
        form_key = f"{state_prefix}_form"
        with st.form(form_key):
            mean_text = st.text_input("参考 Mean", value=mean_default, key=f"{state_prefix}_mean")
            sd_text = st.text_input("参考 SD", value=sd_default, key=f"{state_prefix}_sd")
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
                    validation_errors.append("厂家参考 Mean 必须为有效正数。")
                if sd_text.strip() and vendor_sd is None:
                    validation_errors.append("厂家参考 SD 必须为有效正数。")

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
                    st.session_state["zscore_notice"] = f"{level_id} 厂家参考值已保存。"
                    st.rerun()


def format_zscore_rule_hits(rule_hits: list[dict[str, Any]]) -> str:
    if not rule_hits:
        return "无"
    ordered_rule_ids = list(dict.fromkeys(hit["rule_id"] for hit in rule_hits))
    return "、".join(ordered_rule_ids)


def build_zscore_chart_control_title(
    template: dict[str, Any],
    phase_scope: str,
    view_mode: str,
    selected_level: str,
) -> str:
    scope_text = selected_level if view_mode == "单水平视图" else "全部 Level"
    phase_scope_label = ZSCORE_PHASE_VIEW_OPTIONS.get(phase_scope, "全图")
    return f"图表控制（点击展开）｜{template['label']}｜{phase_scope_label}｜{view_mode}｜{scope_text}"


def render_zscore_chart_controls(
    template: dict[str, Any],
    default_phase_scope: str,
) -> tuple[str, str, str]:
    phase_scope = st.session_state.get("zscore_phase_scope", default_phase_scope)
    if phase_scope not in ZSCORE_PHASE_VIEW_OPTIONS:
        phase_scope = default_phase_scope
        st.session_state["zscore_phase_scope"] = phase_scope

    view_mode = st.session_state.get("zscore_view_mode", "单水平视图")
    if view_mode not in {"单水平视图", "合并视图"}:
        view_mode = "单水平视图"
    selected_level = st.session_state.get("zscore_selected_level", template["level_ids"][0])
    if selected_level not in template["level_ids"]:
        selected_level = template["level_ids"][0]
        st.session_state["zscore_selected_level"] = selected_level

    with st.expander(
        build_zscore_chart_control_title(template, phase_scope, view_mode, selected_level),
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
        if st.session_state.get("zscore_selected_level") not in template["level_ids"]:
            st.session_state["zscore_selected_level"] = template["level_ids"][0]
        if view_mode == "单水平视图":
            selected_level = st.radio(
                "Level Selector",
                options=template["level_ids"],
                horizontal=True,
                key="zscore_selected_level",
            )
        else:
            selected_level = st.session_state.get("zscore_selected_level", template["level_ids"][0])
    return phase_scope, view_mode, selected_level


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
        return

    if st.session_state.get("zscore_phase_scope") not in ZSCORE_PHASE_VIEW_OPTIONS:
        st.session_state["zscore_phase_scope"] = default_phase_scope
    if st.session_state.get("zscore_selected_level") not in template["level_ids"]:
        st.session_state["zscore_selected_level"] = template["level_ids"][0]


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


def render_zscore_batch_summary_row(batch, phase_label: str, template_label: str) -> None:
    summary_items = [
        ("项目名称", batch["project_name"]),
        ("批次编号", batch["id"]),
        ("仪器", batch["instrument"]),
        ("试剂", batch["reagent"]),
        ("质控品", batch["qc_material"]),
        ("浓度", batch["concentration"]),
        ("质控品批号", batch["lot_no"]),
        ("level_count", f"{int(batch['level_count'])}-level"),
        ("规则模板", template_label),
        ("当前阶段", phase_label),
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
        f"\u8bb0\u5f55 {int(row['id'])} | \u5e8f\u53f7 {int(row['sequence'])} | "
        f"{test_time} | {float(row['value']):.4f} | {row['operator']}"
    )


def build_result_select_options(results_df: pd.DataFrame) -> tuple[list[str], dict[str, int | None]]:
    option_map = {"\u8bf7\u9009\u62e9\u68c0\u6d4b\u8bb0\u5f55": None}
    for _, row in results_df.iterrows():
        option_map[build_result_label(row)] = int(row["id"])
    return list(option_map.keys()), option_map


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
                    ("Mean", "-" if stats["mean"] is None else f"{stats['mean']:.4f}"),
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
                        ("\u5b9e\u65f6 Mean", "-" if realtime_stats["mean"] is None else f"{realtime_stats['mean']:.4f}"),
                        ("\u5b9e\u65f6 SD", "-" if realtime_stats["sd"] is None else f"{realtime_stats['sd']:.4f}"),
                        ("\u5b9e\u65f6 CV%", "-" if realtime_stats["cv"] is None else f"{realtime_stats['cv']:.2f}%"),
                    ]
                )
                if realtime_message:
                    st.info(realtime_message)
                st.caption(
                    "\u7edf\u8ba1\u53e3\u5f84\uff1a\u5b9e\u65f6\u7edf\u8ba1\u4ec5\u57fa\u4e8e\u5f53\u524d\u6279\u6b21\u4e2d\u5224\u5b9a\u4e3a\u201c\u5728\u63a7\u201d\u7684\u6b63\u5f0f\u6570\u636e\u8ba1\u7b97\uff0c"
                    "\u5df2\u81ea\u52a8\u6392\u9664\u8b66\u544a\u548c\u5931\u63a7\u7ed3\u679c\uff1b"
                    "\u5f53\u68c0\u6d4b\u8bb0\u5f55\u88ab\u4fee\u6539\u6216\u5220\u9664\u540e\uff0c\u5b9e\u65f6 Mean / SD / CV% \u53ef\u80fd\u968f\u4e4b\u81ea\u52a8\u53d8\u5316\u3002"
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
                        "\u6807\u51c6\u89c6\u56fe\u8303\u56f4\uff08Mean \u00b1 nSD\uff09",
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
            st.markdown(
                "- `1_2s`\uff1a\u5355\u70b9\u8d85\u8fc7 \u00b12SD\uff0c\u8b66\u544a\n"
                "- `1_3s`\uff1a\u5355\u70b9\u8d85\u8fc7 \u00b13SD\uff0c\u5931\u63a7\n"
                "- `2_2s`\uff1a\u8fde\u7eed 2 \u4e2a\u70b9\u540c\u4fa7\u8d85\u8fc7 \u00b12SD\uff0c\u5931\u63a7\n"
                "- `R_4s`\uff1a\u8fde\u7eed 2 \u4e2a\u70b9\u5dee\u503c\u8d85\u8fc7 4SD\uff0c\u5931\u63a7\n"
                "- `4_1s`\uff1a\u8fde\u7eed 4 \u4e2a\u70b9\u540c\u4fa7\u8d85\u8fc7 \u00b11SD\uff0c\u5931\u63a7\n"
                "- `10x`\uff1a\u8fde\u7eed 10 \u4e2a\u70b9\u4f4d\u4e8e\u5747\u503c\u540c\u4fa7\uff0c\u5931\u63a7"
            )

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
    st.subheader("\u65b9\u6cd5\u5165\u53e3")
    st.caption("\u8bf7\u5148\u9009\u62e9\u5f53\u524d\u9700\u8981\u4f7f\u7528\u7684\u8d28\u63a7\u65b9\u6cd5\u9875\u9762\u3002")
    st.info("LJ \u9875\u9762\u4fdd\u7559\u5f53\u524d\u5b8c\u6574\u5de5\u4f5c\u6d41\uff1bZ-score \u548c Instant \u9875\u9762\u6682\u4e3a\u5360\u4f4d\u9875\u3002")


def render_zscore_placeholder_page() -> None:
    st.subheader("Z-score")
    st.caption("Z-score 当前按 IQC 多水平流程运行，项目创建时固定 2-level 或 3-level，批次自动继承。")
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
            render_zscore_batch_summary_row(batch, overall_phase_label, template["label"])

        with chart_col:
            st.subheader("图表与判读")
            phase_scope, view_mode, selected_level = render_zscore_chart_controls(
                template,
                default_phase_scope,
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
                f"当前项目固定为 {level_count}-level｜模板自动绑定为 {template['label']}｜"
                f"每个 level 累计 {required_n} 次且具备有效 SD 后才算 ready。"
            )

            st.markdown("**本次数据录入**")
            st.datetime_input("检测时间", key="zscore_entry_test_time")
            st.text_input(
                "检测人",
                key="zscore_entry_operator",
                placeholder="请输入本次检测人",
            )
            if operator_options:
                st.caption(f"历史检测人：{', '.join(operator_options[:5])}")

            st.divider()
            st.markdown("**多水平结果录入**")
            level_render_config = {
                "Level 1": ("zscore_level1_value", "zscore-level1-log10-value", "zscore-level1-log10-hint"),
                "Level 2": ("zscore_level2_value", "zscore-level2-log10-value", "zscore-level2-log10-hint"),
                "Level 3": ("zscore_level3_value", "zscore-level3-log10-value", "zscore-level3-log10-hint"),
            }
            for level_id in template["level_ids"]:
                value_key, value_element_id, hint_element_id = level_render_config[level_id]
                render_zscore_level_input_block(
                    level_label=level_id,
                    value_key=value_key,
                    value_element_id=value_element_id,
                    hint_element_id=hint_element_id,
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
                        validation_errors.append(f"{level_result['level_id']} 检测值必须为有效正数。")

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
                        st.session_state["zscore_notice"] = "Z-score run 已保存。"
                        st.session_state["zscore_reset_entry_form"] = True
                        st.rerun()

            st.divider()
            st.markdown("**各 Level 建靶 / 正式统计**")
            stat_cols = st.columns(len(required_level_ids), gap="large")
            for stat_col, level_id in zip(stat_cols, required_level_ids):
                profile = level_target_profiles[level_id]
                with stat_col:
                    st.markdown(f"**{level_id}**")
                    render_compact_stat_metrics(
                        [
                            ("已收集", f"{profile['collected_n']}"),
                            ("required_n", f"{profile['required_n']}"),
                            ("Ready", "是" if profile["is_ready"] else "否"),
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
                        required_n=required_n,
                        profile=profile,
                    )

            st.divider()
            st.markdown("**总体阶段状态**")
            render_compact_stat_metrics(
                [
                    ("当前阶段", overall_phase_label),
                    ("全部 Level 已完成", "是" if formal_rules_enabled else "否"),
                    ("正式规则已启用", "是" if formal_rules_enabled else "否"),
                ]
            )
            st.write(
                "当前工作区已进入正式质控，正式规则与正式期实时统计均按真实持久化历史计算。"
                if formal_rules_enabled
                else "当前仍处于建靶中，正式规则未启用；本次 run 会继续累计 provisional 与 level readiness。"
            )

        plot_df = build_zscore_plot_dataframe(history_runs, None, display_phase=None)
        building_history_runs = [run for run in history_runs if str(run.get("phase")) == PHASE_TARGET_BUILDING]
        formal_history_runs = [run for run in history_runs if str(run.get("phase")) == PHASE_FORMAL_QC]
        if phase_scope == "building":
            latest_run = building_history_runs[-1] if building_history_runs else None
        elif phase_scope == "formal":
            latest_run = formal_history_runs[-1] if formal_history_runs else None
        else:
            latest_run = history_runs[-1] if history_runs else None

        with chart_col:
            phase_title = {
                "building": "建靶观察图",
                "formal": "正式 Z-score 图",
                "all": "全图",
            }[phase_scope]
            if view_mode == "单水平视图":
                figure = plot_zscore_single_level(
                    plot_df=plot_df,
                    level_id=selected_level,
                    title=f"{phase_title}｜{selected_level}",
                    phase_scope=phase_scope,
                )
            else:
                figure = plot_zscore_overlay(
                    plot_df=plot_df,
                    title=f"{phase_title}｜{template['label']}",
                    active_levels=required_level_ids,
                    phase_scope=phase_scope,
                )
            st.pyplot(figure, clear_figure=False, width="stretch")
            render_zscore_latest_analysis_panel(latest_run, overall_phase, formal_rules_enabled)
            render_zscore_rules_config_expander(template, overall_phase, formal_rules_enabled)

def render_instant_placeholder_page() -> None:
    st.subheader("Instant")
    st.caption("\u8be5\u9875\u9762\u9884\u7559\u7ed9\u540e\u7eed\u72ec\u7acb\u7684 Instant \u5de5\u4f5c\u6d41\uff0c\u5f53\u524d\u4ec5\u63d0\u4f9b\u9759\u6001\u9875\u9762\u7ed3\u6784\uff0c\u4e0d\u5305\u542b\u5206\u6790\u903b\u8f91\u3002")

    st.markdown("**\u8ba1\u5212\u4e2d\u7684\u6d41\u7a0b**")
    st.markdown(
        "- \u5efa\u7acb\u6216\u9009\u62e9\u540e\u7eed Instant \u5de5\u4f5c\u5bf9\u8c61\n"
        "- \u5f55\u5165\u6216\u5bfc\u5165\u5f85\u5206\u6790\u6570\u636e\n"
        "- \u5728\u9875\u9762\u5185\u67e5\u770b\u540e\u7eed\u6458\u8981\u3001\u7ed3\u679c\u4e0e\u8f93\u51fa\u533a\u57df\n"
        "- \u5728\u65b9\u6cd5\u5b66\u5b9e\u73b0\u540e\u8865\u5145\u5bfc\u51fa\u4e0e\u62a5\u544a\u80fd\u529b"
    )

    st.warning("Instant \u5206\u6790\u903b\u8f91\u3001\u8ba1\u7b97\u6d41\u7a0b\u4e0e\u7ed3\u679c\u5224\u8bfb\u76ee\u524d\u5c1a\u672a\u5b9e\u73b0\u3002")

    future_input_col, future_result_col = st.columns(2)
    with future_input_col:
        st.markdown("**\u9884\u7559\u8f93\u5165\u533a**")
        st.info("\u540e\u7eed\u5c06\u5728\u8fd9\u91cc\u653e\u7f6e Instant \u7684\u8f93\u5165\u8868\u5355\u3001\u6570\u636e\u5bfc\u5165\u5165\u53e3\u6216\u5de5\u4f5c\u5bf9\u8c61\u9009\u62e9\u533a\u57df\u3002")

    with future_result_col:
        st.markdown("**\u9884\u7559\u7ed3\u679c\u533a**")
        st.info("\u540e\u7eed\u5c06\u5728\u8fd9\u91cc\u653e\u7f6e Instant \u7684\u6458\u8981\u7ed3\u679c\u3001\u56fe\u8868\u548c\u5bfc\u51fa\u533a\u57df\u3002")

    with st.container():
        st.markdown("**\u5f53\u524d\u72b6\u6001**")
        st.write("\u5f53\u524d\u9875\u9762\u4ec5\u4e3a\u7ed3\u6784\u9aa8\u67b6\uff0c\u7528\u4e8e\u540e\u7eed\u72ec\u7acb\u63a5\u5165 Instant \u9875\u9762\u5185\u5bb9\u3002")


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
    st.caption("\u65e9\u671f\u5f00\u53d1\u7248\u672c\uff0c\u4ec5\u4f9b\u53c2\u8003\uff0c\u5982\u6709\u7591\u95ee\u53ef\u8054\u7cfb\u5f00\u53d1\u8005\u6216\u53f3\u4e0a\u89d2\u201c\u95ee\u9898\u53cd\u9988\u201d\u7559\u8a00\u3002")


render_page_chrome()
selected_method = st.radio(
    "\u65b9\u6cd5\u9875\u9762",
    options=["Main", "LJ", "Z-score", "Instant"],
    horizontal=True,
    key="top_level_method_selector",
)

if selected_method == "Main":
    render_main_entry_page()
elif selected_method == "LJ":
    render_lj_mode()
elif selected_method == "Z-score":
    render_zscore_placeholder_page()
else:
    render_instant_placeholder_page()
