from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
import math
from typing import Any

import pandas as pd

from database import (
    add_zscore_level_results as db_add_zscore_level_results,
    add_zscore_run as db_add_zscore_run,
    delete_zscore_level_results_by_run as db_delete_zscore_level_results_by_run,
    delete_zscore_run as db_delete_zscore_run,
    delete_zscore_targets_by_batch as db_delete_zscore_targets_by_batch,
    get_zscore_batch,
    get_zscore_level_results_df,
    get_zscore_level_targets_df,
    get_zscore_run_with_levels as db_get_zscore_run_with_levels,
    get_zscore_runs_df,
    get_zscore_runs_with_levels_for_batch as db_get_zscore_runs_with_levels_for_batch,
    update_zscore_batch_effective_building_count as db_update_zscore_batch_effective_building_count,
    update_zscore_level_results as db_update_zscore_level_results,
    update_zscore_run as db_update_zscore_run,
    upsert_zscore_level_target as db_upsert_zscore_level_target,
)


PHASE_TARGET_BUILDING = "target_building"
PHASE_FORMAL_QC = "formal_qc"

PHASE_LABELS = {
    PHASE_TARGET_BUILDING: "建靶中",
    PHASE_FORMAL_QC: "正式质控",
}

RULE_PRIORITY = {
    "1_3s": 1,
    "R_4s": 2,
    "2_2s": 3,
    "2of3_2s": 4,
    "4_1s": 5,
    "3_1s": 6,
    "10_x": 7,
    "12_x": 8,
    "1_2s": 9,
}

DEFAULT_TARGETS = {
    "Level 1": {"target_mean": 100.0, "target_sd": 5.0},
    "Level 2": {"target_mean": 150.0, "target_sd": 7.5},
    "Level 3": {"target_mean": 200.0, "target_sd": 10.0},
}

LEVEL_LABEL_FIELD_MAP = {
    "Level 1": "level_1_label",
    "Level 2": "level_2_label",
    "Level 3": "level_3_label",
}


@dataclass(frozen=True)
class ZScoreRuleDefinition:
    rule_id: str
    severity: str
    scope: str
    description: str
    error_bias: str


RULE_DEFINITIONS = {
    "1_2s": ZScoreRuleDefinition(
        rule_id="1_2s",
        severity="warning",
        scope="within-run / within-level",
        description="当前 run 单水平结果位于均值同侧且超过 ±2SD，作为警告信号。",
        error_bias="warning",
    ),
    "1_3s": ZScoreRuleDefinition(
        rule_id="1_3s",
        severity="reject",
        scope="within-run / within-level",
        description="当前 run 单水平结果超过 ±3SD，提示明显随机误差风险。",
        error_bias="random",
    ),
    "2_2s": ZScoreRuleDefinition(
        rule_id="2_2s",
        severity="reject",
        scope="across-run / within-level",
        description="同一水平连续 2 次位于均值同侧且超过 ±2SD。",
        error_bias="systematic",
    ),
    "2of3_2s": ZScoreRuleDefinition(
        rule_id="2of3_2s",
        severity="reject",
        scope="within-run / across-level",
        description="同一 run 内 3 个水平里有 2 个位于均值同侧且超过 ±2SD。",
        error_bias="systematic",
    ),
    "R_4s": ZScoreRuleDefinition(
        rule_id="R_4s",
        severity="reject",
        scope="within-run / across-level",
        description="同一 run 内至少 2 个水平相差超过 4SD 且方向相反。",
        error_bias="random",
    ),
    "4_1s": ZScoreRuleDefinition(
        rule_id="4_1s",
        severity="reject",
        scope="across-run / within-level",
        description="同一水平连续 4 次位于均值同侧且超过 ±1SD。",
        error_bias="systematic",
    ),
    "3_1s": ZScoreRuleDefinition(
        rule_id="3_1s",
        severity="reject",
        scope="within-run / across-level",
        description="同一 run 内 3 个水平全部位于均值同侧且超过 ±1SD。",
        error_bias="systematic",
    ),
    "10_x": ZScoreRuleDefinition(
        rule_id="10_x",
        severity="reject",
        scope="across-run / within-level",
        description="同一水平连续 10 次位于均值同侧。",
        error_bias="systematic",
    ),
    "12_x": ZScoreRuleDefinition(
        rule_id="12_x",
        severity="reject",
        scope="across-run / within-level",
        description="同一水平连续 12 次位于均值同侧。",
        error_bias="systematic",
    ),
}


def get_phase_label(phase: str) -> str:
    return PHASE_LABELS.get(str(phase), str(phase))


def compute_zscore(raw_value: float | None, target_mean: float | None, target_sd: float | None) -> float | None:
    if raw_value is None or target_mean is None or target_sd is None:
        return None
    if not math.isfinite(raw_value) or not math.isfinite(target_mean) or not math.isfinite(target_sd):
        return None
    if target_sd <= 0 or math.isclose(target_sd, 0.0, abs_tol=1e-12):
        return None
    return float((raw_value - target_mean) / target_sd)


def calculate_level_target_stats(raw_values: list[float]) -> dict[str, Any]:
    cleaned_values = [float(value) for value in raw_values if value is not None and math.isfinite(float(value))]
    if not cleaned_values:
        return {
            "count": 0,
            "target_mean": None,
            "target_sd": None,
            "target_cv": None,
        }

    mean_value = float(sum(cleaned_values) / len(cleaned_values))
    if len(cleaned_values) >= 2:
        variance = sum((value - mean_value) ** 2 for value in cleaned_values) / (len(cleaned_values) - 1)
        sd_value = math.sqrt(variance)
    else:
        sd_value = None
    cv_value = _safe_cv(mean_value, sd_value)
    return {
        "count": len(cleaned_values),
        "target_mean": mean_value,
        "target_sd": float(sd_value) if sd_value is not None else None,
        "target_cv": cv_value,
    }


def build_zscore_rule_templates() -> dict[str, dict[str, Any]]:
    template_specs = {
        "2_level_classic": {
            "label": "2-level classic",
            "level_ids": ["Level 1", "Level 2"],
            "rule_ids": ["1_2s", "1_3s", "2_2s", "R_4s", "4_1s", "10_x"],
            "required_n": 5,
            "note": "两水平流程采用经典多规则组合，适用于双水平室内质控；正式规则仅在正式质控期启用。",
        },
        "3_level_threes": {
            "label": "3-level threes",
            "level_ids": ["Level 1", "Level 2", "Level 3"],
            "rule_ids": ["1_2s", "1_3s", "2of3_2s", "R_4s", "3_1s", "12_x"],
            "required_n": 5,
            "note": "三水平流程采用面向三水平 IQC 的规则组合，用于多水平联合观察与判读。",
        },
    }

    templates: dict[str, dict[str, Any]] = {}
    for template_id, spec in template_specs.items():
        target_map: dict[str, dict[str, float | None]] = {}
        for level_id in spec["level_ids"]:
            target_mean = float(DEFAULT_TARGETS[level_id]["target_mean"])
            target_sd = float(DEFAULT_TARGETS[level_id]["target_sd"])
            target_map[level_id] = {
                "target_mean": target_mean,
                "target_sd": target_sd,
                "target_cv": _safe_cv(target_mean, target_sd),
            }
        templates[template_id] = {
            "template_id": template_id,
            "label": spec["label"],
            "level_ids": list(spec["level_ids"]),
            "rule_ids": list(spec["rule_ids"]),
            "rules": [rule_to_dict(RULE_DEFINITIONS[rule_id]) for rule_id in spec["rule_ids"]],
            "default_targets": target_map,
            "required_n": int(spec["required_n"]),
            "note": spec["note"],
        }
    return deepcopy(templates)


def get_template_id_for_level_count(level_count: int) -> str:
    normalized_level_count = int(level_count)
    if normalized_level_count == 2:
        return "2_level_classic"
    if normalized_level_count == 3:
        return "3_level_threes"
    raise ValueError("Z-score 水平数只能是 2 或 3。")


def get_level_ids_for_level_count(level_count: int) -> list[str]:
    template_id = get_template_id_for_level_count(level_count)
    return list(build_zscore_rule_templates()[template_id]["level_ids"])


def format_level_id_display(level_id: str) -> str:
    normalized_level_id = str(level_id or "").strip()
    if normalized_level_id.startswith("Level "):
        suffix = normalized_level_id.removeprefix("Level ").strip()
        if suffix:
            return f"水平 {suffix}"
    return normalized_level_id or "水平"


def get_zscore_level_label_map(batch: Any, level_ids: list[str] | None = None) -> dict[str, str]:
    resolved_level_ids = list(level_ids or get_level_ids_for_level_count(int(_mapping_get(batch, "level_count") or 2)))
    label_map: dict[str, str] = {}
    for level_id in resolved_level_ids:
        field_name = LEVEL_LABEL_FIELD_MAP[level_id]
        cleaned_label = str(_mapping_get(batch, field_name) or "").strip()
        label_map[level_id] = cleaned_label or level_id
    return label_map


def format_zscore_level_label_summary(batch: Any, level_ids: list[str] | None = None) -> str:
    label_map = get_zscore_level_label_map(batch, level_ids)
    segments = []
    for level_id, level_label in label_map.items():
        display_level = format_level_id_display(level_id)
        if level_label == level_id:
            segments.append(display_level)
        else:
            segments.append(f"{display_level}：{level_label}")
    return " | ".join(segments)


