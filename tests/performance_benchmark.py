from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import database
from database import (
    add_result,
    add_zscore_level_results as db_add_zscore_level_results,
    add_zscore_run as db_add_zscore_run,
    create_batch,
    create_project,
    create_zscore_batch,
    create_zscore_project,
    init_db,
)
from pages.lj_sections import build_lj_workbench_context
from pages.zscore_sections import (
    build_zscore_phase_export_dataframe,
    build_zscore_workbench_context,
)
from plotting import close_figure, plot_lj_chart
from services.export_utils import dataframe_to_csv_bytes, dataframe_to_xlsx_bytes
from zscore_logic import build_zscore_plot_dataframe, create_zscore_run, get_zscore_runs
from zscore_plotting import plot_zscore_overlay, plot_zscore_single_level


BASE_TIME = pd.Timestamp("2026-05-01 08:00:00")


@contextmanager
def temporary_database():
    tempdir = TemporaryDirectory()
    original_db_path = database.DB_PATH
    original_legacy_candidates = list(database.LEGACY_DB_CANDIDATES)
    database.DB_PATH = Path(tempdir.name) / "performance_benchmark.db"
    database.LEGACY_DB_CANDIDATES = []
    init_db()
    try:
        yield
    finally:
        database.DB_PATH = original_db_path
        database.LEGACY_DB_CANDIDATES = original_legacy_candidates
        try:
            tempdir.cleanup()
        except PermissionError:
            pass


def measure(label: str, callback):
    start = perf_counter()
    result = callback()
    elapsed_ms = (perf_counter() - start) * 1000
    print(f"{label}: {elapsed_ms:.2f} ms")
    return result


def _counts() -> list[int]:
    raw_value = os.getenv("LJQC_BENCH_COUNTS", "20,200,2000")
    return [int(item.strip()) for item in raw_value.split(",") if item.strip()]


def _create_lj_fixture(record_count: int) -> int:
    project_id = create_project(f"Bench LJ {record_count}", input_value_type="raw")
    batch_id = create_batch(
        project_id=project_id,
        instrument="Bench Inst",
        reagent="Bench Reagent",
        qc_material="Bench QC",
        concentration="Normal",
        lot_no=f"LJ-{record_count}",
        target_n=20,
    )
    for index in range(record_count):
        add_result(
            batch_id=batch_id,
            test_time=(BASE_TIME + pd.Timedelta(hours=index)).strftime("%Y-%m-%d %H:%M:%S"),
            operator="bench",
            value=100.0 + ((index % 9) - 4) * 0.2,
            reagent_lot_changed=1 if index and index % 200 == 0 else 0,
        )
    return batch_id


def _create_zscore_fixture(record_count: int) -> int:
    project_id = create_zscore_project(f"Bench Z {record_count}", level_count=2, input_value_type="raw")
    batch_id = create_zscore_batch(
        project_id=project_id,
        instrument="Bench Inst",
        reagent="Bench Reagent",
        qc_material="Bench QC",
        concentration="Normal",
        lot_no=f"ZS-{record_count}",
        target_n=20,
    )
    building_count = min(record_count, 20)
    for index in range(building_count):
        create_zscore_run(
            batch_id=batch_id,
            test_time=BASE_TIME + pd.Timedelta(hours=index),
            operator="bench",
            level_results=[
                {"level_id": "Level 1", "raw_value": 100.0 + ((index % 7) - 3) * 0.15},
                {"level_id": "Level 2", "raw_value": 150.0 + ((index % 5) - 2) * 0.2},
            ],
            template_id="2_level_classic",
            required_n=20,
        )
    if record_count <= building_count:
        return batch_id

    for index in range(building_count, record_count):
        level_1_raw = 100.0 + ((index % 7) - 3) * 0.15
        level_2_raw = 150.0 + ((index % 5) - 2) * 0.2
        run_id = db_add_zscore_run(
            batch_id=batch_id,
            project_id=project_id,
            test_sequence=index + 1,
            test_time=(BASE_TIME + pd.Timedelta(hours=index)).strftime("%Y-%m-%d %H:%M:%S"),
            operator="bench",
            level_count=2,
            phase="formal_qc",
            run_status="accept",
            rule_template_id="2_level_classic",
            rule_hits_run=[],
            error_type_hint="unknown",
            analysis_prompt="benchmark seeded formal run",
            manual_note="",
        )
        db_add_zscore_level_results(
            run_id,
            [
                {
                    "level_id": "Level 1",
                    "raw_value": level_1_raw,
                    "log_value": None,
                    "zscore": (level_1_raw - 100.0) / 5.0,
                    "status": "accept",
                    "rule_hits_local": [],
                    "is_in_control_for_realtime_stats": True,
                },
                {
                    "level_id": "Level 2",
                    "raw_value": level_2_raw,
                    "log_value": None,
                    "zscore": (level_2_raw - 150.0) / 7.5,
                    "status": "accept",
                    "rule_hits_local": [],
                    "is_in_control_for_realtime_stats": True,
                },
            ],
        )
    return batch_id


