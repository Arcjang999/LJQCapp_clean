from __future__ import annotations

import math
from datetime import datetime
from typing import Any

import pandas as pd

from database import (
    PROJECT_METHOD_LJ,
    add_instant_result,
    get_connection,
    get_instant_batch,
    get_instant_result,
    get_instant_results,
    save_instant_result_analysis_snapshot,
    set_instant_result_effective_state,
)
from services.outlier_service import (
    DEFAULT_GRUBBS_ALPHA,
    GRUBBS_FORMULA_TEXT,
    GRUBBS_METHOD_NAME,
    GRUBBS_METHOD_LABEL,
    MIN_GRUBBS_SAMPLE_SIZE,
    calculate_grubbs_test,
)
from services.value_type_service import get_input_value_type_label, normalize_input_value_type


INSTANT_TRANSFER_READY_COUNT = 20
INSTANT_MANUAL_STATUS_NORMAL = "normal"
INSTANT_MANUAL_STATUS_PENDING_REVIEW = "pending_review"
INSTANT_MANUAL_STATUS_KEEP = "keep"
INSTANT_MANUAL_STATUS_DISABLED = "disabled"
INSTANT_MANUAL_STATUS_RESTORED = "restored"
INSTANT_TRANSFER_STATUS_NOT_TRANSFERRED = "not_transferred"
INSTANT_TRANSFER_STATUS_TRANSFERRED = "transferred"
MANUAL_STATUS_LABELS = {
    INSTANT_MANUAL_STATUS_NORMAL: "正常",
    INSTANT_MANUAL_STATUS_PENDING_REVIEW: "待处理",
    INSTANT_MANUAL_STATUS_KEEP: "保留",
    INSTANT_MANUAL_STATUS_DISABLED: "已禁用",
    INSTANT_MANUAL_STATUS_RESTORED: "已恢复",
}


def get_instant_manual_status_label(status: Any) -> str:
    normalized_status = str(status or "").strip().lower()
    return MANUAL_STATUS_LABELS.get(normalized_status, normalized_status or "正常")


def normalize_instant_manual_status(
    status: Any,
    *,
    fallback: str = INSTANT_MANUAL_STATUS_NORMAL,
) -> str:
    normalized_status = str(status or "").strip().lower()
    if normalized_status in MANUAL_STATUS_LABELS:
        return normalized_status
    return fallback


def normalize_instant_transfer_status(
    status: Any,
    *,
    fallback: str = INSTANT_TRANSFER_STATUS_NOT_TRANSFERRED,
) -> str:
    normalized_status = str(status or "").strip().lower()
    if normalized_status in {INSTANT_TRANSFER_STATUS_NOT_TRANSFERRED, INSTANT_TRANSFER_STATUS_TRANSFERRED}:
        return normalized_status
    return fallback


def build_instant_operator_options(results_df: pd.DataFrame) -> list[str]:
    if results_df.empty or "operator" not in results_df.columns:
        return []
    operators: list[str] = []
    for operator in reversed(results_df["operator"].fillna("").astype(str).tolist()):
        cleaned_operator = operator.strip()
        if cleaned_operator and cleaned_operator not in operators:
            operators.append(cleaned_operator)
    return operators


def _safe_float_or_none(value: Any) -> float | None:
    try:
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return None
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric):
        return None
    return numeric


def _format_float_for_meta(value: Any, digits: int = 4) -> str:
    numeric = _safe_float_or_none(value)
    if numeric is None:
        return "-"
    return f"{numeric:.{digits}f}"


def _build_empty_analysis_dataframe(results_df: pd.DataFrame | None = None) -> pd.DataFrame:
    base = results_df.copy() if results_df is not None else pd.DataFrame()
    default_columns = [
        "id",
        "batch_id",
        "project_id",
        "test_time",
        "operator",
        "value",
        "log_value",
        "is_effective",
        "is_outlier_suspect",
        "outlier_method",
        "grubbs_statistic",
        "grubbs_threshold",
        "manual_status",
        "manual_note",
        "created_at",
        "sequence",
        "effective_sequence",
        "status",
        "analysis_prompt",
    ]
    for column_name in default_columns:
        if column_name not in base.columns:
            base[column_name] = pd.Series(dtype="object")
    return base.iloc[0:0].copy()


def get_latest_instant_row(analysis_df: pd.DataFrame) -> pd.Series | None:
    if analysis_df.empty:
        return None
    latest_df = analysis_df.sort_values(["test_time", "id"])
    if latest_df.empty:
        return None
    return latest_df.iloc[-1]