def build_zscore_batch_summary_items(
    batch: Any,
    phase_label: str,
    formal_rules_enabled: bool,
    template_label: str,
    level_ids: list[str] | None = None,
) -> list[tuple[str, str]]:
    resolved_level_ids = list(level_ids or get_level_ids_for_level_count(int(_mapping_get(batch, "level_count") or 2)))
    return [
        ("当前阶段", str(phase_label)),
        ("全部水平已完成建靶", "是" if formal_rules_enabled else "否"),
        ("正式规则已启用", "是" if formal_rules_enabled else "否"),
        ("项目名称", str(_mapping_get(batch, "project_name") or "-")),
        ("批次编号", str(_mapping_get(batch, "id") or "-")),
        ("仪器", str(_mapping_get(batch, "instrument") or "-")),
        ("试剂", str(_mapping_get(batch, "reagent") or "-")),
        ("质控品", str(_mapping_get(batch, "qc_material") or "-")),
        ("浓度", str(_mapping_get(batch, "concentration") or "-")),
        ("水平说明", format_zscore_level_label_summary(batch, resolved_level_ids)),
        ("质控品批号", str(_mapping_get(batch, "lot_no") or "-")),
        ("水平数", f"{int(_mapping_get(batch, 'level_count') or 0)} 水平"),
        ("规则组合", str(template_label or "-")),
    ]


def get_building_stat_run_ids(history_runs: list[dict[str, Any]]) -> set[int]:
    ordered_runs: list[tuple[int, dict[str, Any]]] = []
    for index, run in enumerate(history_runs, start=1):
        run_id = int(run.get("run_id") or index)
        ordered_runs.append((run_id, run))
    ordered_runs.sort(
        key=lambda item: (
            get_zscore_display_sequence(item[1]),
            item[0],
        )
    )

    building_run_ids: set[int] = set()
    saw_building_prefix = False
    building_window_closed = False
    for run_id, run in ordered_runs:
        current_phase = _normalize_run_phase(run.get("phase"))
        if not building_window_closed and current_phase == PHASE_TARGET_BUILDING:
            building_run_ids.add(run_id)
            saw_building_prefix = True
            continue
        if not building_window_closed and current_phase == PHASE_FORMAL_QC and saw_building_prefix:
            building_run_ids.add(run_id)
            building_window_closed = True
            continue
        building_window_closed = True
    return building_run_ids


def get_zscore_display_sequence(run: dict[str, Any]) -> int:
    return int(run.get("test_sequence") or run.get("run_id") or run.get("id") or 0)


def sort_zscore_runs_for_maintenance(
    saved_runs: list[dict[str, Any]],
    *,
    reverse: bool = True,
) -> list[dict[str, Any]]:
    return sorted(
        deepcopy(saved_runs),
        key=lambda run: (
            get_zscore_display_sequence(run),
            int(run.get("run_id") or run.get("id") or 0),
        ),
        reverse=reverse,
    )


def build_zscore_maintenance_dialog_state(
    action: str,
    available_runs: list[dict[str, Any]],
    preferred_run_id: int | None = None,
) -> dict[str, Any]:
    normalized_action = str(action or "").strip().lower()
    notice_map = {
        "update": "Z-score 检测记录已更新，并已完成整批次重算。",
        "delete": "Z-score 检测记录已删除，并已完成整批次重算。",
    }
    if normalized_action not in notice_map:
        raise ValueError(f"不支持的维护动作：{action}")

    ordered_runs = sort_zscore_runs_for_maintenance(available_runs)
    valid_run_ids = {int(run.get("run_id") or run.get("id") or 0) for run in ordered_runs}
    selected_run_id: int | None = None
    if preferred_run_id is not None and int(preferred_run_id) in valid_run_ids:
        selected_run_id = int(preferred_run_id)
    elif ordered_runs:
        selected_run_id = int(ordered_runs[0].get("run_id") or ordered_runs[0].get("id") or 0)

    return {
        "keep_dialog_open": True,
        "selected_run_id": selected_run_id,
        "dialog_notice": notice_map[normalized_action],
    }


def build_zscore_plot_dataframe(
    saved_runs: list[dict[str, Any]],
    draft_run: dict[str, Any] | None = None,
    display_phase: str | None = None,
) -> pd.DataFrame:
    expected_columns = [
        "run_id",
        "test_sequence",
        "run_index",
        "test_time",
        "level_id",
        "zscore",
        "status",
        "rule_hits",
        "raw_value",
        "log_value",
        "building_reference_mean",
        "building_reference_sd",
        "formal_reference_mean",
        "formal_reference_sd",
        "phase",
        "plot_phase",
        "is_building_stat_point",
        "is_preview",
        "manual_note",
    ]
    building_run_ids = get_building_stat_run_ids(saved_runs)
    reference_map = _build_plot_reference_map(saved_runs, building_run_ids)
    rows: list[dict[str, Any]] = []
    for index, run in enumerate(saved_runs, start=1):
        run_id = int(run.get("run_id") or index)
        test_sequence = get_zscore_display_sequence(run)
        run_phase = _normalize_run_phase(run.get("phase"))
        is_building_stat_point = run_id in building_run_ids
        if display_phase == PHASE_TARGET_BUILDING and not is_building_stat_point:
            continue
        if display_phase == PHASE_FORMAL_QC and run_phase != PHASE_FORMAL_QC:
            continue
        for level_result in run.get("level_results", []):
            level_id = str(level_result.get("level_id"))
            raw_value = level_result.get("raw_value")
            if raw_value is None:
                continue
            reference_profile = reference_map.get((run_id, level_id), {})
            rows.append(
                {
                    "run_id": run_id,
                    "test_sequence": test_sequence,
                    "run_index": test_sequence,
                    "test_time": run.get("test_time"),
                    "level_id": level_id,
                    "zscore": level_result.get("zscore"),
                    "status": level_result.get("status", run.get("run_status", "accept")),
                    "rule_hits": ",".join(level_result.get("rule_hits_local", [])),
                    "raw_value": raw_value,
                    "log_value": level_result.get("log_value"),
                    "building_reference_mean": reference_profile.get("building_reference_mean"),
                    "building_reference_sd": reference_profile.get("building_reference_sd"),
                    "formal_reference_mean": reference_profile.get("formal_reference_mean"),
                    "formal_reference_sd": reference_profile.get("formal_reference_sd"),
                    "phase": run_phase,
                    "plot_phase": run_phase,
                    "is_building_stat_point": is_building_stat_point,
                    "is_preview": False,
                    "manual_note": str(run.get("manual_note", "") or ""),
                }
            )

    if draft_run and draft_run.get("run_status") != "pending":
        draft_phase = _normalize_run_phase(draft_run.get("phase"))
        draft_is_building_stat_point = draft_phase == PHASE_TARGET_BUILDING
        if not (
            display_phase == PHASE_TARGET_BUILDING and not draft_is_building_stat_point
            or display_phase == PHASE_FORMAL_QC and draft_phase != PHASE_FORMAL_QC
        ):
            preview_sequence = max((int(run.get("test_sequence") or 0) for run in saved_runs), default=0) + 1
            for level_result in draft_run.get("level_results", []):
                raw_value = level_result.get("raw_value")
                if raw_value is None:
                    continue
                rows.append(
                    {
                        "run_id": preview_sequence,
                        "test_sequence": preview_sequence,
                        "run_index": preview_sequence,
                        "test_time": draft_run.get("test_time"),
                        "level_id": level_result.get("level_id"),
                        "zscore": level_result.get("zscore"),
                        "status": level_result.get("status", draft_run.get("run_status", "accept")),
                        "rule_hits": ",".join(level_result.get("rule_hits_local", [])),
                        "raw_value": raw_value,
                        "log_value": level_result.get("log_value"),
                        "building_reference_mean": level_result.get("target_mean") if draft_phase == PHASE_TARGET_BUILDING else None,
                        "building_reference_sd": level_result.get("target_sd") if draft_phase == PHASE_TARGET_BUILDING else None,
                        "formal_reference_mean": level_result.get("target_mean") if draft_phase == PHASE_FORMAL_QC else None,
                        "formal_reference_sd": level_result.get("target_sd") if draft_phase == PHASE_FORMAL_QC else None,
                        "phase": draft_phase,
                        "plot_phase": draft_phase,
                        "is_building_stat_point": draft_is_building_stat_point,
                        "is_preview": True,
                        "manual_note": str(draft_run.get("manual_note", "") or ""),
                    }
                )

    return pd.DataFrame(rows, columns=expected_columns)


