from __future__ import annotations

import math
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import matplotlib.pyplot as plt
from matplotlib.figure import Figure
from matplotlib.legend import Legend
import pandas as pd
from streamlit.testing.v1 import AppTest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import database
from database import (
    create_zscore_batch,
    create_zscore_project,
    get_zscore_batch,
    get_zscore_project,
    get_zscore_runs_with_levels_for_batch,
    init_db,
)
from zscore_logic import (
    PHASE_FORMAL_QC,
    PHASE_TARGET_BUILDING,
    build_zscore_maintenance_dialog_state,
    build_level_target_profiles,
    build_zscore_batch_summary_items,
    build_zscore_plot_dataframe,
    build_zscore_rule_templates,
    create_zscore_run,
    determine_zscore_phase,
    evaluate_zscore_run,
    evaluate_zscore_run_with_phase,
    format_zscore_level_label_summary,
    get_building_stat_run_ids,
    get_zscore_display_sequence,
    get_level_ids_for_level_count,
    get_phase_label,
    get_template_id_for_level_count,
    get_zscore_level_label_map,
    get_zscore_level_targets,
    get_zscore_runs,
    resolve_zscore_batch_context,
    should_enable_formal_rules,
    delete_saved_zscore_run,
    rebuild_zscore_batch_state,
    update_saved_zscore_run,
    upsert_zscore_level_target,
)
from zscore_plotting import filter_zscore_plot_df, plot_zscore_overlay, plot_zscore_single_level


TEMPLATES = build_zscore_rule_templates()
PLOT_COLUMNS = [
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
]
BASE_TIME = pd.Timestamp("2026-03-28 08:00:00")
ZSCORE_PAGE_APPTEST_SCRIPT = f"""
import sys
from pathlib import Path

ROOT = Path({str(PROJECT_ROOT)!r})
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pages.zscore_page import render_zscore_page

render_zscore_page()
"""
ZSCORE_ENTRY_SAVE_BUTTON_KEY = "FormSubmitter:zscore_entry_form-\u4fdd\u5b58\u672c\u6b21\u68c0\u6d4b"
ZSCORE_FULL_RANGE_VIEW = "\u5168\u8303\u56f4\u89c6\u56fe"
ZSCORE_OVERLAY_VIEW = "合并视图"


class TemporaryDatabaseContext:
    def __enter__(self):
        self._tempdir = TemporaryDirectory()
        self._original_db_path = database.DB_PATH
        self._original_legacy_candidates = list(database.LEGACY_DB_CANDIDATES)
        database.DB_PATH = Path(self._tempdir.name) / "zscore_smoke_test.db"
        database.LEGACY_DB_CANDIDATES = []
        init_db()
        return self

    def __exit__(self, exc_type, exc, exc_tb):
        database.DB_PATH = self._original_db_path
        database.LEGACY_DB_CANDIDATES = self._original_legacy_candidates
        try:
            self._tempdir.cleanup()
        except PermissionError:
            pass


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
                "test_sequence": run_index,
                "run_index": run_index,
                "test_time": BASE_TIME + pd.Timedelta(hours=run_index),
                "level_id": level_id,
                "zscore": zscore,
                "status": status,
                "rule_hits": "",
                "raw_value": raw_value,
                "log_value": math.log10(raw_value),
                "building_reference_mean": float(target_info["target_mean"]),
                "building_reference_sd": float(target_info["target_sd"]),
                "formal_reference_mean": float(target_info["target_mean"]),
                "formal_reference_sd": float(target_info["target_sd"]),
                "phase": PHASE_FORMAL_QC,
                "plot_phase": PHASE_FORMAL_QC,
                "is_building_stat_point": False,
                "is_preview": False,
            }
        )
    return pd.DataFrame(rows, columns=PLOT_COLUMNS)


def build_mixed_phase_plot_df() -> pd.DataFrame:
    level_1_target = TEMPLATES["2_level_classic"]["default_targets"]["Level 1"]
    rows = [
        {
            "run_id": 1,
            "test_sequence": 1,
            "run_index": 1,
            "test_time": BASE_TIME,
            "level_id": "Level 1",
            "zscore": 0.2,
            "status": PHASE_TARGET_BUILDING,
            "rule_hits": "",
            "raw_value": 101.0,
            "log_value": math.log10(101.0),
            "building_reference_mean": 101.0,
            "building_reference_sd": None,
            "formal_reference_mean": float(level_1_target["target_mean"]),
            "formal_reference_sd": float(level_1_target["target_sd"]),
            "phase": PHASE_TARGET_BUILDING,
            "plot_phase": PHASE_TARGET_BUILDING,
            "is_building_stat_point": True,
            "is_preview": False,
        },
        {
            "run_id": 2,
            "test_sequence": 2,
            "run_index": 2,
            "test_time": BASE_TIME + pd.Timedelta(hours=1),
            "level_id": "Level 1",
            "zscore": -0.1,
            "status": "accept",
            "rule_hits": "",
            "raw_value": 99.0,
            "log_value": math.log10(99.0),
            "building_reference_mean": 100.0,
            "building_reference_sd": 1.0,
            "formal_reference_mean": float(level_1_target["target_mean"]),
            "formal_reference_sd": float(level_1_target["target_sd"]),
            "phase": PHASE_FORMAL_QC,
            "plot_phase": PHASE_FORMAL_QC,
            "is_building_stat_point": False,
            "is_preview": False,
        },
    ]
    return pd.DataFrame(rows, columns=PLOT_COLUMNS)


def assert_is_figure(figure: object) -> None:
    assert isinstance(figure, Figure), f"Expected matplotlib Figure, got {type(figure)!r}"


def collect_legend_texts(figure: Figure) -> dict[str, list[str]]:
    legend_map: dict[str, list[str]] = {}
    for axis in figure.axes:
        legends = [artist for artist in axis.artists if isinstance(artist, Legend)]
        for legend in legends:
            legend_map[legend.get_title().get_text()] = [text.get_text() for text in legend.get_texts()]
    return legend_map


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