def analyze_instant_results(results_df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, object]]:
    summary: dict[str, object] = {
        "total_count": 0,
        "effective_count": 0,
        "disabled_count": 0,
        "outlier_suspect_total_count": 0,
        "pending_outlier_review_count": 0,
        "kept_outlier_count": 0,
        "disabled_outlier_count": 0,
        "transferable_effective_count": 0,
        "mean": None,
        "sd": None,
        "cv": None,
        "grubbs_method_label": GRUBBS_METHOD_LABEL,
        "grubbs_formula": GRUBBS_FORMULA_TEXT,
        "grubbs_alpha": DEFAULT_GRUBBS_ALPHA,
        "grubbs_sample_size": 0,
        "grubbs_mean": None,
        "grubbs_sd": None,
        "grubbs_ready": False,
        "grubbs_statistic": None,
        "grubbs_threshold": None,
        "grubbs_suspected_result_id": None,
        "grubbs_suspected_effective_sequence": None,
        "grubbs_suspected_value": None,
        "transfer_ready": False,
        "transfer_message": "",
        "transfer_status": INSTANT_TRANSFER_STATUS_NOT_TRANSFERRED,
        "transfer_action_enabled": False,
        "transfer_blockers": [],
        "latest_status": "暂无数据",
        "latest_message": "暂无检测结果，请先录入即时法数据。",
        "latest_meta": [],
    }
    if results_df.empty:
        return _build_empty_analysis_dataframe(results_df), summary

    analysis_df = results_df.copy().sort_values(["test_time", "id"]).reset_index(drop=True)
    analysis_df["sequence"] = analysis_df.index + 1
    analysis_df["is_effective"] = analysis_df["is_effective"].fillna(1).astype(int)
    analysis_df["manual_status"] = analysis_df["manual_status"].map(normalize_instant_manual_status)
    analysis_df["effective_sequence"] = pd.Series([pd.NA] * len(analysis_df), dtype="object")
    analysis_df["status"] = "有效点"
    analysis_df["analysis_prompt"] = "已纳入即时法有效统计。"
    analysis_df["is_outlier_suspect"] = analysis_df["is_outlier_suspect"].fillna(0).astype(int)
    analysis_df["outlier_method"] = analysis_df["outlier_method"].fillna("")
    analysis_df["grubbs_statistic"] = analysis_df["grubbs_statistic"].apply(_safe_float_or_none)
    analysis_df["grubbs_threshold"] = analysis_df["grubbs_threshold"].apply(_safe_float_or_none)

    disabled_mask = analysis_df["is_effective"] != 1
    analysis_df.loc[disabled_mask, "status"] = "已禁用"
    analysis_df.loc[disabled_mask, "analysis_prompt"] = "该记录已手工禁用，不参与即时法有效点统计与格拉布斯法判定。"

    effective_indices = analysis_df.index[analysis_df["is_effective"] == 1].tolist()
    for position, dataframe_index in enumerate(effective_indices, start=1):
        analysis_df.at[dataframe_index, "effective_sequence"] = position

    effective_df = analysis_df.loc[analysis_df["is_effective"] == 1].copy()
    effective_values = effective_df["value"].astype(float).tolist() if not effective_df.empty else []
    total_count = len(analysis_df)
    effective_count = len(effective_values)
    disabled_count = total_count - effective_count

    mean_value = None
    sd_value = None
    cv_value = None
    if effective_count >= 1:
        mean_value = float(pd.Series(effective_values).mean())
    if effective_count >= 2:
        sd_value = float(pd.Series(effective_values).std(ddof=1))
        if mean_value is not None and not math.isclose(mean_value, 0.0, abs_tol=1e-12):
            cv_value = float(sd_value / mean_value * 100.0)

    grubbs_result = calculate_grubbs_test(effective_values, alpha=DEFAULT_GRUBBS_ALPHA)
    grubbs_ready = effective_count >= MIN_GRUBBS_SAMPLE_SIZE
    suspected_result_id = None
    suspected_effective_sequence = None

    if effective_count == 0:
        latest_status = "暂无数据"
        latest_message = "当前批次还没有有效点，请先录入检测结果。"
    elif effective_count < MIN_GRUBBS_SAMPLE_SIZE:
        accumulation_message = (
            f"当前仅有 {effective_count} 个有效点，达到 {MIN_GRUBBS_SAMPLE_SIZE} 个有效点后才开始格拉布斯法提示。"
        )
        analysis_df.loc[analysis_df["is_effective"] == 1, "status"] = "继续累计"
        analysis_df.loc[analysis_df["is_effective"] == 1, "analysis_prompt"] = accumulation_message
        latest_status = "继续累计"
        latest_message = accumulation_message
    else:
        shared_statistic = _safe_float_or_none(grubbs_result.get("statistic"))
        shared_threshold = _safe_float_or_none(grubbs_result.get("threshold"))
        if grubbs_result.get("evaluation_ready"):
            analysis_df.loc[analysis_df["is_effective"] == 1, "outlier_method"] = GRUBBS_METHOD_NAME
            analysis_df.loc[analysis_df["is_effective"] == 1, "grubbs_statistic"] = shared_statistic
            analysis_df.loc[analysis_df["is_effective"] == 1, "grubbs_threshold"] = shared_threshold
        else:
            analysis_df.loc[analysis_df["is_effective"] == 1, "outlier_method"] = ""
            analysis_df.loc[analysis_df["is_effective"] == 1, "grubbs_statistic"] = None
            analysis_df.loc[analysis_df["is_effective"] == 1, "grubbs_threshold"] = None

        if grubbs_result.get("evaluation_ready") and grubbs_result.get("is_suspect"):
            suspected_position = int(grubbs_result["suspected_index"])
            suspected_dataframe_index = effective_indices[suspected_position]
            suspected_result_id = int(analysis_df.at[suspected_dataframe_index, "id"])
            suspected_effective_sequence = int(analysis_df.at[suspected_dataframe_index, "effective_sequence"])
            analysis_df.loc[analysis_df["is_effective"] == 1, "status"] = "有效点"
            analysis_df.loc[analysis_df["is_effective"] == 1, "analysis_prompt"] = (
                f"当前样本已出现疑似离群点（有效序号 #{suspected_effective_sequence}），"
                "请到记录维护区人工确认是否禁用。"
            )
            analysis_df.loc[analysis_df["is_effective"] == 1, "is_outlier_suspect"] = 0
            analysis_df.at[suspected_dataframe_index, "status"] = "疑似离群"
            analysis_df.at[suspected_dataframe_index, "analysis_prompt"] = (
                "系统提示该点疑似离群，请结合复测与业务判断后手工确认。"
            )
            analysis_df.at[suspected_dataframe_index, "is_outlier_suspect"] = 1
        elif grubbs_result.get("reason") == "zero_variation":
            zero_variation_message = "当前有效点波动为 0，格拉布斯统计量暂无法形成有效离群提示。"
            analysis_df.loc[analysis_df["is_effective"] == 1, "status"] = "有效点"
            analysis_df.loc[analysis_df["is_effective"] == 1, "analysis_prompt"] = zero_variation_message
        else:
            analysis_df.loc[analysis_df["is_effective"] == 1, "status"] = "有效点"
            analysis_df.loc[analysis_df["is_effective"] == 1, "analysis_prompt"] = (
                "已按格拉布斯法检查，当前未见疑似离群。"
            )
            analysis_df.loc[analysis_df["is_effective"] == 1, "is_outlier_suspect"] = 0

        latest_row = get_latest_instant_row(analysis_df)
        latest_status = str(latest_row.get("status", "")) if latest_row is not None else "有效点"
        latest_message = (
            str(latest_row.get("analysis_prompt", "")).strip()
            if latest_row is not None
            else "已完成即时法基础判定。"
        )

    if effective_count > 0 and effective_count < MIN_GRUBBS_SAMPLE_SIZE:
        latest_row = get_latest_instant_row(analysis_df)
        latest_status = str(latest_row.get("status", "继续累计")) if latest_row is not None else "继续累计"
        latest_message = (
            str(latest_row.get("analysis_prompt", "")).strip()
            if latest_row is not None
            else latest_message
        )
    elif effective_count > 0 and effective_count >= MIN_GRUBBS_SAMPLE_SIZE:
        latest_row = get_latest_instant_row(analysis_df)
        latest_status = str(latest_row.get("status", latest_status)) if latest_row is not None else latest_status
        latest_message = (
            str(latest_row.get("analysis_prompt", latest_message)).strip()
            if latest_row is not None
            else latest_message
        )
    else:
        latest_row = get_latest_instant_row(analysis_df)

    transfer_ready = effective_count >= INSTANT_TRANSFER_READY_COUNT
    suspect_mask = analysis_df["is_outlier_suspect"] == 1
    pending_outlier_review_count = int(
        (
            (analysis_df["is_effective"] == 1)
            & suspect_mask
            & (analysis_df["manual_status"] == INSTANT_MANUAL_STATUS_PENDING_REVIEW)
        ).sum()
    )
    kept_outlier_count = int(
        (
            (analysis_df["is_effective"] == 1)
            & suspect_mask
            & analysis_df["manual_status"].isin(
                [INSTANT_MANUAL_STATUS_KEEP, INSTANT_MANUAL_STATUS_RESTORED]
            )
        ).sum()
    )
    disabled_outlier_count = int(
        (
            suspect_mask
            & (analysis_df["manual_status"] == INSTANT_MANUAL_STATUS_DISABLED)
        ).sum()
    )
    outlier_suspect_total_count = int(suspect_mask.sum())
    transferable_effective_count = int(
        (
            (analysis_df["is_effective"] == 1)
            & ~(
                suspect_mask
                & (analysis_df["manual_status"] == INSTANT_MANUAL_STATUS_PENDING_REVIEW)
            )
        ).sum()
    )
    latest_meta = [
        ("总记录数", total_count),
        ("有效点数", effective_count),
        ("判定方法", GRUBBS_METHOD_LABEL),
    ]
    if latest_row is not None:
        latest_meta.append(("检测序号", f"#{int(latest_row['sequence'])}"))
        effective_sequence = latest_row.get("effective_sequence")
        if not pd.isna(effective_sequence):
            latest_meta.append(("有效序号", f"#{int(effective_sequence)}"))
    if effective_count > 0:
        latest_meta.extend(
            [
                ("n", effective_count),
                ("均值", _format_float_for_meta(mean_value)),
                ("SD", _format_float_for_meta(sd_value)),
                ("Grubbs G", _format_float_for_meta(grubbs_result.get("statistic"))),
                ("G临界值", _format_float_for_meta(grubbs_result.get("threshold"))),
                ("alpha", f"{float(grubbs_result.get('alpha', DEFAULT_GRUBBS_ALPHA)):.0%}"),
            ]
        )
    latest_meta.append(
        ("手工状态", get_instant_manual_status_label(latest_row.get("manual_status")) if latest_row is not None else "-")
    )

    summary.update(
        {
            "total_count": total_count,
            "effective_count": effective_count,
            "disabled_count": disabled_count,
            "outlier_suspect_total_count": outlier_suspect_total_count,
            "pending_outlier_review_count": pending_outlier_review_count,
            "kept_outlier_count": kept_outlier_count,
            "disabled_outlier_count": disabled_outlier_count,
            "transferable_effective_count": transferable_effective_count,
            "mean": mean_value,
            "sd": sd_value,
            "cv": cv_value,
            "grubbs_method_label": GRUBBS_METHOD_LABEL,
            "grubbs_formula": GRUBBS_FORMULA_TEXT,
            "grubbs_alpha": float(grubbs_result.get("alpha", DEFAULT_GRUBBS_ALPHA)),
            "grubbs_sample_size": effective_count,
            "grubbs_mean": mean_value,
            "grubbs_sd": sd_value,
            "grubbs_ready": grubbs_ready,
            "grubbs_statistic": _safe_float_or_none(grubbs_result.get("statistic")),
            "grubbs_threshold": _safe_float_or_none(grubbs_result.get("threshold")),
            "grubbs_suspected_result_id": suspected_result_id,
            "grubbs_suspected_effective_sequence": suspected_effective_sequence,
            "grubbs_suspected_value": _safe_float_or_none(grubbs_result.get("suspected_value")),
            "transfer_ready": transfer_ready,
            "transfer_message": "已达到 20 个有效点，可确认转入 LJ 法。" if transfer_ready else "",
            "transfer_status": INSTANT_TRANSFER_STATUS_NOT_TRANSFERRED,
            "transfer_action_enabled": False,
            "transfer_blockers": [],
            "latest_status": latest_status,
            "latest_message": latest_message,
            "latest_meta": latest_meta,
        }
    )
    return analysis_df, summary