def _build_plot_reference_map(
    saved_runs: list[dict[str, Any]],
    building_run_ids: set[int],
) -> dict[tuple[int, str], dict[str, float | None]]:
    reference_map: dict[tuple[int, str], dict[str, float | None]] = {}
    if not saved_runs:
        return reference_map

    ordered_runs = sorted(
        saved_runs,
        key=lambda run: (
            get_zscore_display_sequence(run),
            int(run.get("run_id") or run.get("id") or 0),
        ),
    )
    level_ids = sorted(
        {
            str(level_result.get("level_id"))
            for run in ordered_runs
            for level_result in run.get("level_results", [])
            if level_result.get("level_id")
        }
    )

    for level_id in level_ids:
        building_values: list[float] = []
        ordered_building_runs = [
            run
            for run in ordered_runs
            if int(run.get("run_id") or 0) in building_run_ids
        ]
        for run in ordered_building_runs:
            run_id = int(run.get("run_id") or 0)
            raw_value = _get_level_raw_value(run, level_id)
            if raw_value is None:
                continue
            building_values.append(float(raw_value))
            stats = calculate_level_target_stats(building_values)
            reference_map.setdefault((run_id, level_id), {})
            reference_map[(run_id, level_id)]["building_reference_mean"] = stats["target_mean"]
            reference_map[(run_id, level_id)]["building_reference_sd"] = stats["target_sd"]

        final_stats = calculate_level_target_stats(building_values)
        for run in ordered_runs:
            run_id = int(run.get("run_id") or 0)
            reference_map.setdefault((run_id, level_id), {})
            reference_map[(run_id, level_id)]["formal_reference_mean"] = final_stats["target_mean"]
            reference_map[(run_id, level_id)]["formal_reference_sd"] = final_stats["target_sd"]
    return reference_map


def _build_building_display_zscores(
    saved_runs: list[dict[str, Any]],
    building_run_ids: set[int],
) -> dict[tuple[int, str], float]:
    fallback_map: dict[tuple[int, str], float] = {}
    if not saved_runs or not building_run_ids:
        return fallback_map

    level_ids = sorted(
        {
            str(level_result.get("level_id"))
            for run in saved_runs
            for level_result in run.get("level_results", [])
            if level_result.get("level_id")
        }
    )
    for level_id in level_ids:
        raw_values: list[float] = []
        ordered_building_runs = [
            run
            for run in sorted(
                saved_runs,
                key=lambda item: (
                    get_zscore_display_sequence(item),
                    int(item.get("run_id") or item.get("id") or 0),
                ),
            )
            if int(run.get("run_id") or 0) in building_run_ids
        ]
        for run in ordered_building_runs:
            run_id = int(run.get("run_id") or 0)
            raw_value = _get_level_raw_value(run, level_id)
            if raw_value is None:
                continue
            raw_values.append(float(raw_value))
            stats = calculate_level_target_stats(raw_values)
            computed_zscore = compute_zscore(raw_value, stats["target_mean"], stats["target_sd"])
            fallback_map[(run_id, level_id)] = float(computed_zscore) if computed_zscore is not None else 0.0
    return fallback_map


def resolve_zscore_batch_context(batch_id: int) -> dict[str, Any]:
    batch = get_zscore_batch(batch_id)
    level_count = int(batch["level_count"])
    template_id = get_template_id_for_level_count(level_count)
    template = deepcopy(build_zscore_rule_templates()[template_id])
    required_level_ids = list(template["level_ids"])
    level_label_map = get_zscore_level_label_map(batch, required_level_ids)
    return {
        "batch": batch,
        "level_count": level_count,
        "template_id": template_id,
        "template": template,
        "required_level_ids": required_level_ids,
        "level_label_map": level_label_map,
        "level_label_summary": format_zscore_level_label_summary(batch, required_level_ids),
        "required_n": int(batch["target_n"] or template["required_n"]),
    }


def get_zscore_level_results(
    run_id: int | None = None,
    batch_id: int | None = None,
    template_id: str | None = None,
) -> list[dict[str, Any]]:
    dataframe = get_zscore_level_results_df(run_id=run_id, batch_id=batch_id, rule_template_id=template_id)
    if dataframe.empty:
        return []

    rows: list[dict[str, Any]] = []
    for record in dataframe.to_dict(orient="records"):
        rows.append(
            {
                "id": int(record["id"]),
                "run_id": int(record["run_id"]),
                "level_id": str(record["level_id"]),
                "raw_value": _float_or_none(record.get("raw_value")),
                "log_value": _float_or_none(record.get("log_value")),
                "zscore": _float_or_none(record.get("zscore")),
                "status": str(record.get("level_status", "pending")),
                "rule_hits_local": _parse_json_list(record.get("rule_hits_local")),
                "is_in_control_for_realtime_stats": bool(record.get("is_in_control_for_realtime_stats", 0)),
                "created_at": record.get("created_at"),
                "phase": _normalize_run_phase(record.get("phase")),
                "run_status": str(record.get("run_status", "pending")),
                "rule_template_id": record.get("rule_template_id"),
                "test_time": record.get("test_time"),
            }
        )
    return rows


def get_zscore_runs(batch_id: int, template_id: str | None = None) -> list[dict[str, Any]]:
    runs_df = get_zscore_runs_df(batch_id, rule_template_id=template_id, include_manual_note=True)
    if runs_df.empty:
        return []

    level_results = get_zscore_level_results(batch_id=batch_id, template_id=template_id)
    level_results_by_run: dict[int, list[dict[str, Any]]] = {}
    for level_result in level_results:
        level_results_by_run.setdefault(int(level_result["run_id"]), []).append(level_result)

    runs: list[dict[str, Any]] = []
    for record in runs_df.to_dict(orient="records"):
        run_id = int(record["id"])
        phase = _normalize_run_phase(record.get("phase"))
        runs.append(
            {
                "run_id": run_id,
                "id": run_id,
                "test_sequence": int(record["test_sequence"]) if pd.notna(record.get("test_sequence")) else run_id,
                "batch_id": int(record["batch_id"]),
                "project_id": int(record["project_id"]),
                "project_name": record.get("project_name"),
                "test_time": record.get("test_time"),
                "operator": str(record.get("operator", "") or ""),
                "level_count": int(record.get("level_count", 0) or 0),
                "phase": phase,
                "phase_label": get_phase_label(phase),
                "run_status": str(record.get("run_status", "pending")),
                "rule_template_id": str(record.get("rule_template_id", "")),
                "rule_hits_run": _parse_json_list(record.get("rule_hits_run")),
                "error_type_hint": str(record.get("error_type_hint", "unknown")),
                "analysis_prompt": str(record.get("analysis_prompt", "") or ""),
                "manual_note": str(record.get("manual_note", "") or ""),
                "created_at": record.get("created_at"),
                "formal_rules_enabled": phase == PHASE_FORMAL_QC,
                "level_results": sorted(
                    deepcopy(level_results_by_run.get(run_id, [])),
                    key=lambda item: item["level_id"],
                ),
            }
        )
    formal_started = any(str(run.get("phase")) == PHASE_FORMAL_QC for run in runs)
    for run in runs:
        run["is_locked_for_maintenance"] = bool(
            formal_started and str(run.get("phase")) == PHASE_TARGET_BUILDING
        )
    return sorted(
        runs,
        key=lambda run: (
            get_zscore_display_sequence(run),
            int(run.get("run_id") or run.get("id") or 0),
        ),
    )


def get_zscore_level_targets(
    batch_id: int,
    template_id: str,
    required_n: int | None = None,
) -> dict[str, dict[str, Any]]:
    templates = build_zscore_rule_templates()
    template = templates[template_id]
    target_n = int(required_n or template.get("required_n") or 5)

    dataframe = get_zscore_level_targets_df(batch_id)
    target_rows = {
        str(record["level_id"]): record
        for record in dataframe.to_dict(orient="records")
        if str(record.get("level_id")) in template["level_ids"]
    }

    profiles: dict[str, dict[str, Any]] = {}
    for level_id in template["level_ids"]:
        record = target_rows.get(level_id, {})
        profiles[level_id] = _build_target_profile(
            level_id=level_id,
            required_n=int(record.get("required_n") or target_n),
            vendor_reference_mean=_float_or_none(record.get("vendor_reference_mean")),
            vendor_reference_sd=_float_or_none(record.get("vendor_reference_sd")),
            vendor_reference_cv=_float_or_none(record.get("vendor_reference_cv")),
            vendor_reference_source_note=_string_or_none(record.get("vendor_reference_source_note")),
            provisional_mean=_float_or_none(record.get("provisional_mean")),
            provisional_sd=_float_or_none(record.get("provisional_sd")),
            provisional_cv=_float_or_none(record.get("provisional_cv")),
            final_target_mean=_float_or_none(record.get("final_target_mean")),
            final_target_sd=_float_or_none(record.get("final_target_sd")),
            final_target_cv=_float_or_none(record.get("final_target_cv")),
            realtime_mean=_float_or_none(record.get("realtime_mean")),
            realtime_sd=_float_or_none(record.get("realtime_sd")),
            realtime_cv=_float_or_none(record.get("realtime_cv")),
            collected_n=int(record.get("collected_n", 0) or 0),
            is_ready=bool(record.get("is_ready", 0)),
            phase=_normalize_run_phase(record.get("phase")),
        )
    return profiles


