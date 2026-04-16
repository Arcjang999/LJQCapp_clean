from __future__ import annotations

import sys
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import database
from database import (
    add_result,
    create_batch,
    create_project,
    create_zscore_batch,
    create_zscore_project,
    get_results,
    init_db,
)
from pages.lj_sections import (
    build_lj_building_outlier_panel_data,
    build_lj_workbench_context,
    resolve_lj_latest_analysis_mode,
)
from plotting import _filter_view_data
from qc_logic import (
    calculate_qc_results,
    disable_lj_building_result,
    keep_lj_building_result,
    restore_lj_building_result,
)
from services.outlier_service import get_outlier_manual_status_label, get_outlier_status_label
from zscore_logic import (
    PHASE_TARGET_BUILDING,
    build_zscore_plot_dataframe,
    create_zscore_run,
    disable_zscore_building_run,
    get_zscore_runs,
    keep_zscore_building_run,
    restore_zscore_building_run,
)


class TemporaryDatabaseContext:
    def __enter__(self):
        self._tempdir = TemporaryDirectory()
        self._original_db_path = database.DB_PATH
        self._original_legacy_candidates = list(database.LEGACY_DB_CANDIDATES)
        database.DB_PATH = Path(self._tempdir.name) / "building_outlier_smoke_test.db"
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


def test_lj_building_outlier_actions_and_plot_filter() -> None:
    with TemporaryDatabaseContext():
        project_id = create_project("LJ Building Outlier", input_value_type="raw")
        batch_id = create_batch(
            project_id=project_id,
            instrument="Inst-LJ",
            reagent="Reagent-LJ",
            qc_material="QC-LJ",
            concentration="Normal",
            lot_no="LOT-LJ",
            target_n=6,
        )
        for index, value in enumerate([100.0, 101.0, 99.0, 100.5, 120.0], start=0):
            add_result(
                batch_id=batch_id,
                test_time=f"2026-04-13 08:{index:02d}:00",
                operator=f"tester-{index}",
                value=float(value),
                log_value=None,
                reagent_lot_changed=0,
                manual_note="",
            )

        qc_df, stats = calculate_qc_results(get_results(batch_id, include_manual_note=True), 6)
        suspect_row = qc_df[qc_df["is_outlier_suspect"] == 1].iloc[0]
        suspect_id = int(suspect_row["id"])
        original_mean = float(stats["mean"])
        assert stats["effective_building_count"] == 5
        assert stats["target_ready"] is False

        keep_lj_building_result(suspect_id)
        qc_df_keep, stats_keep = calculate_qc_results(get_results(batch_id, include_manual_note=True), 6)
        kept_row = qc_df_keep[qc_df_keep["id"] == suspect_id].iloc[0]
        assert kept_row["manual_status"] == "keep"
        assert int(kept_row["is_building_included"]) == 1
        assert stats_keep["effective_building_count"] == 5

        disable_lj_building_result(suspect_id)
        qc_df_disabled, stats_disabled = calculate_qc_results(get_results(batch_id, include_manual_note=True), 6)
        disabled_row = qc_df_disabled[qc_df_disabled["id"] == suspect_id].iloc[0]
        building_plot_df = _filter_view_data(qc_df_disabled, "建靶图")
        assert disabled_row["manual_status"] == "disabled"
        assert int(disabled_row["is_building_included"]) == 0
        assert stats_disabled["effective_building_count"] == 4
        assert stats_disabled["mean"] != original_mean
        assert suspect_id not in building_plot_df["id"].tolist()

        restore_lj_building_result(suspect_id)
        qc_df_restored, stats_restored = calculate_qc_results(get_results(batch_id, include_manual_note=True), 6)
        restored_row = qc_df_restored[qc_df_restored["id"] == suspect_id].iloc[0]
        assert restored_row["manual_status"] == "restored"
        assert int(restored_row["is_building_included"]) == 1
        assert stats_restored["effective_building_count"] == 5


def test_lj_latest_analysis_switches_between_building_and_formal() -> None:
    with TemporaryDatabaseContext():
        project_id = create_project("LJ Panel Mode", input_value_type="raw")
        batch_id = create_batch(
            project_id=project_id,
            instrument="Inst-LJ",
            reagent="Reagent-LJ",
            qc_material="QC-LJ",
            concentration="Normal",
            lot_no="LOT-LJ-PANEL",
            target_n=5,
        )
        for index, value in enumerate([100.0, 101.0, 99.0, 100.5], start=0):
            add_result(
                batch_id=batch_id,
                test_time=f"2026-04-13 10:{index:02d}:00",
                operator=f"tester-{index}",
                value=float(value),
                log_value=None,
                reagent_lot_changed=0,
                manual_note="",
            )

        early_project_id = create_project("LJ Panel Early", input_value_type="raw")
        early_batch_id = create_batch(
            project_id=early_project_id,
            instrument="Inst-LJ",
            reagent="Reagent-LJ",
            qc_material="QC-LJ",
            concentration="Normal",
            lot_no="LOT-LJ-EARLY",
            target_n=5,
        )
        for index, value in enumerate([100.0, 101.0], start=0):
            add_result(
                batch_id=early_batch_id,
                test_time=f"2026-04-13 11:{index:02d}:00",
                operator=f"tester-{index}",
                value=float(value),
                log_value=None,
                reagent_lot_changed=0,
                manual_note="",
            )
        early_context = build_lj_workbench_context(early_batch_id)
        early_panel_data = build_lj_building_outlier_panel_data(
            early_context["stats"],
            "最近已保存检测序号 #2",
        )
        assert resolve_lj_latest_analysis_mode(early_context["stats"]) == "building"
        assert early_panel_data["grubbs_ready"] is False

        building_context = build_lj_workbench_context(batch_id)
        building_panel_data = build_lj_building_outlier_panel_data(
            building_context["stats"],
            "最近已保存检测序号 #4",
        )
        assert resolve_lj_latest_analysis_mode(building_context["stats"]) == "building"
        assert building_panel_data["phase_label"] == "建靶期"
        assert building_panel_data["grubbs_ready"] is True
        assert building_panel_data["suspect_details"] is None

        add_result(
            batch_id=batch_id,
            test_time="2026-04-13 10:04:00",
            operator="tester-4",
            value=120.0,
            log_value=None,
            reagent_lot_changed=0,
            manual_note="",
        )
        building_outlier_context = build_lj_workbench_context(batch_id)
        building_outlier_panel_data = build_lj_building_outlier_panel_data(
            building_outlier_context["stats"],
            "最近已保存检测序号 #5",
        )
        assert resolve_lj_latest_analysis_mode(building_outlier_context["stats"]) == "building"
        assert building_outlier_panel_data["suspect_details"] is not None
        assert building_outlier_panel_data["suspect_details"]["status_label"] == "疑似离群"

        add_result(
            batch_id=batch_id,
            test_time="2026-04-13 10:05:00",
            operator="tester-5",
            value=100.2,
            log_value=None,
            reagent_lot_changed=0,
            manual_note="",
        )
        formal_context = build_lj_workbench_context(batch_id)
        assert resolve_lj_latest_analysis_mode(formal_context["stats"]) == "formal"


