from __future__ import annotations

from datetime import datetime
import math

import pandas as pd

from database import (
    get_batch,
    get_result,
    get_results,
    save_result_outlier_snapshot,
    set_result_building_inclusion_state,
)
from services.outlier_service import (
    DEFAULT_GRUBBS_ALPHA,
    GRUBBS_METHOD_NAME,
    calculate_grubbs_test,
    derive_outlier_status,
    normalize_outlier_manual_status,
    OUTLIER_MANUAL_STATUS_DISABLED,
    OUTLIER_MANUAL_STATUS_KEEP,
    OUTLIER_MANUAL_STATUS_NORMAL,
    OUTLIER_MANUAL_STATUS_RESTORED,
)


LJ_BUILDING_PHASE_LABEL = "建靶数据"
LJ_FORMAL_PHASE_LABEL = "正式数据"


RULE_PRIORITY = {
    "1_3s": 1,
    "2_2s": 2,
    "R_4s": 3,
    "4_1s": 4,
    "10x": 5,
    "1_2s": 6,
}

RULE_ERROR_TYPE = {
    "1_3s": "\u968f\u673a\u8bef\u5dee",
    "R_4s": "\u968f\u673a\u8bef\u5dee",
    "2_2s": "\u7cfb\u7edf\u8bef\u5dee",
    "4_1s": "\u7cfb\u7edf\u8bef\u5dee",
    "10x": "\u7cfb\u7edf\u8bef\u5dee",
    "1_2s": "\u8b66\u544a",
}

RULE_PROMPT = {
    "1_2s": (
        "\u5f53\u524d\u7ed3\u679c\u8d85\u8fc7 \u00b12SD\uff0c"
        "\u5c5e\u4e8e\u8b66\u544a\u4fe1\u53f7\uff0c\u5efa\u8bae\u5173\u6ce8\u540e\u7eed\u7ed3\u679c\u5e76\u590d\u67e5\u8fd1\u671f\u72b6\u6001\u3002"
    ),
    "1_3s": (
        "\u5f53\u524d\u7ed3\u679c\u8d85\u8fc7 \u00b13SD\uff0c"
        "\u63d0\u793a\u660e\u663e\u5931\u63a7\uff0c\u504f\u5411\u968f\u673a\u8bef\u5dee\uff0c"
        "\u5efa\u8bae\u6682\u505c\u653e\u884c\u5e76\u68c0\u67e5\u52a0\u6837\u3001\u4eea\u5668\u77ac\u65f6\u6ce2\u52a8\u548c\u64cd\u4f5c\u5f02\u5e38\u3002"
    ),
    "2_2s": (
        "\u8fde\u7eed\u4e24\u4e2a\u7ed3\u679c\u540c\u4fa7\u8d85\u8fc7 \u00b12SD\uff0c"
        "\u63d0\u793a\u7cfb\u7edf\u8bef\u5dee\uff0c\u5efa\u8bae\u68c0\u67e5\u6821\u51c6\u3001\u8bd5\u5242\u6279\u6b21\u53d8\u5316\u548c\u4eea\u5668\u6f02\u79fb\u3002"
    ),
    "R_4s": (
        "\u76f8\u90bb\u4e24\u4e2a\u7ed3\u679c\u5dee\u503c\u8d85\u8fc7 4SD\uff0c"
        "\u63d0\u793a\u968f\u673a\u8bef\u5dee\uff0c\u5efa\u8bae\u68c0\u67e5\u79fb\u6db2\u3001\u6c14\u6ce1\u3001\u77ac\u65f6\u4eea\u5668\u5f02\u5e38\u3002"
    ),
    "4_1s": (
        "\u8fde\u7eed\u56db\u4e2a\u7ed3\u679c\u540c\u4fa7\u8d85\u8fc7 \u00b11SD\uff0c"
        "\u63d0\u793a\u7cfb\u7edf\u504f\u79fb\uff0c\u5efa\u8bae\u68c0\u67e5\u6821\u51c6\u72b6\u6001\u548c\u7cfb\u7edf\u7a33\u5b9a\u6027\u3002"
    ),
    "10x": (
        "\u8fde\u7eed\u5341\u4e2a\u7ed3\u679c\u4f4d\u4e8e\u5747\u503c\u540c\u4fa7\uff0c"
        "\u63d0\u793a\u6301\u7eed\u7cfb\u7edf\u504f\u79fb\uff0c\u5efa\u8bae\u68c0\u67e5\u9776\u503c\u9002\u914d\u6027\u3001\u6821\u51c6\u72b6\u6001\u548c\u65b9\u6cd5\u5b66\u6f02\u79fb\u3002"
    ),
}