def upsert_zscore_level_target(batch_id: int, level_id: str, **fields) -> None:
    payload = deepcopy(fields)

    if "vendor_reference_mean" in payload or "vendor_reference_sd" in payload:
        payload["vendor_reference_cv"] = _safe_cv(
            payload.get("vendor_reference_mean"),
            payload.get("vendor_reference_sd"),
        )
    if "provisional_mean" in payload or "provisional_sd" in payload:
        payload["provisional_cv"] = _safe_cv(payload.get("provisional_mean"), payload.get("provisional_sd"))
    if "final_target_mean" in payload or "final_target_sd" in payload:
        payload["final_target_cv"] = _safe_cv(payload.get("final_target_mean"), payload.get("final_target_sd"))
    if "realtime_mean" in payload or "realtime_sd" in payload:
        payload["realtime_cv"] = _safe_cv(payload.get("realtime_mean"), payload.get("realtime_sd"))

    db_upsert_zscore_level_target(batch_id, level_id, **payload)


def build_level_target_profiles(
    history_runs: list[dict[str, Any]] | None = None,
    template_id: str = "2_level_classic",
    required_n: int | None = None,
    batch_id: int | None = None,
) -> dict[str, dict[str, Any]]:
    if batch_id is not None:
        return get_zscore_level_targets(batch_id, template_id, required_n=required_n)

    templates = build_zscore_rule_templates()
    template = templates[template_id]
    target_n = int(required_n or template.get("required_n") or 5)
    history_runs = history_runs or []

    target_profiles: dict[str, dict[str, Any]] = {}
    for level_id in template["level_ids"]:
        building_values: list[float] = []
        for run in history_runs:
            if _normalize_run_phase(run.get("phase")) != PHASE_TARGET_BUILDING:
                continue
            raw_value = _get_level_raw_value(run, level_id)
            if raw_value is not None:
                building_values.append(float(raw_value))

        provisional_stats = calculate_level_target_stats(building_values)
        final_stats = (
            calculate_level_target_stats(building_values[:target_n])
            if len(building_values) >= target_n
            else {"target_mean": None, "target_sd": None, "target_cv": None}
        )
        is_ready = len(building_values) >= target_n and final_stats["target_sd"] is not None
        target_profiles[level_id] = _build_target_profile(
            level_id=level_id,
            required_n=target_n,
            provisional_mean=provisional_stats["target_mean"],
            provisional_sd=provisional_stats["target_sd"],
            provisional_cv=provisional_stats["target_cv"],
            final_target_mean=final_stats["target_mean"] if is_ready else None,
            final_target_sd=final_stats["target_sd"] if is_ready else None,
            final_target_cv=final_stats["target_cv"] if is_ready else None,
            collected_n=len(building_values),
            is_ready=is_ready,
            phase=PHASE_FORMAL_QC if is_ready else PHASE_TARGET_BUILDING,
        )
    return target_profiles


def get_effective_building_run_count(
    target_profiles: dict[str, dict[str, Any]],
    required_level_ids: list[str] | None = None,
) -> int:
    level_ids = required_level_ids or list(target_profiles.keys())
    if not level_ids:
        return 0
    return min(
        int(target_profiles.get(level_id, {}).get("collected_n", 0) or 0)
        for level_id in level_ids
    )


def should_enable_formal_rules(
    target_profiles: dict[str, dict[str, Any]],
    required_level_ids: list[str] | None = None,
) -> bool:
    level_ids = required_level_ids or list(target_profiles.keys())
    if not level_ids:
        return False
    if not all(bool(target_profiles.get(level_id, {}).get("is_ready")) for level_id in level_ids):
        return False

    required_counts = [
        int(target_profiles.get(level_id, {}).get("required_n", 0) or 0)
        for level_id in level_ids
    ]
    if not required_counts:
        return False

    effective_building_count = get_effective_building_run_count(target_profiles, level_ids)
    required_count = max(required_counts)
    if required_count <= 0:
        return False
    return effective_building_count >= required_count


def determine_zscore_phase(
    target_profiles: dict[str, dict[str, Any]],
    required_level_ids: list[str] | None = None,
) -> str:
    return PHASE_FORMAL_QC if should_enable_formal_rules(target_profiles, required_level_ids) else PHASE_TARGET_BUILDING


def calculate_formal_realtime_stats(
    history_runs: list[dict[str, Any]],
    level_ids: list[str] | None = None,
) -> dict[str, dict[str, Any]]:
    inferred_level_ids = level_ids or sorted(
        {
            level_result.get("level_id")
            for run in history_runs
            for level_result in run.get("level_results", [])
            if level_result.get("level_id")
        }
    )
    raw_values_by_level: dict[str, list[float]] = {level_id: [] for level_id in inferred_level_ids}

    for run in history_runs:
        if _normalize_run_phase(run.get("phase")) != PHASE_FORMAL_QC:
            continue
        for level_result in run.get("level_results", []):
            level_id = str(level_result.get("level_id"))
            if level_id not in raw_values_by_level:
                continue
            if not bool(level_result.get("is_in_control_for_realtime_stats", False)):
                continue
            raw_value = _float_or_none(level_result.get("raw_value"))
            if raw_value is not None:
                raw_values_by_level[level_id].append(raw_value)

    realtime_profiles: dict[str, dict[str, Any]] = {}
    for level_id, raw_values in raw_values_by_level.items():
        stats = calculate_level_target_stats(raw_values)
        realtime_profiles[level_id] = {
            "count": stats["count"],
            "realtime_mean": stats["target_mean"],
            "realtime_sd": stats["target_sd"],
            "realtime_cv": stats["target_cv"],
        }
    return realtime_profiles


def create_zscore_run(
    batch_id: int,
    test_time: Any,
    operator: str,
    level_results: list[dict[str, Any]],
    template_id: str,
    required_n: int | None = None,
    manual_note: str = "",
) -> dict[str, Any]:
    batch_context = resolve_zscore_batch_context(batch_id)
    batch = batch_context["batch"]
    expected_template_id = str(batch_context["template_id"])
    if template_id != expected_template_id:
        raise ValueError("当前 Z-score 批次的规则组合必须与项目的水平数配置一致。")

    template = deepcopy(batch_context["template"])
    target_n = int(required_n or batch_context["required_n"])

    normalized_level_results = _normalize_input_level_results(level_results, template)
    for level_result in normalized_level_results:
        if level_result.get("raw_value") is None:
            raise ValueError(f"{level_result['level_id']} 检测值不能为空")

    history_runs = get_zscore_runs(batch_id, template_id)
    next_test_sequence = max((int(run.get("test_sequence") or 0) for run in history_runs), default=0) + 1
    target_profiles = get_zscore_level_targets(batch_id, template_id, required_n=target_n)
    current_phase = determine_zscore_phase(target_profiles, template["level_ids"])
    updated_profiles = deepcopy(target_profiles)

    if current_phase == PHASE_TARGET_BUILDING:
        for level_result in normalized_level_results:
            level_id = str(level_result["level_id"])
            updated_profiles[level_id] = _advance_building_profile(
                updated_profiles[level_id],
                float(level_result["raw_value"]),
                target_n,
            )

    current_run = evaluate_zscore_run_with_phase(
        normalized_level_results,
        history_runs,
        template_id,
        required_n=target_n,
        target_profiles=updated_profiles,
        phase_override=current_phase,
    )
    current_run["test_time"] = pd.Timestamp(test_time)
    current_run["operator"] = str(operator or "").strip()
    current_run["test_sequence"] = next_test_sequence
    current_run["template_id"] = template_id
    current_run["template_label"] = template["label"]
    current_run["manual_note"] = str(manual_note or "")

    is_realtime_accepted_run = current_phase == PHASE_FORMAL_QC and current_run["run_status"] == "accept"
    for level_result in current_run["level_results"]:
        level_result["is_in_control_for_realtime_stats"] = bool(
            is_realtime_accepted_run and level_result.get("status") == "accept"
        )

    for level_id in template["level_ids"]:
        _persist_target_profile(batch_id, level_id, updated_profiles[level_id], target_n)

    run_id = db_add_zscore_run(
        batch_id=batch_id,
        project_id=int(batch["project_id"]),
        test_sequence=next_test_sequence,
        test_time=_format_test_time(current_run["test_time"]),
        operator=current_run["operator"],
        level_count=len(template["level_ids"]),
        phase=current_run["phase"],
        run_status=current_run["run_status"],
        rule_template_id=template_id,
        rule_hits_run=current_run["rule_hits_run"],
        error_type_hint=current_run["error_type_hint"],
        analysis_prompt=current_run["analysis_prompt"],
        manual_note=current_run["manual_note"],
    )
    db_add_zscore_level_results(run_id, current_run["level_results"])

    persisted_runs = get_zscore_runs(batch_id, template_id)
    realtime_profiles = calculate_formal_realtime_stats(persisted_runs, template["level_ids"])
    for level_id in template["level_ids"]:
        updated_profiles[level_id]["realtime_mean"] = realtime_profiles[level_id]["realtime_mean"]
        updated_profiles[level_id]["realtime_sd"] = realtime_profiles[level_id]["realtime_sd"]
        updated_profiles[level_id]["realtime_cv"] = realtime_profiles[level_id]["realtime_cv"]
        _persist_target_profile(batch_id, level_id, updated_profiles[level_id], target_n)
    db_update_zscore_batch_effective_building_count(
        batch_id,
        get_effective_building_run_count(updated_profiles, template["level_ids"]),
    )

    final_runs = get_zscore_runs(batch_id, template_id)
    latest_run = deepcopy(final_runs[-1])
    latest_run["target_profiles"] = get_zscore_level_targets(batch_id, template_id, required_n=target_n)
    return latest_run


