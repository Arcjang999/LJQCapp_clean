from __future__ import annotations

import math
import sqlite3
import gc
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

import database
from database import (
    add_instant_result,
    add_result,
    create_batch,
    create_instant_batch,
    create_instant_project,
    create_project,
    create_zscore_batch,
    create_zscore_project,
    get_batch,
    get_connection,
    get_db_path,
    get_instant_results,
    get_results,
    init_db,
    reset_database,
)
from qc_logic import (
    LJ_BUILDING_PHASE_LABEL,
    LJ_FORMAL_PHASE_LABEL,
    calculate_qc_results,
    persist_lj_batch_outlier_snapshot,
)
from services.instant_service import build_instant_workbench_context, persist_instant_batch_analysis
from zscore_logic import (
    PHASE_FORMAL_QC,
    PHASE_TARGET_BUILDING,
    create_zscore_run,
    disable_zscore_building_run,
    get_template_id_for_level_count,
    get_zscore_level_targets,
    get_zscore_runs,
)


DEMO_PREFIX = "【演示】"
DEMO_NOTICE = "仅演示，请勿用于真实质控"
DEFAULT_PROFILE = "full"
PROFILE_CHOICES = {"basic", "full"}


@dataclass(frozen=True)
class DemoDatasetSpec:
    key: str
    name: str
    method: str
    level_count: int = 1


FULL_DATASETS: tuple[DemoDatasetSpec, ...] = (
    DemoDatasetSpec("instant_01", "【演示】Instant-01 20点累计-可转入LJ", "instant"),
    DemoDatasetSpec("instant_02", "【演示】Instant-02 20点累计-SI疑似离群", "instant"),
    DemoDatasetSpec("lj_01", "【演示】LJ-01 20点建靶完成-待正式期", "lj"),
    DemoDatasetSpec("lj_02", "【演示】LJ-02 20点建靶完成-正式期在控", "lj"),
    DemoDatasetSpec("lj_03", "【演示】LJ-03 20点建靶完成-随机误差规则：1_2s/1_3s/R_4s", "lj"),
    DemoDatasetSpec("lj_04", "【演示】LJ-04 20点建靶完成-系统误差规则：2_2s/4_1s/10x", "lj"),
    DemoDatasetSpec("z2_01", "【演示】Z2-01 双水平20点建靶-正式期在控", "zscore", 2),
    DemoDatasetSpec("z2_02", "【演示】Z2-02 双水平20点建靶-正式期规则：1_2s/1_3s/2_2s/R_4s/4_1s/10_x", "zscore", 2),
    DemoDatasetSpec("z2_03", "【演示】Z2-03 双水平20点建靶-建靶离群处理", "zscore", 2),
    DemoDatasetSpec("z3_01", "【演示】Z3-01 三水平20点建靶-正式期在控", "zscore", 3),
    DemoDatasetSpec("z3_02", "【演示】Z3-02 三水平20点建靶-正式期规则：2of3_2s/R_4s/3_1s/12_x", "zscore", 3),
    DemoDatasetSpec("z3_03", "【演示】Z3-03 三水平20点建靶-建靶离群处理", "zscore", 3),
)

BASIC_KEYS = {"instant_01", "lj_01", "lj_02", "lj_03", "z2_01", "z3_02"}
SPEC_BY_KEY = {spec.key: spec for spec in FULL_DATASETS}
SPEC_BY_NAME = {spec.name: spec for spec in FULL_DATASETS}

BUILDING_Z_OFFSETS = [
    -0.72,
    -0.28,
    0.16,
    0.54,
    -0.46,
    0.82,
    -0.12,
    0.34,
    -0.62,
    0.08,
    0.68,
    -0.38,
    0.24,
    -0.84,
    0.44,
    0.02,
    -0.18,
    0.76,
    -0.56,
    0.30,
]

OPERATOR_NAMES = ("演示员A", "演示员B", "演示员C")


def _normalize_profile(profile: str | None) -> str:
    normalized = str(profile or DEFAULT_PROFILE).strip().lower()
    if normalized not in PROFILE_CHOICES:
        raise ValueError("profile 只支持 basic 或 full")
    return normalized


def _specs_for_profile(profile: str | None) -> list[DemoDatasetSpec]:
    normalized = _normalize_profile(profile)
    if normalized == "basic":
        return [spec for spec in FULL_DATASETS if spec.key in BASIC_KEYS]
    return list(FULL_DATASETS)


def _demo_lot_no(spec: DemoDatasetSpec) -> str:
    return f"{spec.name} 批次-{DEMO_NOTICE}"


def _operator(index: int) -> str:
    return OPERATOR_NAMES[index % len(OPERATOR_NAMES)]


def _time_series(start: datetime, count: int) -> list[str]:
    return [
        (start + timedelta(days=index)).strftime("%Y-%m-%d %H:%M:%S")
        for index in range(count)
    ]


def _round(value: float, digits: int = 4) -> float:
    return float(round(float(value), digits))


def _values_from_z(mean: float, sd: float, z_values: list[float]) -> list[float]:
    return [_round(mean + sd * z_value) for z_value in z_values]


def _count_table(connection: sqlite3.Connection, table_name: str) -> int:
    try:
        row = connection.execute(f"SELECT COUNT(1) AS row_count FROM {table_name}").fetchone()
    except sqlite3.Error:
        return 0
    return int(row["row_count"] if row is not None else 0)