def run_lj_benchmark(record_count: int) -> None:
    batch_id = _create_lj_fixture(record_count)
    context = measure("  enter_lj_current_batch", lambda: build_lj_workbench_context(batch_id))
    measure(
        "  add_lj_result",
        lambda: (
            add_result(
                batch_id=batch_id,
                test_time=(BASE_TIME + pd.Timedelta(hours=record_count + 1)).strftime("%Y-%m-%d %H:%M:%S"),
                operator="bench",
                value=100.1,
            ),
            build_lj_workbench_context(batch_id),
        ),
    )
    figure = measure(
        "  switch_lj_chart_view",
        lambda: plot_lj_chart(
            context["qc_df"],
            context["stats"],
            "benchmark",
            view_mode="正式质控图",
            y_axis_label=context["input_value_type_label"],
        ),
    )
    close_figure(figure)
    measure(
        "  open_lj_export_area_lazy_gate",
        lambda: (
            len(context["qc_df"]),
            bool(context["stats"].get("target_ready")),
        ),
    )
    measure(
        "  prepare_lj_export_bytes",
        lambda: (
            dataframe_to_csv_bytes(context["qc_df"].head(20)),
            dataframe_to_xlsx_bytes(context["qc_df"].head(20)),
        ),
    )


def run_zscore_benchmark(record_count: int) -> None:
    batch_id = _create_zscore_fixture(record_count)
    context = measure("  enter_zscore_current_batch", lambda: build_zscore_workbench_context(batch_id))
    measure(
        "  add_zscore_run",
        lambda: create_zscore_run(
            batch_id=batch_id,
            test_time=BASE_TIME + pd.Timedelta(hours=record_count + 1),
            operator="bench",
            level_results=[
                {"level_id": "Level 1", "raw_value": 100.2},
                {"level_id": "Level 2", "raw_value": 150.2},
            ],
            template_id="2_level_classic",
            required_n=20,
        ),
    )
    runs = get_zscore_runs(batch_id, "2_level_classic")
    plot_df = build_zscore_plot_dataframe(runs)
    figure = measure(
        "  switch_zscore_single_view",
        lambda: plot_zscore_single_level(plot_df, "Level 1", "benchmark"),
    )
    close_figure(figure)
    figure = measure(
        "  switch_zscore_overlay_view",
        lambda: plot_zscore_overlay(plot_df, "benchmark", active_levels=["Level 1", "Level 2"]),
    )
    close_figure(figure)
    measure(
        "  open_zscore_export_area_lazy_gate",
        lambda: (
            len(context["history_runs"]),
            len(context["plot_df"]),
        ),
    )
    measure(
        "  prepare_zscore_export_bytes",
        lambda: (
            dataframe_to_csv_bytes(
                build_zscore_phase_export_dataframe(runs, ["Level 1", "Level 2"], "formal", "raw").head(20)
            ),
            dataframe_to_xlsx_bytes(
                build_zscore_phase_export_dataframe(runs, ["Level 1", "Level 2"], "formal", "raw").head(20)
            ),
        ),
    )


def main() -> None:
    for record_count in _counts():
        print(f"\nrecord_count={record_count}")
        with temporary_database():
            run_lj_benchmark(record_count)
            run_zscore_benchmark(record_count)


if __name__ == "__main__":
    main()