def _load_instant_results_with_connection(
    connection,
    batch_id: int,
    *,
    include_manual_note: bool = True,
) -> pd.DataFrame:
    select_columns = """
                id,
                batch_id,
                project_id,
                test_time,
                operator,
                value,
                log_value,
                is_effective,
                is_outlier_suspect,
                outlier_method,
                grubbs_statistic,
                grubbs_threshold,
                manual_status,
                created_at,
                lj_transfer_status,
                lj_transfer_target_batch_id,
                lj_transfer_at
    """
    if include_manual_note:
        select_columns += ",\n                manual_note"
    dataframe = pd.read_sql_query(
        """
        SELECT
            {}
        FROM instant_results
        WHERE batch_id = ?
        ORDER BY datetime(test_time) ASC, id ASC
        """.format(select_columns),
        connection,
        params=(batch_id,),
    )
    if not dataframe.empty:
        dataframe["test_time"] = pd.to_datetime(dataframe["test_time"])
        dataframe["created_at"] = pd.to_datetime(dataframe["created_at"])
        dataframe["is_effective"] = dataframe["is_effective"].fillna(1).astype(int)
        dataframe["is_outlier_suspect"] = dataframe["is_outlier_suspect"].fillna(0).astype(int)
        dataframe["manual_status"] = dataframe["manual_status"].map(normalize_instant_manual_status)
        if include_manual_note:
            dataframe["manual_note"] = dataframe["manual_note"].fillna("")
    return dataframe