def test_db_persistence_supports_vendor_targets_and_formal_realtime_stats() -> None:
    with TemporaryDatabaseContext():
        project_id = create_zscore_project("Z-score Smoke Project", level_count=2)
        batch_id = create_zscore_batch(
            project_id=project_id,
            instrument="AU5800",
            reagent="Chemistry",
            qc_material="Control A",
            concentration="Normal",
            lot_no="LOT-001",
            target_n=5,
        )

        upsert_zscore_level_target(
            batch_id=batch_id,
            level_id="Level 1",
            vendor_reference_mean=100.0,
            vendor_reference_sd=5.0,
            vendor_reference_source_note="COA",
            required_n=3,
        )
        upsert_zscore_level_target(
            batch_id=batch_id,
            level_id="Level 2",
            vendor_reference_mean=150.0,
            vendor_reference_sd=7.5,
            vendor_reference_source_note="手工录入",
            required_n=3,
        )

        create_zscore_run(
            batch_id=batch_id,
            test_time=BASE_TIME,
            operator="tester-1",
            level_results=[
                {"level_id": "Level 1", "raw_value": 100.0},
                {"level_id": "Level 2", "raw_value": 150.0},
            ],
            template_id="2_level_classic",
            required_n=3,
        )
        create_zscore_run(
            batch_id=batch_id,
            test_time=BASE_TIME + pd.Timedelta(hours=1),
            operator="tester-2",
            level_results=[
                {"level_id": "Level 1", "raw_value": 101.0},
                {"level_id": "Level 2", "raw_value": 151.0},
            ],
            template_id="2_level_classic",
            required_n=3,
        )
        transition_run = create_zscore_run(
            batch_id=batch_id,
            test_time=BASE_TIME + pd.Timedelta(hours=2),
            operator="tester-3",
            level_results=[
                {"level_id": "Level 1", "raw_value": 99.0},
                {"level_id": "Level 2", "raw_value": 149.0},
            ],
            template_id="2_level_classic",
            required_n=3,
        )
        assert transition_run["phase"] == PHASE_TARGET_BUILDING
        assert transition_run["run_status"] == PHASE_TARGET_BUILDING

        first_formal_run = create_zscore_run(
            batch_id=batch_id,
            test_time=BASE_TIME + pd.Timedelta(hours=3),
            operator="tester-4",
            level_results=[
                {"level_id": "Level 1", "raw_value": 100.0},
                {"level_id": "Level 2", "raw_value": 150.0},
            ],
            template_id="2_level_classic",
            required_n=3,
        )
        assert first_formal_run["phase"] == PHASE_FORMAL_QC
        assert first_formal_run["run_status"] == "accept"
        warning_run = create_zscore_run(
            batch_id=batch_id,
            test_time=BASE_TIME + pd.Timedelta(hours=4),
            operator="tester-5",
            level_results=[
                {"level_id": "Level 1", "raw_value": 102.1},
                {"level_id": "Level 2", "raw_value": 150.0},
            ],
            template_id="2_level_classic",
            required_n=3,
        )
        assert warning_run["run_status"] == "warning"

        saved_runs = get_zscore_runs(batch_id, "2_level_classic")
        assert len(saved_runs) == 5
        assert len(saved_runs[-1]["level_results"]) == 2
        assert all(
            not level_result["is_in_control_for_realtime_stats"]
            for level_result in saved_runs[-1]["level_results"]
        )

        targets = get_zscore_level_targets(batch_id, "2_level_classic", required_n=3)
        level_1 = targets["Level 1"]
        level_2 = targets["Level 2"]
        assert level_1["vendor_reference_mean"] == 100.0
        assert level_1["vendor_reference_sd"] == 5.0
        assert level_1["vendor_reference_cv"] == 5.0
        assert level_1["vendor_reference_source_note"] == "COA"
        assert level_1["collected_n"] == 3
        assert level_1["is_ready"] is True
        assert level_1["phase"] == PHASE_FORMAL_QC
        assert level_1["final_target_mean"] == 100.0
        assert round(level_1["final_target_sd"], 6) == 1.0
        assert round(level_1["realtime_mean"], 6) == 100.0
        assert level_1["realtime_sd"] is None
        assert level_1["realtime_cv"] is None
        assert level_2["vendor_reference_source_note"] == "手工录入"
        assert round(level_2["realtime_mean"], 6) == 150.0
        assert level_2["realtime_sd"] is None


def test_zscore_project_level_count_persistence() -> None:
    with TemporaryDatabaseContext():
        project_id_2 = create_zscore_project("2-level Project", level_count=2)
        project_id_3 = create_zscore_project("3-level Project", level_count=3)
        assert int(get_zscore_project(project_id_2)["level_count"]) == 2
        assert int(get_zscore_project(project_id_3)["level_count"]) == 3


def test_zscore_batch_inherits_level_count() -> None:
    with TemporaryDatabaseContext():
        project_id_2 = create_zscore_project("2-level Batch Project", level_count=2)
        project_id_3 = create_zscore_project("3-level Batch Project", level_count=3)
        batch_id_2 = create_zscore_batch(
            project_id=project_id_2,
            instrument="Inst-2",
            reagent="Reagent-2",
            qc_material="QC-2",
            concentration="Normal",
            lot_no="LOT-2",
            target_n=5,
        )
        batch_id_3 = create_zscore_batch(
            project_id=project_id_3,
            instrument="Inst-3",
            reagent="Reagent-3",
            qc_material="QC-3",
            concentration="High",
            lot_no="LOT-3",
            target_n=5,
        )
        assert int(get_zscore_batch(batch_id_2)["level_count"]) == 2
        assert int(get_zscore_batch(batch_id_3)["level_count"]) == 3


