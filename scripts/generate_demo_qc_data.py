from __future__ import annotations

import argparse
import calendar
import math
import random
import re
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import database
from database import (
    add_result,
    create_batch,
    create_instant_batch,
    create_instant_project,
    create_project,
    create_zscore_batch,
    create_zscore_project,
    get_connection,
    get_instant_results,
    get_results,
)
from qc_logic import LJ_BUILDING_PHASE_LABEL, LJ_FORMAL_PHASE_LABEL, calculate_qc_results, persist_lj_batch_outlier_snapshot
from services.instant_service import build_instant_workbench_context, save_instant_result
from zscore_logic import (
    PHASE_FORMAL_QC,
    PHASE_TARGET_BUILDING,
    create_zscore_run,
    get_template_id_for_level_count,
    get_zscore_level_targets,
    get_zscore_runs,
)


DEFAULT_SEED = 20260416
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "demo_qc_data.db"
ON_CONFLICT_CHOICES = ("skip", "append", "replace")
LJ_METHOD_SCOPE = "lj"
ZS_METHOD_SCOPE = "zscore"
INSTANT_METHOD_SCOPE = "instant"
PROJECT_METHOD_BY_SCOPE = {
    LJ_METHOD_SCOPE: "lj",
    ZS_METHOD_SCOPE: "zscore",
}
OPERATOR_POOL = ("赵宁", "陈敏", "周岚", "郭岩")


@dataclass(frozen=True)
class DatasetIdentity:
    method_scope: str
    project_base_name: str
    lot_no: str
    usage: str


@dataclass
class DatasetHandle:
    method_scope: str
    method_label: str
    project_name: str
    batch_lot_no: str
    usage: str
    input_value_type: str
    project_id: int
    batch_id: int
    created: bool
    action: str


@dataclass
class DatasetSummary:
    method_label: str
    project_name: str
    batch_lot_no: str
    usage: str
    input_value_type: str
    action: str
    total_records: int
    building_records: int
    formal_records: int
    effective_records: int
    date_start: str
    date_end: str
    has_outlier: bool
    abnormal_records: int
    warning_records: int
    reject_records: int
    formal_started: bool


LJ_BUILDING_DATASET = DatasetIdentity(
    method_scope=LJ_METHOD_SCOPE,
    project_base_name="[DEMO] LJ 建靶演示",
    lot_no="DEMO-LJ-BUILD-202604",
    usage="用于测试 LJ 建靶期离群值识别、维护区和默认展示状态。",
)
LJ_FORMAL_DATASET = DatasetIdentity(
    method_scope=LJ_METHOD_SCOPE,
    project_base_name="[DEMO] LJ 正式期 202603",
    lot_no="DEMO-LJ-FORMAL-202603",
    usage="用于测试 LJ 正式期图表、异常点、月报和 3 月月度时间范围。",
)
ZS_BUILDING_DATASET = DatasetIdentity(
    method_scope=ZS_METHOD_SCOPE,
    project_base_name="[DEMO] ZS 建靶演示",
    lot_no="DEMO-ZS-BUILD-202604",
    usage="用于测试 Z-score 两水平建靶、run 级维护和离群值提示。",
)
ZS_FORMAL_DATASET = DatasetIdentity(
    method_scope=ZS_METHOD_SCOPE,
    project_base_name="[DEMO] ZS 正式期 202603",
    lot_no="DEMO-ZS-FORMAL-202603",
    usage="用于测试 Z-score 正式期图、维护记录和月报异常/失控汇总。",
)
INSTANT_DATASET = DatasetIdentity(
    method_scope=INSTANT_METHOD_SCOPE,
    project_base_name="[DEMO] Instant 演示",
    lot_no="DEMO-INSTANT-BUILD-202604",
    usage="用于测试即时法页面、Grubbs 提示、累计点数与接近转入 LJ 的状态。",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate deterministic demo QC data for LJ, Z-score, and Instant modules.",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB_PATH,
        help="Target SQLite database path. Default keeps demo data isolated from the runtime database.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help="Random seed used to build deterministic demo data.",
    )
    parser.add_argument(
        "--on-conflict",
        choices=ON_CONFLICT_CHOICES,
        default="skip",
        help="How to handle existing demo project names: skip, append with a numeric suffix, or replace matching demo datasets.",
    )
    return parser.parse_args()


@contextmanager
def use_database_path(db_path: Path):
    target_path = Path(db_path).expanduser()
    if not target_path.is_absolute():
        target_path = (Path.cwd() / target_path).resolve()
    target_path.parent.mkdir(parents=True, exist_ok=True)

    original_db_path = database.DB_PATH
    original_legacy_candidates = list(database.LEGACY_DB_CANDIDATES)
    database.DB_PATH = target_path
    database.LEGACY_DB_CANDIDATES = []
    try:
        database.init_db()
        yield target_path
    finally:
        database.DB_PATH = original_db_path
        database.LEGACY_DB_CANDIDATES = original_legacy_candidates


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _round_value(value: float, digits: int = 3) -> float:
    return float(round(float(value), digits))


