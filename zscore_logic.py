from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import math
from typing import Any


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
        description="当前 run 单水平超出 ±2SD，作为警告信号。",
        error_bias="warning",
    ),
    "1_3s": ZScoreRuleDefinition(
        rule_id="1_3s",
        severity="reject",
        scope="within-run / within-level",
        description="当前 run 单水平超出 ±3SD，提示明显随机误差风险。",
        error_bias="random",
    ),
    "2_2s": ZScoreRuleDefinition(
        rule_id="2_2s",
        severity="reject",
        scope="across-run / within-level",
        description="同一水平连续 2 次位于均值同侧且超出 ±2SD。",
        error_bias="systematic",
    ),
    "2of3_2s": ZScoreRuleDefinition(
        rule_id="2of3_2s",
        severity="reject",
        scope="within-run / across-level",
        description="3 水平同一次 run 中有 2 个结果位于均值同侧且超出 ±2SD。",
        error_bias="systematic",
    ),
    "R_4s": ZScoreRuleDefinition(
        rule_id="R_4s",
        severity="reject",
        scope="within-run / across-level",
        description="同一次 run 内至少 2 个水平相差超过 4SD，且方向相反。",
        error_bias="random",
    ),
    "4_1s": ZScoreRuleDefinition(
        rule_id="4_1s",
        severity="reject",
        scope="across-run / within-level",
        description="同一水平连续 4 次位于均值同侧且超出 ±1SD。",
        error_bias="systematic",
    ),
    "3_1s": ZScoreRuleDefinition(
        rule_id="3_1s",
        severity="reject",
        scope="within-run / across-level",
        description="3 水平同一次 run 全部位于均值同侧且超出 ±1SD。",
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


def compute_zscore(raw_value: float | None, target_mean: float | None, target_sd: float | None) -> float | None:
    if raw_value is None or target_mean is None or target_sd is None:
        return None
    if not math.isfinite(raw_value) or not math.isfinite(target_mean) or not math.isfinite(target_sd):
        return None
    if math.isclose(target_sd, 0.0, abs_tol=1e-12) or target_sd < 0:
        return None
    return float((raw_value - target_mean) / target_sd)


def build_zscore_rule_templates() -> dict[str, dict[str, Any]]:
    template_specs = {
        "2_level_classic": {
            "label": "2-level classic",
            "level_ids": ["Level 1", "Level 2"],
            "rule_ids": ["1_2s", "1_3s", "2_2s", "R_4s", "4_1s", "10_x"],
            "note": "经典 2 水平 multirule 骨架，重点保留 across-run within-level 判读。",
        },
        "3_level_threes": {
            "label": "3-level threes",
            "level_ids": ["Level 1", "Level 2", "Level 3"],
            "rule_ids": ["1_2s", "1_3s", "2of3_2s", "R_4s", "3_1s", "12_x"],
            "note": "3 水平模板显式采用 within-run across-level 规则，不直接照搬 classic 2-level 组合。",
        },
    }

    templates: dict[str, dict[str, Any]] = {}
    for template_id, spec in template_specs.items():
        target_map: dict[str, dict[str, float]] = {}
        for level_id in spec["level_ids"]:
            target_mean = float(DEFAULT_TARGETS[level_id]["target_mean"])
            target_sd = float(DEFAULT_TARGETS[level_id]["target_sd"])
            target_map[level_id] = {
                "target_mean": target_mean,
                "target_sd": target_sd,
                "target_cv": float(target_sd / target_mean * 100) if not math.isclose(target_mean, 0.0) else None,
            }
        templates[template_id] = {
            "template_id": template_id,
            "label": spec["label"],
            "level_ids": list(spec["level_ids"]),
            "rule_ids": list(spec["rule_ids"]),
            "rules": [rule_to_dict(RULE_DEFINITIONS[rule_id]) for rule_id in spec["rule_ids"]],
            "default_targets": target_map,
            "note": spec["note"],
        }
    return deepcopy(templates)


def evaluate_zscore_run(
    level_results: list[dict[str, Any]],
    history_runs: list[dict[str, Any]],
    template_id: str,
) -> dict[str, Any]:
    templates = build_zscore_rule_templates()
    template = templates[template_id]
    current_run = _build_run_shell(level_results, template, history_runs)
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


def build_zscore_analysis_prompt(status: str, rule_hits: list[dict[str, Any]], error_type_hint: str) -> str:
    if status == "pending":
        return "请完整录入当前模板要求的所有水平结果后再进行判读。"
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
                    f"{level_result['level_id']} \u8fde\u7eed 2 \u6b21\u540c\u4fa7\u8d85 2SD",
                )
            )
    return hits


def rule_2of3_2s(level_results: list[dict[str, Any]], history_runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    del history_runs
    zscores = _current_zscore_map(level_results)
    positive_levels = [level_id for level_id, value in zscores.items() if value is not None and value >= 2]
    negative_levels = [level_id for level_id, value in zscores.items() if value is not None and value <= -2]
    if len(positive_levels) >= 2:
        return [_make_rule_hit("2of3_2s", positive_levels, "\u5f53\u524d run \u4e2d 2/3 \u6c34\u5e73\u540c\u4fa7\u8d85 2SD")]
    if len(negative_levels) >= 2:
        return [_make_rule_hit("2of3_2s", negative_levels, "\u5f53\u524d run \u4e2d 2/3 \u6c34\u5e73\u540c\u4fa7\u8d85 2SD")]
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
            f"{min_result['level_id']} / {max_result['level_id']} within-run \u5dee\u503c\u8d85 4SD",
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
        return [_make_rule_hit("3_1s", [level_result["level_id"] for level_result in level_results], "3 \u6c34\u5e73 within-run \u540c\u4fa7\u8d85 1SD")]
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
) -> dict[str, Any]:
    input_map = {str(item.get("level_id")): deepcopy(item) for item in level_results}
    normalized_results = []
    for level_id in template["level_ids"]:
        default_target = template["default_targets"][level_id]
        current = {
            "level_id": level_id,
            "raw_value": None,
            "log_value": None,
            "target_mean": default_target["target_mean"],
            "target_sd": default_target["target_sd"],
            "zscore": None,
            "rule_hits_local": [],
            "status": "pending",
        }
        current.update(input_map.get(level_id, {}))
        raw_value = current.get("raw_value")
        if raw_value is not None and current.get("log_value") is None and raw_value > 0:
            current["log_value"] = math.log10(raw_value)
        current["zscore"] = compute_zscore(
            current.get("raw_value"),
            current.get("target_mean"),
            current.get("target_sd"),
        )
        current["status"] = "accept" if current["zscore"] is not None else "pending"
        normalized_results.append(current)

    run_status = "accept" if all(result["zscore"] is not None for result in normalized_results) else "pending"
    analysis_prompt = (
        "请完整录入当前模板要求的所有水平结果后再进行判读。"
        if run_status == "pending"
        else "当前 run 未触发已启用的 Z-score 规则，可继续观察后续趋势。"
    )
    return {
        "run_id": len(history_runs) + 1,
        "test_time": None,
        "operator": "",
        "template_id": template["template_id"],
        "template_label": template["label"],
        "level_results": normalized_results,
        "run_status": run_status,
        "rule_hits_run": [],
        "error_type_hint": "unknown",
        "analysis_prompt": analysis_prompt,
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
                    f"{level_result['level_id']} \u8fde\u7eed {lookback} \u6b21\u540c\u4fa7\u8d85 {threshold:g}SD",
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
                    f"{level_result['level_id']} \u8fde\u7eed {lookback} \u6b21\u4f4d\u4e8e\u5747\u503c\u540c\u4fa7",
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