def test_zscore_level_labels_persist_and_fallback() -> None:
    with TemporaryDatabaseContext():
        project_id_2 = create_zscore_project("Label Project 2", level_count=2)
        batch_id_2 = create_zscore_batch(
            project_id=project_id_2,
            instrument="Inst-L2",
            reagent="Reagent-L2",
            qc_material="QC-L2",
            concentration="Normal",
            lot_no="LOT-L2",
            target_n=5,
            level_1_label="Low",
            level_2_label="High",
        )
        batch_2 = get_zscore_batch(batch_id_2)
        label_map_2 = get_zscore_level_label_map(batch_2, ["Level 1", "Level 2"])
        assert label_map_2 == {"Level 1": "Low", "Level 2": "High"}
        assert format_zscore_level_label_summary(batch_2, ["Level 1", "Level 2"]) == "水平 1：Low | 水平 2：High"

        project_id_3 = create_zscore_project("Label Project 3", level_count=3)
        batch_id_3 = create_zscore_batch(
            project_id=project_id_3,
            instrument="Inst-L3",
            reagent="Reagent-L3",
            qc_material="QC-L3",
            concentration="High",
            lot_no="LOT-L3",
            target_n=5,
            level_1_label="Low",
            level_2_label="",
            level_3_label="High",
        )
        batch_3 = get_zscore_batch(batch_id_3)
        label_map_3 = get_zscore_level_label_map(batch_3, ["Level 1", "Level 2", "Level 3"])
        assert label_map_3 == {"Level 1": "Low", "Level 2": "Level 2", "Level 3": "High"}


def test_level_count_binds_template_and_required_level_ids() -> None:
    assert get_template_id_for_level_count(2) == "2_level_classic"
    assert get_template_id_for_level_count(3) == "3_level_threes"
    assert get_level_ids_for_level_count(2) == ["Level 1", "Level 2"]
    assert get_level_ids_for_level_count(3) == ["Level 1", "Level 2", "Level 3"]


def test_batch_context_auto_shapes_template_by_level_count() -> None:
    with TemporaryDatabaseContext():
        project_id = create_zscore_project("Context Project", level_count=3)
        batch_id = create_zscore_batch(
            project_id=project_id,
            instrument="Ctx-Inst",
            reagent="Ctx-Reagent",
            qc_material="Ctx-QC",
            concentration="High",
            lot_no="CTX-LOT",
            target_n=6,
        )
        context = resolve_zscore_batch_context(batch_id)
        assert context["level_count"] == 3
        assert context["template_id"] == "3_level_threes"
        assert context["required_level_ids"] == ["Level 1", "Level 2", "Level 3"]
        assert context["required_n"] == 6


def test_zscore_summary_items_update_with_batch_context() -> None:
    with TemporaryDatabaseContext():
        project_id = create_zscore_project("Summary Project", level_count=2)
        batch_id_1 = create_zscore_batch(
            project_id=project_id,
            instrument="Inst-S1",
            reagent="Reagent-S1",
            qc_material="QC-S1",
            concentration="Normal",
            lot_no="LOT-S1",
            target_n=5,
            level_1_label="Low",
            level_2_label="High",
        )
        batch_id_2 = create_zscore_batch(
            project_id=project_id,
            instrument="Inst-S2",
            reagent="Reagent-S2",
            qc_material="QC-S2",
            concentration="High",
            lot_no="LOT-S2",
            target_n=5,
            level_1_label="A",
            level_2_label="B",
        )
        summary_1 = dict(
            build_zscore_batch_summary_items(
                get_zscore_batch(batch_id_1),
                phase_label="建靶中",
                formal_rules_enabled=False,
                template_label="2-level classic",
                level_ids=["Level 1", "Level 2"],
            )
        )
        summary_2 = dict(
            build_zscore_batch_summary_items(
                get_zscore_batch(batch_id_2),
                phase_label="正式质控",
                formal_rules_enabled=True,
                template_label="2-level classic",
                level_ids=["Level 1", "Level 2"],
            )
        )
        assert summary_1["当前阶段"] == "建靶中"
        assert summary_1["全部水平已完成建靶"] == "否"
        assert summary_1["正式规则已启用"] == "否"
        assert summary_1["水平说明"] == "水平 1：Low | 水平 2：High"
        assert summary_2["当前阶段"] == "正式质控"
        assert summary_2["全部水平已完成建靶"] == "是"
        assert summary_2["项目名称"] == "Summary Project"
        assert summary_2["批次编号"] != summary_1["批次编号"]


def test_create_zscore_run_respects_batch_level_count() -> None:
    with TemporaryDatabaseContext():
        project_id_2 = create_zscore_project("Run Project 2", level_count=2)
        batch_id_2 = create_zscore_batch(
            project_id=project_id_2,
            instrument="Inst-A",
            reagent="Reagent-A",
            qc_material="QC-A",
            concentration="Normal",
            lot_no="LOT-A",
            target_n=5,
        )
        run_2 = create_zscore_run(
            batch_id=batch_id_2,
            test_time=BASE_TIME,
            operator="tester",
            level_results=[
                {"level_id": "Level 1", "raw_value": 100.0},
                {"level_id": "Level 2", "raw_value": 150.0},
            ],
            template_id="2_level_classic",
            required_n=5,
        )
        assert len(run_2["level_results"]) == 2

        project_id_3 = create_zscore_project("Run Project 3", level_count=3)
        batch_id_3 = create_zscore_batch(
            project_id=project_id_3,
            instrument="Inst-B",
            reagent="Reagent-B",
            qc_material="QC-B",
            concentration="High",
            lot_no="LOT-B",
            target_n=5,
        )
        try:
            create_zscore_run(
                batch_id=batch_id_3,
                test_time=BASE_TIME,
                operator="tester",
                level_results=[
                    {"level_id": "Level 1", "raw_value": 100.0},
                    {"level_id": "Level 2", "raw_value": 150.0},
                ],
                template_id="3_level_threes",
                required_n=5,
            )
        except ValueError as exc:
            assert "Level 3" in str(exc)
        else:
            raise AssertionError("3-level 批次缺少 Level 3 时应报错")