def _empty_qc_dataframe(results_df: pd.DataFrame | None = None) -> pd.DataFrame:
    base = results_df.copy() if results_df is not None else pd.DataFrame()
    default_columns = [
        "id",
        "batch_id",
        "test_time",
        "operator",
        "value",
        "log_value",
        "reagent_lot_changed",
        "created_at",
        "sequence",
        "effective_sequence",
        "phase",
        "z",
        "status",
        "rule_hits",
        "error_type",
        "analysis_prompt",
        "is_building_included",
        "is_outlier_suspect",
        "outlier_status",
        "outlier_method",
        "grubbs_statistic",
        "grubbs_threshold",
        "manual_status",
        "handled_at",
    ]
    for column in default_columns:
        if column not in base.columns:
            base[column] = pd.Series(dtype="object")
    return base.iloc[0:0].copy()


def _empty_lj_stats(target_count: int) -> dict:
    return {
        "mean": None,
        "sd": None,
        "cv": None,
        "target_ready": False,
        "message": (
            f"当前建靶未完成，请至少录入前 {target_count} 个有效建靶点。"
        ),
        "latest_analysis": "建靶未完成，暂无 Westgard 规则分析。",
        "rule_summary": _empty_rule_summary(),
        "building_total_count": 0,
        "effective_building_count": 0,
        "disabled_building_count": 0,
        "has_formal_started": False,
        "current_suspect_id": None,
        "current_suspect_row": None,
        "grubbs_alpha": DEFAULT_GRUBBS_ALPHA,
    }


def _normalize_lj_result_columns(results_df: pd.DataFrame) -> pd.DataFrame:
    dataframe = results_df.copy()
    defaults: dict[str, object] = {
        "is_building_included": 1,
        "is_outlier_suspect": 0,
        "outlier_status": "normal",
        "outlier_method": "",
        "grubbs_statistic": pd.NA,
        "grubbs_threshold": pd.NA,
        "manual_status": OUTLIER_MANUAL_STATUS_NORMAL,
        "handled_at": pd.NA,
    }
    for column_name, default_value in defaults.items():
        if column_name not in dataframe.columns:
            dataframe[column_name] = default_value
    dataframe["is_building_included"] = dataframe["is_building_included"].fillna(1).astype(int)
    dataframe["is_outlier_suspect"] = dataframe["is_outlier_suspect"].fillna(0).astype(int)
    dataframe["manual_status"] = dataframe["manual_status"].map(normalize_outlier_manual_status)
    return dataframe


def _assign_lj_building_phase(
    dataframe: pd.DataFrame,
    target_count: int,
) -> tuple[pd.DataFrame, int]:
    effective_build_count = 0
    build_boundary_index = len(dataframe) - 1
    for index, row in dataframe.iterrows():
        dataframe.at[index, "sequence"] = index + 1
        dataframe.at[index, "effective_sequence"] = pd.NA
        if effective_build_count < target_count:
            dataframe.at[index, "phase"] = LJ_BUILDING_PHASE_LABEL
            if bool(int(row.get("is_building_included", 1) or 0)):
                effective_build_count += 1
                dataframe.at[index, "effective_sequence"] = effective_build_count
            build_boundary_index = index
            continue
        dataframe.at[index, "phase"] = LJ_FORMAL_PHASE_LABEL
    if effective_build_count >= target_count:
        return dataframe, int(build_boundary_index)
    return dataframe, len(dataframe) - 1


