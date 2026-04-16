from __future__ import annotations

from datetime import datetime
import hashlib
from html import escape as html_escape
from io import BytesIO
import math
from string import ascii_uppercase
from zipfile import ZIP_DEFLATED, ZipFile

import pandas as pd
import streamlit as st

from database import add_result, export_batch_results_for_phase, get_batch, get_results, update_result
from import_review import (
    build_lj_building_template_dataframe,
    build_review_issues_dataframe,
    review_lj_building_import_csv,
    review_lj_formal_import_csv,
)
from pages.management import (
    guard_work_tab_selection,
    prepare_project_batch_context,
    render_project_batch_management,
)
from plotting import figure_to_png_bytes, plot_lj_chart
from qc_logic import (
    calculate_qc_results,
    calculate_realtime_stats,
    calculate_target_building_cv_hint,
    disable_lj_building_result,
    keep_lj_building_result,
    persist_lj_batch_outlier_snapshot,
    restore_lj_building_result,
)
from services.outlier_service import (
    DEFAULT_GRUBBS_ALPHA,
    get_current_outlier_status_label,
)
from services.value_type_service import (
    get_input_value_type_label,
    get_measurement_label,
    normalize_input_value_type,
    parse_project_input_value,
)
from ui.common import (
    TEXT,
    build_chart_control_title,
    build_operator_options,
    build_safe_export_name,
    format_rule_code,
    format_rule_description,
    get_saved_batch_cv_limit,
    prepare_display_records,
    render_compact_stat_metrics,
    render_cv_limit_hint,
    render_import_review_summary,
    render_rule_summary_metrics,
    render_section_intro,
    render_standard_view_help,
    render_status_panel,
)
from ui.dialogs import (
    bump_record_maintenance_dialog_nonce as bump_record_maintenance_dialog_nonce_impl,
    render_record_maintenance_dialog as render_record_maintenance_dialog_impl,
    render_records_table as render_records_table_impl,
)

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
        "phase_label": "建靶期",
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
    st.markdown("**建靶期离群值判断模块**")
    render_compact_stat_metrics(
        [
            ("当前阶段", str(panel_data["phase_label"])),
            ("当前有效建靶点数", str(panel_data["effective_building_count"])),
            ("当前已禁用点数", str(panel_data["disabled_building_count"])),
            ("当前建靶均值", _format_lj_stat_text(panel_data["mean"])),
            ("当前建靶 SD", _format_lj_stat_text(panel_data["sd"])),
            ("当前建靶 CV%", _format_lj_stat_text(panel_data["cv"], digits=2, suffix="%")),
        ]
    )
    st.caption(str(panel_data["source_text"]))
    if not bool(panel_data["grubbs_ready"]):
        st.info("当前点数不足，暂不进行格拉布斯法判断。")
    elif panel_data["suspect_details"] is None:
        if bool(panel_data["target_ready"]):
            st.success("当前未发现疑似离群建靶点，建靶统计已可用于进入正式期。")
        else:
            st.success("当前未发现疑似离群建靶点。")
    else:
        suspect_details = panel_data["suspect_details"]
        st.warning(
            "发现疑似离群建靶点："
            f"序号 #{suspect_details['sequence']} | "
            f"时间 {suspect_details['test_time']} | "
            f"G={float(suspect_details['grubbs_statistic'] or 0.0):.4f} | "
            f"G临界值={float(suspect_details['grubbs_threshold'] or 0.0):.4f} | "
            f"alpha={float(suspect_details['alpha']):.2f} | "
            f"状态={suspect_details['status_label']}"
        )
    st.caption("如需保留、禁用或恢复，请到“检测记录维护”区处理。")