def test_plot_phase_filtering_views() -> None:
    mixed_df = build_mixed_phase_plot_df()
    building_df = filter_zscore_plot_df(mixed_df, "building")
    formal_df = filter_zscore_plot_df(mixed_df, "formal")
    all_df = filter_zscore_plot_df(mixed_df, "all")
    assert building_df["phase"].tolist() == [PHASE_TARGET_BUILDING]
    assert formal_df["phase"].tolist() == [PHASE_FORMAL_QC]
    assert set(all_df["phase"].tolist()) == {PHASE_TARGET_BUILDING, PHASE_FORMAL_QC}


def test_collected_n_matches_building_plot_points() -> None:
    with TemporaryDatabaseContext():
        project_id = create_zscore_project("Collected Plot Project", level_count=2)
        batch_id = create_zscore_batch(
            project_id=project_id,
            instrument="Inst-C",
            reagent="Reagent-C",
            qc_material="QC-C",
            concentration="Normal",
            lot_no="LOT-C",
            target_n=5,
        )
        for hour, values in enumerate(
            [
                (100.0, 150.0),
                (101.0, 151.0),
                (99.0, 149.0),
                (100.5, 150.5),
                (99.5, 149.5),
            ],
            start=0,
        ):
            create_zscore_run(
                batch_id=batch_id,
                test_time=BASE_TIME + pd.Timedelta(hours=hour),
                operator=f"tester-{hour}",
                level_results=[
                    {"level_id": "Level 1", "raw_value": values[0]},
                    {"level_id": "Level 2", "raw_value": values[1]},
                ],
                template_id="2_level_classic",
                required_n=5,
            )

        profiles = get_zscore_level_targets(batch_id, "2_level_classic", required_n=5)
        saved_runs = get_zscore_runs(batch_id, "2_level_classic")
        assert get_building_stat_run_ids(saved_runs) == {1, 2, 3, 4, 5}
        plot_df = build_zscore_plot_dataframe(saved_runs)
        building_df = filter_zscore_plot_df(plot_df, "building")
        level_1_building_points = building_df[building_df["level_id"] == "Level 1"]
        level_2_building_points = building_df[building_df["level_id"] == "Level 2"]
        assert profiles["Level 1"]["collected_n"] == 5
        assert profiles["Level 2"]["collected_n"] == 5
        assert len(level_1_building_points) == 5
        assert len(level_2_building_points) == 5


def test_create_zscore_run_rejects_unexpected_level_for_two_level_batch() -> None:
    with TemporaryDatabaseContext():
        project_id = create_zscore_project("Unexpected Level Project", level_count=2)
        batch_id = create_zscore_batch(
            project_id=project_id,
            instrument="Inst-X",
            reagent="Reagent-X",
            qc_material="QC-X",
            concentration="Normal",
            lot_no="LOT-X",
            target_n=5,
        )
        try:
            create_zscore_run(
                batch_id=batch_id,
                test_time=BASE_TIME,
                operator="tester",
                level_results=[
                    {"level_id": "Level 1", "raw_value": 100.0},
                    {"level_id": "Level 2", "raw_value": 150.0},
                    {"level_id": "Level 3", "raw_value": 200.0},
                ],
                template_id="2_level_classic",
                required_n=5,
            )
        except ValueError as exc:
            assert "Level 3" in str(exc)
        else:
            raise AssertionError("2-level batch should reject Level 3 input")


def test_edit_saved_run_rebuilds_targets_realtime_and_status() -> None:
    with TemporaryDatabaseContext():
        project_id = create_zscore_project("Edit Rebuild Project", level_count=2)
        batch_id = create_zscore_batch(
            project_id=project_id,
            instrument="Inst-E",
            reagent="Reagent-E",
            qc_material="QC-E",
            concentration="Normal",
            lot_no="LOT-E",
            target_n=5,
        )

        for hour, values in enumerate(
            [
                (100.0, 150.0),
                (101.0, 151.0),
                (99.0, 149.0),
                (100.0, 150.0),
                (100.5, 150.5),
                (99.5, 149.5),
                (101.8, 150.0),
            ],
            start=0,
        ):
            create_zscore_run(
                batch_id=batch_id,
                test_time=BASE_TIME + pd.Timedelta(hours=hour),
                operator=f"tester-{hour}",
                level_results=[
                    {"level_id": "Level 1", "raw_value": values[0]},
                    {"level_id": "Level 2", "raw_value": values[1]},
                ],
                template_id="2_level_classic",
                required_n=5,
            )

        initial_targets = get_zscore_level_targets(batch_id, "2_level_classic", required_n=5)
        initial_runs = get_zscore_runs(batch_id, "2_level_classic")
        assert initial_runs[-1]["run_status"] == "warning"

        rebuild_state = update_saved_zscore_run(
            run_id=int(initial_runs[6]["run_id"]),
            test_time=BASE_TIME + pd.Timedelta(hours=6),
            operator="editor-6",
            level_results=[
                {"level_id": "Level 1", "raw_value": 100.2},
                {"level_id": "Level 2", "raw_value": 150.0},
            ],
        )

        updated_targets = get_zscore_level_targets(batch_id, "2_level_classic", required_n=5)
        updated_runs = get_zscore_runs(batch_id, "2_level_classic")
        assert rebuild_state["overall_phase"] == PHASE_FORMAL_QC
        assert updated_runs[6]["operator"] == "editor-6"
        assert updated_runs[-1]["run_status"] == "accept"
        assert math.isclose(
            float(initial_targets["Level 1"]["final_target_mean"]),
            float(updated_targets["Level 1"]["final_target_mean"]),
            rel_tol=1e-9,
            abs_tol=1e-9,
        )
        assert round(updated_targets["Level 1"]["final_target_mean"], 6) == round(
            (100.0 + 101.0 + 99.0 + 100.0 + 100.5) / 5.0,
            6,
        )
        assert round(updated_targets["Level 1"]["realtime_mean"], 6) == round((99.5 + 100.2) / 2.0, 6)
        assert round(updated_targets["Level 1"]["realtime_sd"], 6) == round(math.sqrt(0.245), 6)