def _apply_lj_building_outlier_snapshot(
    dataframe: pd.DataFrame,
    target_count: int,
) -> tuple[pd.DataFrame, dict]:
    stats = _empty_lj_stats(target_count)
    if dataframe.empty:
        return dataframe, stats

    dataframe = _normalize_lj_result_columns(dataframe)
    dataframe = dataframe.sort_values(["test_time", "id"]).reset_index(drop=True)
    dataframe["sequence"] = pd.NA
    dataframe["effective_sequence"] = pd.NA
    dataframe["phase"] = LJ_BUILDING_PHASE_LABEL
    dataframe["z"] = pd.NA
    dataframe["status"] = "待建靶"
    dataframe["rule_hits"] = ""
    dataframe["error_type"] = "无"
    dataframe["analysis_prompt"] = "建靶未完成，暂不进行 Westgard 判定。"
    dataframe["is_outlier_suspect"] = 0
    dataframe["outlier_method"] = ""
    dataframe["grubbs_statistic"] = pd.NA
    dataframe["grubbs_threshold"] = pd.NA

    dataframe, build_boundary_index = _assign_lj_building_phase(dataframe, target_count)
    build_mask = dataframe["phase"] == LJ_BUILDING_PHASE_LABEL
    building_df = dataframe.loc[build_mask].copy()
    effective_building_df = building_df[building_df["is_building_included"] == 1].copy()
    formal_mask = dataframe["phase"] == LJ_FORMAL_PHASE_LABEL

    stats["building_total_count"] = int(len(building_df))
    stats["effective_building_count"] = int(len(effective_building_df))
    stats["disabled_building_count"] = int((building_df["is_building_included"] == 0).sum())
    stats["has_formal_started"] = bool(formal_mask.any())

    if not building_df.empty:
        dataframe.loc[build_mask, "status"] = LJ_BUILDING_PHASE_LABEL
        dataframe.loc[build_mask, "analysis_prompt"] = "建靶数据，不参与 Westgard 规则判定。"

    grubbs_result = calculate_grubbs_test(
        effective_building_df["value"].astype(float).tolist(),
        alpha=DEFAULT_GRUBBS_ALPHA,
    )
    suspect_build_id = None
    if grubbs_result.get("is_suspect"):
        suspected_index = int(grubbs_result.get("suspected_index") or 0)
        suspect_row = effective_building_df.iloc[suspected_index]
        suspect_build_id = int(suspect_row["id"])
        stats["current_suspect_id"] = suspect_build_id

    for index, row in dataframe.iterrows():
        phase = str(row.get("phase") or LJ_BUILDING_PHASE_LABEL)
        included = bool(int(row.get("is_building_included", 1) or 0))
        manual_status = normalize_outlier_manual_status(row.get("manual_status"))
        is_suspect = bool(phase == LJ_BUILDING_PHASE_LABEL and suspect_build_id == int(row["id"]))

        dataframe.at[index, "is_outlier_suspect"] = int(is_suspect)
        dataframe.at[index, "outlier_method"] = (
            GRUBBS_METHOD_NAME
            if phase == LJ_BUILDING_PHASE_LABEL and bool(grubbs_result.get("evaluation_ready"))
            else ""
        )
        dataframe.at[index, "grubbs_threshold"] = (
            grubbs_result.get("threshold")
            if phase == LJ_BUILDING_PHASE_LABEL and bool(grubbs_result.get("evaluation_ready"))
            else pd.NA
        )
        dataframe.at[index, "grubbs_statistic"] = (
            grubbs_result.get("statistic")
            if is_suspect
            else pd.NA
        )
        dataframe.at[index, "outlier_status"] = derive_outlier_status(
            is_building_included=included,
            is_suspect=is_suspect,
            manual_status=manual_status,
        )

        if phase != LJ_BUILDING_PHASE_LABEL:
            continue
        if not included:
            dataframe.at[index, "status"] = "建靶禁用"
            dataframe.at[index, "analysis_prompt"] = "该建靶点已禁用，不参与建靶统计与建靶图。"
            continue
        if manual_status == OUTLIER_MANUAL_STATUS_KEEP:
            dataframe.at[index, "status"] = "建靶保留"
            dataframe.at[index, "analysis_prompt"] = "该建靶点已人工保留，继续参与建靶统计。"
            continue
        if manual_status == OUTLIER_MANUAL_STATUS_RESTORED:
            dataframe.at[index, "status"] = "建靶恢复"
            dataframe.at[index, "analysis_prompt"] = "该建靶点已恢复参与建靶统计。"
            continue
        if is_suspect:
            dataframe.at[index, "status"] = "建靶疑似离群"
            dataframe.at[index, "analysis_prompt"] = (
                f"当前建靶点触发 Grubbs 疑似离群提示："
                f"G={float(grubbs_result.get('statistic') or 0.0):.4f}，"
                f"G临界值={float(grubbs_result.get('threshold') or 0.0):.4f}，"
                f"alpha={DEFAULT_GRUBBS_ALPHA:.2f}。"
            )

    if not effective_building_df.empty:
        mean = float(effective_building_df["value"].mean())
        stats["mean"] = mean
        if len(effective_building_df) >= 2:
            sd = float(effective_building_df["value"].std(ddof=1))
            stats["sd"] = sd
            if not math.isclose(mean, 0.0, abs_tol=1e-12):
                stats["cv"] = float(sd / mean * 100)

    stats["target_ready"] = int(len(effective_building_df)) >= int(target_count) and stats["sd"] is not None
    if stats["target_ready"]:
        stats["message"] = "建靶已完成，后续结果会自动进行 Westgard 质控判定。"
    else:
        remaining = max(int(target_count) - int(len(effective_building_df)), 0)
        stats["message"] = f"当前建靶未完成，还需补充 {remaining} 个有效建靶点。"

    if suspect_build_id is not None:
        suspect_row = dataframe.loc[dataframe["id"] == suspect_build_id].iloc[0]
        stats["current_suspect_row"] = suspect_row.to_dict()
    return dataframe, stats