def render_lj_formal_westgard_panel(
    latest_status: str,
    latest_compact_message: str,
    latest_rule_hits: str,
    latest_source_text: str,
) -> None:
    st.markdown("**Westgard 分析模块**")
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
    current_note = str(latest_row.get("manual_note", "") or "")
    st.caption("\u5f53\u524d\u5f02\u5e38\u8bb0\u5f55\u53ef\u76f4\u63a5\u8865\u5145\u5f02\u5e38\u5907\u6ce8\uff0c\u5199\u56de\u540c\u4e00\u6761\u68c0\u6d4b\u8bb0\u5f55\u3002")
    with st.form(f"lj_abnormal_note_form_{result_id}"):
        manual_note = st.text_area(
            "\u5f02\u5e38\u5907\u6ce8\uff08\u53ef\u9009\uff09",
            value=current_note,
            height=88,
            key=f"lj_abnormal_note_{result_id}",
        )
        submitted = st.form_submit_button("\u4fdd\u5b58\u5f53\u524d\u5f02\u5e38\u5907\u6ce8", width="stretch")

        if submitted:
            update_result(
                result_id=result_id,
                test_time=pd.Timestamp(latest_row["test_time"]).strftime("%Y-%m-%d %H:%M:%S"),
                operator=str(latest_row.get("operator", "") or ""),
                value=float(latest_row["value"]),
                log_value=None if pd.isna(latest_row.get("log_value")) else float(latest_row["log_value"]),
                reagent_lot_changed=int(latest_row.get("reagent_lot_changed", 0) or 0),
                manual_note=str(manual_note or "").strip(),
            )
            st.rerun()

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