def test_delete_saved_run_rebuilds_batch_and_plot_points() -> None:
    with TemporaryDatabaseContext():
        project_id = create_zscore_project("Delete Rebuild Project", level_count=2)
        batch_id = create_zscore_batch(
            project_id=project_id,
            instrument="Inst-D",
            reagent="Reagent-D",
            qc_material="QC-D",
            concentration="Normal",
            lot_no="LOT-D",
            target_n=5,
        )

        for hour, values in enumerate(
            [
                (100.0, 150.0),
                (101.0, 151.0),
                (99.0, 149.0),
                (100.0, 150.0),
                (100.5, 150.5),
                (99.0, 149.0),
            ],
            start=0,
        ):
            create_zscore_run(
                batch_id=batch_id,
                test_time=BASE_TIME + pd.Timedelta(hours=hour),
                operator=f"tester-{hour}",
                level_results=[
                    {"level_id": "Level 1", "raw_value": values[0]},
                    {"level_id": "Level 2", "raw_value": values[1]},
                ],
                template_id="2_level_classic",
                required_n=5,
            )

        initial_runs = get_zscore_runs(batch_id, "2_level_classic")
        delete_run_id = int(initial_runs[5]["run_id"])
        delete_saved_zscore_run(delete_run_id)

        remaining_runs = get_zscore_runs(batch_id, "2_level_classic")
        raw_runs = get_zscore_runs_with_levels_for_batch(batch_id)
        targets = get_zscore_level_targets(batch_id, "2_level_classic", required_n=5)
        plot_df = build_zscore_plot_dataframe(remaining_runs)

        assert len(remaining_runs) == 5
        assert {int(run["run_id"]) for run in raw_runs} == {1, 2, 3, 4, 5}
        assert sum(len(run["level_results"]) for run in raw_runs) == 10
        assert len(plot_df) == 10
        assert round(targets["Level 1"]["final_target_mean"], 6) == round(
            (100.0 + 101.0 + 99.0 + 100.0 + 100.5) / 5.0,
            6,
        )
        assert targets["Level 1"]["realtime_mean"] is None
        assert sorted(plot_df["test_sequence"].drop_duplicates().tolist()) == [1, 2, 3, 4, 5]


def test_saved_run_maintenance_respects_level_count_for_two_and_three_level_batches() -> None:
    with TemporaryDatabaseContext():
        project_id_2 = create_zscore_project("Maintain 2-Level Project", level_count=2)
        batch_id_2 = create_zscore_batch(
            project_id=project_id_2,
            instrument="Inst-M2",
            reagent="Reagent-M2",
            qc_material="QC-M2",
            concentration="Normal",
            lot_no="LOT-M2",
            target_n=5,
        )
        run_2 = create_zscore_run(
            batch_id=batch_id_2,
            test_time=BASE_TIME,
            operator="tester-2",
            level_results=[
                {"level_id": "Level 1", "raw_value": 100.0},
                {"level_id": "Level 2", "raw_value": 150.0},
            ],
            template_id="2_level_classic",
            required_n=5,
        )
        try:
            update_saved_zscore_run(
                run_id=int(run_2["run_id"]),
                test_time=BASE_TIME,
                operator="tester-2-edit",
                level_results=[
                    {"level_id": "Level 1", "raw_value": 100.0},
                    {"level_id": "Level 2", "raw_value": 150.0},
                    {"level_id": "Level 3", "raw_value": 200.0},
                ],
            )
        except ValueError as exc:
            assert "Level 3" in str(exc)
        else:
            raise AssertionError("2-level batch should reject Level 3 when editing a saved run")

        project_id_3 = create_zscore_project("Maintain 3-Level Project", level_count=3)
        batch_id_3 = create_zscore_batch(
            project_id=project_id_3,
            instrument="Inst-M3",
            reagent="Reagent-M3",
            qc_material="QC-M3",
            concentration="High",
            lot_no="LOT-M3",
            target_n=5,
        )
        for hour, values in enumerate(
            [
                (100.0, 150.0, 200.0),
                (101.0, 151.0, 201.0),
                (99.0, 149.0, 199.0),
                (100.0, 150.0, 200.0),
                (100.5, 150.5, 200.5),
                (99.5, 149.5, 199.5),
                (101.0, 150.0, 201.0),
            ],
            start=0,
        ):
            create_zscore_run(
                batch_id=batch_id_3,
                test_time=BASE_TIME + pd.Timedelta(hours=hour),
                operator=f"tester-3-{hour}",
                level_results=[
                    {"level_id": "Level 1", "raw_value": values[0]},
                    {"level_id": "Level 2", "raw_value": values[1]},
                    {"level_id": "Level 3", "raw_value": values[2]},
                ],
                template_id="3_level_threes",
                required_n=5,
            )

        runs_before_delete = get_zscore_runs(batch_id_3, "3_level_threes")
        update_saved_zscore_run(
            run_id=int(runs_before_delete[5]["run_id"]),
            test_time=BASE_TIME + pd.Timedelta(hours=5),
            operator="editor-5",
            level_results=[
                {"level_id": "Level 1", "raw_value": 102.0},
                {"level_id": "Level 2", "raw_value": 151.0},
                {"level_id": "Level 3", "raw_value": 202.0},
            ],
        )
        rebuild_state = delete_saved_zscore_run(int(runs_before_delete[6]["run_id"]))
        saved_runs = get_zscore_runs(batch_id_3, "3_level_threes")

        assert rebuild_state["overall_phase"] == PHASE_FORMAL_QC
        assert set(rebuild_state["target_profiles"].keys()) == {"Level 1", "Level 2", "Level 3"}
        assert rebuild_state["target_profiles"]["Level 3"]["collected_n"] == 5
        assert all(len(run["level_results"]) == 3 for run in saved_runs)
        assert all(
            {level_result["level_id"] for level_result in run["level_results"]} == {"Level 1", "Level 2", "Level 3"}
            for run in saved_runs
        )