def add_zscore_level_results(run_id: int, level_results: list[dict[str, Any]]) -> None:
    db_add_zscore_level_results(run_id, level_results)


def rebuild_zscore_batch_state(batch_id: int) -> dict[str, Any]:
    batch_context = resolve_zscore_batch_context(batch_id)
    batch = batch_context["batch"]
    template_id = str(batch_context["template_id"])
    template = deepcopy(batch_context["template"])
    level_ids = list(template["level_ids"])
    target_n = int(batch_context["required_n"])

    raw_runs = sorted(
        deepcopy(db_get_zscore_runs_with_levels_for_batch(batch_id)),
        key=lambda run: (
            int(run.get("test_sequence")) if run.get("test_sequence") is not None else int(run["id"]),
            int(run["id"]),
        ),
    )
    target_profiles = _build_rebuild_target_profiles(batch_id, template_id, level_ids, target_n)
    rebuilt_runs: list[dict[str, Any]] = []

    for raw_run in raw_runs:
        normalized_level_results = _normalize_input_level_results(
            [
                {
                    "id": raw_level.get("id"),
                    "level_id": raw_level.get("level_id"),
                    "raw_value": raw_level.get("raw_value"),
                    "log_value": raw_level.get("log_value"),
                }
                for raw_level in raw_run.get("level_results", [])
            ],
            template,
        )
        for level_result in normalized_level_results:
            if level_result.get("raw_value") is None:
                raise ValueError(f"run #{raw_run['id']} 的 {level_result['level_id']} 原始值不能为空")

        current_phase = determine_zscore_phase(target_profiles, level_ids)
        updated_profiles = deepcopy(target_profiles)
        if current_phase == PHASE_TARGET_BUILDING:
            for level_result in normalized_level_results:
                level_id = str(level_result["level_id"])
                updated_profiles[level_id] = _advance_building_profile(
                    updated_profiles[level_id],
                    float(level_result["raw_value"]),
                    target_n,
                )

        current_run = evaluate_zscore_run_with_phase(
            normalized_level_results,
            rebuilt_runs,
            template_id,
            required_n=target_n,
            target_profiles=updated_profiles,
            phase_override=current_phase,
        )
        current_run["run_id"] = int(raw_run["id"])
        current_run["id"] = int(raw_run["id"])
        current_run["test_sequence"] = (
            int(raw_run["test_sequence"]) if raw_run.get("test_sequence") is not None else int(raw_run["id"])
        )
        current_run["batch_id"] = int(raw_run["batch_id"])
        current_run["project_id"] = int(raw_run.get("project_id") or batch["project_id"])
        current_run["test_time"] = pd.Timestamp(raw_run["test_time"])
        current_run["operator"] = str(raw_run.get("operator", "") or "").strip()
        current_run["rule_template_id"] = template_id
        current_run["template_id"] = template_id
        current_run["template_label"] = template["label"]
        current_run["manual_note"] = str(raw_run.get("manual_note", "") or "")
        current_run["created_at"] = raw_run.get("created_at")

        raw_level_map = {
            str(raw_level.get("level_id")): raw_level
            for raw_level in raw_run.get("level_results", [])
        }
        is_realtime_accepted_run = current_phase == PHASE_FORMAL_QC and current_run["run_status"] == "accept"
        for level_result in current_run["level_results"]:
            raw_level = raw_level_map.get(str(level_result["level_id"]), {})
            if raw_level.get("id") is not None:
                level_result["id"] = int(raw_level["id"])
            level_result["run_id"] = int(raw_run["id"])
            level_result["is_in_control_for_realtime_stats"] = bool(
                is_realtime_accepted_run and level_result.get("status") == "accept"
            )

        target_profiles = updated_profiles
        rebuilt_runs.append(current_run)

    realtime_profiles = calculate_formal_realtime_stats(rebuilt_runs, level_ids)
    for level_id in level_ids:
        target_profiles[level_id]["realtime_mean"] = realtime_profiles[level_id]["realtime_mean"]
        target_profiles[level_id]["realtime_sd"] = realtime_profiles[level_id]["realtime_sd"]
        target_profiles[level_id]["realtime_cv"] = realtime_profiles[level_id]["realtime_cv"]

    db_delete_zscore_targets_by_batch(batch_id)
    for level_id in level_ids:
        _persist_target_profile(batch_id, level_id, target_profiles[level_id], target_n)
    db_update_zscore_batch_effective_building_count(
        batch_id,
        get_effective_building_run_count(target_profiles, level_ids),
    )

    for run in rebuilt_runs:
        db_update_zscore_run(
            int(run["id"]),
            test_sequence=int(run["test_sequence"]) if run.get("test_sequence") is not None else None,
            level_count=len(level_ids),
            phase=run["phase"],
            run_status=run["run_status"],
            rule_template_id=template_id,
            rule_hits_run=run["rule_hits_run"],
            error_type_hint=run["error_type_hint"],
            analysis_prompt=run["analysis_prompt"],
        )
        db_update_zscore_level_results(
            int(run["id"]),
            [
                {
                    "level_id": level_result["level_id"],
                    "raw_value": level_result.get("raw_value"),
                    "log_value": level_result.get("log_value"),
                    "zscore": level_result.get("zscore"),
                    "status": level_result.get("status"),
                    "rule_hits_local": level_result.get("rule_hits_local", []),
                    "is_in_control_for_realtime_stats": level_result.get("is_in_control_for_realtime_stats", False),
                }
                for level_result in run["level_results"]
            ],
        )

    persisted_runs = get_zscore_runs(batch_id, template_id)
    persisted_targets = get_zscore_level_targets(batch_id, template_id, required_n=target_n)
    return {
        "batch_id": batch_id,
        "template_id": template_id,
        "runs": persisted_runs,
        "target_profiles": persisted_targets,
        "overall_phase": determine_zscore_phase(persisted_targets, level_ids),
        "latest_run": deepcopy(persisted_runs[-1]) if persisted_runs else None,
    }


def update_saved_zscore_run(
    run_id: int,
    test_time: Any,
    operator: str,
    level_results: list[dict[str, Any]],
    manual_note: str | None = None,
) -> dict[str, Any]:
    existing_run = _get_zscore_run_for_maintenance(run_id)
    batch_id = int(existing_run["batch_id"])
    if bool(existing_run.get("is_locked_for_maintenance")):
        raise ValueError("建靶期数据在正式期启用后已锁定，不允许编辑。")
    batch_context = resolve_zscore_batch_context(batch_id)
    template = deepcopy(batch_context["template"])

    cleaned_operator = str(operator or "").strip()
    if not cleaned_operator:
        raise ValueError("检测人不能为空")

    normalized_level_results = _normalize_input_level_results(level_results, template)
    for level_result in normalized_level_results:
        if level_result.get("raw_value") is None:
            raise ValueError(f"{level_result['level_id']} 原始值不能为空")

    db_update_zscore_run(
        run_id,
        test_time=_format_test_time(test_time),
        operator=cleaned_operator,
        manual_note=manual_note,
    )
    db_update_zscore_level_results(
        run_id,
        [
            {
                "level_id": level_result["level_id"],
                "raw_value": level_result.get("raw_value"),
                "log_value": level_result.get("log_value"),
            }
            for level_result in normalized_level_results
        ],
    )
    return rebuild_zscore_batch_state(batch_id)


def update_saved_zscore_run_manual_note(run_id: int, manual_note: str) -> dict[str, Any]:
    existing_run = _get_zscore_run_for_maintenance(run_id)
    batch_id = int(existing_run["batch_id"])
    template_id = str(existing_run["rule_template_id"])
    db_update_zscore_run(run_id, manual_note=str(manual_note or ""))
    refreshed_runs = get_zscore_runs(batch_id, template_id)
    updated_run = next(
        (run for run in refreshed_runs if int(run.get("run_id") or 0) == int(run_id)),
        None,
    )
    return {
        "batch_id": batch_id,
        "template_id": template_id,
        "runs": refreshed_runs,
        "updated_run": deepcopy(updated_run) if updated_run is not None else None,
    }


def delete_saved_zscore_run(run_id: int) -> dict[str, Any]:
    existing_run = _get_zscore_run_for_maintenance(run_id)
    batch_id = int(existing_run["batch_id"])
    if bool(existing_run.get("is_locked_for_maintenance")):
        raise ValueError("建靶期数据在正式期启用后已锁定，不允许删除。")
    db_delete_zscore_level_results_by_run(run_id)
    db_delete_zscore_run(run_id)
    return rebuild_zscore_batch_state(batch_id)


def _get_zscore_run_for_maintenance(run_id: int) -> dict[str, Any]:
    raw_run = db_get_zscore_run_with_levels(run_id)
    batch_id = int(raw_run["batch_id"])
    batch_context = resolve_zscore_batch_context(batch_id)
    template_id = str(batch_context["template_id"])
    saved_runs = get_zscore_runs(batch_id, template_id)
    for saved_run in saved_runs:
        if int(saved_run["run_id"]) == int(run_id):
            return saved_run
    raise ValueError(f"未找到 Z-score run {run_id}")