def calculate_qc_results(results_df: pd.DataFrame, target_count: int) -> tuple[pd.DataFrame, dict]:
    stats = _empty_lj_stats(target_count)
    if results_df.empty:
        return _empty_qc_dataframe(results_df), stats

    dataframe, stats = _apply_lj_building_outlier_snapshot(results_df, target_count)
    formal_mask = dataframe["phase"] == LJ_FORMAL_PHASE_LABEL
    if not stats.get("target_ready"):
        latest_row = dataframe.sort_values(["test_time", "id"]).iloc[-1]
        stats["latest_analysis"] = str(latest_row.get("analysis_prompt", stats["latest_analysis"]))
        return dataframe, stats

    if math.isclose(float(stats["sd"]), 0.0, abs_tol=1e-12):
        dataframe.loc[formal_mask, "status"] = "无法判定（SD=0）"
        dataframe.loc[formal_mask, "analysis_prompt"] = "建靶已完成，但 SD=0，暂时无法进行 Westgard 规则分析。"
        stats["message"] = "建靶已完成，但 SD=0，后续结果暂时无法计算 z 值和 Westgard 规则。"
        if formal_mask.any():
            stats["latest_analysis"] = dataframe.loc[formal_mask, "analysis_prompt"].iloc[-1]
        return dataframe, stats

    dataframe.loc[formal_mask, "z"] = (
        dataframe.loc[formal_mask, "value"] - float(stats["mean"])
    ) / float(stats["sd"])
    _apply_westgard_rules(dataframe, formal_mask)

    formal_df = dataframe.loc[formal_mask].copy()
    if not formal_df.empty:
        stats["latest_analysis"] = _build_latest_analysis(formal_df.iloc[-1])
        stats["rule_summary"] = _build_rule_summary(formal_df)
    else:
        latest_row = dataframe.sort_values(["test_time", "id"]).iloc[-1]
        stats["latest_analysis"] = str(latest_row.get("analysis_prompt", stats["latest_analysis"]))
    return dataframe, stats


