from __future__ import annotations

import math

import matplotlib.pyplot as plt
from matplotlib.figure import Figure
import pandas as pd

from zscore_logic import (
    PHASE_FORMAL_QC,
    PHASE_TARGET_BUILDING,
    build_level_target_profiles,
    build_zscore_rule_templates,
    determine_zscore_phase,
    evaluate_zscore_run,
    evaluate_zscore_run_with_phase,
    get_phase_label,
    should_enable_formal_rules,
)
from zscore_plotting import plot_zscore_overlay, plot_zscore_single_level


TEMPLATES = build_zscore_rule_templates()
PLOT_COLUMNS = [
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
BASE_TIME = pd.Timestamp("2026-03-28 08:00:00")


def make_level_results(template_id: str, zscore_map: dict[str, float]) -> list[dict[str, float]]:
    template = TEMPLATES[template_id]
    level_results: list[dict[str, float]] = []
    for level_id in template["level_ids"]:
        target_info = template["default_targets"][level_id]
        zscore = float(zscore_map.get(level_id, 0.0))
        raw_value = float(target_info["target_mean"] + zscore * target_info["target_sd"])
        log_value = math.log10(raw_value) if raw_value > 0 else None
        level_results.append(
            {
                "level_id": level_id,
                "raw_value": raw_value,
                "log_value": log_value,
                "target_mean": float(target_info["target_mean"]),
                "target_sd": float(target_info["target_sd"]),
            }
        )
    return level_results


def build_history_runs(template_id: str, history_zscores: list[dict[str, float]]) -> list[dict[str, object]]:
    history_runs: list[dict[str, object]] = []
    for run_index, zscore_map in enumerate(history_zscores, start=1):
        run = evaluate_zscore_run(make_level_results(template_id, zscore_map), history_runs, template_id)
        run["run_id"] = run_index
        run["test_time"] = BASE_TIME + pd.Timedelta(hours=run_index)
        run["operator"] = f"tester-{run_index}"
        history_runs.append(run)
    return history_runs


def build_phase_history_runs(
    template_id: str,
    history_zscores: list[dict[str, float]],
    required_n: int,
) -> list[dict[str, object]]:
    history_runs: list[dict[str, object]] = []
    for run_index, zscore_map in enumerate(history_zscores, start=1):
        run = evaluate_zscore_run_with_phase(
            make_level_results(template_id, zscore_map),
            history_runs,
            template_id,
            required_n,
        )
        run["run_id"] = run_index
        run["test_time"] = BASE_TIME + pd.Timedelta(hours=run_index)
        run["operator"] = f"phase-tester-{run_index}"
        history_runs.append(run)
    return history_runs


def evaluate_case(
    template_id: str,
    current_zscores: dict[str, float],
    history_zscores: list[dict[str, float]] | None = None,
) -> dict[str, object]:
    history_runs = build_history_runs(template_id, history_zscores or [])
    current_run = evaluate_zscore_run(make_level_results(template_id, current_zscores), history_runs, template_id)
    current_run["run_id"] = len(history_runs) + 1
    current_run["test_time"] = BASE_TIME + pd.Timedelta(hours=len(history_runs) + 1)
    current_run["operator"] = "current-user"
    return current_run


def evaluate_phase_case(
    template_id: str,
    current_zscores: dict[str, float],
    history_zscores: list[dict[str, float]] | None = None,
    required_n: int = 5,
) -> dict[str, object]:
    history_runs = build_phase_history_runs(template_id, history_zscores or [], required_n)
    current_run = evaluate_zscore_run_with_phase(
        make_level_results(template_id, current_zscores),
        history_runs,
        template_id,
        required_n,
    )
    current_run["run_id"] = len(history_runs) + 1
    current_run["test_time"] = BASE_TIME + pd.Timedelta(hours=len(history_runs) + 1)
    current_run["operator"] = "phase-current-user"
    return current_run


def get_rule_ids(run: dict[str, object]) -> set[str]:
    return {hit["rule_id"] for hit in run.get("rule_hits_run", [])}


def get_rule_hit(run: dict[str, object], rule_id: str) -> dict[str, object]:
    for hit in run.get("rule_hits_run", []):
        if hit["rule_id"] == rule_id:
            return hit
    raise AssertionError(f"Rule hit not found: {rule_id}")


def build_plot_df() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    sample_runs = [
        ("Level 1", 1, 0.4, "accept"),
        ("Level 1", 2, 2.3, "warning"),
        ("Level 2", 1, -0.3, "accept"),
        ("Level 2", 2, -2.4, "reject"),
    ]
    for level_id, run_index, zscore, status in sample_runs:
        target_info = TEMPLATES["2_level_classic"]["default_targets"][level_id]
        raw_value = float(target_info["target_mean"] + zscore * target_info["target_sd"])
        rows.append(
            {
                "run_id": run_index,
                "run_index": run_index,
                "test_time": BASE_TIME + pd.Timedelta(hours=run_index),
                "level_id": level_id,
                "zscore": zscore,
                "status": status,
                "rule_hits": "",
                "raw_value": raw_value,
                "log_value": math.log10(raw_value),
                "phase": PHASE_FORMAL_QC,
                "is_preview": False,
            }
        )
    return pd.DataFrame(rows, columns=PLOT_COLUMNS)


def assert_is_figure(figure: object) -> None:
    assert isinstance(figure, Figure), f"Expected matplotlib Figure, got {type(figure)!r}"


def test_template_rule_sets() -> None:
    assert TEMPLATES["2_level_classic"]["rule_ids"] == ["1_2s", "1_3s", "2_2s", "R_4s", "4_1s", "10_x"]
    assert TEMPLATES["3_level_threes"]["rule_ids"] == ["1_2s", "1_3s", "2of3_2s", "R_4s", "3_1s", "12_x"]
    assert TEMPLATES["2_level_classic"]["required_n"] == 5
    assert TEMPLATES["3_level_threes"]["required_n"] == 5


def test_2_level_1_2s_warning() -> None:
    run = evaluate_case("2_level_classic", {"Level 1": 2.4, "Level 2": 0.2})
    assert "1_2s" in get_rule_ids(run)
    assert "1_3s" not in get_rule_ids(run)
    assert run["run_status"] == "warning"


def test_2_level_1_3s_reject() -> None:
    run = evaluate_case("2_level_classic", {"Level 1": 3.2, "Level 2": 0.1})
    assert "1_3s" in get_rule_ids(run)
    assert run["run_status"] == "reject"
    assert run["error_type_hint"] == "random"


def test_2_level_r_4s_within_run_across_level() -> None:
    run = evaluate_case("2_level_classic", {"Level 1": 2.5, "Level 2": -2.1})
    assert "R_4s" in get_rule_ids(run)
    assert run["run_status"] == "reject"
    assert get_rule_hit(run, "R_4s")["scope"] == "within-run / across-level"
    assert run["error_type_hint"] == "random"


def test_2_level_2_2s() -> None:
    run = evaluate_case(
        "2_level_classic",
        {"Level 1": 2.2, "Level 2": 0.0},
        history_zscores=[{"Level 1": 2.3, "Level 2": 0.0}],
    )
    assert "2_2s" in get_rule_ids(run)
    assert run["run_status"] == "reject"


def test_2_level_4_1s() -> None:
    run = evaluate_case(
        "2_level_classic",
        {"Level 1": 1.4, "Level 2": 0.0},
        history_zscores=[
            {"Level 1": 1.3, "Level 2": 0.0},
            {"Level 1": 1.5, "Level 2": 0.0},
            {"Level 1": 1.2, "Level 2": 0.0},
        ],
    )
    assert "4_1s" in get_rule_ids(run)
    assert run["run_status"] == "reject"


def test_2_level_10_x() -> None:
    run = evaluate_case(
        "2_level_classic",
        {"Level 1": 0.3, "Level 2": 0.0},
        history_zscores=[{"Level 1": 0.4, "Level 2": 0.0} for _ in range(9)],
    )
    assert "10_x" in get_rule_ids(run)
    assert run["run_status"] == "reject"


def test_3_level_1_2s_warning() -> None:
    run = evaluate_case("3_level_threes", {"Level 1": 2.1, "Level 2": 0.0, "Level 3": 0.0})
    assert "1_2s" in get_rule_ids(run)
    assert run["run_status"] == "warning"


def test_3_level_1_3s_reject() -> None:
    run = evaluate_case("3_level_threes", {"Level 1": 0.0, "Level 2": 3.1, "Level 3": 0.0})
    assert "1_3s" in get_rule_ids(run)
    assert run["run_status"] == "reject"


def test_3_level_2of3_2s_only_in_three_level_template() -> None:
    run = evaluate_case("3_level_threes", {"Level 1": 2.3, "Level 2": 2.2, "Level 3": 0.1})
    assert "2of3_2s" in get_rule_ids(run)
    assert run["run_status"] == "reject"
    assert get_rule_hit(run, "2of3_2s")["scope"] == "within-run / across-level"
    assert "2of3_2s" not in TEMPLATES["2_level_classic"]["rule_ids"]


def test_3_level_r_4s() -> None:
    run = evaluate_case("3_level_threes", {"Level 1": 2.4, "Level 2": -2.3, "Level 3": 0.2})
    assert "R_4s" in get_rule_ids(run)
    assert run["run_status"] == "reject"


def test_3_level_3_1s() -> None:
    run = evaluate_case("3_level_threes", {"Level 1": 1.2, "Level 2": 1.4, "Level 3": 1.1})
    assert "3_1s" in get_rule_ids(run)
    assert run["run_status"] == "reject"


def test_3_level_12_x() -> None:
    run = evaluate_case(
        "3_level_threes",
        {"Level 1": 0.2, "Level 2": 0.0, "Level 3": 0.0},
        history_zscores=[{"Level 1": 0.3, "Level 2": 0.0, "Level 3": 0.0} for _ in range(11)],
    )
    assert "12_x" in get_rule_ids(run)
    assert run["run_status"] == "reject"


def test_level_target_profiles_track_each_level_independently() -> None:
    history_runs = build_phase_history_runs(
        "2_level_classic",
        [
            {"Level 1": 0.1, "Level 2": -0.2},
            {"Level 1": 0.3, "Level 2": 0.0},
        ],
        required_n=3,
    )
    profiles = build_level_target_profiles(history_runs, "2_level_classic", required_n=3)
    level_1 = profiles["Level 1"]
    assert level_1["collected_n"] == 2
    assert level_1["required_n"] == 3
    assert level_1["target_mean_provisional"] is not None
    assert level_1["target_sd_provisional"] is not None
    assert level_1["target_cv_provisional"] is not None
    assert level_1["is_ready"] is False
    assert level_1["phase"] == PHASE_TARGET_BUILDING


def test_phase_stays_target_building_until_all_levels_ready() -> None:
    target_profiles = {
        "Level 1": {"is_ready": True},
        "Level 2": {"is_ready": False},
    }
    assert should_enable_formal_rules(target_profiles, ["Level 1", "Level 2"]) is False
    assert determine_zscore_phase(target_profiles, ["Level 1", "Level 2"]) == PHASE_TARGET_BUILDING
    assert get_phase_label(PHASE_TARGET_BUILDING) == "建靶中"


def test_target_building_run_does_not_trigger_formal_rules() -> None:
    run = evaluate_phase_case(
        "2_level_classic",
        {"Level 1": 3.5, "Level 2": 0.0},
        history_zscores=[
            {"Level 1": 0.2, "Level 2": 0.1},
            {"Level 1": 0.3, "Level 2": -0.1},
        ],
        required_n=3,
    )
    assert run["phase"] == PHASE_TARGET_BUILDING
    assert run["run_status"] == PHASE_TARGET_BUILDING
    assert run["formal_rules_enabled"] is False
    assert run["rule_hits_run"] == []


def test_formal_rules_enable_only_after_all_levels_ready() -> None:
    history_runs = build_phase_history_runs(
        "2_level_classic",
        [
            {"Level 1": -0.3, "Level 2": 0.1},
            {"Level 1": 0.1, "Level 2": -0.2},
            {"Level 1": 0.4, "Level 2": 0.3},
        ],
        required_n=3,
    )
    profiles = build_level_target_profiles(history_runs, "2_level_classic", required_n=3)
    assert should_enable_formal_rules(profiles, ["Level 1", "Level 2"]) is True
    assert determine_zscore_phase(profiles, ["Level 1", "Level 2"]) == PHASE_FORMAL_QC

    run = evaluate_zscore_run_with_phase(
        make_level_results("2_level_classic", {"Level 1": 3.2, "Level 2": 0.0}),
        history_runs,
        "2_level_classic",
        required_n=3,
    )
    assert run["phase"] == PHASE_FORMAL_QC
    assert run["formal_rules_enabled"] is True
    assert "1_3s" in get_rule_ids(run)
    assert run["run_status"] == "reject"


def test_plotting_handles_empty_frames() -> None:
    bare_empty_df = pd.DataFrame()
    fixed_empty_df = pd.DataFrame(columns=PLOT_COLUMNS)

    single_empty = plot_zscore_single_level(bare_empty_df, "Level 1", "空图")
    overlay_empty = plot_zscore_overlay(fixed_empty_df, "空图")
    assert_is_figure(single_empty)
    assert_is_figure(overlay_empty)
    plt.close(single_empty)
    plt.close(overlay_empty)


def test_plotting_single_level_returns_figure() -> None:
    figure = plot_zscore_single_level(build_plot_df(), "Level 1", "单水平图")
    assert_is_figure(figure)
    plt.close(figure)


def test_plotting_overlay_returns_figure() -> None:
    figure = plot_zscore_overlay(build_plot_df(), "合并图")
    assert_is_figure(figure)
    plt.close(figure)


def run_all_tests() -> None:
    test_functions = [
        test_template_rule_sets,
        test_2_level_1_2s_warning,
        test_2_level_1_3s_reject,
        test_2_level_r_4s_within_run_across_level,
        test_2_level_2_2s,
        test_2_level_4_1s,
        test_2_level_10_x,
        test_3_level_1_2s_warning,
        test_3_level_1_3s_reject,
        test_3_level_2of3_2s_only_in_three_level_template,
        test_3_level_r_4s,
        test_3_level_3_1s,
        test_3_level_12_x,
        test_level_target_profiles_track_each_level_independently,
        test_phase_stays_target_building_until_all_levels_ready,
        test_target_building_run_does_not_trigger_formal_rules,
        test_formal_rules_enable_only_after_all_levels_ready,
        test_plotting_handles_empty_frames,
        test_plotting_single_level_returns_figure,
        test_plotting_overlay_returns_figure,
    ]

    for test_func in test_functions:
        test_func()
        print(f"PASS {test_func.__name__}")

    print(f"All {len(test_functions)} Z-score smoke tests passed.")


if __name__ == "__main__":
    run_all_tests()
