from __future__ import annotations

import math

import pandas as pd


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
        "phase",
        "z",
        "status",
        "rule_hits",
        "error_type",
        "analysis_prompt",
    ]
    for column in default_columns:
        if column not in base.columns:
            base[column] = pd.Series(dtype="object")
    return base.iloc[0:0].copy()


def calculate_qc_results(results_df: pd.DataFrame, target_count: int) -> tuple[pd.DataFrame, dict]:
    dataframe = results_df.copy()
    stats = {
        "mean": None,
        "sd": None,
        "cv": None,
        "target_ready": False,
        "message": (
            f"\u5f53\u524d\u5efa\u9776\u672a\u5b8c\u6210\uff0c"
            f"\u8bf7\u81f3\u5c11\u5f55\u5165\u524d {target_count} \u6b21\u7ed3\u679c\u3002"
        ),
        "latest_analysis": "\u5efa\u9776\u672a\u5b8c\u6210\uff0c\u6682\u65e0 Westgard \u89c4\u5219\u5206\u6790\u3002",
        "rule_summary": _empty_rule_summary(),
    }

    if dataframe.empty:
        return _empty_qc_dataframe(results_df), stats

    dataframe = dataframe.sort_values(["test_time", "id"]).reset_index(drop=True)
    dataframe["sequence"] = dataframe.index + 1
    dataframe["phase"] = dataframe["sequence"].apply(
        lambda seq: "\u5efa\u9776\u6570\u636e" if seq <= target_count else "\u6b63\u5f0f\u6570\u636e"
    )
    dataframe["z"] = pd.NA
    dataframe["status"] = "\u5f85\u5efa\u9776"
    dataframe["rule_hits"] = ""
    dataframe["error_type"] = "\u65e0"
    dataframe["analysis_prompt"] = "\u5efa\u9776\u672a\u5b8c\u6210\uff0c\u6682\u4e0d\u8fdb\u884c Westgard \u5224\u5b9a\u3002"

    if len(dataframe) < target_count:
        return dataframe, stats

    target_df = dataframe.iloc[:target_count].copy()
    mean = float(target_df["value"].mean())
    sd = float(target_df["value"].std(ddof=1))
    cv = None if math.isclose(mean, 0.0, abs_tol=1e-12) else float(sd / mean * 100)

    stats = {
        "mean": mean,
        "sd": sd,
        "cv": cv,
        "target_ready": True,
        "message": (
            "\u5efa\u9776\u5df2\u5b8c\u6210\uff0c"
            "\u540e\u7eed\u7ed3\u679c\u4f1a\u81ea\u52a8\u8fdb\u884c Westgard \u8d28\u63a7\u5224\u5b9a\u3002"
        ),
        "latest_analysis": "\u6682\u65e0\u6b63\u5f0f\u8d28\u63a7\u6570\u636e\u3002",
        "rule_summary": _empty_rule_summary(),
    }

    build_mask = dataframe["sequence"] <= target_count
    dataframe.loc[build_mask, "status"] = "\u5efa\u9776\u6570\u636e"
    dataframe.loc[build_mask, "analysis_prompt"] = "\u5efa\u9776\u6570\u636e\uff0c\u4e0d\u53c2\u4e0e Westgard \u89c4\u5219\u5224\u5b9a\u3002"

    if math.isclose(sd, 0.0, abs_tol=1e-12):
        formal_mask = dataframe["sequence"] > target_count
        dataframe.loc[formal_mask, "status"] = "\u65e0\u6cd5\u5224\u5b9a\uff08SD=0\uff09"
        dataframe.loc[formal_mask, "analysis_prompt"] = (
            "\u5efa\u9776\u5df2\u5b8c\u6210\uff0c\u4f46 SD=0\uff0c\u6682\u65f6\u65e0\u6cd5\u8fdb\u884c Westgard \u89c4\u5219\u5206\u6790\u3002"
        )
        stats["message"] = (
            "\u5efa\u9776\u5df2\u5b8c\u6210\uff0c\u4f46 SD=0\uff0c"
            "\u540e\u7eed\u7ed3\u679c\u6682\u65f6\u65e0\u6cd5\u8ba1\u7b97 z \u503c\u548c Westgard \u89c4\u5219\u3002"
        )
        stats["latest_analysis"] = dataframe.loc[formal_mask, "analysis_prompt"].iloc[-1] if formal_mask.any() else stats["latest_analysis"]
        return dataframe, stats

    formal_mask = dataframe["sequence"] > target_count
    dataframe.loc[formal_mask, "z"] = (
        dataframe.loc[formal_mask, "value"] - mean
    ) / sd
    _apply_westgard_rules(dataframe, formal_mask)

    formal_df = dataframe.loc[formal_mask].copy()
    if not formal_df.empty:
        stats["latest_analysis"] = _build_latest_analysis(formal_df.iloc[-1])
        stats["rule_summary"] = _build_rule_summary(formal_df)
    return dataframe, stats


def calculate_target_building_cv_hint(results_df: pd.DataFrame, target_count: int) -> dict:
    # This helper is only for build-stage CV reminders and must not affect existing判读逻辑.
    empty_hint = {
        "collected_n": 0,
        "evaluated_n": 0,
        "mean": None,
        "sd": None,
        "cv": None,
        "can_evaluate": False,
        "target_ready": False,
    }
    if results_df.empty:
        return empty_hint

    dataframe = results_df.copy().sort_values(["test_time", "id"]).reset_index(drop=True)
    evaluated_n = min(len(dataframe), int(target_count))
    if evaluated_n <= 0:
        return empty_hint

    building_df = dataframe.iloc[:evaluated_n].copy()
    mean = float(building_df["value"].mean()) if not building_df.empty else None
    if len(building_df) < 2:
        return {
            **empty_hint,
            "collected_n": len(building_df),
            "evaluated_n": evaluated_n,
            "mean": mean,
        }

    sd = float(building_df["value"].std(ddof=1))
    cv = None if mean is None or math.isclose(mean, 0.0, abs_tol=1e-12) else float(sd / mean * 100)
    return {
        "collected_n": len(building_df),
        "evaluated_n": evaluated_n,
        "mean": mean,
        "sd": sd,
        "cv": cv,
        "can_evaluate": cv is not None,
        "target_ready": len(building_df) >= int(target_count) and cv is not None,
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
    formal_df = qc_df[qc_df["phase"] == "\u6b63\u5f0f\u6570\u636e"].copy()
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