def build_lj_workbench_context(selected_batch_id: int) -> dict[str, object]:
    batch = get_batch(selected_batch_id)
    qc_df, stats = persist_lj_batch_outlier_snapshot(selected_batch_id)
    results_df = get_results(selected_batch_id, include_manual_note=True)
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
        "building_cv_hint": calculate_target_building_cv_hint(results_df, int(batch["target_n"])),
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
    view_options = ["建靶图", "正式质控图", "全部数据图"]
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
    batch = context["batch"]
    input_value_type = context["input_value_type"]
    input_value_type_label = context["input_value_type_label"]
    operator_options = context["operator_options"]
    stats = context["stats"]
    building_cv_hint = context["building_cv_hint"]
    cv_limit = context["cv_limit"]
    results_df = context["results_df"]

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
        st.caption(f"检测时间、检测人、{input_value_type_label} 与变更点集中在同一操作卡。")
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
                add_result(
                    batch_id=selected_batch_id,
                    test_time=test_time.strftime("%Y-%m-%d %H:%M:%S"),
                    operator=cleaned_operator,
                    value=float(parsed_value),
                    log_value=log_value,
                    reagent_lot_changed=int(reagent_lot_changed),
                    manual_note="",
                )
                st.success("检测结果已保存。")
                st.session_state["reset_entry_form"] = True
                st.rerun()

    with st.container(border=True):
        st.markdown("**建靶统计**")
        render_compact_stat_metrics(
            [
                ("总记录数", f"{stats.get('building_total_count', 0)}"),
                ("生效建靶点", f"{stats.get('effective_building_count', 0)}"),
                ("已禁用点", f"{stats.get('disabled_building_count', 0)}"),
                ("均值", "-" if stats["mean"] is None else f"{stats['mean']:.4f}"),
                ("SD", "-" if stats["sd"] is None else f"{stats['sd']:.4f}"),
                ("CV%", "-" if stats["cv"] is None else f"{stats['cv']:.2f}%"),
            ]
        )
        st.caption(
            "建靶进度："
            + (
                "已完成，后续结果自动进行 Westgard 判定。"
                if stats.get("target_ready")
                else f"尚需继续录入至少 {int(batch['target_n'])} 次结果。"
            )
        )
        if cv_limit is not None:
            st.caption(f"当前批次已保存 CV 要求：≤ {cv_limit:.2f}%")
            render_cv_limit_hint(
                building_cv_hint.get("cv"),
                cv_limit,
                "当前累计建靶",
            )

    with st.container(border=True):
        st.markdown("**实时统计**")
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
        )
        render_compact_stat_metrics(
            [
                ("实时均值", "-" if realtime_stats["mean"] is None else f"{realtime_stats['mean']:.4f}"),
                ("实时 SD", "-" if realtime_stats["sd"] is None else f"{realtime_stats['sd']:.4f}"),
                ("实时 CV%", "-" if realtime_stats["cv"] is None else f"{realtime_stats['cv']:.2f}%"),
            ]
        )
        if realtime_message:
            st.info(realtime_message)
        st.caption(
            "统计说明：实时统计仅基于当前批次中判定为“在控”的正式数据计算，"
            "已自动排除警告和失控结果；"
            "当检测记录被修改或删除后，实时均值 / SD / CV% 会随之自动变化。"
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
            title=_build_lj_chart_title(batch, chart_view_mode),
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
        st.info("当前仍在建靶期，上方最新分析仅显示离群值判断；Westgard 规则汇总会在正式期启用后显示。")
        return

    st.markdown("**本批次规则汇总**")
    render_rule_summary_metrics(stats.get("rule_summary", {}))

    with st.expander("Westgard 规则说明", expanded=False):
        st.caption(
            "建靶期可参考，正式质控期会输出规则结论。"
        )
        for rule_id in ["1_2s", "1_3s", "2_2s", "R_4s", "4_1s", "10x"]:
            st.markdown(f"- `{format_rule_code(rule_id)}`：{format_rule_description(rule_id)}")


def render_lj_records_section(qc_df: pd.DataFrame, input_value_type: str) -> None:
    with st.expander("当前批次检测记录", expanded=False):
        st.caption("查看当前批次的完整检测记录、规则触发和分析提示。")
        display_df = prepare_display_records(qc_df, input_value_type=input_value_type)
        render_records_table_impl(display_df)


def render_lj_maintenance_section(context: dict[str, object]) -> None:
    qc_df = context["qc_df"]
    input_value_type = context["input_value_type"]
    stats = context["stats"]

    building_df = (
        qc_df[qc_df["phase"] == "建靶数据"]
        .sort_values(["test_time", "id"], ascending=[False, False])
        .reset_index(drop=True)
    )
    notice_message = str(st.session_state.pop("lj_outlier_notice", "") or "")
    if notice_message:
        st.success(notice_message)

    with st.container(border=True):
        render_compact_stat_metrics(
            [
                ("总记录数", f"{stats.get('building_total_count', 0)}"),
                ("生效建靶点", f"{stats.get('effective_building_count', 0)}"),
                ("已禁用点", f"{stats.get('disabled_building_count', 0)}"),
                ("均值", "-" if stats.get("mean") is None else f"{stats['mean']:.4f}"),
                ("SD", "-" if stats.get("sd") is None else f"{stats['sd']:.4f}"),
                ("CV%", "-" if stats.get("cv") is None else f"{stats['cv']:.2f}%"),
            ]
        )

        suspect_row = stats.get("current_suspect_row")
        if suspect_row is not None:
            suspect_time = pd.Timestamp(suspect_row["test_time"]).strftime("%Y-%m-%d %H:%M")
            st.warning(
                "当前存在疑似离群建靶点："
                f"序号 #{int(suspect_row.get('sequence', 0) or 0)} | "
                f"时间 {suspect_time} | "
                f"G={float(suspect_row.get('grubbs_statistic') or 0.0):.4f} | "
                f"G临界值={float(suspect_row.get('grubbs_threshold') or 0.0):.4f} | "
                f"alpha={DEFAULT_GRUBBS_ALPHA:.2f} | "
                f"状态={_get_lj_building_record_current_status_label(suspect_row)}"
            )

        if building_df.empty:
            st.info("当前批次暂无建靶期记录可维护。")
        else:
            option_map: dict[str, int] = {}
            option_labels: list[str] = []
            for _, row in building_df.iterrows():
                label = (
                    f"序号 #{int(row.get('sequence', 0) or 0)} | "
                    f"{pd.Timestamp(row['test_time']).strftime('%Y-%m-%d %H:%M')} | "
                    f"{_get_lj_building_record_current_status_label(row)}"
                )
                option_labels.append(label)
                option_map[label] = int(row["id"])

            selected_label = st.selectbox(
                "选择需要处理的建靶点",
                options=option_labels,
                key="lj_outlier_record_selector",
            )
            selected_result_id = option_map[selected_label]
            selected_row = building_df[building_df["id"] == selected_result_id].iloc[0]
            statistic_text = ""
            if not pd.isna(selected_row.get("grubbs_statistic")):
                statistic_text = f"{float(selected_row.get('grubbs_statistic')):.4f}"
            threshold_text = ""
            if not pd.isna(selected_row.get("grubbs_threshold")):
                threshold_text = f"{float(selected_row.get('grubbs_threshold')):.4f}"
            st.caption(
                f"当前状态：{_get_lj_building_record_current_status_label(selected_row)} | "
                f"G={statistic_text} | "
                f"G临界值={threshold_text} | "
                f"alpha={DEFAULT_GRUBBS_ALPHA:.2f}"
            )
            if stats.get("has_formal_started"):
                st.info("正式期启用后，LJ 建靶期离群值状态将锁定，不再允许保留、禁用或恢复。")

            action_cols = st.columns(3)
            keep_disabled = bool(stats.get("has_formal_started"))
            disable_disabled = bool(stats.get("has_formal_started")) or int(selected_row.get("is_building_included", 1) or 0) == 0
            restore_disabled = bool(stats.get("has_formal_started")) or int(selected_row.get("is_building_included", 1) or 0) == 1

            if action_cols[0].button("保留", key=f"lj_keep_{selected_result_id}", width="stretch", disabled=keep_disabled):
                keep_lj_building_result(int(selected_result_id))
                st.session_state["lj_outlier_notice"] = "建靶点已标记为保留，并已重算建靶统计。"
                st.rerun()
            if action_cols[1].button("禁用", key=f"lj_disable_{selected_result_id}", width="stretch", disabled=disable_disabled):
                disable_lj_building_result(int(selected_result_id))
                st.session_state["lj_outlier_notice"] = "建靶点已禁用，并已重算建靶统计。"
                st.rerun()
            if action_cols[2].button("恢复", key=f"lj_restore_{selected_result_id}", width="stretch", disabled=restore_disabled):
                restore_lj_building_result(int(selected_result_id))
                st.session_state["lj_outlier_notice"] = "建靶点已恢复，并已重算建靶统计。"
                st.rerun()

        if st.button(
            "打开检测记录维护",
            key="open_record_maintenance_dialog",
            width="stretch",
            disabled=qc_df.empty,
        ):
            bump_record_maintenance_dialog_nonce_impl()
            st.session_state["show_record_maintenance_dialog"] = True

    if st.session_state.get("show_record_maintenance_dialog", False):
        render_record_maintenance_dialog_impl(qc_df, input_value_type=input_value_type)


def render_lj_export_import_section(
    context: dict[str, object],
    selected_batch_id: int,
    figure,
    chart_state: dict[str, object],
) -> None:
    batch = context["batch"]
    input_value_type = context["input_value_type"]
    input_value_type_label = context["input_value_type_label"]
    qc_df = context["qc_df"]
    stats = context["stats"]
    results_df = context["results_df"]

    building_export_df = export_batch_results_for_phase(batch, qc_df, "building")
    formal_export_df = export_batch_results_for_phase(batch, qc_df, "formal")
    building_csv_bytes = building_export_df.to_csv(index=False).encode("utf-8-sig")
    building_xlsx_bytes = dataframe_to_xlsx_bytes(building_export_df)
    formal_csv_bytes = formal_export_df.to_csv(index=False).encode("utf-8-sig")
    formal_xlsx_bytes = dataframe_to_xlsx_bytes(formal_export_df)
    png_bytes = figure_to_png_bytes(figure)
    project_name_fragment = build_safe_export_name(
        batch["project_name"] if "project_name" in batch.keys() else None,
        "project",
    )
    lot_no_fragment = build_safe_export_name(
        batch["lot_no"] if "lot_no" in batch.keys() else None,
        f"batch_{batch['id']}",
    )
    lj_building_template_df = build_lj_building_template_dataframe(input_value_type)
    lj_building_template_csv_bytes = lj_building_template_df.to_csv(index=False).encode("utf-8-sig")
    lj_import_scope = f"lj_building_import_{selected_batch_id}"
    lj_import_review_state_key = f"{lj_import_scope}_review"
    lj_import_success_key = f"{lj_import_scope}_success"
    lj_import_uploader_nonce_key = f"{lj_import_scope}_uploader_nonce"
    lj_import_uploader_nonce = int(st.session_state.get(lj_import_uploader_nonce_key, 0))
    lj_import_uploader_key = f"{lj_import_scope}_file_{lj_import_uploader_nonce}"
    lj_building_import_disabled = bool(stats.get("target_ready"))
    lj_import_success_message = str(st.session_state.pop(lj_import_success_key, "") or "")
    if lj_import_success_message:
        st.success(lj_import_success_message)
    lj_formal_import_scope = f"lj_formal_import_{selected_batch_id}"
    lj_formal_import_review_state_key = f"{lj_formal_import_scope}_review"
    lj_formal_import_success_key = f"{lj_formal_import_scope}_success"
    lj_formal_import_uploader_nonce_key = f"{lj_formal_import_scope}_uploader_nonce"
    lj_formal_import_uploader_nonce = int(st.session_state.get(lj_formal_import_uploader_nonce_key, 0))
    lj_formal_import_uploader_key = f"{lj_formal_import_scope}_file_{lj_formal_import_uploader_nonce}"
    lj_target_ready = bool(stats.get("target_ready"))
    lj_formal_import_success_message = str(st.session_state.pop(lj_formal_import_success_key, "") or "")
    if lj_formal_import_success_message:
        st.success(lj_formal_import_success_message)

    st.markdown("**导出**")
    st.markdown("**分阶段数据导出**")
    st.caption(f"可分别导出当前批次的建靶期或正式期数据，主值列统一为“{input_value_type_label}”。")
    export_format = st.radio(
        "导出数据格式",
        options=["Excel (.xlsx)", "CSV (.csv)"],
        horizontal=True,
        key="export_format",
    )
    phase_export_cols = st.columns(2)
    phase_export_cols[0].download_button(
        label="导出建靶期数据",
        data=building_xlsx_bytes if export_format == "Excel (.xlsx)" else building_csv_bytes,
        file_name=(
            f"{project_name_fragment}_batch_{batch['id']}_{lot_no_fragment}_target_building_results.xlsx"
            if export_format == "Excel (.xlsx)"
            else f"{project_name_fragment}_batch_{batch['id']}_{lot_no_fragment}_target_building_results.csv"
        ),
        mime=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            if export_format == "Excel (.xlsx)"
            else "text/csv"
        ),
        width="stretch",
        disabled=building_export_df.empty,
    )
    phase_export_cols[1].download_button(
        label="导出正式期数据",
        data=formal_xlsx_bytes if export_format == "Excel (.xlsx)" else formal_csv_bytes,
        file_name=(
            f"{project_name_fragment}_batch_{batch['id']}_{lot_no_fragment}_formal_qc_results.xlsx"
            if export_format == "Excel (.xlsx)"
            else f"{project_name_fragment}_batch_{batch['id']}_{lot_no_fragment}_formal_qc_results.csv"
        ),
        mime=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            if export_format == "Excel (.xlsx)"
            else "text/csv"
        ),
        width="stretch",
        disabled=formal_export_df.empty,
    )
    st.download_button(
        label="导出当前 LJ 图 PNG",
        data=png_bytes,
        file_name=(
            f"{project_name_fragment}_batch_{batch['id']}_{lot_no_fragment}_"
            f"{build_safe_export_name(chart_state['view_mode'], 'chart')}.png"
        ),
        mime="image/png",
        width="stretch",
    )

    st.divider()
    st.markdown("**月度质控图导出**")
    st.caption("仅导出正式数据，日期范围最长 30 天。")
    formal_qc_df = qc_df[qc_df["phase"] == "正式数据"].copy() if "phase" in qc_df.columns else pd.DataFrame()
    if formal_qc_df.empty:
        st.info("当前批次还没有正式质控数据。")
    else:
        default_monthly_start = formal_qc_df["test_time"].min().date()
        default_monthly_end = formal_qc_df["test_time"].max().date()
        monthly_col_start, monthly_col_end = st.columns(2)
        monthly_start = monthly_col_start.date_input(
            "开始日期",
            value=default_monthly_start,
            key="monthly_export_start",
        )
        monthly_end = monthly_col_end.date_input(
            "结束日期",
            value=default_monthly_end,
            key="monthly_export_end",
        )

        monthly_error = ""
        day_span = (pd.Timestamp(monthly_end).date() - pd.Timestamp(monthly_start).date()).days + 1
        if monthly_end < monthly_start:
            monthly_error = "结束日期不能早于开始日期。"
        elif day_span > 30:
            monthly_error = "月度质控图导出范围最长为30天，请重新选择日期范围。"

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
                st.info("所选日期范围内没有正式质控数据，无法导出月度质控图。")
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
                    view_mode="正式质控图",
                    y_axis_mode=chart_state["y_axis_mode"],
                    standard_sd_limit=float(st.session_state.get("chart_standard_sd_limit", 4.0)),
                    y_axis_label=input_value_type_label,
                )
                monthly_png_bytes = figure_to_png_bytes(monthly_figure)
                monthly_file_name = (
                    f"{project_name_fragment}_batch_{batch['id']}_{lot_no_fragment}_monthly_qc_"
                    f"{monthly_start.strftime('%Y-%m-%d')}_to_{monthly_end.strftime('%Y-%m-%d')}.png"
                )

        st.download_button(
            label="导出月度质控图 PNG",
            data=monthly_png_bytes if monthly_png_bytes is not None else b"",
            file_name=(
                monthly_file_name
                or f"{project_name_fragment}_batch_{batch['id']}_{lot_no_fragment}_monthly_qc.png"
            ),
            mime="image/png",
            width="stretch",
            disabled=monthly_png_bytes is None,
        )

    st.divider()
    st.markdown("**CSV 导入**")
    st.caption("建靶期和正式期分别提供模板下载、审查和导入。")
    st.markdown("**建靶期 CSV 导入**")
    st.caption(f"先下载标准模板，再上传 CSV 审查；只有无阻断错误时，才允许确认导入当前批次建靶期{input_value_type_label}数据。")
    st.markdown("- `试剂批号变更（可选）` 在建靶期一般不填。")
    st.markdown("- 正式期仅在“更换试剂批号后的第一条记录”填写“是”。")
    st.markdown("- 其余记录填“否”或留空。")
    st.markdown("- 该字段表示“变更点”，不是持续状态。")
    st.download_button(
        label="下载建靶期 CSV 模板",
        data=lj_building_template_csv_bytes,
        file_name=(
            f"{project_name_fragment}_batch_{batch['id']}_{lot_no_fragment}_target_building_import_template.csv"
        ),
        mime="text/csv",
        width="stretch",
    )
    if lj_building_import_disabled:
        st.info("当前批次建靶已完成。V1 的 LJ 建靶期导入仅支持未完成建靶的当前批次。")
        st.session_state.pop(lj_import_review_state_key, None)

    uploaded_lj_building_csv = st.file_uploader(
        "上传建靶期 CSV",
        type=["csv"],
        key=lj_import_uploader_key,
        disabled=lj_building_import_disabled,
        help="请优先使用上方标准模板，目前仅支持 CSV。",
    )
    uploaded_lj_building_bytes = uploaded_lj_building_csv.getvalue() if uploaded_lj_building_csv is not None else b""
    current_lj_import_signature = hashlib.sha256(uploaded_lj_building_bytes).hexdigest() if uploaded_lj_building_bytes else ""

    lj_import_review_state = st.session_state.get(lj_import_review_state_key)
    if not current_lj_import_signature:
        st.session_state.pop(lj_import_review_state_key, None)
        lj_import_review_state = None
    elif (
        lj_import_review_state is not None
        and lj_import_review_state.get("file_signature") != current_lj_import_signature
    ):
        st.session_state.pop(lj_import_review_state_key, None)
        lj_import_review_state = None

    import_action_cols = st.columns(2)
    review_lj_building_clicked = import_action_cols[0].button(
        "审查 CSV",
        key=f"{lj_import_scope}_review_button",
        width="stretch",
        disabled=lj_building_import_disabled or uploaded_lj_building_csv is None,
    )
    if review_lj_building_clicked:
        lj_import_review_state = review_lj_building_import_csv(
            file_bytes=uploaded_lj_building_bytes,
            existing_results_df=results_df,
            target_n=int(batch["target_n"]),
            input_value_type=input_value_type,
        )
        lj_import_review_state["file_signature"] = current_lj_import_signature
        st.session_state[lj_import_review_state_key] = lj_import_review_state

    confirm_lj_import_disabled = True
    if lj_import_review_state is not None:
        review_summary = lj_import_review_state["summary"]
        review_issues_df = build_review_issues_dataframe(lj_import_review_state["issues"])
        render_import_review_summary(review_summary)
        if review_summary["has_blocking_errors"]:
            st.error("审查未通过：存在阻断错误，当前整批不会导入。")
        else:
            st.success("审查通过：当前没有阻断错误，可以确认导入。")

        if review_issues_df.empty:
            st.info("本次审查未发现错误或警告。")
        else:
            st.dataframe(review_issues_df, hide_index=True, width="stretch")

        confirm_lj_import_disabled = (
            lj_building_import_disabled
            or review_summary["has_blocking_errors"]
            or not lj_import_review_state["normalized_rows"]
        )

    confirm_lj_import_clicked = import_action_cols[1].button(
        "确认导入建靶期数据",
        key=f"{lj_import_scope}_confirm_button",
        width="stretch",
        disabled=confirm_lj_import_disabled,
    )
    if confirm_lj_import_clicked and lj_import_review_state is not None:
        for row in lj_import_review_state["normalized_rows"]:
            add_result(
                batch_id=selected_batch_id,
                test_time=row["test_time"],
                operator=row["operator"],
                value=float(row["value"]),
                log_value=row.get("log_value"),
                reagent_lot_changed=int(row["reagent_lot_changed"]),
                manual_note=row["manual_note"],
            )
        imported_row_count = len(lj_import_review_state["normalized_rows"])
        st.session_state.pop(lj_import_review_state_key, None)
        st.session_state[lj_import_uploader_nonce_key] = lj_import_uploader_nonce + 1
        st.session_state[lj_import_success_key] = (
            f"已追加导入 {imported_row_count} 条建靶期记录，并自动重算当前建靶统计。"
        )
        st.rerun()

    st.divider()
    st.markdown("**LJ 正式期 CSV 导入**")
    st.caption(f"先下载标准模板，再上传 CSV 审查；导入目标为当前批次正式期，只有无阻断错误时才允许确认导入当前批次{input_value_type_label}数据。")
    st.markdown("- `试剂批号变更（可选）` 在建靶期一般不填。")
    st.markdown("- 正式期仅在“更换试剂批号后的第一条记录”填写“是”。")
    st.markdown("- 其余记录填“否”或留空。")
    st.markdown("- 该字段表示“变更点”，不是持续状态。")
    st.download_button(
        label="下载正式期 CSV 模板",
        data=lj_building_template_csv_bytes,
        file_name=(
            f"{project_name_fragment}_batch_{batch['id']}_{lot_no_fragment}_formal_qc_import_template.csv"
        ),
        mime="text/csv",
        width="stretch",
    )
    if not lj_target_ready:
        st.info("当前批次尚未完成建靶，不能导入正式期数据。你仍可先上传 CSV 做审查。")

    uploaded_lj_formal_csv = st.file_uploader(
        "上传正式期 CSV",
        type=["csv"],
        key=lj_formal_import_uploader_key,
        help="请优先使用上方标准模板，目前仅支持 CSV。",
    )
    uploaded_lj_formal_bytes = uploaded_lj_formal_csv.getvalue() if uploaded_lj_formal_csv is not None else b""
    current_lj_formal_signature = (
        f"{hashlib.sha256(uploaded_lj_formal_bytes).hexdigest()}:{int(lj_target_ready)}:{len(results_df)}"
        if uploaded_lj_formal_bytes
        else ""
    )

    lj_formal_import_review_state = st.session_state.get(lj_formal_import_review_state_key)
    if not current_lj_formal_signature:
        st.session_state.pop(lj_formal_import_review_state_key, None)
        lj_formal_import_review_state = None
    elif (
        lj_formal_import_review_state is not None
        and lj_formal_import_review_state.get("file_signature") != current_lj_formal_signature
    ):
        st.session_state.pop(lj_formal_import_review_state_key, None)
        lj_formal_import_review_state = None

    formal_import_action_cols = st.columns(2)
    review_lj_formal_clicked = formal_import_action_cols[0].button(
        "审查正式期 CSV",
        key=f"{lj_formal_import_scope}_review_button",
        width="stretch",
        disabled=uploaded_lj_formal_csv is None,
    )
    if review_lj_formal_clicked:
        lj_formal_import_review_state = review_lj_formal_import_csv(
            file_bytes=uploaded_lj_formal_bytes,
            existing_results_df=results_df,
            target_n=int(batch["target_n"]),
            target_ready=lj_target_ready,
            input_value_type=input_value_type,
        )
        lj_formal_import_review_state["file_signature"] = current_lj_formal_signature
        st.session_state[lj_formal_import_review_state_key] = lj_formal_import_review_state

    confirm_lj_formal_import_disabled = True
    if lj_formal_import_review_state is not None:
        formal_review_summary = lj_formal_import_review_state["summary"]
        formal_review_issues_df = build_review_issues_dataframe(
            lj_formal_import_review_state["issues"]
        )
        render_import_review_summary(formal_review_summary)
        if formal_review_summary["has_blocking_errors"]:
            st.error("正式期审查未通过：存在阻断错误，当前整批不会导入。")
        else:
            st.success("正式期审查通过：当前没有阻断错误，可以确认导入。")

        if formal_review_issues_df.empty:
            st.info("本次正式期审查未发现错误或警告。")
        else:
            st.dataframe(formal_review_issues_df, hide_index=True, width="stretch")

        confirm_lj_formal_import_disabled = (
            (not lj_target_ready)
            or formal_review_summary["has_blocking_errors"]
            or not lj_formal_import_review_state["normalized_rows"]
        )

    confirm_lj_formal_import_clicked = formal_import_action_cols[1].button(
        "确认导入正式期数据",
        key=f"{lj_formal_import_scope}_confirm_button",
        width="stretch",
        disabled=confirm_lj_formal_import_disabled,
    )
    if confirm_lj_formal_import_clicked and lj_formal_import_review_state is not None:
        for row in lj_formal_import_review_state["normalized_rows"]:
            add_result(
                batch_id=selected_batch_id,
                test_time=row["test_time"],
                operator=row["operator"],
                value=float(row["value"]),
                log_value=row.get("log_value"),
                reagent_lot_changed=int(row["reagent_lot_changed"]),
                manual_note=row["manual_note"],
            )
        imported_formal_row_count = len(lj_formal_import_review_state["normalized_rows"])
        st.session_state.pop(lj_formal_import_review_state_key, None)
        st.session_state[lj_formal_import_uploader_nonce_key] = lj_formal_import_uploader_nonce + 1
        st.session_state[lj_formal_import_success_key] = (
            f"已追加导入 {imported_formal_row_count} 条正式期记录，并自动刷新 LJ 图、最新结果分析和规则统计。"
        )
        st.rerun()