def evaluate_zscore_run_with_phase(
    level_results: list[dict[str, Any]],
    history_runs: list[dict[str, Any]],
    template_id: str,
    required_n: int | None = None,
    target_profiles: dict[str, dict[str, Any]] | None = None,
    phase_override: str | None = None,
) -> dict[str, Any]:
    templates = build_zscore_rule_templates()
    template = templates[template_id]
    profiles = deepcopy(target_profiles) if target_profiles is not None else build_level_target_profiles(
        history_runs=history_runs,
        template_id=template_id,
        required_n=required_n,
    )
    current_phase = _normalize_run_phase(phase_override) if phase_override is not None else determine_zscore_phase(
        profiles,
        template["level_ids"],
    )
    formal_rules_enabled = (
        current_phase == PHASE_FORMAL_QC
        and should_enable_formal_rules(profiles, template["level_ids"])
    )
    formal_history_runs = [
        deepcopy(run) for run in history_runs if _normalize_run_phase(run.get("phase")) == PHASE_FORMAL_QC
    ]

    current_run = evaluate_zscore_run(
        level_results=level_results,
        history_runs=formal_history_runs if formal_rules_enabled else [],
        template_id=template_id,
        target_profiles=profiles,
        phase_override=current_phase,
    )
    current_run["phase"] = current_phase
    current_run["phase_label"] = get_phase_label(current_phase)
    current_run["formal_rules_enabled"] = formal_rules_enabled
    current_run["target_profiles"] = deepcopy(profiles)

    if current_run["run_status"] == "pending":
        return current_run

    if not formal_rules_enabled:
        current_run["run_status"] = PHASE_TARGET_BUILDING
        current_run["rule_hits_run"] = []
        current_run["error_type_hint"] = "not_applicable"
        current_run["analysis_prompt"] = "当前阶段为建靶中，本次 run 仅用于累计靶值，不进行正式规则判读。"
        for level_result in current_run["level_results"]:
            level_result["rule_hits_local"] = []
            level_result["status"] = PHASE_TARGET_BUILDING
        return current_run

    current_run["analysis_prompt"] = build_zscore_analysis_prompt(
        current_run["run_status"],
        current_run["rule_hits_run"],
        current_run["error_type_hint"],
        phase=current_phase,
    )
    return current_run


def evaluate_zscore_run(
    level_results: list[dict[str, Any]],
    history_runs: list[dict[str, Any]],
    template_id: str,
    target_profiles: dict[str, dict[str, Any]] | None = None,
    phase_override: str | None = None,
) -> dict[str, Any]:
    templates = build_zscore_rule_templates()
    template = templates[template_id]
    current_phase = phase_override or (
        determine_zscore_phase(target_profiles or {}, template["level_ids"]) if target_profiles else PHASE_FORMAL_QC
    )
    current_run = _build_run_shell(level_results, template, history_runs, target_profiles, current_phase)
    if current_run["run_status"] == "pending":
        return current_run

    rule_hits: list[dict[str, Any]] = []
    for rule_id in template["rule_ids"]:
        rule_hits.extend(RULE_FUNCTIONS[rule_id](current_run["level_results"], history_runs))

    current_run["rule_hits_run"] = _sort_rule_hits(rule_hits)
    current_run["run_status"] = _derive_run_status(current_run["rule_hits_run"])
    current_run["error_type_hint"] = classify_zscore_error_type(current_run["rule_hits_run"])
    current_run["analysis_prompt"] = build_zscore_analysis_prompt(
        current_run["run_status"],
        current_run["rule_hits_run"],
        current_run["error_type_hint"],
        phase=current_phase,
    )

    for level_result in current_run["level_results"]:
        local_hits = [hit for hit in current_run["rule_hits_run"] if level_result["level_id"] in hit["levels"]]
        level_result["rule_hits_local"] = [hit["rule_id"] for hit in local_hits]
        level_result["status"] = _derive_run_status(local_hits)
    return current_run


def classify_zscore_error_type(rule_hits: list[dict[str, Any]]) -> str:
    reject_hits = [hit for hit in rule_hits if hit["severity"] == "reject"]
    if not reject_hits:
        return "unknown"

    biases = {RULE_DEFINITIONS[hit["rule_id"]].error_bias for hit in reject_hits}
    biases.discard("warning")
    if not biases:
        return "unknown"
    if len(biases) == 1:
        return next(iter(biases))
    return "mixed"


def build_zscore_analysis_prompt(
    status: str,
    rule_hits: list[dict[str, Any]],
    error_type_hint: str,
    phase: str = PHASE_FORMAL_QC,
) -> str:
    if status == "pending":
        return "请完整录入当前模板要求的所有水平结果后再进行分析。"
    if phase != PHASE_FORMAL_QC:
        return "当前阶段为建靶中，仅用于累计靶值与观察趋势，不进行正式规则判读。"
    if status == "accept":
        return "当前 run 未触发已启用的 Z-score 规则，可继续观察后续趋势。"
    if status == "warning":
        first_rule = rule_hits[0]["rule_id"] if rule_hits else "1_2s"
        return f"当前 run 出现 {first_rule} 警告信号，建议结合后续 run 持续观察。"
    if error_type_hint == "random":
        return "当前 run 触发拒绝规则，偏向 random 误差，请优先检查瞬时波动、加样与操作因素。"
    if error_type_hint == "systematic":
        return "当前 run 触发拒绝规则，偏向 systematic 误差，请优先检查校准、靶值与系统漂移。"
    if error_type_hint == "mixed":
        return "当前 run 同时出现 random 与 systematic 信号，建议综合检查系统与操作因素。"
    return "当前 run 触发拒绝规则，请结合规则命中情况进一步复核。"