def _fetch_instant_batch_row_with_connection(connection, batch_id: int):
    row = connection.execute(
        """
        SELECT
            instant_batches.*,
            instant_projects.name AS project_name,
            target_projects.name AS transferred_to_lj_project_name,
            target_batches.lot_no AS transferred_to_lj_batch_lot_no
        FROM instant_batches
        LEFT JOIN instant_projects ON instant_projects.id = instant_batches.project_id
        LEFT JOIN projects AS target_projects
            ON target_projects.id = instant_batches.transferred_to_lj_project_id
        LEFT JOIN batches AS target_batches
            ON target_batches.id = instant_batches.transferred_to_lj_batch_id
        WHERE instant_batches.id = ?
        """,
        (batch_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"未找到即时法批次 {batch_id}")
    return row


def _find_existing_lj_project(connection, project_name: str, input_value_type: str):
    return connection.execute(
        """
        SELECT id, name, input_value_type
        FROM projects
        WHERE method_type = ? AND name = ? AND input_value_type = ?
        ORDER BY id ASC
        LIMIT 1
        """,
        (PROJECT_METHOD_LJ, project_name, input_value_type),
    ).fetchone()


def _lj_project_name_exists(connection, project_name: str) -> bool:
    row = connection.execute(
        """
        SELECT 1
        FROM projects
        WHERE method_type = ? AND name = ?
        LIMIT 1
        """,
        (PROJECT_METHOD_LJ, project_name),
    ).fetchone()
    return row is not None


def _resolve_unique_lj_project_name(connection, source_project_name: str, input_value_type: str) -> str:
    cleaned_source_name = str(source_project_name or "").strip() or "即时法转入项目"
    if not _lj_project_name_exists(connection, cleaned_source_name):
        return cleaned_source_name

    input_value_type_label = get_input_value_type_label(input_value_type)
    candidate_name = f"{cleaned_source_name}（{input_value_type_label}）"
    if not _lj_project_name_exists(connection, candidate_name):
        return candidate_name

    suffix_index = 2
    while True:
        candidate_name = f"{cleaned_source_name}（{input_value_type_label}-{suffix_index}）"
        if not _lj_project_name_exists(connection, candidate_name):
            return candidate_name
        suffix_index += 1


def _resolve_lj_project_plan(connection, source_project_name: str, input_value_type: str) -> dict[str, object]:
    normalized_input_value_type = normalize_input_value_type(input_value_type)
    existing_project = _find_existing_lj_project(connection, source_project_name, normalized_input_value_type)
    if existing_project is not None:
        return {
            "action": "reuse",
            "project_id": int(existing_project["id"]),
            "project_name": str(existing_project["name"]),
            "input_value_type": normalized_input_value_type,
        }

    project_name = _resolve_unique_lj_project_name(connection, source_project_name, normalized_input_value_type)
    return {
        "action": "create",
        "project_id": None,
        "project_name": project_name,
        "input_value_type": normalized_input_value_type,
    }


def _lj_batch_lot_exists(connection, project_id: int, lot_no: str) -> bool:
    row = connection.execute(
        """
        SELECT 1
        FROM batches
        WHERE project_id = ?
          AND LOWER(TRIM(lot_no)) = LOWER(TRIM(?))
        LIMIT 1
        """,
        (int(project_id), str(lot_no or "").strip()),
    ).fetchone()
    return row is not None


def _resolve_lj_batch_lot_no(connection, project_id: int, source_lot_no: str) -> str:
    cleaned_lot_no = str(source_lot_no or "").strip() or "即时法转入批次"
    if not _lj_batch_lot_exists(connection, project_id, cleaned_lot_no):
        return cleaned_lot_no

    suffix_index = 2
    while True:
        candidate_lot_no = f"{cleaned_lot_no}-转入{suffix_index}"
        if not _lj_batch_lot_exists(connection, project_id, candidate_lot_no):
            return candidate_lot_no
        suffix_index += 1


def _build_instant_transfer_blockers(
    batch,
    summary: dict[str, object],
) -> list[str]:
    blockers: list[str] = []
    transfer_status = normalize_instant_transfer_status(batch["transfer_status"])
    if transfer_status == INSTANT_TRANSFER_STATUS_TRANSFERRED:
        blockers.append("该批次已转入 LJ 法")
    if int(summary["effective_count"]) < INSTANT_TRANSFER_READY_COUNT:
        blockers.append(f"有效点不足 {INSTANT_TRANSFER_READY_COUNT} 个")
    pending_count = int(summary.get("pending_outlier_review_count", 0) or 0)
    if pending_count > 0:
        blockers.append(f"仍存在 {pending_count} 个待处理疑似离群点")
    return blockers


def _build_instant_transfer_state(
    batch,
    analysis_df: pd.DataFrame,
    summary: dict[str, object],
) -> dict[str, object]:
    with get_connection() as connection:
        project_plan = _resolve_lj_project_plan(
            connection,
            str(batch["project_name"]),
            str(batch["input_value_type"]),
        )
        preview_project_id = project_plan["project_id"]
        if preview_project_id is None:
            preview_project_id = -1
        preview_batch_lot_no = _resolve_lj_batch_lot_no(
            connection,
            int(preview_project_id) if int(preview_project_id) > 0 else 0,
            str(batch["lot_no"]),
        ) if int(preview_project_id) > 0 else str(batch["lot_no"])
        if int(preview_project_id) <= 0:
            # Preview an exact future lot_no using the name that will be created; new project starts without batches.
            preview_batch_lot_no = str(batch["lot_no"])

    transfer_status = normalize_instant_transfer_status(batch["transfer_status"])
    blockers = _build_instant_transfer_blockers(batch, summary)
    transferred_to_lj_batch_display = (
        str(batch["transferred_to_lj_batch_lot_no"] or "").strip()
        or (f"批次 {int(batch['transferred_to_lj_batch_id'])}" if batch["transferred_to_lj_batch_id"] else "")
    )
    pending_rows = analysis_df[
        (analysis_df["is_effective"] == 1)
        & (analysis_df["is_outlier_suspect"] == 1)
        & (analysis_df["manual_status"] == INSTANT_MANUAL_STATUS_PENDING_REVIEW)
    ].copy()
    pending_sequences = (
        pending_rows["effective_sequence"].dropna().astype(int).tolist()
        if not pending_rows.empty and "effective_sequence" in pending_rows.columns
        else []
    )
    return {
        "status": transfer_status,
        "eligible": len(blockers) == 0,
        "blockers": blockers,
        "pending_outlier_review_count": int(summary.get("pending_outlier_review_count", 0) or 0),
        "pending_outlier_effective_sequences": pending_sequences,
        "target_project_action": str(project_plan["action"]),
        "target_project_id": project_plan["project_id"],
        "target_project_name": str(project_plan["project_name"]),
        "target_batch_lot_no": preview_batch_lot_no,
        "is_transferred": transfer_status == INSTANT_TRANSFER_STATUS_TRANSFERRED,
        "transferred_at": batch["transferred_at"],
        "transferred_effective_count": int(batch["transferred_effective_count"] or 0)
        if batch["transferred_effective_count"] is not None
        else 0,
        "transferred_to_lj_project_id": batch["transferred_to_lj_project_id"],
        "transferred_to_lj_project_name": str(batch["transferred_to_lj_project_name"] or "").strip(),
        "transferred_to_lj_batch_id": batch["transferred_to_lj_batch_id"],
        "transferred_to_lj_batch_display": transferred_to_lj_batch_display,
    }


def build_instant_transfer_preview(batch_id: int) -> dict[str, object]:
    batch = get_instant_batch(batch_id)
    results_df = get_instant_results(batch_id, include_manual_note=True)
    analysis_df, summary = analyze_instant_results(results_df)
    transfer_state = _build_instant_transfer_state(batch, analysis_df, summary)
    summary["transfer_status"] = transfer_state["status"]
    summary["transfer_action_enabled"] = transfer_state["eligible"]
    summary["transfer_blockers"] = list(transfer_state["blockers"])
    return {
        "batch": batch,
        "analysis_df": analysis_df,
        "summary": summary,
        "transfer_state": transfer_state,
    }


def persist_instant_batch_analysis(batch_id: int) -> None:
    results_df = get_instant_results(batch_id, include_manual_note=True)
    analysis_df, _ = analyze_instant_results(results_df)
    analysis_rows: list[dict[str, object]] = []
    for _, row in analysis_df.iterrows():
        if int(row.get("is_effective", 1) or 0) != 1:
            continue
        analysis_rows.append(
            {
                "id": int(row["id"]),
                "is_outlier_suspect": int(row.get("is_outlier_suspect", 0) or 0),
                "outlier_method": str(row.get("outlier_method", "") or ""),
                "grubbs_statistic": _safe_float_or_none(row.get("grubbs_statistic")),
                "grubbs_threshold": _safe_float_or_none(row.get("grubbs_threshold")),
            }
        )
    save_instant_result_analysis_snapshot(batch_id, analysis_rows)


def build_instant_workbench_context(batch_id: int) -> dict[str, object]:
    batch = get_instant_batch(batch_id)
    results_df = get_instant_results(batch_id, include_manual_note=True)
    analysis_df, summary = analyze_instant_results(results_df)
    latest_row = get_latest_instant_row(analysis_df)
    transfer_state = _build_instant_transfer_state(batch, analysis_df, summary)
    summary["transfer_status"] = transfer_state["status"]
    summary["transfer_action_enabled"] = transfer_state["eligible"]
    summary["transfer_blockers"] = list(transfer_state["blockers"])
    input_value_type = normalize_input_value_type(batch["input_value_type"])
    return {
        "batch": batch,
        "results_df": results_df,
        "analysis_df": analysis_df,
        "summary": summary,
        "transfer_state": transfer_state,
        "latest_row": latest_row,
        "input_value_type": input_value_type,
        "input_value_type_label": get_input_value_type_label(input_value_type),
        "operator_options": build_instant_operator_options(results_df),
    }


def save_instant_result(
    *,
    batch_id: int,
    test_time: str,
    operator: str,
    value: float,
    log_value: float | None,
) -> int:
    result_id = add_instant_result(
        batch_id=batch_id,
        test_time=test_time,
        operator=operator,
        value=value,
        log_value=log_value,
    )
    persist_instant_batch_analysis(batch_id)
    return result_id


def disable_instant_result(result_id: int) -> int:
    result_row = get_instant_result(result_id)
    batch_id = int(result_row["batch_id"])
    set_instant_result_effective_state(
        result_id,
        is_effective=0,
        manual_status=INSTANT_MANUAL_STATUS_DISABLED,
    )
    persist_instant_batch_analysis(batch_id)
    return batch_id


def restore_instant_result(result_id: int) -> int:
    result_row = get_instant_result(result_id)
    batch_id = int(result_row["batch_id"])
    set_instant_result_effective_state(
        result_id,
        is_effective=1,
        manual_status=INSTANT_MANUAL_STATUS_RESTORED,
    )
    persist_instant_batch_analysis(batch_id)
    return batch_id


def keep_instant_result(result_id: int) -> int:
    result_row = get_instant_result(result_id)
    batch_id = int(result_row["batch_id"])
    set_instant_result_effective_state(
        result_id,
        is_effective=1,
        manual_status=INSTANT_MANUAL_STATUS_KEEP,
    )
    persist_instant_batch_analysis(batch_id)
    return batch_id


def confirm_instant_transfer_to_lj(batch_id: int) -> dict[str, object]:
    transferred_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        batch = _fetch_instant_batch_row_with_connection(connection, batch_id)
        results_df = _load_instant_results_with_connection(connection, batch_id, include_manual_note=True)
        analysis_df, summary = analyze_instant_results(results_df)
        blockers = _build_instant_transfer_blockers(batch, summary)
        if blockers:
            raise ValueError("；".join(blockers))

        effective_df = (
            analysis_df.loc[analysis_df["is_effective"] == 1]
            .copy()
            .sort_values(["test_time", "id"], ascending=[True, True])
            .reset_index(drop=True)
        )
        transferred_effective_count = int(len(effective_df))
        if transferred_effective_count < INSTANT_TRANSFER_READY_COUNT:
            raise ValueError(f"有效点不足 {INSTANT_TRANSFER_READY_COUNT} 个，不能确认转入 LJ 法。")

        project_plan = _resolve_lj_project_plan(
            connection,
            str(batch["project_name"]),
            str(batch["input_value_type"]),
        )
        target_project_id = project_plan["project_id"]
        target_project_name = str(project_plan["project_name"])
        if target_project_id is None:
            cursor = connection.execute(
                """
                INSERT INTO projects (name, method_type, input_value_type)
                VALUES (?, ?, ?)
                """,
                (
                    target_project_name,
                    PROJECT_METHOD_LJ,
                    str(project_plan["input_value_type"]),
                ),
            )
            target_project_id = int(cursor.lastrowid)
        else:
            target_project_id = int(target_project_id)

        target_batch_lot_no = _resolve_lj_batch_lot_no(
            connection,
            target_project_id,
            str(batch["lot_no"]),
        )
        batch_cursor = connection.execute(
            """
            INSERT INTO batches (
                project_id,
                instrument,
                reagent,
                qc_material,
                concentration,
                lot_no,
                target_n,
                cv_limit,
                source_method,
                source_instant_project_id,
                source_instant_batch_id,
                source_transfer_time
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                target_project_id,
                str(batch["instrument"] or ""),
                str(batch["reagent"] or ""),
                str(batch["qc_material"] or ""),
                str(batch["concentration"] or ""),
                target_batch_lot_no,
                INSTANT_TRANSFER_READY_COUNT,
                None,
                "instant",
                int(batch["project_id"]),
                int(batch["id"]),
                transferred_at,
            ),
        )
        target_batch_id = int(batch_cursor.lastrowid)

        for _, row in effective_df.iterrows():
            log_value = row.get("log_value")
            connection.execute(
                """
                INSERT INTO results (
                    batch_id,
                    test_time,
                    operator,
                    value,
                    log_value,
                    reagent_lot_changed,
                    manual_note
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    target_batch_id,
                    pd.Timestamp(row["test_time"]).strftime("%Y-%m-%d %H:%M:%S"),
                    str(row.get("operator", "") or ""),
                    float(row["value"]),
                    None if pd.isna(log_value) else float(log_value),
                    0,
                    str(row.get("manual_note", "") or ""),
                ),
            )

        connection.execute(
            """
            UPDATE instant_batches
            SET transfer_status = ?,
                transferred_to_lj_project_id = ?,
                transferred_to_lj_batch_id = ?,
                transferred_at = ?,
                transferred_effective_count = ?,
                lj_transfer_status = ?,
                lj_transfer_target_batch_id = ?,
                lj_transfer_marked_at = ?
            WHERE id = ?
            """,
            (
                INSTANT_TRANSFER_STATUS_TRANSFERRED,
                target_project_id,
                target_batch_id,
                transferred_at,
                transferred_effective_count,
                INSTANT_TRANSFER_STATUS_TRANSFERRED,
                target_batch_id,
                transferred_at,
                batch_id,
            ),
        )
        connection.execute(
            """
            UPDATE instant_results
            SET lj_transfer_status = ?,
                lj_transfer_target_batch_id = ?,
                lj_transfer_at = ?
            WHERE batch_id = ? AND is_effective = 1
            """,
            (
                INSTANT_TRANSFER_STATUS_TRANSFERRED,
                target_batch_id,
                transferred_at,
                batch_id,
            ),
        )

    return {
        "source_project_id": int(batch["project_id"]),
        "source_project_name": str(batch["project_name"]),
        "source_batch_id": int(batch["id"]),
        "source_batch_lot_no": str(batch["lot_no"] or "").strip(),
        "target_project_id": target_project_id,
        "target_project_name": target_project_name,
        "target_batch_id": target_batch_id,
        "target_batch_lot_no": target_batch_lot_no,
        "transferred_at": transferred_at,
        "transferred_effective_count": transferred_effective_count,
        "building_count": min(INSTANT_TRANSFER_READY_COUNT, transferred_effective_count),
        "formal_count": max(0, transferred_effective_count - INSTANT_TRANSFER_READY_COUNT),
        "target_project_action": str(project_plan["action"]),
    }