def calculate_target_building_cv_hint(results_df: pd.DataFrame, target_count: int) -> dict:
    # This helper is only for build-stage CV reminders and must not affect existing判读逻辑.
    empty_hint = {
        "collected_n": 0,
        "effective_n": 0,
        "disabled_n": 0,
        "evaluated_n": 0,
        "mean": None,
        "sd": None,
        "cv": None,
        "can_evaluate": False,
        "target_ready": False,
    }
    if results_df.empty:
        return empty_hint

    qc_df, stats = calculate_qc_results(results_df, target_count)
    building_df = qc_df[qc_df["phase"] == LJ_BUILDING_PHASE_LABEL].copy()
    effective_building_df = building_df[building_df["is_building_included"] == 1].copy()
    return {
        "collected_n": len(building_df),
        "effective_n": len(effective_building_df),
        "disabled_n": int((building_df["is_building_included"] == 0).sum()),
        "evaluated_n": len(effective_building_df),
        "mean": stats.get("mean"),
        "sd": stats.get("sd"),
        "cv": stats.get("cv"),
        "can_evaluate": stats.get("cv") is not None,
        "target_ready": bool(stats.get("target_ready")),
    }


def format_stats_message(stats: dict) -> str:
    message = stats.get("message", "")
    if not stats.get("target_ready"):
        return message

    mean = stats.get("mean")
    sd = stats.get("sd")
    cv = stats.get("cv")
    separator = "\uff0c"
    stats_text = [
        f"Mean={mean:.4f}" if mean is not None else "Mean=-",
        f"SD={sd:.4f}" if sd is not None else "SD=-",
        f"CV%={cv:.2f}" if cv is not None else "CV%=-",
    ]
    return message + "\uff08" + separator.join(stats_text) + "\uff09"


def calculate_realtime_stats(
    results_df: pd.DataFrame,
    target_n: int,
    start_time=None,
    end_time=None,
) -> tuple[dict, str]:
    empty_stats = {"mean": None, "sd": None, "cv": None}
    if results_df.empty:
        return empty_stats, "\u6682\u65e0\u6570\u636e\uff0c\u65e0\u6cd5\u8ba1\u7b97\u5b9e\u65f6\u7edf\u8ba1\u3002"

    qc_df, _ = calculate_qc_results(results_df, target_n)
    formal_df = qc_df[qc_df["phase"] == LJ_FORMAL_PHASE_LABEL].copy()
    if formal_df.empty:
        return empty_stats, "\u6682\u65e0\u6b63\u5f0f\u8d28\u63a7\u6570\u636e\uff0c\u65e0\u6cd5\u8ba1\u7b97\u5b9e\u65f6\u7edf\u8ba1\u3002"

    in_control_df = formal_df[formal_df["status"] == "\u7b26\u5408\u8d28\u63a7"].copy()
    if in_control_df.empty:
        return empty_stats, "\u5f53\u524d\u6279\u6b21\u6682\u65e0\u5224\u5b9a\u4e3a\u201c\u5728\u63a7\u201d\u7684\u6b63\u5f0f\u8d28\u63a7\u6570\u636e\u3002"

    if start_time is None:
        start_time = in_control_df["test_time"].min()
    if end_time is None:
        end_time = formal_df["test_time"].max()

    start_timestamp = pd.Timestamp(start_time)
    end_timestamp = pd.Timestamp(end_time)
    filtered = in_control_df[
        (in_control_df["test_time"] >= start_timestamp) & (in_control_df["test_time"] <= end_timestamp)
    ].copy()
    if filtered.empty:
        return empty_stats, "\u6240\u9009\u65f6\u95f4\u8303\u56f4\u5185\u6ca1\u6709\u5224\u5b9a\u4e3a\u201c\u5728\u63a7\u201d\u7684\u6b63\u5f0f\u8d28\u63a7\u6570\u636e\u3002"

    mean = float(filtered["value"].mean())
    if len(filtered) < 2:
        return {"mean": mean, "sd": None, "cv": None}, "\u6240\u9009\u533a\u95f4\u6570\u636e\u4e0d\u8db3 2 \u6761\uff0cSD \u548c CV% \u6682\u65f6\u65e0\u6cd5\u8ba1\u7b97\u3002"

    sd = float(filtered["value"].std(ddof=1))
    cv = None if math.isclose(mean, 0.0, abs_tol=1e-12) else float(sd / mean * 100)
    return {"mean": mean, "sd": sd, "cv": cv}, ""