def rule_1_2s(level_results: list[dict[str, Any]], history_runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    del history_runs
    hits = []
    for level_result in level_results:
        zscore = level_result.get("zscore")
        if zscore is None:
            continue
        if 2 <= abs(zscore) < 3:
            hits.append(
                _make_rule_hit(
                    "1_2s",
                    [level_result["level_id"]],
                    f"{level_result['level_id']} | z={zscore:.2f}",
                )
            )
    return hits


def rule_1_3s(level_results: list[dict[str, Any]], history_runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    del history_runs
    hits = []
    for level_result in level_results:
        zscore = level_result.get("zscore")
        if zscore is None:
            continue
        if abs(zscore) >= 3:
            hits.append(
                _make_rule_hit(
                    "1_3s",
                    [level_result["level_id"]],
                    f"{level_result['level_id']} | z={zscore:.2f}",
                )
            )
    return hits


def rule_2_2s(level_results: list[dict[str, Any]], history_runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    hits = []
    for level_result in level_results:
        zscore = level_result.get("zscore")
        previous = _get_previous_level_zscore(history_runs, level_result["level_id"])
        if zscore is None or previous is None:
            continue
        if abs(zscore) >= 2 and abs(previous) >= 2 and _same_side([zscore, previous]):
            hits.append(
                _make_rule_hit(
                    "2_2s",
                    [level_result["level_id"]],
                    f"{level_result['level_id']} 连续 2 次同侧超 2SD",
                )
            )
    return hits


def rule_2of3_2s(level_results: list[dict[str, Any]], history_runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    del history_runs
    zscores = _current_zscore_map(level_results)
    positive_levels = [level_id for level_id, value in zscores.items() if value is not None and value >= 2]
    negative_levels = [level_id for level_id, value in zscores.items() if value is not None and value <= -2]
    if len(positive_levels) >= 2:
        return [_make_rule_hit("2of3_2s", positive_levels, "当前 run 中 2/3 水平同侧超 2SD")]
    if len(negative_levels) >= 2:
        return [_make_rule_hit("2of3_2s", negative_levels, "当前 run 中 2/3 水平同侧超 2SD")]
    return []


def rule_R_4s(level_results: list[dict[str, Any]], history_runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    del history_runs
    usable = [level_result for level_result in level_results if level_result.get("zscore") is not None]
    if len(usable) < 2:
        return []

    max_result = max(usable, key=lambda item: item["zscore"])
    min_result = min(usable, key=lambda item: item["zscore"])
    if max_result["zscore"] <= 0 or min_result["zscore"] >= 0:
        return []
    if (max_result["zscore"] - min_result["zscore"]) < 4:
        return []
    return [
        _make_rule_hit(
            "R_4s",
            [min_result["level_id"], max_result["level_id"]],
            f"{min_result['level_id']} / {max_result['level_id']} within-run 差值超 4SD",
        )
    ]


def rule_4_1s(level_results: list[dict[str, Any]], history_runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _run_length_rule(level_results, history_runs, lookback=4, threshold=1, rule_id="4_1s")


def rule_3_1s(level_results: list[dict[str, Any]], history_runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    del history_runs
    zscores = [level_result.get("zscore") for level_result in level_results]
    if len(zscores) < 3 or any(zscore is None for zscore in zscores):
        return []
    if all(zscore >= 1 for zscore in zscores) or all(zscore <= -1 for zscore in zscores):
        return [_make_rule_hit("3_1s", [level_result["level_id"] for level_result in level_results], "3 水平 within-run 同侧超 1SD")]
    return []


def rule_10x(level_results: list[dict[str, Any]], history_runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _x_rule(level_results, history_runs, lookback=10, rule_id="10_x")


def rule_12x(level_results: list[dict[str, Any]], history_runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _x_rule(level_results, history_runs, lookback=12, rule_id="12_x")


RULE_FUNCTIONS = {
    "1_2s": rule_1_2s,
    "1_3s": rule_1_3s,
    "2_2s": rule_2_2s,
    "2of3_2s": rule_2of3_2s,
    "R_4s": rule_R_4s,
    "4_1s": rule_4_1s,
    "3_1s": rule_3_1s,
    "10_x": rule_10x,
    "12_x": rule_12x,
}


def rule_to_dict(rule: ZScoreRuleDefinition) -> dict[str, Any]:
    return {
        "rule_id": rule.rule_id,
        "severity": rule.severity,
        "scope": rule.scope,
        "description": rule.description,
        "error_bias": rule.error_bias,
    }


def _build_run_shell(
    level_results: list[dict[str, Any]],
    template: dict[str, Any],
    history_runs: list[dict[str, Any]],
    target_profiles: dict[str, dict[str, Any]] | None = None,
    phase: str = PHASE_FORMAL_QC,
) -> dict[str, Any]:
    input_map = {str(item.get("level_id")): deepcopy(item) for item in level_results}
    normalized_results = []
    for level_id in template["level_ids"]:
        default_target = template["default_targets"][level_id]
        target_profile = deepcopy((target_profiles or {}).get(level_id, {}))
        target_mean, target_sd = _resolve_level_target_reference(target_profile, default_target, phase)
        current = {
            "level_id": level_id,
            "raw_value": None,
            "log_value": None,
            "target_mean": target_mean,
            "target_sd": target_sd,
            "target_phase": target_profile.get("phase", PHASE_FORMAL_QC if target_profiles else phase),
            "rule_hits_local": [],
            "status": "pending",
            "is_in_control_for_realtime_stats": False,
        }
        current.update(input_map.get(level_id, {}))
        current["target_mean"] = target_mean
        current["target_sd"] = target_sd
        raw_value = _float_or_none(current.get("raw_value"))
        current["raw_value"] = raw_value
        if raw_value is not None and current.get("log_value") is None and raw_value > 0:
            current["log_value"] = math.log10(raw_value)
        current["zscore"] = compute_zscore(
            current.get("raw_value"),
            current.get("target_mean"),
            current.get("target_sd"),
        )
        current["status"] = "accept" if current["raw_value"] is not None else "pending"
        normalized_results.append(current)

    has_all_values = all(result["raw_value"] is not None for result in normalized_results)
    run_status = "accept" if has_all_values else "pending"
    analysis_prompt = (
        "请完整录入当前模板要求的所有水平结果后再进行分析。"
        if run_status == "pending"
        else build_zscore_analysis_prompt(run_status, [], "unknown", phase=phase)
    )
    return {
        "run_id": len(history_runs) + 1,
        "test_time": None,
        "operator": "",
        "template_id": template["template_id"],
        "template_label": template["label"],
        "level_results": normalized_results,
        "phase": phase,
        "phase_label": get_phase_label(phase),
        "run_status": run_status,
        "rule_hits_run": [],
        "error_type_hint": "unknown",
        "analysis_prompt": analysis_prompt,
        "formal_rules_enabled": phase == PHASE_FORMAL_QC,
        "target_profiles": deepcopy(target_profiles or {}),
    }


def _current_zscore_map(level_results: list[dict[str, Any]]) -> dict[str, float | None]:
    return {level_result["level_id"]: level_result.get("zscore") for level_result in level_results}


def _get_previous_level_zscore(history_runs: list[dict[str, Any]], level_id: str) -> float | None:
    for run in reversed(history_runs):
        for level_result in run.get("level_results", []):
            if level_result.get("level_id") == level_id:
                zscore = level_result.get("zscore")
                if zscore is not None:
                    return float(zscore)
    return None


def _get_level_zscore_series(
    history_runs: list[dict[str, Any]],
    current_level_results: list[dict[str, Any]],
    level_id: str,
) -> list[float]:
    series: list[float] = []
    for run in history_runs:
        for level_result in run.get("level_results", []):
            if level_result.get("level_id") == level_id and level_result.get("zscore") is not None:
                series.append(float(level_result["zscore"]))
                break
    for level_result in current_level_results:
        if level_result.get("level_id") == level_id and level_result.get("zscore") is not None:
            series.append(float(level_result["zscore"]))
            break
    return series


def _run_length_rule(
    level_results: list[dict[str, Any]],
    history_runs: list[dict[str, Any]],
    lookback: int,
    threshold: float,
    rule_id: str,
) -> list[dict[str, Any]]:
    hits = []
    for level_result in level_results:
        series = _get_level_zscore_series(history_runs, level_results, level_result["level_id"])
        if len(series) < lookback:
            continue
        window = series[-lookback:]
        if all(value >= threshold for value in window) or all(value <= -threshold for value in window):
            hits.append(
                _make_rule_hit(
                    rule_id,
                    [level_result["level_id"]],
                    f"{level_result['level_id']} 连续 {lookback} 次同侧超 {threshold:g}SD",
                )
            )
    return hits


def _x_rule(
    level_results: list[dict[str, Any]],
    history_runs: list[dict[str, Any]],
    lookback: int,
    rule_id: str,
) -> list[dict[str, Any]]:
    hits = []
    for level_result in level_results:
        series = _get_level_zscore_series(history_runs, level_results, level_result["level_id"])
        if len(series) < lookback:
            continue
        window = series[-lookback:]
        if any(math.isclose(value, 0.0, abs_tol=1e-12) for value in window):
            continue
        if all(value > 0 for value in window) or all(value < 0 for value in window):
            hits.append(
                _make_rule_hit(
                    rule_id,
                    [level_result["level_id"]],
                    f"{level_result['level_id']} 连续 {lookback} 次位于均值同侧",
                )
            )
    return hits


def _make_rule_hit(rule_id: str, levels: list[str], detail: str) -> dict[str, Any]:
    definition = RULE_DEFINITIONS[rule_id]
    return {
        "rule_id": rule_id,
        "severity": definition.severity,
        "scope": definition.scope,
        "levels": list(levels),
        "detail": detail,
        "description": definition.description,
    }


def _same_side(values: list[float]) -> bool:
    return all(value > 0 for value in values) or all(value < 0 for value in values)


def _sort_rule_hits(rule_hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def sort_key(item: dict[str, Any]) -> tuple[int, int]:
        severity_rank = 0 if item["severity"] == "reject" else 1
        return severity_rank, RULE_PRIORITY.get(item["rule_id"], 999)

    return sorted(rule_hits, key=sort_key)


def _derive_run_status(rule_hits: list[dict[str, Any]]) -> str:
    if any(hit["severity"] == "reject" for hit in rule_hits):
        return "reject"
    if any(hit["severity"] == "warning" for hit in rule_hits):
        return "warning"
    return "accept"


def _normalize_run_phase(phase: Any) -> str:
    normalized = str(phase or PHASE_TARGET_BUILDING)
    if normalized not in {PHASE_TARGET_BUILDING, PHASE_FORMAL_QC}:
        return PHASE_TARGET_BUILDING
    return normalized


def _get_level_raw_value(run: dict[str, Any], level_id: str) -> float | None:
    for level_result in run.get("level_results", []):
        if level_result.get("level_id") == level_id:
            raw_value = level_result.get("raw_value")
            if raw_value is not None:
                return float(raw_value)
            return None
    return None


def _resolve_level_target_reference(
    target_profile: dict[str, Any],
    default_target: dict[str, Any],
    phase: str,
) -> tuple[float | None, float | None]:
    if phase == PHASE_FORMAL_QC:
        final_mean = target_profile.get("final_target_mean")
        final_sd = target_profile.get("final_target_sd")
        if final_mean is not None and final_sd is not None:
            return float(final_mean), float(final_sd)

    provisional_mean = target_profile.get("provisional_mean")
    provisional_sd = target_profile.get("provisional_sd")
    if provisional_mean is not None and provisional_sd is not None:
        return float(provisional_mean), float(provisional_sd)

    if phase == PHASE_FORMAL_QC:
        return float(default_target["target_mean"]), float(default_target["target_sd"])
    return provisional_mean, provisional_sd


def _build_target_profile(
    level_id: str,
    required_n: int,
    vendor_reference_mean: float | None = None,
    vendor_reference_sd: float | None = None,
    vendor_reference_cv: float | None = None,
    vendor_reference_source_note: str | None = None,
    provisional_mean: float | None = None,
    provisional_sd: float | None = None,
    provisional_cv: float | None = None,
    final_target_mean: float | None = None,
    final_target_sd: float | None = None,
    final_target_cv: float | None = None,
    realtime_mean: float | None = None,
    realtime_sd: float | None = None,
    realtime_cv: float | None = None,
    collected_n: int = 0,
    is_ready: bool = False,
    phase: str | None = None,
) -> dict[str, Any]:
    resolved_is_ready = bool(
        is_ready or (final_target_mean is not None and final_target_sd is not None and int(collected_n) >= int(required_n))
    )
    resolved_phase = _normalize_run_phase(phase or (PHASE_FORMAL_QC if resolved_is_ready else PHASE_TARGET_BUILDING))
    profile = {
        "level_id": level_id,
        "vendor_reference_mean": vendor_reference_mean,
        "vendor_reference_sd": vendor_reference_sd,
        "vendor_reference_cv": vendor_reference_cv if vendor_reference_cv is not None else _safe_cv(vendor_reference_mean, vendor_reference_sd),
        "vendor_reference_source_note": vendor_reference_source_note,
        "provisional_mean": provisional_mean,
        "provisional_sd": provisional_sd,
        "provisional_cv": provisional_cv if provisional_cv is not None else _safe_cv(provisional_mean, provisional_sd),
        "final_target_mean": final_target_mean,
        "final_target_sd": final_target_sd,
        "final_target_cv": final_target_cv if final_target_cv is not None else _safe_cv(final_target_mean, final_target_sd),
        "realtime_mean": realtime_mean,
        "realtime_sd": realtime_sd,
        "realtime_cv": realtime_cv if realtime_cv is not None else _safe_cv(realtime_mean, realtime_sd),
        "collected_n": int(collected_n),
        "required_n": int(required_n),
        "is_ready": resolved_is_ready,
        "phase": resolved_phase,
        "phase_label": get_phase_label(resolved_phase),
    }
    profile["target_mean_provisional"] = profile["provisional_mean"]
    profile["target_sd_provisional"] = profile["provisional_sd"]
    profile["target_cv_provisional"] = profile["provisional_cv"]
    profile["target_mean_final"] = profile["final_target_mean"]
    profile["target_sd_final"] = profile["final_target_sd"]
    profile["target_cv_final"] = profile["final_target_cv"]
    return profile


def _advance_building_profile(profile: dict[str, Any], raw_value: float, required_n: int) -> dict[str, Any]:
    next_profile = deepcopy(profile)
    next_profile["required_n"] = int(required_n)

    count = int(next_profile.get("collected_n", 0) or 0)
    mean = _float_or_none(next_profile.get("provisional_mean"))
    sd = _float_or_none(next_profile.get("provisional_sd"))
    updated_stats = _incremental_stats(count, mean, sd, raw_value)

    next_profile["collected_n"] = updated_stats["count"]
    next_profile["provisional_mean"] = updated_stats["target_mean"]
    next_profile["provisional_sd"] = updated_stats["target_sd"]
    next_profile["provisional_cv"] = updated_stats["target_cv"]
    next_profile["target_mean_provisional"] = updated_stats["target_mean"]
    next_profile["target_sd_provisional"] = updated_stats["target_sd"]
    next_profile["target_cv_provisional"] = updated_stats["target_cv"]

    if not bool(next_profile.get("is_ready")) and updated_stats["count"] >= int(required_n) and updated_stats["target_sd"] is not None:
        next_profile["final_target_mean"] = updated_stats["target_mean"]
        next_profile["final_target_sd"] = updated_stats["target_sd"]
        next_profile["final_target_cv"] = updated_stats["target_cv"]
        next_profile["target_mean_final"] = updated_stats["target_mean"]
        next_profile["target_sd_final"] = updated_stats["target_sd"]
        next_profile["target_cv_final"] = updated_stats["target_cv"]
        next_profile["is_ready"] = True

    next_profile["phase"] = PHASE_FORMAL_QC if bool(next_profile.get("is_ready")) else PHASE_TARGET_BUILDING
    next_profile["phase_label"] = get_phase_label(next_profile["phase"])
    return next_profile


def _incremental_stats(
    current_count: int,
    current_mean: float | None,
    current_sd: float | None,
    new_value: float,
) -> dict[str, Any]:
    if current_count <= 0 or current_mean is None:
        return {
            "count": 1,
            "target_mean": float(new_value),
            "target_sd": None,
            "target_cv": None,
        }

    previous_m2 = 0.0
    if current_count >= 2 and current_sd is not None:
        previous_m2 = float(current_sd) ** 2 * (current_count - 1)

    next_count = current_count + 1
    delta = float(new_value) - float(current_mean)
    next_mean = float(current_mean) + delta / next_count
    delta2 = float(new_value) - next_mean
    next_m2 = previous_m2 + delta * delta2
    next_sd = math.sqrt(next_m2 / (next_count - 1)) if next_count >= 2 else None
    return {
        "count": next_count,
        "target_mean": next_mean,
        "target_sd": float(next_sd) if next_sd is not None else None,
        "target_cv": _safe_cv(next_mean, next_sd),
    }


def _persist_target_profile(batch_id: int, level_id: str, profile: dict[str, Any], required_n: int) -> None:
    upsert_zscore_level_target(
        batch_id=batch_id,
        level_id=level_id,
        vendor_reference_mean=profile.get("vendor_reference_mean"),
        vendor_reference_sd=profile.get("vendor_reference_sd"),
        vendor_reference_cv=profile.get("vendor_reference_cv"),
        vendor_reference_source_note=profile.get("vendor_reference_source_note"),
        provisional_mean=profile.get("provisional_mean"),
        provisional_sd=profile.get("provisional_sd"),
        provisional_cv=profile.get("provisional_cv"),
        final_target_mean=profile.get("final_target_mean"),
        final_target_sd=profile.get("final_target_sd"),
        final_target_cv=profile.get("final_target_cv"),
        realtime_mean=profile.get("realtime_mean"),
        realtime_sd=profile.get("realtime_sd"),
        realtime_cv=profile.get("realtime_cv"),
        collected_n=int(profile.get("collected_n", 0) or 0),
        required_n=int(required_n),
        is_ready=int(bool(profile.get("is_ready"))),
        phase=_normalize_run_phase(profile.get("phase")),
    )


def _build_rebuild_target_profiles(
    batch_id: int,
    template_id: str,
    level_ids: list[str],
    required_n: int,
) -> dict[str, dict[str, Any]]:
    existing_profiles = get_zscore_level_targets(batch_id, template_id, required_n=required_n)
    rebuilt_profiles: dict[str, dict[str, Any]] = {}
    for level_id in level_ids:
        existing_profile = existing_profiles.get(level_id, {})
        rebuilt_profiles[level_id] = _build_target_profile(
            level_id=level_id,
            required_n=required_n,
            vendor_reference_mean=existing_profile.get("vendor_reference_mean"),
            vendor_reference_sd=existing_profile.get("vendor_reference_sd"),
            vendor_reference_cv=existing_profile.get("vendor_reference_cv"),
            vendor_reference_source_note=existing_profile.get("vendor_reference_source_note"),
            collected_n=0,
            is_ready=False,
            phase=PHASE_TARGET_BUILDING,
        )
    return rebuilt_profiles


def _normalize_input_level_results(level_results: list[dict[str, Any]], template: dict[str, Any]) -> list[dict[str, Any]]:
    _validate_input_level_ids(level_results, template)
    input_map = {str(item.get("level_id")): deepcopy(item) for item in level_results}
    normalized_results: list[dict[str, Any]] = []
    for level_id in template["level_ids"]:
        current = {
            "level_id": level_id,
            "raw_value": None,
            "log_value": None,
        }
        current.update(input_map.get(level_id, {}))
        raw_value = _float_or_none(current.get("raw_value"))
        current["raw_value"] = raw_value
        if raw_value is not None and current.get("log_value") is None and raw_value > 0:
            current["log_value"] = math.log10(raw_value)
        normalized_results.append(current)
    return normalized_results


def _validate_input_level_ids(level_results: list[dict[str, Any]], template: dict[str, Any]) -> None:
    expected_level_ids = set(template["level_ids"])
    observed_level_counts: dict[str, int] = {}
    unexpected_level_ids: set[str] = set()

    for item in level_results:
        level_id = str(item.get("level_id") or "").strip()
        if not level_id:
            continue
        observed_level_counts[level_id] = observed_level_counts.get(level_id, 0) + 1
        if level_id not in expected_level_ids:
            unexpected_level_ids.add(level_id)

    if unexpected_level_ids:
        level_list = ", ".join(sorted(unexpected_level_ids))
        raise ValueError(f"当前批次固定为 {len(expected_level_ids)} 水平，不接受以下水平输入：{level_list}。")

    duplicated_level_ids = sorted(
        level_id for level_id, count in observed_level_counts.items() if count > 1 and level_id in expected_level_ids
    )
    if duplicated_level_ids:
        level_list = ", ".join(duplicated_level_ids)
        raise ValueError(f"本次检测记录存在重复的水平录入：{level_list}。")


def _mapping_get(row: Any, field_name: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(field_name, default)
    if hasattr(row, "keys") and field_name in row.keys():
        return row[field_name]
    try:
        return row[field_name]
    except (KeyError, IndexError, TypeError):
        return default


def _parse_json_list(raw_value: Any) -> list[Any]:
    if raw_value is None:
        return []
    if isinstance(raw_value, list):
        return raw_value
    text = str(raw_value).strip()
    if not text:
        return []
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError:
        return []
    return loaded if isinstance(loaded, list) else []


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric_value):
        return None
    return numeric_value


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _safe_cv(mean_value: float | None, sd_value: float | None) -> float | None:
    if mean_value is None or sd_value is None:
        return None
    if math.isclose(float(mean_value), 0.0, abs_tol=1e-12):
        return None
    return float(float(sd_value) / float(mean_value) * 100)


def _format_test_time(value: Any) -> str:
    return pd.Timestamp(value).strftime("%Y-%m-%d %H:%M:%S")