def test_building_runs_lock_after_batch_enters_formal() -> None:
    with TemporaryDatabaseContext():
        project_id = create_zscore_project("Locking Project", level_count=2)
        batch_id = create_zscore_batch(
            project_id=project_id,
            instrument="Inst-L",
            reagent="Reagent-L",
            qc_material="QC-L",
            concentration="Normal",
            lot_no="LOT-L",
            target_n=5,
        )

        for hour, values in enumerate(
            [
                (100.0, 150.0),
                (101.0, 151.0),
                (99.0, 149.0),
                (100.5, 150.5),
            ],
            start=0,
        ):
            create_zscore_run(
                batch_id=batch_id,
                test_time=BASE_TIME + pd.Timedelta(hours=hour),
                operator=f"tester-{hour}",
                level_results=[
                    {"level_id": "Level 1", "raw_value": values[0]},
                    {"level_id": "Level 2", "raw_value": values[1]},
                ],
                template_id="2_level_classic",
                required_n=5,
            )

        pre_formal_runs = get_zscore_runs(batch_id, "2_level_classic")
        assert all(not bool(run["is_locked_for_maintenance"]) for run in pre_formal_runs)

        create_zscore_run(
            batch_id=batch_id,
            test_time=BASE_TIME + pd.Timedelta(hours=4),
            operator="tester-4",
            level_results=[
                {"level_id": "Level 1", "raw_value": 99.5},
                {"level_id": "Level 2", "raw_value": 149.5},
            ],
            template_id="2_level_classic",
            required_n=5,
        )
        create_zscore_run(
            batch_id=batch_id,
            test_time=BASE_TIME + pd.Timedelta(hours=5),
            operator="tester-5",
            level_results=[
                {"level_id": "Level 1", "raw_value": 99.2},
                {"level_id": "Level 2", "raw_value": 149.2},
            ],
            template_id="2_level_classic",
            required_n=5,
        )

        post_formal_runs = get_zscore_runs(batch_id, "2_level_classic")
        locked_runs = [run for run in post_formal_runs if run["phase"] == PHASE_TARGET_BUILDING]
        formal_runs = [run for run in post_formal_runs if run["phase"] == PHASE_FORMAL_QC]

        assert locked_runs
        assert formal_runs
        assert all(bool(run["is_locked_for_maintenance"]) for run in locked_runs)
        assert all(not bool(run["is_locked_for_maintenance"]) for run in formal_runs)

        locked_run_id = int(locked_runs[0]["run_id"])
        try:
            update_saved_zscore_run(
                run_id=locked_run_id,
                test_time=BASE_TIME,
                operator="editor",
                level_results=[
                    {"level_id": "Level 1", "raw_value": 100.0},
                    {"level_id": "Level 2", "raw_value": 150.0},
                ],
            )
        except ValueError as exc:
            assert "已锁定" in str(exc)
        else:
            raise AssertionError("Locked building run should not be editable")

        try:
            delete_saved_zscore_run(locked_run_id)
        except ValueError as exc:
            assert "已锁定" in str(exc)
        else:
            raise AssertionError("Locked building run should not be deletable")


def test_test_sequence_keeps_incrementing_and_feeds_plot_axis() -> None:
    with TemporaryDatabaseContext():
        project_id = create_zscore_project("Sequence Project", level_count=2)
        batch_id = create_zscore_batch(
            project_id=project_id,
            instrument="Inst-S",
            reagent="Reagent-S",
            qc_material="QC-S",
            concentration="Normal",
            lot_no="LOT-S",
            target_n=5,
        )

        for hour, values in enumerate(
            [
                (100.0, 150.0),
                (101.0, 151.0),
                (99.0, 149.0),
            ],
            start=0,
        ):
            create_zscore_run(
                batch_id=batch_id,
                test_time=BASE_TIME + pd.Timedelta(hours=hour),
                operator=f"tester-{hour}",
                level_results=[
                    {"level_id": "Level 1", "raw_value": values[0]},
                    {"level_id": "Level 2", "raw_value": values[1]},
                ],
                template_id="2_level_classic",
                required_n=5,
            )

        initial_runs = get_zscore_runs(batch_id, "2_level_classic")
        assert [get_zscore_display_sequence(run) for run in initial_runs] == [1, 2, 3]

        delete_saved_zscore_run(int(initial_runs[1]["run_id"]))
        create_zscore_run(
            batch_id=batch_id,
            test_time=BASE_TIME + pd.Timedelta(hours=3),
            operator="tester-3",
            level_results=[
                {"level_id": "Level 1", "raw_value": 102.0},
                {"level_id": "Level 2", "raw_value": 152.0},
            ],
            template_id="2_level_classic",
            required_n=5,
        )

        final_runs = get_zscore_runs(batch_id, "2_level_classic")
        assert [get_zscore_display_sequence(run) for run in final_runs] == [1, 3, 4]

        plot_df = build_zscore_plot_dataframe(final_runs)
        assert sorted(plot_df["test_sequence"].drop_duplicates().tolist()) == [1, 3, 4]
        assert sorted(plot_df["run_index"].drop_duplicates().tolist()) == [1, 3, 4]


def test_plotting_uses_raw_value_axis_and_mean_sd_reference_lines() -> None:
    figure = plot_zscore_single_level(build_plot_df(), "Level 1", "单水平图")
    assert_is_figure(figure)
    axis = figure.axes[0]
    assert axis.get_xlabel() == "检测序号"
    assert axis.get_ylabel() == "检测值"

    constant_line_values = sorted(
        {
            round(float(line.get_ydata()[0]), 2)
            for line in axis.lines
            if len(line.get_ydata()) > 0 and len({round(float(value), 6) for value in line.get_ydata()}) == 1
        }
    )
    assert 100.0 in constant_line_values
    assert 105.0 in constant_line_values
    assert 95.0 in constant_line_values
    assert 110.0 in constant_line_values
    assert 90.0 in constant_line_values
    plt.close(figure)