def _sample_time_in_window(
    rng: random.Random,
    sample_date: date,
    start_hour: int,
    start_minute: int,
    end_hour: int,
    end_minute: int,
) -> datetime:
    start_total = start_hour * 60 + start_minute
    end_total = end_hour * 60 + end_minute
    selected_total = rng.randint(start_total, end_total)
    sampled_time = time(hour=selected_total // 60, minute=selected_total % 60)
    return datetime.combine(sample_date, sampled_time)


def build_daily_schedule(
    rng: random.Random,
    *,
    start_date: date,
    count: int,
    start_hour: int = 8,
    start_minute: int = 5,
    end_hour: int = 10,
    end_minute: int = 25,
) -> list[datetime]:
    return [
        _sample_time_in_window(
            rng,
            sample_date=start_date + timedelta(days=offset),
            start_hour=start_hour,
            start_minute=start_minute,
            end_hour=end_hour,
            end_minute=end_minute,
        )
        for offset in range(count)
    ]


def build_march_formal_schedule(rng: random.Random, *, total_count: int = 50) -> list[datetime]:
    year = 2026
    month = 3
    _, day_count = calendar.monthrange(year, month)
    all_days = [date(year, month, day) for day in range(1, day_count + 1)]
    if total_count < len(all_days):
        raise ValueError("Formal schedule must cover every day of March.")

    timestamps = [
        _sample_time_in_window(rng, sample_date=day, start_hour=8, start_minute=5, end_hour=10, end_minute=25)
        for day in all_days
    ]
    extra_days = sorted(rng.sample(all_days, total_count - len(all_days)))
    timestamps.extend(
        _sample_time_in_window(rng, sample_date=day, start_hour=14, start_minute=5, end_hour=16, end_minute=55)
        for day in extra_days
    )
    return sorted(timestamps)


def build_single_level_values(
    rng: random.Random,
    *,
    count: int,
    mean: float,
    sd: float,
    outlier_index: int | None = None,
    outlier_z: float | None = None,
    clip: float = 1.4,
) -> list[float]:
    values: list[float] = []
    carry = rng.gauss(0.0, 0.18)
    for index in range(count):
        wave = 0.32 * math.sin(index * 0.58 + 0.4) + 0.18 * math.cos(index * 0.21 + 0.3)
        carry = 0.38 * carry + rng.gauss(0.0, 0.42)
        z_value = _clamp(wave + 0.55 * carry + rng.gauss(0.0, 0.12), -clip, clip)
        if outlier_index is not None and index == outlier_index:
            z_value = float(outlier_z if outlier_z is not None else 4.6)
        values.append(_round_value(mean + z_value * sd))
    return values


def build_multilevel_values(
    rng: random.Random,
    *,
    count: int,
    mean_level_1: float,
    sd_level_1: float,
    mean_level_2: float,
    sd_level_2: float,
    outlier_index: int | None = None,
    outlier_level_id: str = "Level 1",
    outlier_z: float = 4.7,
) -> list[dict[str, float]]:
    results: list[dict[str, float]] = []
    carry_level_1 = rng.gauss(0.0, 0.16)
    carry_level_2 = rng.gauss(0.0, 0.16)
    for index in range(count):
        shared_signal = 0.26 * math.sin(index * 0.41 + 0.2) + rng.gauss(0.0, 0.16)
        carry_level_1 = 0.36 * carry_level_1 + rng.gauss(0.0, 0.34)
        carry_level_2 = 0.30 * carry_level_2 + rng.gauss(0.0, 0.42)
        level_1_z = _clamp(shared_signal * 0.48 + carry_level_1 * 0.60 + rng.gauss(0.0, 0.14), -1.35, 1.35)
        level_2_z = _clamp(
            -shared_signal * 0.18 + carry_level_2 * 0.58 + 0.16 * math.cos(index * 0.33 + 0.7) + rng.gauss(0.0, 0.16),
            -1.45,
            1.45,
        )
        if outlier_index is not None and index == outlier_index:
            if outlier_level_id == "Level 2":
                level_2_z = outlier_z
                level_1_z = 0.34
            else:
                level_1_z = outlier_z
                level_2_z = 0.28
        results.append(
            {
                "Level 1": _round_value(mean_level_1 + level_1_z * sd_level_1),
                "Level 2": _round_value(mean_level_2 + level_2_z * sd_level_2),
            }
        )
    return results


def build_lj_formal_zscores(rng: random.Random, count: int) -> list[float]:
    zscores: list[float] = []
    for index in range(count):
        base = (
            0.48 * math.sin(index * 0.44 + 0.35)
            + 0.24 * math.cos(index * 0.18 + 0.1)
            + rng.gauss(0.0, 0.24)
        )
        zscores.append(_clamp(base, -1.1, 1.1))

    injections = {
        7: 2.18,
        15: -3.08,
        26: 2.21,
        27: 2.37,
        40: -2.14,
    }
    for index, z_value in injections.items():
        zscores[index] = z_value
    return zscores


def build_zscore_formal_zmaps(rng: random.Random, count: int) -> list[dict[str, float]]:
    zmaps: list[dict[str, float]] = []
    for index in range(count):
        level_1 = _clamp(
            0.45 * math.sin(index * 0.39 + 0.45) + 0.22 * math.cos(index * 0.11) + rng.gauss(0.0, 0.22),
            -1.15,
            1.15,
        )
        level_2 = _clamp(
            -0.40 * math.cos(index * 0.34 + 0.7) + 0.26 * math.sin(index * 0.19 + 0.2) + rng.gauss(0.0, 0.24),
            -1.15,
            1.15,
        )
        zmaps.append({"Level 1": level_1, "Level 2": level_2})

    injections = {
        8: {"Level 1": 2.24, "Level 2": 0.18},
        16: {"Level 1": 0.26, "Level 2": -3.16},
        28: {"Level 1": 2.35, "Level 2": -2.22},
        37: {"Level 1": 2.12, "Level 2": 0.12},
        38: {"Level 1": 2.28, "Level 2": 0.08},
    }
    for index, value in injections.items():
        zmaps[index] = value
    return zmaps


def _operator_for(index: int) -> str:
    return OPERATOR_POOL[index % len(OPERATOR_POOL)]


def _compose_lj_note(index: int, timestamp: datetime, *, abnormal: bool = False, reagent_lot_changed: bool = False) -> str:
    note_parts: list[str] = []
    if timestamp.hour >= 14:
        note_parts.append("下午复测")
    if abnormal:
        note_parts.append("建议关注偏移")
    if reagent_lot_changed:
        note_parts.append("试剂批切换后复核")
    return "；".join(note_parts)


def _compose_zscore_note(index: int, timestamp: datetime, *, abnormal: bool = False) -> str:
    note_parts: list[str] = []
    if timestamp.hour >= 14:
        note_parts.append("同日复测")
    if abnormal:
        note_parts.append("建议查看 run 级规则证据")
    return "；".join(note_parts)


def _fetch_named_projects(method_scope: str) -> list[tuple[int, str]]:
    with get_connection() as connection:
        if method_scope == INSTANT_METHOD_SCOPE:
            rows = connection.execute(
                "SELECT id, name FROM instant_projects ORDER BY id ASC"
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT id, name FROM projects WHERE method_type = ? ORDER BY id ASC",
                (PROJECT_METHOD_BY_SCOPE[method_scope],),
            ).fetchall()
    return [(int(row["id"]), str(row["name"])) for row in rows]


def _matching_project_rows(method_scope: str, base_name: str) -> list[tuple[int, str]]:
    name_pattern = re.compile(rf"^{re.escape(base_name)}(?: #(?P<suffix>\d+))?$")
    return [
        (project_id, project_name)
        for project_id, project_name in _fetch_named_projects(method_scope)
        if name_pattern.fullmatch(project_name)
    ]


def _delete_projects(method_scope: str, project_ids: Iterable[int]) -> int:
    project_id_list = sorted({int(project_id) for project_id in project_ids})
    if not project_id_list:
        return 0
    placeholders = ", ".join("?" for _ in project_id_list)
    with get_connection() as connection:
        if method_scope == INSTANT_METHOD_SCOPE:
            sql = f"DELETE FROM instant_projects WHERE id IN ({placeholders})"
            connection.execute(sql, tuple(project_id_list))
        else:
            sql = f"DELETE FROM projects WHERE method_type = ? AND id IN ({placeholders})"
            connection.execute(sql, (PROJECT_METHOD_BY_SCOPE[method_scope], *project_id_list))
    return len(project_id_list)


def resolve_project_name(identity: DatasetIdentity, on_conflict: str) -> tuple[str | None, str]:
    matching_rows = _matching_project_rows(identity.method_scope, identity.project_base_name)
    exact_exists = any(project_name == identity.project_base_name for _, project_name in matching_rows)

    if on_conflict == "replace":
        _delete_projects(identity.method_scope, [project_id for project_id, _ in matching_rows])
        return identity.project_base_name, "replaced" if matching_rows else "created"

    if on_conflict == "skip" and exact_exists:
        return None, "skipped"

    if on_conflict == "append" and matching_rows:
        suffixes = [
            int(match.group(1) or 1)
            for _, project_name in matching_rows
            if (match := re.fullmatch(rf"{re.escape(identity.project_base_name)}(?: #(\d+))?", project_name))
        ]
        next_suffix = max(suffixes or [1]) + 1
        return f"{identity.project_base_name} #{next_suffix}", "appended"

    return identity.project_base_name, "created"


def find_existing_project_id(method_scope: str, project_name: str) -> int:
    with get_connection() as connection:
        if method_scope == INSTANT_METHOD_SCOPE:
            row = connection.execute(
                "SELECT id FROM instant_projects WHERE name = ?",
                (project_name,),
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT id FROM projects WHERE method_type = ? AND name = ?",
                (PROJECT_METHOD_BY_SCOPE[method_scope], project_name),
            ).fetchone()
    if row is None:
        raise ValueError(f"Project not found: {project_name}")
    return int(row["id"])


def find_existing_batch_id(method_scope: str, project_id: int, lot_no: str) -> int:
    table_name = "instant_batches" if method_scope == INSTANT_METHOD_SCOPE else "batches"
    with get_connection() as connection:
        row = connection.execute(
            f"SELECT id FROM {table_name} WHERE project_id = ? AND lot_no = ?",
            (int(project_id), lot_no),
        ).fetchone()
    if row is None:
        raise ValueError(f"Batch not found: {lot_no}")
    return int(row["id"])


def create_lj_building_dataset(identity: DatasetIdentity, rng: random.Random, on_conflict: str) -> DatasetHandle:
    project_name, action = resolve_project_name(identity, on_conflict)
    if project_name is None:
        project_id = find_existing_project_id(identity.method_scope, identity.project_base_name)
        batch_id = find_existing_batch_id(identity.method_scope, project_id, identity.lot_no)
        return DatasetHandle(
            method_scope=LJ_METHOD_SCOPE,
            method_label="LJ",
            project_name=identity.project_base_name,
            batch_lot_no=identity.lot_no,
            usage=identity.usage,
            input_value_type="raw",
            project_id=project_id,
            batch_id=batch_id,
            created=False,
            action=action,
        )

    project_id = create_project(project_name, input_value_type="raw")
    batch_id = create_batch(
        project_id=project_id,
        instrument="AU5800 [DEMO]",
        reagent="ALT 试剂盒 [DEMO]",
        qc_material="临床化学质控品 L2 [DEMO]",
        concentration="中值水平",
        lot_no=identity.lot_no,
        target_n=20,
        cv_limit=5.0,
    )
    timestamps = build_daily_schedule(rng, start_date=date(2026, 4, 1), count=19)
    values = build_single_level_values(
        rng,
        count=19,
        mean=102.4,
        sd=0.92,
        outlier_index=9,
        outlier_z=4.85,
    )
    for index, (timestamp, value) in enumerate(zip(timestamps, values, strict=True), start=0):
        add_result(
            batch_id=batch_id,
            test_time=timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            operator=_operator_for(index),
            value=value,
            log_value=None,
            reagent_lot_changed=0,
            manual_note="复孔偏高，待人工判断" if index == 9 else "",
        )
    persist_lj_batch_outlier_snapshot(batch_id)
    return DatasetHandle(
        method_scope=LJ_METHOD_SCOPE,
        method_label="LJ",
        project_name=project_name,
        batch_lot_no=identity.lot_no,
        usage=identity.usage,
        input_value_type="raw",
        project_id=project_id,
        batch_id=batch_id,
        created=True,
        action=action,
    )


def create_lj_formal_dataset(identity: DatasetIdentity, rng: random.Random, on_conflict: str) -> DatasetHandle:
    project_name, action = resolve_project_name(identity, on_conflict)
    if project_name is None:
        project_id = find_existing_project_id(identity.method_scope, identity.project_base_name)
        batch_id = find_existing_batch_id(identity.method_scope, project_id, identity.lot_no)
        return DatasetHandle(
            method_scope=LJ_METHOD_SCOPE,
            method_label="LJ",
            project_name=identity.project_base_name,
            batch_lot_no=identity.lot_no,
            usage=identity.usage,
            input_value_type="raw",
            project_id=project_id,
            batch_id=batch_id,
            created=False,
            action=action,
        )

    project_id = create_project(project_name, input_value_type="raw")
    batch_id = create_batch(
        project_id=project_id,
        instrument="AU5800 [DEMO]",
        reagent="AST 试剂盒 [DEMO]",
        qc_material="临床化学质控品 L2 [DEMO]",
        concentration="中值水平",
        lot_no=identity.lot_no,
        target_n=20,
        cv_limit=4.5,
    )

    build_timestamps = build_daily_schedule(rng, start_date=date(2026, 2, 1), count=20)
    build_values = build_single_level_values(rng, count=20, mean=81.8, sd=0.86, clip=1.05)
    for index, (timestamp, value) in enumerate(zip(build_timestamps, build_values, strict=True), start=0):
        add_result(
            batch_id=batch_id,
            test_time=timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            operator=_operator_for(index),
            value=value,
            log_value=None,
            reagent_lot_changed=0,
            manual_note="",
        )

    build_results_df = get_results(batch_id, include_manual_note=True)
    _, build_stats = calculate_qc_results(build_results_df, target_count=20)
    mean_value = float(build_stats["mean"])
    sd_value = float(build_stats["sd"])

    formal_timestamps = build_march_formal_schedule(rng, total_count=50)
    formal_zscores = build_lj_formal_zscores(rng, count=50)
    for index, (timestamp, z_value) in enumerate(zip(formal_timestamps, formal_zscores, strict=True), start=20):
        reagent_lot_changed = 1 if index == 43 else 0
        abnormal = abs(z_value) >= 2.0
        add_result(
            batch_id=batch_id,
            test_time=timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            operator=_operator_for(index),
            value=_round_value(mean_value + z_value * sd_value),
            log_value=None,
            reagent_lot_changed=reagent_lot_changed,
            manual_note=_compose_lj_note(index, timestamp, abnormal=abnormal, reagent_lot_changed=bool(reagent_lot_changed)),
        )

    persist_lj_batch_outlier_snapshot(batch_id)
    return DatasetHandle(
        method_scope=LJ_METHOD_SCOPE,
        method_label="LJ",
        project_name=project_name,
        batch_lot_no=identity.lot_no,
        usage=identity.usage,
        input_value_type="raw",
        project_id=project_id,
        batch_id=batch_id,
        created=True,
        action=action,
    )


def create_zscore_building_dataset(identity: DatasetIdentity, rng: random.Random, on_conflict: str) -> DatasetHandle:
    project_name, action = resolve_project_name(identity, on_conflict)
    if project_name is None:
        project_id = find_existing_project_id(identity.method_scope, identity.project_base_name)
        batch_id = find_existing_batch_id(identity.method_scope, project_id, identity.lot_no)
        return DatasetHandle(
            method_scope=ZS_METHOD_SCOPE,
            method_label="Z-score",
            project_name=identity.project_base_name,
            batch_lot_no=identity.lot_no,
            usage=identity.usage,
            input_value_type="raw",
            project_id=project_id,
            batch_id=batch_id,
            created=False,
            action=action,
        )

    project_id = create_zscore_project(project_name, level_count=2, input_value_type="raw")
    batch_id = create_zscore_batch(
        project_id=project_id,
        instrument="Cobas c 702 [DEMO]",
        reagent="钠离子试剂 [DEMO]",
        qc_material="电解质双水平质控 [DEMO]",
        concentration="双水平",
        lot_no=identity.lot_no,
        target_n=20,
        level_1_label="低水平",
        level_2_label="高水平",
    )
    template_id = get_template_id_for_level_count(2)
    timestamps = build_daily_schedule(rng, start_date=date(2026, 4, 1), count=19)
    run_values = build_multilevel_values(
        rng,
        count=19,
        mean_level_1=98.4,
        sd_level_1=1.05,
        mean_level_2=152.7,
        sd_level_2=1.72,
        outlier_index=10,
        outlier_level_id="Level 1",
        outlier_z=4.9,
    )
    for index, (timestamp, level_values) in enumerate(zip(timestamps, run_values, strict=True), start=0):
        create_zscore_run(
            batch_id=batch_id,
            test_time=timestamp,
            operator=_operator_for(index),
            template_id=template_id,
            required_n=20,
            manual_note="低水平结果偏高，便于演示 run 级维护" if index == 10 else "",
            level_results=[
                {"level_id": "Level 1", "raw_value": level_values["Level 1"]},
                {"level_id": "Level 2", "raw_value": level_values["Level 2"]},
            ],
        )
    return DatasetHandle(
        method_scope=ZS_METHOD_SCOPE,
        method_label="Z-score",
        project_name=project_name,
        batch_lot_no=identity.lot_no,
        usage=identity.usage,
        input_value_type="raw",
        project_id=project_id,
        batch_id=batch_id,
        created=True,
        action=action,
    )


def create_zscore_formal_dataset(identity: DatasetIdentity, rng: random.Random, on_conflict: str) -> DatasetHandle:
    project_name, action = resolve_project_name(identity, on_conflict)
    if project_name is None:
        project_id = find_existing_project_id(identity.method_scope, identity.project_base_name)
        batch_id = find_existing_batch_id(identity.method_scope, project_id, identity.lot_no)
        return DatasetHandle(
            method_scope=ZS_METHOD_SCOPE,
            method_label="Z-score",
            project_name=identity.project_base_name,
            batch_lot_no=identity.lot_no,
            usage=identity.usage,
            input_value_type="raw",
            project_id=project_id,
            batch_id=batch_id,
            created=False,
            action=action,
        )

    project_id = create_zscore_project(project_name, level_count=2, input_value_type="raw")
    batch_id = create_zscore_batch(
        project_id=project_id,
        instrument="Cobas c 702 [DEMO]",
        reagent="总胆红素试剂 [DEMO]",
        qc_material="生化双水平质控品 [DEMO]",
        concentration="双水平",
        lot_no=identity.lot_no,
        target_n=20,
        level_1_label="低水平",
        level_2_label="高水平",
    )
    template_id = get_template_id_for_level_count(2)
    build_timestamps = build_daily_schedule(rng, start_date=date(2026, 2, 1), count=20)
    build_values = build_multilevel_values(
        rng,
        count=20,
        mean_level_1=26.8,
        sd_level_1=0.42,
        mean_level_2=31.2,
        sd_level_2=0.56,
    )
    for index, (timestamp, level_values) in enumerate(zip(build_timestamps, build_values, strict=True), start=0):
        create_zscore_run(
            batch_id=batch_id,
            test_time=timestamp,
            operator=_operator_for(index),
            template_id=template_id,
            required_n=20,
            level_results=[
                {"level_id": "Level 1", "raw_value": level_values["Level 1"]},
                {"level_id": "Level 2", "raw_value": level_values["Level 2"]},
            ],
        )

    target_profiles = get_zscore_level_targets(batch_id, template_id, required_n=20)
    level_1_mean = float(target_profiles["Level 1"]["final_target_mean"])
    level_1_sd = float(target_profiles["Level 1"]["final_target_sd"])
    level_2_mean = float(target_profiles["Level 2"]["final_target_mean"])
    level_2_sd = float(target_profiles["Level 2"]["final_target_sd"])

    formal_timestamps = build_march_formal_schedule(rng, total_count=50)
    formal_zmaps = build_zscore_formal_zmaps(rng, count=50)
    for index, (timestamp, z_map) in enumerate(zip(formal_timestamps, formal_zmaps, strict=True), start=20):
        abnormal = any(abs(z_value) >= 2.0 for z_value in z_map.values())
        create_zscore_run(
            batch_id=batch_id,
            test_time=timestamp,
            operator=_operator_for(index),
            template_id=template_id,
            required_n=20,
            manual_note=_compose_zscore_note(index, timestamp, abnormal=abnormal),
            level_results=[
                {"level_id": "Level 1", "raw_value": _round_value(level_1_mean + z_map["Level 1"] * level_1_sd)},
                {"level_id": "Level 2", "raw_value": _round_value(level_2_mean + z_map["Level 2"] * level_2_sd)},
            ],
        )

    return DatasetHandle(
        method_scope=ZS_METHOD_SCOPE,
        method_label="Z-score",
        project_name=project_name,
        batch_lot_no=identity.lot_no,
        usage=identity.usage,
        input_value_type="raw",
        project_id=project_id,
        batch_id=batch_id,
        created=True,
        action=action,
    )


def create_instant_dataset(identity: DatasetIdentity, rng: random.Random, on_conflict: str) -> DatasetHandle:
    project_name, action = resolve_project_name(identity, on_conflict)
    if project_name is None:
        project_id = find_existing_project_id(identity.method_scope, identity.project_base_name)
        batch_id = find_existing_batch_id(identity.method_scope, project_id, identity.lot_no)
        return DatasetHandle(
            method_scope=INSTANT_METHOD_SCOPE,
            method_label="Instant",
            project_name=identity.project_base_name,
            batch_lot_no=identity.lot_no,
            usage=identity.usage,
            input_value_type="ct",
            project_id=project_id,
            batch_id=batch_id,
            created=False,
            action=action,
        )

    project_id = create_instant_project(project_name, input_value_type="ct")
    batch_id = create_instant_batch(
        project_id=project_id,
        instrument="ABI 7500 Fast [DEMO]",
        reagent="呼吸道核酸试剂 [DEMO]",
        qc_material="Ct 质控品 [DEMO]",
        concentration="单水平",
        lot_no=identity.lot_no,
    )
    timestamps = build_daily_schedule(rng, start_date=date(2026, 4, 2), count=19)
    values = build_single_level_values(
        rng,
        count=19,
        mean=28.4,
        sd=0.32,
        outlier_index=11,
        outlier_z=5.6,
        clip=1.25,
    )
    for index, (timestamp, value) in enumerate(zip(timestamps, values, strict=True), start=0):
        save_instant_result(
            batch_id=batch_id,
            test_time=timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            operator=_operator_for(index),
            value=value,
            log_value=None,
        )
    return DatasetHandle(
        method_scope=INSTANT_METHOD_SCOPE,
        method_label="Instant",
        project_name=project_name,
        batch_lot_no=identity.lot_no,
        usage=identity.usage,
        input_value_type="ct",
        project_id=project_id,
        batch_id=batch_id,
        created=True,
        action=action,
    )


def summarize_lj_dataset(handle: DatasetHandle) -> DatasetSummary:
    results_df = get_results(handle.batch_id, include_manual_note=True)
    qc_df, stats = calculate_qc_results(results_df, target_count=20)
    formal_df = qc_df[qc_df["phase"] == LJ_FORMAL_PHASE_LABEL].copy()
    abnormal_records = int(formal_df["rule_hits"].fillna("").astype(str).str.strip().ne("").sum()) if not formal_df.empty else 0
    warning_records = int(
        formal_df["rule_hits"].fillna("").astype(str).str.contains(r"\b1_2s\b", regex=True).sum()
    ) if not formal_df.empty else 0
    reject_records = max(0, abnormal_records - warning_records)
    return DatasetSummary(
        method_label=handle.method_label,
        project_name=handle.project_name,
        batch_lot_no=handle.batch_lot_no,
        usage=handle.usage,
        input_value_type=handle.input_value_type,
        action=handle.action,
        total_records=int(len(qc_df)),
        building_records=int((qc_df["phase"] == LJ_BUILDING_PHASE_LABEL).sum()),
        formal_records=int(len(formal_df)),
        effective_records=int(stats["effective_building_count"]) if not formal_df.empty else int(len(qc_df)),
        date_start=qc_df["test_time"].min().strftime("%Y-%m-%d %H:%M:%S") if not qc_df.empty else "-",
        date_end=qc_df["test_time"].max().strftime("%Y-%m-%d %H:%M:%S") if not qc_df.empty else "-",
        has_outlier=bool(qc_df["is_outlier_suspect"].fillna(0).astype(int).sum() > 0),
        abnormal_records=abnormal_records,
        warning_records=warning_records,
        reject_records=reject_records,
        formal_started=bool(stats.get("has_formal_started")),
    )


def summarize_zscore_dataset(handle: DatasetHandle) -> DatasetSummary:
    template_id = get_template_id_for_level_count(2)
    runs = get_zscore_runs(handle.batch_id, template_id)
    building_runs = [run for run in runs if str(run.get("phase")) == PHASE_TARGET_BUILDING]
    formal_runs = [run for run in runs if str(run.get("phase")) == PHASE_FORMAL_QC]
    warning_records = sum(1 for run in formal_runs if str(run.get("run_status")) == "warning")
    reject_records = sum(1 for run in formal_runs if str(run.get("run_status")) == "reject")
    has_outlier = any(
        int(level_result.get("is_outlier_suspect", 0) or 0) == 1
        for run in runs
        for level_result in run.get("level_results", [])
    )
    all_test_times = [datetime.fromisoformat(str(run["test_time"])) for run in runs]
    target_profiles = get_zscore_level_targets(handle.batch_id, template_id, required_n=20)
    effective_records = min(
        int(target_profiles["Level 1"]["collected_n"]),
        int(target_profiles["Level 2"]["collected_n"]),
    )
    return DatasetSummary(
        method_label=handle.method_label,
        project_name=handle.project_name,
        batch_lot_no=handle.batch_lot_no,
        usage=handle.usage,
        input_value_type=handle.input_value_type,
        action=handle.action,
        total_records=len(runs),
        building_records=len(building_runs),
        formal_records=len(formal_runs),
        effective_records=effective_records,
        date_start=min(all_test_times).strftime("%Y-%m-%d %H:%M:%S") if all_test_times else "-",
        date_end=max(all_test_times).strftime("%Y-%m-%d %H:%M:%S") if all_test_times else "-",
        has_outlier=has_outlier,
        abnormal_records=warning_records + reject_records,
        warning_records=warning_records,
        reject_records=reject_records,
        formal_started=bool(formal_runs),
    )


def summarize_instant_dataset(handle: DatasetHandle) -> DatasetSummary:
    context = build_instant_workbench_context(handle.batch_id)
    analysis_df = context["analysis_df"]
    summary = context["summary"]
    result_df = get_instant_results(handle.batch_id, include_manual_note=True)
    return DatasetSummary(
        method_label=handle.method_label,
        project_name=handle.project_name,
        batch_lot_no=handle.batch_lot_no,
        usage=handle.usage,
        input_value_type=handle.input_value_type,
        action=handle.action,
        total_records=int(len(result_df)),
        building_records=int(len(result_df)),
        formal_records=0,
        effective_records=int(summary["effective_count"]),
        date_start=analysis_df["test_time"].min().strftime("%Y-%m-%d %H:%M:%S") if not analysis_df.empty else "-",
        date_end=analysis_df["test_time"].max().strftime("%Y-%m-%d %H:%M:%S") if not analysis_df.empty else "-",
        has_outlier=bool(int(summary.get("outlier_suspect_total_count", 0) or 0) > 0),
        abnormal_records=int(summary.get("outlier_suspect_total_count", 0) or 0),
        warning_records=0,
        reject_records=0,
        formal_started=False,
    )


def generate_demo_data(*, db_path: Path, seed: int, on_conflict: str) -> list[DatasetSummary]:
    master_rng = random.Random(seed)
    summaries: list[DatasetSummary] = []
    with use_database_path(db_path):
        handles = [
            create_lj_building_dataset(LJ_BUILDING_DATASET, random.Random(master_rng.randint(1, 10_000_000)), on_conflict),
            create_lj_formal_dataset(LJ_FORMAL_DATASET, random.Random(master_rng.randint(1, 10_000_000)), on_conflict),
            create_zscore_building_dataset(ZS_BUILDING_DATASET, random.Random(master_rng.randint(1, 10_000_000)), on_conflict),
            create_zscore_formal_dataset(ZS_FORMAL_DATASET, random.Random(master_rng.randint(1, 10_000_000)), on_conflict),
            create_instant_dataset(INSTANT_DATASET, random.Random(master_rng.randint(1, 10_000_000)), on_conflict),
        ]
        for handle in handles:
            if handle.method_scope == LJ_METHOD_SCOPE:
                summaries.append(summarize_lj_dataset(handle))
            elif handle.method_scope == ZS_METHOD_SCOPE:
                summaries.append(summarize_zscore_dataset(handle))
            else:
                summaries.append(summarize_instant_dataset(handle))
    return summaries


def validate_demo_data(summaries: list[DatasetSummary]) -> None:
    summary_by_batch = {summary.batch_lot_no: summary for summary in summaries}

    lj_build = summary_by_batch[LJ_BUILDING_DATASET.lot_no]
    if lj_build.building_records != 19 or not lj_build.has_outlier:
        raise AssertionError("LJ 建靶演示批次未满足 19 条建靶记录且含离群值的要求。")

    lj_formal = summary_by_batch[LJ_FORMAL_DATASET.lot_no]
    if lj_formal.formal_records != 50 or not lj_formal.formal_started:
        raise AssertionError("LJ 正式期演示批次未满足 50 条正式期记录的要求。")

    zs_build = summary_by_batch[ZS_BUILDING_DATASET.lot_no]
    if zs_build.building_records != 19 or not zs_build.has_outlier:
        raise AssertionError("Z-score 建靶演示批次未满足 19 次建靶检测且含离群值的要求。")

    zs_formal = summary_by_batch[ZS_FORMAL_DATASET.lot_no]
    if zs_formal.formal_records != 50 or not zs_formal.formal_started:
        raise AssertionError("Z-score 正式期演示批次未满足 50 次正式期检测的要求。")

    instant_summary = summary_by_batch[INSTANT_DATASET.lot_no]
    if instant_summary.effective_records != 19 or not instant_summary.has_outlier:
        raise AssertionError("即时法演示批次未满足 19 条有效记录且含离群值的要求。")


def print_summary(summaries: list[DatasetSummary], *, db_path: Path, seed: int, on_conflict: str) -> None:
    print("Demo QC data generation completed.")
    print(f"Database: {db_path}")
    print(f"Seed: {seed}")
    print(f"Conflict mode: {on_conflict}")
    print("")
    print("Generated datasets:")
    for summary in summaries:
        print(
            f"- {summary.project_name} | batch={summary.batch_lot_no} | method={summary.method_label} | "
            f"input={summary.input_value_type} | action={summary.action}"
        )
        print(
            f"  records={summary.total_records} | building={summary.building_records} | formal={summary.formal_records} | "
            f"effective={summary.effective_records}"
        )
        print(
            f"  range={summary.date_start} -> {summary.date_end} | outlier={summary.has_outlier} | "
            f"abnormal={summary.abnormal_records} | warning={summary.warning_records} | reject={summary.reject_records} | "
            f"formal_started={summary.formal_started}"
        )
        print(f"  usage={summary.usage}")


def main() -> int:
    args = parse_args()
    db_path = Path(args.db).expanduser()
    if not db_path.is_absolute():
        db_path = (Path.cwd() / db_path).resolve()

    summaries = generate_demo_data(db_path=db_path, seed=int(args.seed), on_conflict=str(args.on_conflict))
    validate_demo_data(summaries)
    print_summary(summaries, db_path=db_path, seed=int(args.seed), on_conflict=str(args.on_conflict))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