def test_lj_outlier_labels_are_clean_and_readable() -> None:
    assert get_outlier_status_label("normal") == "正常"
    assert get_outlier_status_label("outlier_suspect") == "疑似离群"
    assert get_outlier_status_label("kept") == "已保留"
    assert get_outlier_status_label("disabled") == "已禁用"
    assert get_outlier_status_label("restored") == "已恢复"
    assert get_outlier_status_label("鐤戜技绂荤兢") == "疑似离群"
    assert get_outlier_manual_status_label("normal") == "未处理"
    assert get_outlier_manual_status_label("keep") == "保留"
    assert get_outlier_manual_status_label("disabled") == "禁用"
    assert get_outlier_manual_status_label("restored") == "恢复"


def test_zscore_run_outlier_actions_recalc_and_plot_filter() -> None:
    with TemporaryDatabaseContext():
        project_id = create_zscore_project("Z-score Building Outlier", level_count=2, input_value_type="raw")
        batch_id = create_zscore_batch(
            project_id=project_id,
            instrument="Inst-Z",
            reagent="Reagent-Z",
            qc_material="QC-Z",
            concentration="Normal",
            lot_no="LOT-Z",
            target_n=5,
        )
        level_pairs = [
            (100.0, 150.0),
            (101.0, 149.5),
            (99.0, 150.5),
            (100.5, 150.2),
            (120.0, 150.1),
        ]
        for index, (level_1_value, level_2_value) in enumerate(level_pairs, start=0):
            create_zscore_run(
                batch_id=batch_id,
                test_time=f"2026-04-13 09:{index:02d}:00",
                operator=f"tester-{index}",
                template_id="2_level_classic",
                required_n=5,
                level_results=[
                    {"level_id": "Level 1", "raw_value": float(level_1_value), "log_value": None},
                    {"level_id": "Level 2", "raw_value": float(level_2_value), "log_value": None},
                ],
            )

        saved_runs = get_zscore_runs(batch_id, "2_level_classic")
        suspect_level_result = next(
            level_result
            for run in saved_runs
            for level_result in run["level_results"]
            if level_result["level_id"] == "Level 1" and int(level_result.get("is_outlier_suspect", 0) or 0) == 1
        )
        suspect_run_id = int(suspect_level_result["run_id"])

        keep_state = keep_zscore_building_run(suspect_run_id)
        kept_run = next(run for run in keep_state["runs"] if int(run["run_id"]) == suspect_run_id)
        assert all(level_result["manual_status"] == "keep" for level_result in kept_run["level_results"])
        assert all(int(level_result["is_building_included"]) == 1 for level_result in kept_run["level_results"])

        disable_state = disable_zscore_building_run(suspect_run_id)
        disabled_run = next(run for run in disable_state["runs"] if int(run["run_id"]) == suspect_run_id)
        plot_df = build_zscore_plot_dataframe(disable_state["runs"])
        assert disable_state["overall_phase"] == PHASE_TARGET_BUILDING
        assert disable_state["target_profiles"]["Level 1"]["collected_n"] == 4
        assert disable_state["target_profiles"]["Level 2"]["collected_n"] == 4
        assert all(level_result["manual_status"] == "disabled" for level_result in disabled_run["level_results"])
        assert all(int(level_result["is_building_included"]) == 0 for level_result in disabled_run["level_results"])
        assert not (plot_df["run_id"] == suspect_run_id).any()

        restore_state = restore_zscore_building_run(suspect_run_id)
        restored_run = next(run for run in restore_state["runs"] if int(run["run_id"]) == suspect_run_id)
        assert restore_state["target_profiles"]["Level 1"]["collected_n"] == 5
        assert restore_state["target_profiles"]["Level 2"]["collected_n"] == 5
        assert all(level_result["manual_status"] == "restored" for level_result in restored_run["level_results"])
        assert all(int(level_result["is_building_included"]) == 1 for level_result in restored_run["level_results"])


if __name__ == "__main__":
    test_lj_building_outlier_actions_and_plot_filter()
    test_lj_latest_analysis_switches_between_building_and_formal()
    test_lj_outlier_labels_are_clean_and_readable()
    test_zscore_run_outlier_actions_recalc_and_plot_filter()
    print("building_outlier_smoke_test passed")