def test_plotting_supports_standard_and_full_range_views() -> None:
    extreme_df = build_plot_df().copy()
    extreme_df.loc[
        (extreme_df["level_id"] == "Level 1") & (extreme_df["run_index"] == 2),
        "raw_value",
    ] = 120.0

    standard_figure = plot_zscore_single_level(
        extreme_df,
        "Level 1",
        "标准视图",
        y_axis_mode="标准视图",
        standard_sd_limit=3.0,
    )
    full_figure = plot_zscore_single_level(
        extreme_df,
        "Level 1",
        "全范围视图",
        y_axis_mode="全范围视图",
        standard_sd_limit=3.0,
    )

    standard_ylim = standard_figure.axes[0].get_ylim()
    full_ylim = full_figure.axes[0].get_ylim()
    assert standard_ylim[1] < 120.0
    assert full_ylim[1] >= 120.0
    plt.close(standard_figure)
    plt.close(full_figure)


def test_single_level_manual_legend_covers_status_phase_and_clip_marker() -> None:
    extreme_df = build_plot_df().copy()
    extreme_df.loc[
        (extreme_df["level_id"] == "Level 1") & (extreme_df["run_index"] == 2),
        "raw_value",
    ] = 140.0

    figure = plot_zscore_single_level(
        extreme_df,
        "Level 1",
        "Legend Single",
        y_axis_mode="标准视图",
        standard_sd_limit=3.0,
    )
    legend_map = collect_legend_texts(figure)

    assert legend_map["状态"] == ["正常", "警告", "失控", "超界裁切点"]
    assert legend_map["阶段 / 样式"] == [
        "建靶期（虚线 / 方形点）",
        "正式期（实线 / 圆形点）",
        "均值 / ±SD 控制线",
        "描边点=含手动备注",
    ]
    plt.close(figure)


def test_overlay_manual_legend_keeps_status_phase_and_level_keys() -> None:
    figure = plot_zscore_overlay(
        build_plot_df(),
        "Legend Overlay",
        y_axis_mode="标准视图",
        standard_sd_limit=3.0,
    )
    legend_map = collect_legend_texts(figure)

    assert legend_map["状态"] == ["正常", "警告", "失控"]
    assert legend_map["阶段 / 样式"] == [
        "建靶期（虚线 / 方形点）",
        "正式期（实线 / 圆形点）",
        "均值 / ±SD 控制线",
        "描边点=含手动备注",
    ]
    assert legend_map["水平"] == ["水平 1", "水平 2"]
    plt.close(figure)


def test_delete_feedback_keeps_maintenance_dialog_context() -> None:
    remaining_runs = [
        {"run_id": 3, "test_sequence": 4},
        {"run_id": 1, "test_sequence": 1},
    ]
    dialog_state = build_zscore_maintenance_dialog_state(
        action="delete",
        available_runs=remaining_runs,
        preferred_run_id=2,
    )

    assert dialog_state["keep_dialog_open"] is True
    assert dialog_state["dialog_notice"] == "Z-score 检测记录已删除，并已完成整批次重算。"
    assert dialog_state["selected_run_id"] == 3


def test_entry_save_preserves_chart_controls_after_rerun() -> None:
    with TemporaryDatabaseContext():
        project_id = create_zscore_project("AppTest Project", level_count=2, input_value_type="ct")
        batch_id = create_zscore_batch(
            project_id=project_id,
            instrument="AppTest Inst",
            reagent="AppTest Reagent",
            qc_material="AppTest QC",
            concentration="Normal",
            lot_no="APPTEST-LOT",
            target_n=5,
            level_1_label="S1",
            level_2_label="S2",
        )
        template_id = get_template_id_for_level_count(2)
        for hour, values in enumerate(
            [
                (100.0, 150.0),
                (101.0, 151.0),
                (99.5, 149.5),
                (100.2, 150.2),
                (100.1, 150.1),
                (99.9, 149.9),
            ],
            start=0,
        ):
            create_zscore_run(
                batch_id=batch_id,
                test_time=BASE_TIME + pd.Timedelta(hours=hour),
                operator=f"apptest-{hour}",
                level_results=[
                    {"level_id": "Level 1", "raw_value": values[0]},
                    {"level_id": "Level 2", "raw_value": values[1]},
                ],
                template_id=template_id,
                required_n=5,
            )

        at = AppTest.from_string(ZSCORE_PAGE_APPTEST_SCRIPT)
        at.session_state["zscore_selected_project_id"] = project_id
        at.session_state["zscore_selected_batch_id"] = batch_id
        at.run()

        at.radio(key="zscore_phase_scope_widget").set_value("all").run()
        at.radio(key="zscore_selected_level_widget").set_value("Level 2").run()
        at.radio(key="zscore_y_axis_mode_widget").set_value(ZSCORE_FULL_RANGE_VIEW).run()
        at.text_input(key="zscore_level1_value").set_value("100.4")
        at.text_input(key="zscore_level2_value").set_value("150.4")
        at.selectbox(key="zscore_entry_operator").set_value("apptest-5")
        at.button(key=ZSCORE_ENTRY_SAVE_BUTTON_KEY).click().run()

        state = at.session_state.filtered_state
        assert state["zscore_phase_scope"] == "all"
        assert state["zscore_view_mode"] == "单水平视图"
        assert state["zscore_selected_level"] == "Level 2"
        assert state["zscore_y_axis_mode"] == ZSCORE_FULL_RANGE_VIEW
        assert len(get_zscore_runs(batch_id, template_id)) == 7
        assert not list(at.exception)