def _open_readonly_connection(db_path: Path) -> sqlite3.Connection | None:
    resolved = Path(db_path).expanduser()
    if not resolved.exists():
        return None
    connection = sqlite3.connect(f"file:{resolved}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _demo_delete_counts(connection: sqlite3.Connection) -> dict[str, int]:
    pattern = f"{DEMO_PREFIX}%"
    counts: dict[str, int] = {
        "projects": 0,
        "instant_projects": 0,
        "batches": 0,
        "results": 0,
        "zscore_runs": 0,
        "zscore_level_results": 0,
        "instant_batches": 0,
        "instant_results": 0,
        "report_exports": 0,
    }
    existing_tables = {
        str(row["name"])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    if "projects" not in existing_tables and "instant_projects" not in existing_tables:
        return counts
    if "projects" not in existing_tables:
        project_ids = []
    else:
        project_ids = [
            int(row["id"])
            for row in connection.execute(
                "SELECT id FROM projects WHERE name LIKE ?",
                (pattern,),
            ).fetchall()
        ]
    if "instant_projects" not in existing_tables:
        instant_project_ids = []
    else:
        instant_project_ids = [
            int(row["id"])
            for row in connection.execute(
                "SELECT id FROM instant_projects WHERE name LIKE ?",
                (pattern,),
            ).fetchall()
        ]
    counts["projects"] = len(project_ids)
    counts["instant_projects"] = len(instant_project_ids)

    if project_ids and "batches" in existing_tables:
        placeholders = ", ".join("?" for _ in project_ids)
        batch_ids = [
            int(row["id"])
            for row in connection.execute(
                f"SELECT id FROM batches WHERE project_id IN ({placeholders})",
                tuple(project_ids),
            ).fetchall()
        ]
        counts["batches"] = len(batch_ids)
        if batch_ids:
            batch_placeholders = ", ".join("?" for _ in batch_ids)
            if "results" in existing_tables:
                counts["results"] = int(
                    connection.execute(
                        f"SELECT COUNT(1) AS row_count FROM results WHERE batch_id IN ({batch_placeholders})",
                        tuple(batch_ids),
                    ).fetchone()["row_count"]
                )
            if "zscore_runs" in existing_tables:
                run_ids = [
                    int(row["id"])
                    for row in connection.execute(
                        f"SELECT id FROM zscore_runs WHERE batch_id IN ({batch_placeholders})",
                        tuple(batch_ids),
                    ).fetchall()
                ]
            else:
                run_ids = []
            counts["zscore_runs"] = len(run_ids)
            if run_ids and "zscore_level_results" in existing_tables:
                run_placeholders = ", ".join("?" for _ in run_ids)
                counts["zscore_level_results"] = int(
                    connection.execute(
                        f"SELECT COUNT(1) AS row_count FROM zscore_level_results WHERE run_id IN ({run_placeholders})",
                        tuple(run_ids),
                    ).fetchone()["row_count"]
                )
            if "report_exports" in existing_tables:
                counts["report_exports"] = int(
                    connection.execute(
                        f"SELECT COUNT(1) AS row_count FROM report_exports WHERE batch_id IN ({batch_placeholders})",
                        tuple(batch_ids),
                    ).fetchone()["row_count"]
                )

    if instant_project_ids and "instant_batches" in existing_tables:
        placeholders = ", ".join("?" for _ in instant_project_ids)
        instant_batch_ids = [
            int(row["id"])
            for row in connection.execute(
                f"SELECT id FROM instant_batches WHERE project_id IN ({placeholders})",
                tuple(instant_project_ids),
            ).fetchall()
        ]
        counts["instant_batches"] = len(instant_batch_ids)
        if instant_batch_ids and "instant_results" in existing_tables:
            batch_placeholders = ", ".join("?" for _ in instant_batch_ids)
            counts["instant_results"] = int(
                connection.execute(
                    f"SELECT COUNT(1) AS row_count FROM instant_results WHERE batch_id IN ({batch_placeholders})",
                    tuple(instant_batch_ids),
                ).fetchone()["row_count"]
            )
    return counts


def _database_totals(connection: sqlite3.Connection) -> dict[str, int]:
    return {
        "projects": _count_table(connection, "projects"),
        "batches": _count_table(connection, "batches"),
        "results": _count_table(connection, "results"),
        "zscore_runs": _count_table(connection, "zscore_runs"),
        "zscore_level_results": _count_table(connection, "zscore_level_results"),
        "instant_projects": _count_table(connection, "instant_projects"),
        "instant_batches": _count_table(connection, "instant_batches"),
        "instant_results": _count_table(connection, "instant_results"),
    }


def _non_demo_fingerprint(connection: sqlite3.Connection) -> dict[str, int]:
    pattern = f"{DEMO_PREFIX}%"
    values: dict[str, int] = {}
    values["projects"] = int(
        connection.execute(
            "SELECT COUNT(1) AS row_count FROM projects WHERE name NOT LIKE ?",
            (pattern,),
        ).fetchone()["row_count"]
    )
    values["instant_projects"] = int(
        connection.execute(
            "SELECT COUNT(1) AS row_count FROM instant_projects WHERE name NOT LIKE ?",
            (pattern,),
        ).fetchone()["row_count"]
    )
    values["batches"] = int(
        connection.execute(
            """
            SELECT COUNT(1) AS row_count
            FROM batches
            INNER JOIN projects ON projects.id = batches.project_id
            WHERE projects.name NOT LIKE ?
            """,
            (pattern,),
        ).fetchone()["row_count"]
    )
    values["instant_batches"] = int(
        connection.execute(
            """
            SELECT COUNT(1) AS row_count
            FROM instant_batches
            INNER JOIN instant_projects ON instant_projects.id = instant_batches.project_id
            WHERE instant_projects.name NOT LIKE ?
            """,
            (pattern,),
        ).fetchone()["row_count"]
    )
    values["results"] = int(
        connection.execute(
            """
            SELECT COUNT(1) AS row_count
            FROM results
            INNER JOIN batches ON batches.id = results.batch_id
            INNER JOIN projects ON projects.id = batches.project_id
            WHERE projects.name NOT LIKE ?
            """,
            (pattern,),
        ).fetchone()["row_count"]
    )
    values["instant_results"] = int(
        connection.execute(
            """
            SELECT COUNT(1) AS row_count
            FROM instant_results
            INNER JOIN instant_projects ON instant_projects.id = instant_results.project_id
            WHERE instant_projects.name NOT LIKE ?
            """,
            (pattern,),
        ).fetchone()["row_count"]
    )
    values["zscore_runs"] = int(
        connection.execute(
            """
            SELECT COUNT(1) AS row_count
            FROM zscore_runs
            INNER JOIN projects ON projects.id = zscore_runs.project_id
            WHERE projects.name NOT LIKE ?
            """,
            (pattern,),
        ).fetchone()["row_count"]
    )
    values["zscore_level_results"] = int(
        connection.execute(
            """
            SELECT COUNT(1) AS row_count
            FROM zscore_level_results
            INNER JOIN zscore_runs ON zscore_runs.id = zscore_level_results.run_id
            INNER JOIN projects ON projects.id = zscore_runs.project_id
            WHERE projects.name NOT LIKE ?
            """,
            (pattern,),
        ).fetchone()["row_count"]
    )
    return values


def build_demo_plan(profile: str = DEFAULT_PROFILE) -> dict[str, Any]:
    specs = _specs_for_profile(profile)
    datasets = []
    for spec in specs:
        if spec.method == "instant":
            building_records = 20
            formal_records = 0
            raw_records = 20
        elif spec.method == "lj":
            building_records = 20
            formal_map = {"lj_01": 0, "lj_02": 12, "lj_03": 9, "lj_04": 12}
            formal_records = formal_map.get(spec.key, 0)
            raw_records = building_records + formal_records
        else:
            building_records = 21 if spec.key in {"z2_03", "z3_03"} else 20
            formal_map = {"z2_01": 12, "z2_02": 12, "z2_03": 0, "z3_01": 12, "z3_02": 15, "z3_03": 0}
            formal_records = formal_map.get(spec.key, 0)
            raw_records = (building_records + formal_records) * spec.level_count
        datasets.append(
            {
                "key": spec.key,
                "name": spec.name,
                "method": spec.method,
                "level_count": spec.level_count,
                "batch_lot_no": _demo_lot_no(spec),
                "target_n": 20 if spec.method != "instant" else None,
                "building_records": building_records,
                "formal_records": formal_records,
                "raw_record_count": raw_records,
                "notice": DEMO_NOTICE,
            }
        )
    return {
        "profile": _normalize_profile(profile),
        "dataset_count": len(datasets),
        "datasets": datasets,
    }


def delete_demo_data(dry_run: bool = False) -> dict[str, Any]:
    db_path = Path(get_db_path()).resolve()
    if dry_run:
        connection = _open_readonly_connection(db_path)
        if connection is None:
            counts = {}
        else:
            with connection:
                counts = _demo_delete_counts(connection)
        return {
            "operation": "delete-demo",
            "dry_run": True,
            "db_path": str(db_path),
            "deleted": counts,
            "message": "dry-run：未删除任何数据",
        }

    init_db()
    with get_connection() as connection:
        before_counts = _demo_delete_counts(connection)
        pattern = f"{DEMO_PREFIX}%"
        connection.execute("DELETE FROM projects WHERE name LIKE ?", (pattern,))
        connection.execute("DELETE FROM instant_projects WHERE name LIKE ?", (pattern,))
    return {
        "operation": "delete-demo",
        "dry_run": False,
        "db_path": str(db_path),
        "deleted": before_counts,
        "message": "已删除所有【演示】前缀的数据，非演示数据未触碰",
    }


def _create_lj_dataset(spec: DemoDatasetSpec) -> dict[str, Any]:
    project_id = create_project(spec.name, input_value_type="raw")
    batch_id = create_batch(
        project_id=project_id,
        instrument=f"{DEMO_PREFIX} 全自动分析仪-{DEMO_NOTICE}",
        reagent=f"{DEMO_PREFIX} LJ演示试剂",
        qc_material=f"{DEMO_PREFIX} LJ演示质控品",
        concentration="单水平",
        lot_no=_demo_lot_no(spec),
        target_n=20,
    )
    base_mean = {
        "lj_01": 100.0,
        "lj_02": 42.0,
        "lj_03": 28.0,
        "lj_04": 65.0,
    }[spec.key]
    base_sd = {
        "lj_01": 1.6,
        "lj_02": 0.9,
        "lj_03": 0.55,
        "lj_04": 1.1,
    }[spec.key]
    timestamps = _time_series(datetime(2026, 1, 1, 8, 30), 20)
    for index, (test_time, value) in enumerate(zip(timestamps, _values_from_z(base_mean, base_sd, BUILDING_Z_OFFSETS), strict=True)):
        add_result(
            batch_id=batch_id,
            test_time=test_time,
            value=value,
            operator=_operator(index),
            manual_note=DEMO_NOTICE if index == 0 else "",
        )

    qc_df, stats = persist_lj_batch_outlier_snapshot(batch_id)
    mean = float(stats["mean"])
    sd = float(stats["sd"])
    formal_z_map = {
        "lj_01": [],
        "lj_02": [0.2, -0.4, 0.6, -0.3, 0.8, -0.7, 0.4, -0.2, 0.7, -0.5, 0.1, -0.6],
        "lj_03": [0.2, 2.2, -0.3, 3.3, -3.2, 0.1, -2.2, 2.3, -0.4],
        "lj_04": [0.5, 0.6, 0.7, 0.8, 1.2, 1.3, 1.4, 1.5, 2.2, 2.3, 0.6, 0.7],
    }
    formal_z = formal_z_map[spec.key]
    formal_times = _time_series(datetime(2026, 2, 1, 8, 30), len(formal_z))
    for index, (test_time, value) in enumerate(zip(formal_times, _values_from_z(mean, sd, formal_z), strict=True), start=20):
        add_result(
            batch_id=batch_id,
            test_time=test_time,
            value=value,
            operator=_operator(index),
            manual_note=DEMO_NOTICE if index == 20 else "",
        )
    qc_df, stats = persist_lj_batch_outlier_snapshot(batch_id)
    formal_df = qc_df[qc_df["phase"] == LJ_FORMAL_PHASE_LABEL].copy()
    return {
        "key": spec.key,
        "project_id": project_id,
        "batch_id": batch_id,
        "project_name": spec.name,
        "batch_lot_no": _demo_lot_no(spec),
        "method": "lj",
        "building_records": int((qc_df["phase"] == LJ_BUILDING_PHASE_LABEL).sum()),
        "formal_records": int(len(formal_df)),
        "raw_record_count": int(len(qc_df)),
        "rule_hits": _collect_lj_rule_hits(formal_df),
    }


def _instant_values_for_key(key: str) -> list[float]:
    if key == "instant_01":
        return [
            28.04,
            28.12,
            28.00,
            28.16,
            28.08,
            28.20,
            28.10,
            28.14,
            28.06,
            28.18,
            28.11,
            28.15,
            28.07,
            28.19,
            28.09,
            28.13,
            28.05,
            28.17,
            28.10,
            28.14,
        ]
    return [
        30.00,
        30.04,
        29.98,
        30.02,
        29.96,
        30.03,
        29.99,
        30.01,
        30.05,
        29.97,
        30.02,
        29.95,
        30.04,
        30.01,
        29.99,
        30.03,
        29.98,
        30.00,
        30.02,
        34.20,
    ]


def _create_instant_dataset(spec: DemoDatasetSpec) -> dict[str, Any]:
    project_id = create_instant_project(spec.name, input_value_type="ct")
    batch_id = create_instant_batch(
        project_id=project_id,
        instrument=f"{DEMO_PREFIX} PCR仪-{DEMO_NOTICE}",
        reagent=f"{DEMO_PREFIX} Instant演示试剂",
        qc_material=f"{DEMO_PREFIX} Instant演示质控品",
        concentration="单水平",
        lot_no=_demo_lot_no(spec),
    )
    timestamps = _time_series(datetime(2026, 1, 1, 9, 10), 20)
    for index, (test_time, value) in enumerate(zip(timestamps, _instant_values_for_key(spec.key), strict=True)):
        add_instant_result(
            batch_id=batch_id,
            test_time=test_time,
            operator=_operator(index),
            value=value,
            log_value=None,
            manual_note=DEMO_NOTICE if index == 0 else "",
        )
        persist_instant_batch_analysis(batch_id)
    context = build_instant_workbench_context(batch_id)
    summary = context["summary"]
    return {
        "key": spec.key,
        "project_id": project_id,
        "batch_id": batch_id,
        "project_name": spec.name,
        "batch_lot_no": _demo_lot_no(spec),
        "method": "instant",
        "building_records": int(summary.get("effective_count", 0) or 0),
        "formal_records": 0,
        "raw_record_count": int(summary.get("total_count", 0) or 0),
        "si_status": str(summary.get("si_status") or ""),
        "outlier_suspect_count": int(summary.get("outlier_suspect_total_count", 0) or 0),
    }


def _stable_z_map(level_count: int, run_index: int) -> dict[str, float]:
    level_ids = [f"Level {index}" for index in range(1, level_count + 1)]
    base_patterns = {
        "Level 1": [0.2, -0.4, 0.6, -0.3, 0.8, -0.7, 0.4, -0.2, 0.7, -0.5, 0.1, -0.6],
        "Level 2": [-0.3, 0.5, -0.6, 0.2, -0.7, 0.6, -0.4, 0.3, -0.5, 0.4, -0.2, 0.1],
        "Level 3": [0.4, -0.2, 0.3, -0.5, 0.6, -0.4, 0.2, -0.1, 0.5, -0.6, 0.3, -0.2],
    }
    return {level_id: base_patterns[level_id][run_index % len(base_patterns[level_id])] for level_id in level_ids}


def _formal_z_maps_for_key(key: str, level_count: int) -> list[dict[str, float]]:
    if key in {"z2_01", "z3_01"}:
        return [_stable_z_map(level_count, index) for index in range(12)]
    if key == "z2_02":
        return [
            {"Level 1": 0.5, "Level 2": -0.4},
            {"Level 1": 0.6, "Level 2": 0.3},
            {"Level 1": 1.2, "Level 2": 0.2},
            {"Level 1": 1.3, "Level 2": -0.3},
            {"Level 1": 1.4, "Level 2": 0.4},
            {"Level 1": 1.5, "Level 2": -0.2},
            {"Level 1": 2.2, "Level 2": 0.1},
            {"Level 1": 2.3, "Level 2": 0.2},
            {"Level 1": 3.2, "Level 2": -1.25},
            {"Level 1": 0.7, "Level 2": 0.2},
            {"Level 1": -0.5, "Level 2": 0.4},
            {"Level 1": 0.4, "Level 2": -0.4},
        ]
    if key == "z3_02":
        return [
            {"Level 1": 0.4, "Level 2": 0.2, "Level 3": -0.2},
            {"Level 1": 0.5, "Level 2": -0.3, "Level 3": 0.3},
            {"Level 1": 1.2, "Level 2": 1.1, "Level 3": 1.3},
            {"Level 1": 1.3, "Level 2": 0.6, "Level 3": 0.4},
            {"Level 1": 1.4, "Level 2": 0.2, "Level 3": 0.5},
            {"Level 1": 1.5, "Level 2": -2.4, "Level 3": 2.1},
            {"Level 1": 1.6, "Level 2": 2.2, "Level 3": 2.3},
            {"Level 1": 1.1, "Level 2": 0.4, "Level 3": 0.3},
            {"Level 1": 0.9, "Level 2": -0.2, "Level 3": 0.1},
            {"Level 1": 0.8, "Level 2": 0.2, "Level 3": -0.2},
            {"Level 1": 0.7, "Level 2": 0.1, "Level 3": 0.2},
            {"Level 1": 0.6, "Level 2": 0.3, "Level 3": -0.1},
            {"Level 1": -0.5, "Level 2": 0.2, "Level 3": 0.4},
            {"Level 1": 0.4, "Level 2": -0.3, "Level 3": 0.2},
            {"Level 1": 0.5, "Level 2": 0.3, "Level 3": -0.4},
        ]
    return []


def _building_raw_map(level_count: int, run_index: int, *, outlier: bool = False) -> dict[str, float]:
    means = {"Level 1": 24.0, "Level 2": 48.0, "Level 3": 96.0}
    sds = {"Level 1": 0.62, "Level 2": 1.15, "Level 3": 2.05}
    level_ids = [f"Level {index}" for index in range(1, level_count + 1)]
    values: dict[str, float] = {}
    for level_position, level_id in enumerate(level_ids):
        offset_index = (run_index + level_position * 3) % len(BUILDING_Z_OFFSETS)
        z_value = BUILDING_Z_OFFSETS[offset_index]
        if outlier:
            z_value = 6.8 if level_id == "Level 1" else 0.35
        values[level_id] = _round(means[level_id] + sds[level_id] * z_value)
    return values


def _raw_map_from_target_z(
    batch_id: int,
    template_id: str,
    z_map: dict[str, float],
) -> dict[str, float]:
    targets = get_zscore_level_targets(batch_id, template_id, required_n=20)
    values: dict[str, float] = {}
    for level_id, z_value in z_map.items():
        target = targets[level_id]
        mean = float(target["final_target_mean"])
        sd = float(target["final_target_sd"])
        values[level_id] = _round(mean + sd * float(z_value), digits=5)
    return values


def _create_zscore_dataset(spec: DemoDatasetSpec) -> dict[str, Any]:
    project_id = create_zscore_project(spec.name, level_count=spec.level_count, input_value_type="raw")
    batch_id = create_zscore_batch(
        project_id=project_id,
        instrument=f"{DEMO_PREFIX} 多水平分析仪-{DEMO_NOTICE}",
        reagent=f"{DEMO_PREFIX} Z-score演示试剂",
        qc_material=f"{DEMO_PREFIX} Z-score演示质控品",
        concentration=f"{spec.level_count}水平",
        lot_no=_demo_lot_no(spec),
        target_n=20,
        level_1_label="Level 1",
        level_2_label="Level 2",
        level_3_label="Level 3" if spec.level_count == 3 else None,
    )
    template_id = get_template_id_for_level_count(spec.level_count)
    build_times = _time_series(datetime(2026, 1, 1, 10, 20), 20)
    outlier_run_id: int | None = None
    for index, test_time in enumerate(build_times):
        raw_map = _building_raw_map(spec.level_count, index)
        run = create_zscore_run(
            batch_id=batch_id,
            test_time=test_time,
            operator=_operator(index),
            template_id=template_id,
            required_n=20,
            manual_note=DEMO_NOTICE if index == 0 else "",
            level_results=[
                {"level_id": level_id, "raw_value": raw_map[level_id]}
                for level_id in sorted(raw_map)
            ],
        )
        if index == 9 and spec.key in {"z2_03", "z3_03"}:
            outlier_time = (datetime(2026, 1, 1, 10, 20) + timedelta(days=index, hours=4)).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            outlier_map = _building_raw_map(spec.level_count, index, outlier=True)
            outlier_run = create_zscore_run(
                batch_id=batch_id,
                test_time=outlier_time,
                operator=_operator(index + 20),
                template_id=template_id,
                required_n=20,
                manual_note=f"{DEMO_NOTICE}；建靶离群处理演示点",
                level_results=[
                    {"level_id": level_id, "raw_value": outlier_map[level_id]}
                    for level_id in sorted(outlier_map)
                ],
            )
            outlier_run_id = int(outlier_run["run_id"])
            disable_zscore_building_run(outlier_run_id)
    formal_z_maps = _formal_z_maps_for_key(spec.key, spec.level_count)
    formal_times = _time_series(datetime(2026, 2, 1, 10, 20), len(formal_z_maps))
    for index, (test_time, z_map) in enumerate(zip(formal_times, formal_z_maps, strict=True), start=20):
        raw_map = _raw_map_from_target_z(batch_id, template_id, z_map)
        create_zscore_run(
            batch_id=batch_id,
            test_time=test_time,
            operator=_operator(index),
            template_id=template_id,
            required_n=20,
            manual_note=DEMO_NOTICE if index == 20 else "",
            level_results=[
                {"level_id": level_id, "raw_value": raw_map[level_id]}
                for level_id in sorted(raw_map)
            ],
        )
    runs = get_zscore_runs(batch_id, template_id)
    formal_runs = [run for run in runs if str(run.get("phase")) == PHASE_FORMAL_QC]
    building_runs = [run for run in runs if str(run.get("phase")) == PHASE_TARGET_BUILDING]
    return {
        "key": spec.key,
        "project_id": project_id,
        "batch_id": batch_id,
        "project_name": spec.name,
        "batch_lot_no": _demo_lot_no(spec),
        "method": "zscore",
        "level_count": spec.level_count,
        "building_records": len(building_runs),
        "formal_records": len(formal_runs),
        "raw_record_count": sum(len(run.get("level_results", [])) for run in runs),
        "run_count": len(runs),
        "outlier_run_id": outlier_run_id,
        "rule_hits": _collect_zscore_rule_hits(formal_runs),
    }


def _collect_lj_rule_hits(formal_df: pd.DataFrame) -> list[str]:
    if formal_df.empty or "rule_hits" not in formal_df.columns:
        return []
    hits: set[str] = set()
    for raw in formal_df["rule_hits"].fillna("").astype(str):
        for segment in raw.split(","):
            rule_id = segment.strip()
            if rule_id:
                hits.add(rule_id)
    return sorted(hits)


def _collect_zscore_rule_hits(runs: list[dict[str, Any]]) -> list[str]:
    hits: set[str] = set()
    for run in runs:
        for hit in run.get("rule_hits_run", []) or []:
            if isinstance(hit, dict):
                rule_id = str(hit.get("rule_id") or "").strip()
            else:
                rule_id = str(hit or "").strip()
            if rule_id:
                hits.add(rule_id)
    return sorted(hits)


def _create_dataset(spec: DemoDatasetSpec) -> dict[str, Any]:
    if spec.method == "lj":
        return _create_lj_dataset(spec)
    if spec.method == "zscore":
        return _create_zscore_dataset(spec)
    if spec.method == "instant":
        return _create_instant_dataset(spec)
    raise ValueError(f"不支持的演示数据类型：{spec.method}")


def seed_demo_data(
    profile: str = DEFAULT_PROFILE,
    replace_demo: bool = False,
    reset_all: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    normalized_profile = _normalize_profile(profile)
    plan = build_demo_plan(normalized_profile)
    db_path = Path(get_db_path()).resolve()
    if dry_run:
        delete_preview = delete_demo_data(dry_run=True) if replace_demo else None
        return {
            "operation": "seed-demo",
            "profile": normalized_profile,
            "dry_run": True,
            "replace_demo": bool(replace_demo),
            "reset_all": bool(reset_all),
            "db_path": str(db_path),
            "plan": plan,
            "delete_preview": delete_preview,
            "created": [],
            "validation": {"ok": True, "checks": [], "failed": [], "message": "dry-run：未执行验证"},
        }

    if reset_all:
        gc.collect()
        reset_database()
    init_db()
    with get_connection() as connection:
        before_totals = _database_totals(connection)
        before_non_demo = _non_demo_fingerprint(connection)
    delete_result = delete_demo_data(dry_run=False) if replace_demo else None

    created: list[dict[str, Any]] = []
    for spec in _specs_for_profile(normalized_profile):
        created.append(_create_dataset(spec))

    validation = validate_demo_data(profile=normalized_profile)
    with get_connection() as connection:
        after_totals = _database_totals(connection)
        after_non_demo = _non_demo_fingerprint(connection)
    return {
        "operation": "seed-demo",
        "profile": normalized_profile,
        "dry_run": False,
        "replace_demo": bool(replace_demo),
        "reset_all": bool(reset_all),
        "db_path": str(db_path),
        "plan": plan,
        "delete_result": delete_result,
        "created": created,
        "created_project_count": len(created),
        "created_batch_count": len(created),
        "created_record_count": sum(int(item.get("raw_record_count", 0) or 0) for item in created),
        "created_run_count": sum(int(item.get("run_count", 0) or 0) for item in created),
        "before_totals": before_totals,
        "after_totals": after_totals,
        "non_demo_preserved": before_non_demo == after_non_demo if replace_demo and not reset_all else None,
        "validation": validation,
    }


def _add_check(checks: list[dict[str, Any]], name: str, ok: bool, detail: str) -> None:
    checks.append({"name": name, "ok": bool(ok), "detail": detail})


def _fetch_demo_project_rows(method_type: str | None = None) -> list[sqlite3.Row]:
    clauses = ["name LIKE ?"]
    params: list[object] = [f"{DEMO_PREFIX}%"]
    if method_type is not None:
        clauses.append("method_type = ?")
        params.append(method_type)
    with get_connection() as connection:
        return connection.execute(
            f"""
            SELECT *
            FROM projects
            WHERE {" AND ".join(clauses)}
            ORDER BY id ASC
            """,
            tuple(params),
        ).fetchall()


def _fetch_demo_instant_project_rows() -> list[sqlite3.Row]:
    with get_connection() as connection:
        return connection.execute(
            """
            SELECT *
            FROM instant_projects
            WHERE name LIKE ?
            ORDER BY id ASC
            """,
            (f"{DEMO_PREFIX}%",),
        ).fetchall()


def _fetch_single_batch_for_project(project_id: int, *, instant: bool = False) -> sqlite3.Row | None:
    table = "instant_batches" if instant else "batches"
    with get_connection() as connection:
        return connection.execute(
            f"""
            SELECT *
            FROM {table}
            WHERE project_id = ?
            ORDER BY id ASC
            LIMIT 1
            """,
            (int(project_id),),
        ).fetchone()


def _validate_lj_dataset(spec: DemoDatasetSpec, project_row: sqlite3.Row, checks: list[dict[str, Any]]) -> None:
    batch_row = _fetch_single_batch_for_project(int(project_row["id"]))
    _add_check(checks, f"{spec.key}: 存在 LJ 批次", batch_row is not None, spec.name)
    if batch_row is None:
        return
    _add_check(checks, f"{spec.key}: 批次名以【演示】开头", str(batch_row["lot_no"]).startswith(DEMO_PREFIX), str(batch_row["lot_no"]))
    _add_check(checks, f"{spec.key}: target_n=20", int(batch_row["target_n"]) == 20, f"target_n={batch_row['target_n']}")
    results_df = get_results(int(batch_row["id"]), include_manual_note=True)
    qc_df, stats = calculate_qc_results(results_df, int(batch_row["target_n"]))
    building_df = qc_df[qc_df["phase"] == LJ_BUILDING_PHASE_LABEL].copy()
    effective_building_df = building_df[building_df["is_building_included"] == 1].copy()
    formal_df = qc_df[qc_df["phase"] == LJ_FORMAL_PHASE_LABEL].copy()
    _add_check(
        checks,
        f"{spec.key}: 有效建靶点=20",
        len(effective_building_df) == 20,
        f"effective_building={len(effective_building_df)}",
    )
    if spec.key == "lj_01":
        _add_check(checks, "lj_01: 无正式期点", len(formal_df) == 0, f"formal={len(formal_df)}")
    if spec.key in {"lj_02", "lj_03", "lj_04"}:
        _add_check(checks, f"{spec.key}: 有正式期点", len(formal_df) > 0, f"formal={len(formal_df)}")
        order_ok = bool(
            not formal_df.empty
            and not effective_building_df.empty
            and formal_df["test_time"].min() > effective_building_df["test_time"].max()
        )
        _add_check(checks, f"{spec.key}: 正式期在20个建靶点之后", order_ok, "")
    rule_hits = set(_collect_lj_rule_hits(formal_df))
    if spec.key == "lj_03":
        expected = {"1_2s", "1_3s", "R_4s"}
        _add_check(checks, "lj_03: 命中随机误差目标规则", expected <= rule_hits, f"hits={sorted(rule_hits)}")
    if spec.key == "lj_04":
        expected = {"2_2s", "4_1s", "10x"}
        _add_check(checks, "lj_04: 命中系统误差目标规则", expected <= rule_hits, f"hits={sorted(rule_hits)}")
    _add_check(
        checks,
        f"{spec.key}: 建靶统计 ready",
        bool(stats.get("target_ready")),
        f"target_ready={stats.get('target_ready')}",
    )


def _validate_zscore_dataset(spec: DemoDatasetSpec, project_row: sqlite3.Row, checks: list[dict[str, Any]]) -> None:
    batch_row = _fetch_single_batch_for_project(int(project_row["id"]))
    _add_check(checks, f"{spec.key}: 存在 Z-score 批次", batch_row is not None, spec.name)
    if batch_row is None:
        return
    batch = get_batch(int(batch_row["id"]))
    _add_check(checks, f"{spec.key}: 批次名以【演示】开头", str(batch["lot_no"]).startswith(DEMO_PREFIX), str(batch["lot_no"]))
    _add_check(checks, f"{spec.key}: target_n=20", int(batch["target_n"]) == 20, f"target_n={batch['target_n']}")
    template_id = get_template_id_for_level_count(spec.level_count)
    runs = get_zscore_runs(int(batch["id"]), template_id)
    target_profiles = get_zscore_level_targets(int(batch["id"]), template_id, required_n=20)
    for level_index in range(1, spec.level_count + 1):
        level_id = f"Level {level_index}"
        collected_n = int(target_profiles[level_id]["collected_n"])
        _add_check(
            checks,
            f"{spec.key}: {level_id} 有效建靶值=20",
            collected_n == 20,
            f"{level_id} collected_n={collected_n}",
        )
    formal_runs = [run for run in runs if str(run.get("phase")) == PHASE_FORMAL_QC]
    for formal_run in formal_runs:
        previous_runs = [
            run
            for run in runs
            if int(run.get("test_sequence") or 0) < int(formal_run.get("test_sequence") or 0)
        ]
        for level_index in range(1, spec.level_count + 1):
            level_id = f"Level {level_index}"
            previous_effective = 0
            for run in previous_runs:
                if str(run.get("phase")) != PHASE_TARGET_BUILDING:
                    continue
                for level_result in run.get("level_results", []):
                    if str(level_result.get("level_id")) == level_id and int(level_result.get("is_building_included", 1) or 0) == 1:
                        previous_effective += 1
            _add_check(
                checks,
                f"{spec.key}: 正式期 run 在 {level_id} 20个有效建靶值之后",
                previous_effective >= 20,
                f"previous_effective={previous_effective}",
            )
    rule_hits = set(_collect_zscore_rule_hits(formal_runs))
    if spec.key == "z2_02":
        expected = {"1_2s", "1_3s", "2_2s", "R_4s", "4_1s", "10_x"}
        _add_check(checks, "z2_02: 命中双水平目标规则", expected <= rule_hits, f"hits={sorted(rule_hits)}")
    if spec.key == "z3_02":
        expected = {"2of3_2s", "R_4s", "3_1s", "12_x"}
        _add_check(checks, "z3_02: 命中三水平目标规则", expected <= rule_hits, f"hits={sorted(rule_hits)}")
    if spec.key in {"z2_03", "z3_03"}:
        disabled_count = sum(
            1
            for run in runs
            for level_result in run.get("level_results", [])
            if int(level_result.get("is_building_included", 1) or 0) == 0
        )
        _add_check(checks, f"{spec.key}: 存在建靶禁用点", disabled_count >= spec.level_count, f"disabled={disabled_count}")


def _validate_instant_dataset(spec: DemoDatasetSpec, project_row: sqlite3.Row, checks: list[dict[str, Any]]) -> None:
    batch_row = _fetch_single_batch_for_project(int(project_row["id"]), instant=True)
    _add_check(checks, f"{spec.key}: 存在 Instant 批次", batch_row is not None, spec.name)
    if batch_row is None:
        return
    _add_check(checks, f"{spec.key}: 批次名以【演示】开头", str(batch_row["lot_no"]).startswith(DEMO_PREFIX), str(batch_row["lot_no"]))
    results_df = get_instant_results(int(batch_row["id"]), include_manual_note=True)
    context = build_instant_workbench_context(int(batch_row["id"]))
    summary = context["summary"]
    _add_check(checks, f"{spec.key}: 有效点=20", int(summary.get("effective_count", 0) or 0) == 20, f"effective={summary.get('effective_count')}")
    _add_check(checks, f"{spec.key}: 总记录=20", len(results_df) == 20, f"total={len(results_df)}")
    if spec.key == "instant_02":
        si_status = str(summary.get("si_status") or "")
        suspect_count = int(summary.get("outlier_suspect_total_count", 0) or 0)
        _add_check(
            checks,
            "instant_02: 出现 SI warning 或 reject",
            si_status in {"warning", "reject"} or suspect_count > 0,
            f"si_status={si_status}, suspect_count={suspect_count}",
        )


def validate_demo_data(profile: str | None = None) -> dict[str, Any]:
    db_path = Path(get_db_path()).resolve()
    checks: list[dict[str, Any]] = []
    if not db_path.exists():
        _add_check(checks, "数据库文件存在", False, str(db_path))
        failed = [check for check in checks if not check["ok"]]
        return {"ok": False, "db_path": str(db_path), "checks": checks, "failed": failed}

    init_db()
    expected_specs = _specs_for_profile(profile) if profile else []
    expected_names = {spec.name for spec in expected_specs}

    project_rows = _fetch_demo_project_rows()
    instant_rows = _fetch_demo_instant_project_rows()
    all_project_names = {str(row["name"]) for row in [*project_rows, *instant_rows]}
    if expected_specs:
        missing = sorted(expected_names - all_project_names)
        _add_check(checks, "profile 演示项目齐全", not missing, f"missing={missing}")
    else:
        _add_check(checks, "存在演示项目", bool(all_project_names), f"count={len(all_project_names)}")

    for name in sorted(all_project_names):
        _add_check(checks, f"{name}: 项目名以【演示】开头", name.startswith(DEMO_PREFIX), name)

    handled_names: set[str] = set()
    for row in project_rows:
        name = str(row["name"])
        spec = SPEC_BY_NAME.get(name)
        if spec is None:
            continue
        handled_names.add(name)
        method_type = str(row["method_type"])
        if spec.method == "lj":
            _add_check(checks, f"{spec.key}: method_type=lj", method_type == "lj", method_type)
            _validate_lj_dataset(spec, row, checks)
        elif spec.method == "zscore":
            _add_check(checks, f"{spec.key}: method_type=zscore", method_type == "zscore", method_type)
            _validate_zscore_dataset(spec, row, checks)

    for row in instant_rows:
        name = str(row["name"])
        spec = SPEC_BY_NAME.get(name)
        if spec is None:
            continue
        handled_names.add(name)
        _validate_instant_dataset(spec, row, checks)

    if expected_specs:
        unhandled = sorted(expected_names - handled_names)
        _add_check(checks, "profile 演示项目均完成明细验证", not unhandled, f"unhandled={unhandled}")

    failed = [check for check in checks if not check["ok"]]
    return {
        "ok": not failed,
        "db_path": str(db_path),
        "checks": checks,
        "failed": failed,
        "passed_count": len(checks) - len(failed),
        "failed_count": len(failed),
    }


def format_operation_summary(result: dict[str, Any]) -> str:
    lines = [
        f"操作：{result.get('operation', '-')}",
        f"数据库：{result.get('db_path', get_db_path())}",
        f"dry-run：{bool(result.get('dry_run', False))}",
    ]
    if "profile" in result:
        lines.append(f"profile：{result.get('profile')}")
    if result.get("dry_run"):
        plan = result.get("plan") or {}
        lines.append(f"计划创建项目数：{plan.get('dataset_count', 0)}")
        if result.get("delete_preview"):
            lines.append(f"将删除演示数据：{result['delete_preview'].get('deleted', {})}")
        return "\n".join(lines)

    if result.get("operation") == "delete-demo":
        lines.append(f"删除摘要：{result.get('deleted', {})}")
        lines.append("创建项目数：0")
        lines.append("创建批次数：0")
        lines.append("创建记录数：0")
        lines.append("规则验证：未执行")
        return "\n".join(lines)

    lines.extend(
        [
            f"创建项目数：{result.get('created_project_count', 0)}",
            f"创建批次数：{result.get('created_batch_count', 0)}",
            f"创建记录数：{result.get('created_record_count', 0)}",
            f"创建 Z-score run 数：{result.get('created_run_count', 0)}",
            f"非演示数据保护：{result.get('non_demo_preserved')}",
        ]
    )
    validation = result.get("validation") or {}
    lines.append(f"规则验证：{'通过' if validation.get('ok') else '失败'}")
    if validation.get("failed"):
        lines.append("失败项：")
        for failed in validation["failed"]:
            lines.append(f"- {failed['name']}：{failed['detail']}")
    return "\n".join(lines)