def persist_lj_batch_outlier_snapshot(batch_id: int) -> tuple[pd.DataFrame, dict]:
    batch = get_batch(batch_id)
    results_df = get_results(batch_id, include_manual_note=True)
    if results_df.empty:
        return _empty_qc_dataframe(results_df), _empty_lj_stats(int(batch["target_n"]))

    qc_df, stats = calculate_qc_results(results_df, int(batch["target_n"]))
    save_result_outlier_snapshot(
        batch_id,
        qc_df[
            [
                "id",
                "is_outlier_suspect",
                "outlier_status",
                "outlier_method",
                "grubbs_statistic",
                "grubbs_threshold",
            ]
        ].to_dict(orient="records"),
    )
    return qc_df, stats


def _get_lj_building_row_for_action(result_id: int) -> tuple[pd.Series, dict]:
    result_row = get_result(result_id)
    batch_id = int(result_row["batch_id"])
    batch = get_batch(batch_id)
    results_df = get_results(batch_id, include_manual_note=True)
    qc_df, stats = calculate_qc_results(results_df, int(batch["target_n"]))
    selected_rows = qc_df[qc_df["id"] == int(result_id)]
    if selected_rows.empty:
        raise ValueError(f"未找到检测记录 {result_id}")
    selected_row = selected_rows.iloc[0]
    if str(selected_row.get("phase")) != LJ_BUILDING_PHASE_LABEL:
        raise ValueError("仅建靶期记录支持离群值处理。")
    if bool(stats.get("has_formal_started")):
        raise ValueError("正式期启用后不再允许调整 LJ 建靶期离群值状态。")
    return selected_row, stats


def disable_lj_building_result(result_id: int) -> tuple[pd.DataFrame, dict]:
    selected_row, _ = _get_lj_building_row_for_action(result_id)
    set_result_building_inclusion_state(
        int(selected_row["id"]),
        is_building_included=0,
        manual_status=OUTLIER_MANUAL_STATUS_DISABLED,
        handled_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )
    return persist_lj_batch_outlier_snapshot(int(selected_row["batch_id"]))


def restore_lj_building_result(result_id: int) -> tuple[pd.DataFrame, dict]:
    selected_row, _ = _get_lj_building_row_for_action(result_id)
    set_result_building_inclusion_state(
        int(selected_row["id"]),
        is_building_included=1,
        manual_status=OUTLIER_MANUAL_STATUS_RESTORED,
        handled_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )
    return persist_lj_batch_outlier_snapshot(int(selected_row["batch_id"]))


def keep_lj_building_result(result_id: int) -> tuple[pd.DataFrame, dict]:
    selected_row, _ = _get_lj_building_row_for_action(result_id)
    set_result_building_inclusion_state(
        int(selected_row["id"]),
        is_building_included=1,
        manual_status=OUTLIER_MANUAL_STATUS_KEEP,
        handled_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )
    return persist_lj_batch_outlier_snapshot(int(selected_row["batch_id"]))