def test_entry_save_preserves_overlay_view_after_rerun() -> None:
    with TemporaryDatabaseContext():
        project_id = create_zscore_project("Overlay AppTest Project", level_count=2, input_value_type="ct")
        batch_id = create_zscore_batch(
            project_id=project_id,
            instrument="Overlay Inst",
            reagent="Overlay Reagent",
            qc_material="Overlay QC",
            concentration="Normal",
            lot_no="OVERLAY-LOT",
            target_n=5,
            level_1_label="S1",
            level_2_label="S2",
        )
        template_id = get_template_id_for_level_count(2)
        for hour, values in enumerate(
            [
                (100.0, 150.0),
                (101.0, 151.0),
                (99.5, 149.5),
                (100.2, 150.2),
                (100.1, 150.1),
                (99.9, 149.9),
            ],
            start=0,
        ):
            create_zscore_run(
                batch_id=batch_id,
                test_time=BASE_TIME + pd.Timedelta(hours=hour),
                operator=f"overlay-{hour}",
                level_results=[
                    {"level_id": "Level 1", "raw_value": values[0]},
                    {"level_id": "Level 2", "raw_value": values[1]},
                ],
                template_id=template_id,
                required_n=5,
            )

        at = AppTest.from_string(ZSCORE_PAGE_APPTEST_SCRIPT)
        at.session_state["zscore_selected_project_id"] = project_id
        at.session_state["zscore_selected_batch_id"] = batch_id
        at.run()

        at.radio(key="zscore_phase_scope_widget").set_value("all").run()
        at.radio(key="zscore_view_mode_widget").set_value(ZSCORE_OVERLAY_VIEW).run()
        at.radio(key="zscore_y_axis_mode_widget").set_value(ZSCORE_FULL_RANGE_VIEW).run()
        at.text_input(key="zscore_level1_value").set_value("100.4")
        at.text_input(key="zscore_level2_value").set_value("150.4")
        at.selectbox(key="zscore_entry_operator").set_value("overlay-5")
        at.button(key=ZSCORE_ENTRY_SAVE_BUTTON_KEY).click().run()

        state = at.session_state.filtered_state
        assert state["zscore_phase_scope"] == "all"
        assert state["zscore_view_mode"] == ZSCORE_OVERLAY_VIEW
        assert at.radio(key="zscore_view_mode_widget").value == ZSCORE_OVERLAY_VIEW
        assert state["zscore_y_axis_mode"] == ZSCORE_FULL_RANGE_VIEW
        assert len(get_zscore_runs(batch_id, template_id)) == 7
        assert not list(at.exception)


def test_plotting_all_view_visually_splits_building_and_formal_phases() -> None:
    figure = plot_zscore_single_level(
        build_mixed_phase_plot_df(),
        "Level 1",
        "全图",
        phase_scope="all",
    )
    assert_is_figure(figure)
    _, labels = figure.axes[0].get_legend_handles_labels()
    assert "水平 1 | 建靶期" in labels
    assert "水平 1 | 正式期" in labels
    plt.close(figure)


def test_plotting_all_view_keeps_continuous_trajectory_and_phase_separator() -> None:
    mixed_phase_df = build_mixed_phase_plot_df()
    figure = plot_zscore_single_level(
        mixed_phase_df,
        "Level 1",
        "鍏ㄥ浘",
        phase_scope="all",
    )
    assert_is_figure(figure)
    axis = figure.axes[0]

    continuous_lines = [
        line
        for line in axis.lines
        if list(map(float, line.get_xdata())) == [1.0, 2.0]
        and len({round(float(value), 6) for value in line.get_ydata()}) > 1
    ]
    assert continuous_lines, "鍏ㄥ浘搴斿綋淇濈暀寤洪澏鏈熶笌姝ｅ紡鏈熶箣闂寸殑杩炵画杞ㄨ抗"

    separator_lines = [
        line
        for line in axis.lines
        if len(line.get_xdata()) == 2
        and all(abs(float(value) - 1.5) < 1e-9 for value in line.get_xdata())
    ]
    assert separator_lines, "鍏ㄥ浘搴斿綋鍦ㄥ缓闈惰浆姝ｅ紡鏈熻竟鐣屽缁樺埗闃舵鍒嗛殧绾?"
    plt.close(figure)

    overlay_figure = plot_zscore_overlay(
        mixed_phase_df,
        "鍏ㄥ浘鍚堝苟",
        phase_scope="all",
    )
    assert_is_figure(overlay_figure)
    overlay_axis = overlay_figure.axes[0]
    overlay_separator_lines = [
        line
        for line in overlay_axis.lines
        if len(line.get_xdata()) == 2
        and all(abs(float(value) - 1.5) < 1e-9 for value in line.get_xdata())
    ]
    assert overlay_separator_lines, "鍚堝苟瑙嗗浘鍏ㄥ浘涔熷簲鏄剧ず闃舵鍒嗛殧绾?"
    plt.close(overlay_figure)


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
        test_db_persistence_supports_vendor_targets_and_formal_realtime_stats,
        test_zscore_project_level_count_persistence,
        test_zscore_batch_inherits_level_count,
        test_zscore_level_labels_persist_and_fallback,
        test_level_count_binds_template_and_required_level_ids,
        test_batch_context_auto_shapes_template_by_level_count,
        test_zscore_summary_items_update_with_batch_context,
        test_create_zscore_run_respects_batch_level_count,
        test_plot_phase_filtering_views,
        test_collected_n_matches_building_plot_points,
        test_create_zscore_run_rejects_unexpected_level_for_two_level_batch,
        test_edit_saved_run_rebuilds_targets_realtime_and_status,
        test_delete_saved_run_rebuilds_batch_and_plot_points,
        test_saved_run_maintenance_respects_level_count_for_two_and_three_level_batches,
        test_building_runs_lock_after_batch_enters_formal,
        test_test_sequence_keeps_incrementing_and_feeds_plot_axis,
        test_plotting_uses_raw_value_axis_and_mean_sd_reference_lines,
        test_plotting_supports_standard_and_full_range_views,
        test_single_level_manual_legend_covers_status_phase_and_clip_marker,
        test_overlay_manual_legend_keeps_status_phase_and_level_keys,
        test_delete_feedback_keeps_maintenance_dialog_context,
        test_entry_save_preserves_chart_controls_after_rerun,
        test_entry_save_preserves_overlay_view_after_rerun,
        test_plotting_all_view_visually_splits_building_and_formal_phases,
        test_plotting_all_view_keeps_continuous_trajectory_and_phase_separator,
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