def _apply_westgard_rules(dataframe: pd.DataFrame, formal_mask: pd.Series) -> None:
    formal_indices = dataframe.index[formal_mask].tolist()

    for position, dataframe_index in enumerate(formal_indices):
        current_z = float(dataframe.at[dataframe_index, "z"])
        rule_hits: list[str] = []

        if abs(current_z) > 3:
            rule_hits.append("1_3s")
        elif abs(current_z) > 2:
            rule_hits.append("1_2s")

        if position >= 1:
            previous_z = float(dataframe.at[formal_indices[position - 1], "z"])
            if abs(current_z) > 2 and abs(previous_z) > 2 and current_z * previous_z > 0:
                rule_hits.append("2_2s")
            if abs(current_z - previous_z) > 4:
                rule_hits.append("R_4s")

        if position >= 3:
            last_four = [float(dataframe.at[index, "z"]) for index in formal_indices[position - 3 : position + 1]]
            if all(abs(value) > 1 for value in last_four) and _same_side(last_four):
                rule_hits.append("4_1s")

        if position >= 9:
            last_ten = [float(dataframe.at[index, "z"]) for index in formal_indices[position - 9 : position + 1]]
            if _same_side(last_ten):
                rule_hits.append("10x")

        unique_hits = _deduplicate_rules(rule_hits)
        dataframe.at[dataframe_index, "rule_hits"] = ", ".join(unique_hits)

        if not unique_hits:
            dataframe.at[dataframe_index, "status"] = "\u7b26\u5408\u8d28\u63a7"
            dataframe.at[dataframe_index, "error_type"] = "\u65e0"
            dataframe.at[dataframe_index, "analysis_prompt"] = (
                "\u672a\u547d\u4e2d Westgard \u89c4\u5219\uff0c\u5f53\u524d\u7ed3\u679c\u7b26\u5408\u8d28\u63a7\u3002"
            )
            continue

        primary_rule = min(unique_hits, key=lambda rule_name: RULE_PRIORITY[rule_name])
        dataframe.at[dataframe_index, "error_type"] = RULE_ERROR_TYPE[primary_rule]
        dataframe.at[dataframe_index, "analysis_prompt"] = _build_analysis_prompt(unique_hits)

        if any(rule_name in {"1_3s", "2_2s", "R_4s", "4_1s", "10x"} for rule_name in unique_hits):
            dataframe.at[dataframe_index, "status"] = "\u5931\u63a7"
        elif unique_hits == ["1_2s"]:
            dataframe.at[dataframe_index, "status"] = "\u8b66\u544a"
        else:
            dataframe.at[dataframe_index, "status"] = "\u7b26\u5408\u8d28\u63a7"


def _same_side(z_values: list[float]) -> bool:
    return all(value > 0 for value in z_values) or all(value < 0 for value in z_values)


def _deduplicate_rules(rule_hits: list[str]) -> list[str]:
    unique_hits: list[str] = []
    for rule_name in rule_hits:
        if rule_name not in unique_hits:
            unique_hits.append(rule_name)
    return unique_hits


def _empty_rule_summary() -> dict:
    return {
        "1_2s": 0,
        "1_3s": 0,
        "2_2s": 0,
        "R_4s": 0,
        "4_1s": 0,
        "10x": 0,
        "warning_count": 0,
        "out_of_control_count": 0,
    }


def _build_rule_summary(formal_df: pd.DataFrame) -> dict:
    summary = _empty_rule_summary()
    for rule_name in ("1_2s", "1_3s", "2_2s", "R_4s", "4_1s", "10x"):
        summary[rule_name] = int(formal_df["rule_hits"].fillna("").str.contains(rule_name, regex=False).sum())

    summary["warning_count"] = int((formal_df["status"] == "\u8b66\u544a").sum())
    summary["out_of_control_count"] = int((formal_df["status"] == "\u5931\u63a7").sum())
    return summary


def _build_analysis_prompt(rule_hits: list[str]) -> str:
    prompt_lines = [f"{rule_name}\uff1a{RULE_PROMPT[rule_name]}" for rule_name in rule_hits]
    return "\n".join(prompt_lines)


def _build_latest_analysis(latest_row: pd.Series) -> str:
    rule_hits = str(latest_row.get("rule_hits", "")).strip()
    status = str(latest_row.get("status", ""))
    if not rule_hits:
        return str(latest_row.get("analysis_prompt", "\u6682\u65e0\u5206\u6790\u63d0\u793a\u3002"))
    return (
        f"\u6700\u65b0\u7ed3\u679c\u72b6\u6001\uff1a{status}\n"
        f"\u89e6\u53d1\u89c4\u5219\uff1a{rule_hits}\n"
        f"{latest_row.get('analysis_prompt', '')}"
    )
