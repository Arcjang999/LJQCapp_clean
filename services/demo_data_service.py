from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
import math
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
    get_connection,
    get_instant_results,
    get_results,
)
from qc_logic import (
    LJ_BUILDING_PHASE_LABEL,
    LJ_FORMAL_PHASE_LABEL,
    calculate_qc_results,
    persist_lj_batch_outlier_snapshot,
)
from services.instant_service import build_instant_workbench_context, persist_instant_batch_analysis
from services.report_service import (
    build_lj_monthly_report_package,
    build_lj_monthly_report_pdf,
    build_zscore_monthly_report_package,
    build_zscore_monthly_report_pdf,
    list_lj_report_month_options,
    list_zscore_report_month_options,
    save_lj_monthly_report_snapshot,
    save_zscore_monthly_report_snapshot,
)
from zscore_logic import (
    PHASE_FORMAL_QC,
    PHASE_TARGET_BUILDING,
    create_zscore_run,
    get_template_id_for_level_count,
    get_zscore_level_targets,
    get_zscore_runs,
)


DEMO_PREFIX = "【演示】"
DEMO_NOTICE = "仅演示，请勿用于真实质控"
BUILDING_START_DATE = date(2026, 3, 1)
APRIL_START_DATE = date(2026, 4, 1)
MAY_START_DATE = date(2026, 5, 1)
BUILDING_RECORD_COUNT = 20
FORMAL_RECORDS_PER_MONTH = 30
TARGET_N = 20
REPORT_MONTHS = ("2026-04", "2026-05")
PROFILE_FULL = "full"
PROFILE_BASIC = "basic"
PROFILE_CHOICES = {PROFILE_FULL, PROFILE_BASIC}
OPERATOR_POOL = ("张宁", "陈敏", "周岚", "郭岩")

NOTE_LJ_RANDOM = (
    "复查质控品复溶和上机前混匀记录，发现混匀时间不足；"
    "重新充分混匀后复测，复测结果在控，本次记录作为操作因素追踪。"
)
NOTE_LJ_13S = (
    "检查加样针和移液通道，发现吸样针存在气泡风险；"
    "排气并清洗加样针后重新检测，复测结果在控，后续连续 3 次结果稳定。"
)
NOTE_LJ_SHIFT = (
    "更换试剂批号后出现同向偏移，已执行两点校准并复测质控，复测结果在控；"
    "后续连续 3 天加强观察。"
)
NOTE_LJ_ENV = (
    "核查仪器温控和室温记录，发现短时环境温度波动；"
    "恢复室温并稳定 30 分钟后复测，质控结果恢复在控。"
)
NOTE_ZS_RANDOM = (
    "本次多水平结果提示随机误差风险，检查加样针气泡和样本杯状态后重新检测，"
    "复测三水平结果均在控。"
)
NOTE_ZS_SYSTEM = (
    "多水平结果同向偏移，考虑校准漂移或试剂批号影响；"
    "执行校准验证并重新检测三水平质控，复测结果恢复在控。"
)
NOTE_ZS_2OF3 = (
    "两个水平同向超过 2SD，已检查试剂平衡时间和校准状态，"
    "重新平衡试剂后复测，结果恢复在控。"
)
NOTE_ZS_12X = (
    "多日连续同侧偏移，已复核靶值适配性、校准曲线和试剂批号，"
    "完成校准维护后恢复在控。"
)


@dataclass(frozen=True)
class DemoDatasetPlan:
    key: str
    method: str
    project_name: str
    lot_no: str
    description: str
    profiles: tuple[str, ...]
    level_count: int | None = None
    abnormal: bool = False


@dataclass(frozen=True)
class DemoDatasetSummary:
    key: str
    method: str
    project_name: str
    lot_no: str
    project_id: int | None = None
    batch_id: int | None = None
    building_records: int = 0
    effective_building_records: int = 0
    formal_records_by_month: dict[str, int] = field(default_factory=dict)
    abnormal_records_by_month: dict[str, int] = field(default_factory=dict)
    report_snapshots_created: int = 0


@dataclass(frozen=True)
class DemoDeleteResult:
    dry_run: bool
    lj_zscore_projects: int
    instant_projects: int

    @property
    def total_projects(self) -> int:
        return self.lj_zscore_projects + self.instant_projects


@dataclass(frozen=True)
class DemoSeedResult:
    profile: str
    dry_run: bool
    replace_demo: bool
    reset_all: bool
    datasets: list[DemoDatasetSummary]
    deleted: DemoDeleteResult | None = None
    report_snapshots_created: int = 0


@dataclass(frozen=True)
class DemoValidationResult:
    profile: str
    checked_datasets: int
    checked_report_packages: int
    checked_pdf_bytes: int


DEMO_DATASETS: tuple[DemoDatasetPlan, ...] = (
    DemoDatasetPlan(
        key="Instant-01",
        method="instant",
        project_name="【演示】Instant-01 20点累计-可转入LJ",
        lot_no="DEMO-INSTANT-01-202603",
        description="20 个有效点，整体稳定，用于展示即时法累计与人工确认转入 LJ。",
        profiles=(PROFILE_BASIC, PROFILE_FULL),
    ),
    DemoDatasetPlan(
        key="Instant-02",
        method="instant",
        project_name="【演示】Instant-02 20点累计-SI疑似离群",
        lot_no="DEMO-INSTANT-02-202603",
        description="20 个有效点，含 1 个 SI 疑似离群点和手工处理备注。",
        profiles=(PROFILE_FULL,),
        abnormal=True,
    ),
    DemoDatasetPlan(
        key="LJ-MR-01",
        method="lj",
        project_name="【演示】LJ-MR-01 两个月月报-稳定运行",
        lot_no="DEMO-LJ-MR-01-202603",
        description="20 个建靶点，2026-04 与 2026-05 各 30 条正式期记录，整体在控。",
        profiles=(PROFILE_BASIC, PROFILE_FULL),
    ),
    DemoDatasetPlan(
        key="LJ-MR-02",
        method="lj",
        project_name="【演示】LJ-MR-02 两个月月报-含失控与纠正措施",
        lot_no="DEMO-LJ-MR-02-202603",
        description="20 个建靶点，连续两个月正式期数据，预置失控/警告和纠正措施备注。",
        profiles=(PROFILE_FULL,),
        abnormal=True,
    ),
    DemoDatasetPlan(
        key="Z2-MR-01",
        method="zscore",
        project_name="【演示】Z2-MR-01 双水平两个月月报-稳定运行",
        lot_no="DEMO-Z2-MR-01-202603",
        description="双水平各 20 个有效建靶值，2026-04 与 2026-05 各 30 次正式期 run。",
        profiles=(PROFILE_BASIC, PROFILE_FULL),
        level_count=2,
    ),
    DemoDatasetPlan(
        key="Z2-MR-02",
        method="zscore",
        project_name="【演示】Z2-MR-02 双水平两个月月报-含失控与纠正措施",
        lot_no="DEMO-Z2-MR-02-202603",
        description="双水平各 20 个有效建靶值，连续两个月正式期 run，预置异常和纠正措施。",
        profiles=(PROFILE_FULL,),
        level_count=2,
        abnormal=True,
    ),
    DemoDatasetPlan(
        key="Z3-MR-01",
        method="zscore",
        project_name="【演示】Z3-MR-01 三水平两个月月报-稳定运行",
        lot_no="DEMO-Z3-MR-01-202603",
        description="三水平各 20 个有效建靶值，2026-04 与 2026-05 各 30 次正式期 run。",
        profiles=(PROFILE_BASIC, PROFILE_FULL),
        level_count=3,
    ),
    DemoDatasetPlan(
        key="Z3-MR-02",
        method="zscore",
        project_name="【演示】Z3-MR-02 三水平两个月月报-含失控与纠正措施",
        lot_no="DEMO-Z3-MR-02-202603",
        description="三水平各 20 个有效建靶值，连续两个月正式期 run，预置异常和纠正措施。",
        profiles=(PROFILE_FULL,),
        level_count=3,
        abnormal=True,
    ),
)


def build_demo_plan(profile: str = PROFILE_FULL) -> list[DemoDatasetPlan]:
    normalized_profile = _normalize_profile(profile)
    return [plan for plan in DEMO_DATASETS if normalized_profile in plan.profiles]


def seed_demo_data(
    profile: str = PROFILE_FULL,
    replace_demo: bool = False,
    reset_all: bool = False,
    dry_run: bool = False,
) -> DemoSeedResult:
    normalized_profile = _normalize_profile(profile)
    plans = build_demo_plan(normalized_profile)
    if dry_run:
        return DemoSeedResult(
            profile=normalized_profile,
            dry_run=True,
            replace_demo=bool(replace_demo),
            reset_all=bool(reset_all),
            datasets=[_dry_run_summary(plan) for plan in plans],
        )

    deleted = None
    if reset_all:
        database.reset_database()
        database.init_db()
    else:
        database.init_db()
        if replace_demo:
            deleted = delete_demo_data(dry_run=False)
        else:
            _raise_if_demo_projects_exist()

    summaries: list[DemoDatasetSummary] = []
    for plan in plans:
        if plan.method == "instant":
            summaries.append(_seed_instant_dataset(plan))
        elif plan.method == "lj":
            summaries.append(_seed_lj_dataset(plan))
        elif plan.method == "zscore":
            summaries.append(_seed_zscore_dataset(plan))
        else:
            raise ValueError(f"Unsupported demo dataset method: {plan.method}")

    snapshots_created = _create_report_history_snapshots(summaries)
    summaries = [
        _mark_summary_snapshots_created(summary, snapshot_count=2)
        if summary.method in {"lj", "zscore"} else summary
        for summary in summaries
    ]
    return DemoSeedResult(
        profile=normalized_profile,
        dry_run=False,
        replace_demo=bool(replace_demo),
        reset_all=bool(reset_all),
        datasets=summaries,
        deleted=deleted,
        report_snapshots_created=snapshots_created,
    )


def delete_demo_data(dry_run: bool = False) -> DemoDeleteResult:
    database.init_db()
    like_pattern = f"{DEMO_PREFIX}%"
    with get_connection() as connection:
        project_rows = connection.execute(
            """
            SELECT id
            FROM projects
            WHERE name LIKE ?
            """,
            (like_pattern,),
        ).fetchall()
        instant_project_rows = connection.execute(
            """
            SELECT id
            FROM instant_projects
            WHERE name LIKE ?
            """,
            (like_pattern,),
        ).fetchall()
        project_ids = [int(row["id"]) for row in project_rows]
        instant_project_ids = [int(row["id"]) for row in instant_project_rows]
        if not dry_run:
            if project_ids:
                placeholders = ", ".join("?" for _ in project_ids)
                connection.execute(
                    f"DELETE FROM projects WHERE id IN ({placeholders})",
                    tuple(project_ids),
                )
            if instant_project_ids:
                placeholders = ", ".join("?" for _ in instant_project_ids)
                connection.execute(
                    f"DELETE FROM instant_projects WHERE id IN ({placeholders})",
                    tuple(instant_project_ids),
                )
    return DemoDeleteResult(
        dry_run=bool(dry_run),
        lj_zscore_projects=len(project_ids),
        instant_projects=len(instant_project_ids),
    )


def validate_demo_data(profile: str = PROFILE_FULL) -> DemoValidationResult:
    normalized_profile = _normalize_profile(profile)
    plans = build_demo_plan(normalized_profile)
    packages_checked = 0
    pdf_bytes_checked = 0

    for plan in plans:
        ids = _find_dataset_ids(plan)
        if plan.method == "instant":
            _validate_instant_dataset(plan, int(ids["batch_id"]))
            continue
        if plan.method == "lj":
            _validate_lj_dataset(plan, int(ids["batch_id"]))
            for report_month in REPORT_MONTHS:
                package = build_lj_monthly_report_package(int(ids["batch_id"]), report_month)
                packages_checked += 1
                _validate_report_actions(package.report.abnormal_records, package.report.corrective_actions)
                if plan.abnormal:
                    _assert(
                        bool(package.report.abnormal_records),
                        f"{plan.key} {report_month} LJ 月报应包含异常/失控记录。",
                    )
                pdf_bytes = build_lj_monthly_report_pdf(package)
                _assert(len(pdf_bytes) > 0, f"{plan.key} {report_month} LJ 月报 PDF 为空。")
                pdf_bytes_checked += len(pdf_bytes)
            continue
        if plan.method == "zscore":
            _validate_zscore_dataset(plan, int(ids["batch_id"]))
            for report_month in REPORT_MONTHS:
                package = build_zscore_monthly_report_package(int(ids["batch_id"]), report_month)
                packages_checked += 1
                _validate_report_actions(package.report.abnormal_records, package.report.corrective_actions)
                if plan.abnormal:
                    _assert(
                        bool(package.report.abnormal_records),
                        f"{plan.key} {report_month} Z-score 月报应包含异常/失控记录。",
                    )
                pdf_bytes = build_zscore_monthly_report_pdf(package)
                _assert(len(pdf_bytes) > 0, f"{plan.key} {report_month} Z-score 月报 PDF 为空。")
                pdf_bytes_checked += len(pdf_bytes)
            continue
        raise ValueError(f"Unsupported demo dataset method: {plan.method}")

    return DemoValidationResult(
        profile=normalized_profile,
        checked_datasets=len(plans),
        checked_report_packages=packages_checked,
        checked_pdf_bytes=pdf_bytes_checked,
    )


def _normalize_profile(profile: str) -> str:
    normalized = str(profile or PROFILE_FULL).strip().lower()
    if normalized not in PROFILE_CHOICES:
        raise ValueError(f"profile 只能是 {PROFILE_BASIC!r} 或 {PROFILE_FULL!r}")
    return normalized


def _dry_run_summary(plan: DemoDatasetPlan) -> DemoDatasetSummary:
    formal_by_month = {}
    if plan.method in {"lj", "zscore"}:
        formal_by_month = {month: FORMAL_RECORDS_PER_MONTH for month in REPORT_MONTHS}
    return DemoDatasetSummary(
        key=plan.key,
        method=plan.method,
        project_name=plan.project_name,
        lot_no=plan.lot_no,
        building_records=BUILDING_RECORD_COUNT if plan.method in {"lj", "zscore"} else 20,
        effective_building_records=BUILDING_RECORD_COUNT if plan.method in {"lj", "zscore"} else 20,
        formal_records_by_month=formal_by_month,
    )


def _mark_summary_snapshots_created(
    summary: DemoDatasetSummary,
    *,
    snapshot_count: int,
) -> DemoDatasetSummary:
    return DemoDatasetSummary(
        key=summary.key,
        method=summary.method,
        project_name=summary.project_name,
        lot_no=summary.lot_no,
        project_id=summary.project_id,
        batch_id=summary.batch_id,
        building_records=summary.building_records,
        effective_building_records=summary.effective_building_records,
        formal_records_by_month=dict(summary.formal_records_by_month),
        abnormal_records_by_month=dict(summary.abnormal_records_by_month),
        report_snapshots_created=snapshot_count,
    )


def _raise_if_demo_projects_exist() -> None:
    existing = delete_demo_data(dry_run=True)
    if existing.total_projects:
        raise ValueError("已存在“【演示】”前缀数据；请使用 replace_demo=True 或先执行 delete_demo_data()。")


def _seed_instant_dataset(plan: DemoDatasetPlan) -> DemoDatasetSummary:
    project_id = create_instant_project(plan.project_name, input_value_type="ct")
    batch_id = create_instant_batch(
        project_id=project_id,
        instrument="ABI 7500 Fast 演示仪器",
        reagent=f"{DEMO_NOTICE} - 呼吸道核酸试剂",
        qc_material=f"{DEMO_NOTICE} - Ct 质控品",
        concentration="单水平",
        lot_no=plan.lot_no,
    )
    values = _instant_values(abnormal=plan.abnormal)
    timestamps = _build_schedule(BUILDING_START_DATE, len(values))
    for index, (timestamp, value) in enumerate(zip(timestamps, values, strict=True), start=1):
        manual_note = ""
        if index == 1:
            manual_note = f"{DEMO_NOTICE}。"
        if plan.abnormal and index == 12:
            manual_note = "疑似离群，经复核为混匀不足，重新混匀复测后在控，仅作为演示记录。"
        add_instant_result(
            batch_id=batch_id,
            test_time=_format_datetime(timestamp),
            operator=_operator_for(index),
            value=value,
            log_value=None,
            manual_note=manual_note,
        )
    persist_instant_batch_analysis(batch_id)
    context = build_instant_workbench_context(batch_id)
    summary = context["summary"]
    return DemoDatasetSummary(
        key=plan.key,
        method=plan.method,
        project_name=plan.project_name,
        lot_no=plan.lot_no,
        project_id=project_id,
        batch_id=batch_id,
        building_records=int(summary.get("total_count", 0) or 0),
        effective_building_records=int(summary.get("effective_count", 0) or 0),
    )


def _seed_lj_dataset(plan: DemoDatasetPlan) -> DemoDatasetSummary:
    project_id = create_project(plan.project_name, input_value_type="raw")
    batch_id = create_batch(
        project_id=project_id,
        instrument="AU5800 演示仪器",
        reagent=f"{DEMO_NOTICE} - 生化试剂盒",
        qc_material=f"{DEMO_NOTICE} - 单水平质控品",
        concentration="中值水平",
        lot_no=plan.lot_no,
        target_n=TARGET_N,
        cv_limit=5.0,
    )
    base_mean = 82.0 if plan.abnormal else 101.0
    base_sd = 1.4 if plan.abnormal else 1.1
    for index, (timestamp, value) in enumerate(
        zip(
            _build_schedule(BUILDING_START_DATE, BUILDING_RECORD_COUNT),
            _values_from_zscores(base_mean, base_sd, _building_zscores()),
            strict=True,
        ),
        start=1,
    ):
        add_result(
            batch_id=batch_id,
            test_time=_format_datetime(timestamp),
            operator=_operator_for(index),
            value=value,
            log_value=None,
            manual_note=f"{DEMO_NOTICE}。" if index == 1 else "",
        )

    target_mean, target_sd = _resolve_lj_target(batch_id)
    formal_sequences = _lj_abnormal_zscores() if plan.abnormal else _stable_single_level_months()
    _insert_lj_formal_month(batch_id, target_mean, target_sd, APRIL_START_DATE, formal_sequences["2026-04"])
    _insert_lj_formal_month(batch_id, target_mean, target_sd, MAY_START_DATE, formal_sequences["2026-05"])
    persist_lj_batch_outlier_snapshot(batch_id)
    return _summarize_lj_plan(plan, project_id, batch_id)


def _seed_zscore_dataset(plan: DemoDatasetPlan) -> DemoDatasetSummary:
    level_count = int(plan.level_count or 2)
    project_id = create_zscore_project(plan.project_name, level_count=level_count, input_value_type="raw")
    concentration_label = "双水平" if level_count == 2 else "三水平"
    batch_id = create_zscore_batch(
        project_id=project_id,
        instrument="Cobas c 702 演示仪器",
        reagent=f"{DEMO_NOTICE} - 多水平试剂",
        qc_material=f"{DEMO_NOTICE} - {concentration_label}质控品",
        concentration=concentration_label,
        lot_no=plan.lot_no,
        target_n=TARGET_N,
        level_1_label="Level 1",
        level_2_label="Level 2",
        level_3_label="Level 3" if level_count == 3 else None,
        cv_limit=6.0,
    )
    template_id = get_template_id_for_level_count(level_count)
    level_ids = [f"Level {index}" for index in range(1, level_count + 1)]
    base_targets = _zscore_base_targets(level_count, abnormal=plan.abnormal)
    building_maps = _building_zscore_maps(level_ids)
    for index, (timestamp, z_map) in enumerate(
        zip(_build_schedule(BUILDING_START_DATE, BUILDING_RECORD_COUNT), building_maps, strict=True),
        start=1,
    ):
        create_zscore_run(
            batch_id=batch_id,
            test_time=timestamp,
            operator=_operator_for(index),
            template_id=template_id,
            required_n=TARGET_N,
            manual_note=f"{DEMO_NOTICE}。" if index == 1 else "",
            level_results=[
                {
                    "level_id": level_id,
                    "raw_value": _round_value(
                        base_targets[level_id]["mean"] + z_map[level_id] * base_targets[level_id]["sd"]
                    ),
                }
                for level_id in level_ids
            ],
        )

    target_profiles = get_zscore_level_targets(batch_id, template_id, required_n=TARGET_N)
    formal_sequences = (
        _zscore_abnormal_zmaps(level_count)
        if plan.abnormal
        else _stable_zscore_months(level_ids)
    )
    _insert_zscore_formal_month(
        batch_id=batch_id,
        template_id=template_id,
        target_profiles=target_profiles,
        level_ids=level_ids,
        start_date=APRIL_START_DATE,
        z_maps=formal_sequences["2026-04"],
    )
    _insert_zscore_formal_month(
        batch_id=batch_id,
        template_id=template_id,
        target_profiles=target_profiles,
        level_ids=level_ids,
        start_date=MAY_START_DATE,
        z_maps=formal_sequences["2026-05"],
    )
    return _summarize_zscore_plan(plan, project_id, batch_id)


def _create_report_history_snapshots(summaries: list[DemoDatasetSummary]) -> int:
    created = 0
    for summary in summaries:
        if summary.batch_id is None:
            continue
        if summary.method == "lj":
            for report_month in REPORT_MONTHS:
                package = build_lj_monthly_report_package(summary.batch_id, report_month)
                save_lj_monthly_report_snapshot(package)
                created += 1
        elif summary.method == "zscore":
            for report_month in REPORT_MONTHS:
                package = build_zscore_monthly_report_package(summary.batch_id, report_month)
                save_zscore_monthly_report_snapshot(package)
                created += 1
    return created


def _summarize_lj_plan(plan: DemoDatasetPlan, project_id: int, batch_id: int) -> DemoDatasetSummary:
    qc_df, stats = persist_lj_batch_outlier_snapshot(batch_id)
    formal_counts = _lj_formal_counts_by_month(qc_df)
    abnormal_counts = _lj_abnormal_counts_by_month(qc_df)
    building_df = qc_df[qc_df["phase"] == LJ_BUILDING_PHASE_LABEL].copy()
    return DemoDatasetSummary(
        key=plan.key,
        method=plan.method,
        project_name=plan.project_name,
        lot_no=plan.lot_no,
        project_id=project_id,
        batch_id=batch_id,
        building_records=int(len(building_df)),
        effective_building_records=int(stats.get("effective_building_count", 0) or 0),
        formal_records_by_month=formal_counts,
        abnormal_records_by_month=abnormal_counts,
    )


def _summarize_zscore_plan(plan: DemoDatasetPlan, project_id: int, batch_id: int) -> DemoDatasetSummary:
    template_id = get_template_id_for_level_count(int(plan.level_count or 2))
    runs = get_zscore_runs(batch_id, template_id)
    building_runs = [run for run in runs if str(run.get("phase")) == PHASE_TARGET_BUILDING]
    formal_counts = _zscore_formal_counts_by_month(runs)
    abnormal_counts = _zscore_abnormal_counts_by_month(runs)
    target_profiles = get_zscore_level_targets(batch_id, template_id, required_n=TARGET_N)
    effective_counts = [
        int(target_profiles[level_id].get("collected_n", 0) or 0)
        for level_id in target_profiles
    ]
    return DemoDatasetSummary(
        key=plan.key,
        method=plan.method,
        project_name=plan.project_name,
        lot_no=plan.lot_no,
        project_id=project_id,
        batch_id=batch_id,
        building_records=len(building_runs),
        effective_building_records=min(effective_counts or [0]),
        formal_records_by_month=formal_counts,
        abnormal_records_by_month=abnormal_counts,
    )


def _insert_lj_formal_month(
    batch_id: int,
    target_mean: float,
    target_sd: float,
    start_date: date,
    zscores: list[dict[str, Any]],
) -> None:
    for offset, item in enumerate(zscores):
        timestamp = _build_schedule(start_date, len(zscores))[offset]
        z_value = float(item["z"])
        add_result(
            batch_id=batch_id,
            test_time=_format_datetime(timestamp),
            operator=_operator_for(offset + 21),
            value=_round_value(target_mean + z_value * target_sd),
            log_value=None,
            reagent_lot_changed=int(item.get("reagent_lot_changed", 0) or 0),
            manual_note=str(item.get("manual_note", "") or ""),
        )


def _insert_zscore_formal_month(
    *,
    batch_id: int,
    template_id: str,
    target_profiles: dict[str, dict[str, Any]],
    level_ids: list[str],
    start_date: date,
    z_maps: list[dict[str, Any]],
) -> None:
    for offset, item in enumerate(z_maps):
        timestamp = _build_schedule(start_date, len(z_maps))[offset]
        z_map = item["z"]
        create_zscore_run(
            batch_id=batch_id,
            test_time=timestamp,
            operator=_operator_for(offset + 21),
            template_id=template_id,
            required_n=TARGET_N,
            manual_note=str(item.get("manual_note", "") or ""),
            level_results=[
                {
                    "level_id": level_id,
                    "raw_value": _round_value(
                        _target_mean(target_profiles[level_id])
                        + float(z_map[level_id]) * _target_sd(target_profiles[level_id])
                    ),
                }
                for level_id in level_ids
            ],
        )


def _resolve_lj_target(batch_id: int) -> tuple[float, float]:
    results_df = get_results(batch_id, include_manual_note=True)
    _, stats = calculate_qc_results(results_df, TARGET_N)
    target_mean = _require_float(stats.get("mean"), "LJ 建靶均值")
    target_sd = _require_float(stats.get("sd"), "LJ 建靶 SD")
    _assert(not math.isclose(target_sd, 0.0, abs_tol=1e-12), "LJ 建靶 SD 不能为 0。")
    return target_mean, target_sd


def _find_dataset_ids(plan: DemoDatasetPlan) -> dict[str, int]:
    with get_connection() as connection:
        if plan.method == "instant":
            project_row = connection.execute(
                "SELECT id FROM instant_projects WHERE name = ?",
                (plan.project_name,),
            ).fetchone()
            _assert(project_row is not None, f"未找到演示项目：{plan.project_name}")
            batch_row = connection.execute(
                "SELECT id FROM instant_batches WHERE project_id = ? AND lot_no = ?",
                (int(project_row["id"]), plan.lot_no),
            ).fetchone()
        else:
            project_row = connection.execute(
                "SELECT id FROM projects WHERE name = ?",
                (plan.project_name,),
            ).fetchone()
            _assert(project_row is not None, f"未找到演示项目：{plan.project_name}")
            batch_row = connection.execute(
                "SELECT id FROM batches WHERE project_id = ? AND lot_no = ?",
                (int(project_row["id"]), plan.lot_no),
            ).fetchone()
    _assert(batch_row is not None, f"未找到演示批次：{plan.lot_no}")
    return {"project_id": int(project_row["id"]), "batch_id": int(batch_row["id"])}


def _validate_instant_dataset(plan: DemoDatasetPlan, batch_id: int) -> None:
    results_df = get_instant_results(batch_id, include_manual_note=True)
    context = build_instant_workbench_context(batch_id)
    summary = context["summary"]
    _assert(len(results_df) == 20, f"{plan.key} 应包含 20 条即时法记录。")
    _assert(int(summary.get("effective_count", 0) or 0) == 20, f"{plan.key} 应包含 20 个有效点。")
    if plan.abnormal:
        suspect_rows = results_df[results_df["is_outlier_suspect"].fillna(0).astype(int) == 1]
        _assert(not suspect_rows.empty, f"{plan.key} 应包含至少 1 个 SI 疑似离群点。")
        _assert(
            suspect_rows["manual_note"].fillna("").astype(str).str.strip().ne("").all(),
            f"{plan.key} 疑似离群点 manual_note 不能为空。",
        )


def _validate_lj_dataset(plan: DemoDatasetPlan, batch_id: int) -> None:
    qc_df, stats = persist_lj_batch_outlier_snapshot(batch_id)
    building_df = qc_df[qc_df["phase"] == LJ_BUILDING_PHASE_LABEL].copy()
    effective_building_df = building_df[building_df["is_building_included"].fillna(1).astype(int) == 1]
    _assert(len(building_df) == TARGET_N, f"{plan.key} LJ 建靶期必须有 20 条建靶数据。")
    _assert(len(effective_building_df) == TARGET_N, f"{plan.key} LJ 建靶期必须有 20 个有效建靶点。")
    _assert(bool(stats.get("target_ready")), f"{plan.key} LJ 建靶应已完成。")
    _assert(_month_phase_count(qc_df, "2026-03", LJ_FORMAL_PHASE_LABEL) == 0, f"{plan.key} 2026-03 不应包含正式期数据。")
    options = set(list_lj_report_month_options(batch_id))
    _assert(set(REPORT_MONTHS).issubset(options), f"{plan.key} LJ 月报月份选项缺少 2026-04 或 2026-05。")
    _assert("2026-03" not in options, f"{plan.key} LJ 月报月份选项不应包含建靶期 2026-03。")
    formal_counts = _lj_formal_counts_by_month(qc_df)
    for report_month in REPORT_MONTHS:
        _assert(
            formal_counts.get(report_month, 0) == FORMAL_RECORDS_PER_MONTH,
            f"{plan.key} {report_month} LJ 正式期记录数应为 30。",
        )
        package = build_lj_monthly_report_package(batch_id, report_month)
        _assert(
            package.report.statistics.formal_count == FORMAL_RECORDS_PER_MONTH,
            f"{plan.key} {report_month} LJ 月报 formal_count 应为 30。",
        )
    _validate_lj_formal_notes_are_abnormal(plan, qc_df)


def _validate_zscore_dataset(plan: DemoDatasetPlan, batch_id: int) -> None:
    level_count = int(plan.level_count or 2)
    template_id = get_template_id_for_level_count(level_count)
    level_ids = [f"Level {index}" for index in range(1, level_count + 1)]
    runs = get_zscore_runs(batch_id, template_id)
    building_runs = [run for run in runs if str(run.get("phase")) == PHASE_TARGET_BUILDING]
    _assert(len(building_runs) == TARGET_N, f"{plan.key} Z-score 建靶期必须有 20 次 run。")
    _assert(
        _zscore_month_phase_count(runs, "2026-03", PHASE_FORMAL_QC) == 0,
        f"{plan.key} 2026-03 不应包含正式期 run。",
    )
    for level_id in level_ids:
        count = sum(
            1
            for run in building_runs
            for level_result in run.get("level_results", [])
            if str(level_result.get("level_id")) == level_id
            and int(level_result.get("is_building_included", 1) or 0) == 1
        )
        _assert(count == TARGET_N, f"{plan.key} {level_id} 必须有 20 个有效建靶值。")
    targets = get_zscore_level_targets(batch_id, template_id, required_n=TARGET_N)
    for level_id in level_ids:
        _assert(
            int(targets[level_id].get("collected_n", 0) or 0) == TARGET_N,
            f"{plan.key} {level_id} target profile collected_n 应为 20。",
        )
    options = set(list_zscore_report_month_options(batch_id))
    _assert(set(REPORT_MONTHS).issubset(options), f"{plan.key} Z-score 月报月份选项缺少 2026-04 或 2026-05。")
    _assert("2026-03" not in options, f"{plan.key} Z-score 月报月份选项不应包含建靶期 2026-03。")
    formal_counts = _zscore_formal_counts_by_month(runs)
    for report_month in REPORT_MONTHS:
        _assert(
            formal_counts.get(report_month, 0) == FORMAL_RECORDS_PER_MONTH,
            f"{plan.key} {report_month} Z-score 正式期 run 数应为 30。",
        )
        package = build_zscore_monthly_report_package(batch_id, report_month)
        _assert(
            package.report.statistics.formal_count == FORMAL_RECORDS_PER_MONTH,
            f"{plan.key} {report_month} Z-score 月报 formal_count 应为 30。",
        )
    _validate_zscore_formal_notes_are_abnormal(plan, runs)


def _validate_report_actions(abnormal_records: list[Any], corrective_actions: list[str]) -> None:
    for record in abnormal_records:
        note = str(getattr(record, "manual_note", "") or "").strip()
        _assert(note, "所有 abnormal_records 的 manual_note 都不能为空。")
    _assert(
        not any("未填写" in str(action or "") for action in corrective_actions),
        "月报 corrective_actions 不能出现“未填写”。",
    )


def _lj_formal_counts_by_month(qc_df: pd.DataFrame) -> dict[str, int]:
    if qc_df.empty:
        return {}
    formal_df = qc_df[qc_df["phase"] == LJ_FORMAL_PHASE_LABEL].copy()
    return _count_dataframe_by_month(formal_df, "test_time")


def _lj_abnormal_counts_by_month(qc_df: pd.DataFrame) -> dict[str, int]:
    if qc_df.empty:
        return {}
    abnormal_df = qc_df[qc_df["status"].isin(["警告", "失控"])].copy()
    return _count_dataframe_by_month(abnormal_df, "test_time")


def _zscore_formal_counts_by_month(runs: list[dict[str, Any]]) -> dict[str, int]:
    return _count_runs_by_month([run for run in runs if str(run.get("phase")) == PHASE_FORMAL_QC])


def _zscore_abnormal_counts_by_month(runs: list[dict[str, Any]]) -> dict[str, int]:
    return _count_runs_by_month(
        [
            run
            for run in runs
            if str(run.get("phase")) == PHASE_FORMAL_QC
            and str(run.get("run_status")) in {"warning", "reject"}
        ]
    )


def _month_phase_count(qc_df: pd.DataFrame, report_month: str, phase_label: str) -> int:
    if qc_df.empty:
        return 0
    dataframe = qc_df.copy()
    dataframe["test_time"] = pd.to_datetime(dataframe["test_time"], errors="coerce")
    return int(
        (
            (dataframe["phase"] == phase_label)
            & (dataframe["test_time"].dt.to_period("M").astype(str) == report_month)
        ).sum()
    )


def _zscore_month_phase_count(runs: list[dict[str, Any]], report_month: str, phase: str) -> int:
    count = 0
    for run in runs:
        if str(run.get("phase")) != phase:
            continue
        if pd.Timestamp(run["test_time"]).to_period("M").strftime("%Y-%m") == report_month:
            count += 1
    return count


def _validate_lj_formal_notes_are_abnormal(plan: DemoDatasetPlan, qc_df: pd.DataFrame) -> None:
    if qc_df.empty or "manual_note" not in qc_df.columns:
        return
    formal_df = qc_df[qc_df["phase"] == LJ_FORMAL_PHASE_LABEL].copy()
    noted_non_abnormal = formal_df[
        formal_df["manual_note"].fillna("").astype(str).str.strip().ne("")
        & ~formal_df["status"].isin(["警告", "失控"])
    ]
    _assert(
        noted_non_abnormal.empty,
        f"{plan.key} 存在 manual_note 写在非警告/失控正式期记录上的情况。",
    )


def _validate_zscore_formal_notes_are_abnormal(plan: DemoDatasetPlan, runs: list[dict[str, Any]]) -> None:
    noted_non_abnormal = [
        run
        for run in runs
        if str(run.get("phase")) == PHASE_FORMAL_QC
        and str(run.get("manual_note", "") or "").strip()
        and str(run.get("run_status")) not in {"warning", "reject"}
    ]
    _assert(
        not noted_non_abnormal,
        f"{plan.key} 存在 manual_note 写在非 warning/reject 正式期 run 上的情况。",
    )


def _count_dataframe_by_month(dataframe: pd.DataFrame, column_name: str) -> dict[str, int]:
    if dataframe.empty:
        return {}
    months = pd.to_datetime(dataframe[column_name], errors="coerce").dt.to_period("M").astype(str)
    return {str(month): int(count) for month, count in months.value_counts().sort_index().items()}


def _count_runs_by_month(runs: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for run in runs:
        month = pd.Timestamp(run["test_time"]).to_period("M").strftime("%Y-%m")
        counts[month] = counts.get(month, 0) + 1
    return counts


def _build_schedule(start_date: date, count: int) -> list[datetime]:
    return [
        datetime.combine(start_date + timedelta(days=offset), time(hour=9, minute=(offset * 3) % 60))
        for offset in range(count)
    ]


def _building_zscores() -> list[float]:
    return [
        -0.82,
        -0.34,
        0.18,
        0.63,
        -0.58,
        0.44,
        -0.12,
        0.76,
        -0.69,
        0.27,
        -0.41,
        0.88,
        -0.21,
        0.55,
        -0.73,
        0.09,
        0.36,
        -0.49,
        0.71,
        -0.16,
    ]


def _stable_single_level_months() -> dict[str, list[dict[str, Any]]]:
    april = [
        -0.42, 0.31, -0.18, 0.46, -0.35, 0.22, -0.51, 0.39, -0.26, 0.58,
        -0.44, 0.16, -0.62, 0.47, -0.21, 0.33, -0.37, 0.52, -0.12, 0.28,
        -0.49, 0.41, -0.23, 0.36, -0.57, 0.19, -0.31, 0.48, -0.15, 0.27,
    ]
    may = [
        0.25, -0.36, 0.43, -0.18, 0.51, -0.47, 0.12, -0.28, 0.61, -0.39,
        0.18, -0.55, 0.34, -0.22, 0.48, -0.31, 0.07, -0.45, 0.53, -0.16,
        0.29, -0.52, 0.37, -0.24, 0.44, -0.33, 0.14, -0.41, 0.56, -0.21,
    ]
    return {
        "2026-04": [{"z": value} for value in april],
        "2026-05": [{"z": value} for value in may],
    }


def _lj_abnormal_zscores() -> dict[str, list[dict[str, Any]]]:
    april = [
        -0.35, 0.41, -0.22, 0.18, -0.47, 0.33, -0.26, 3.25, 0.36, -0.28,
        0.19, -0.41, 0.52, -0.33, 2.18, -0.24, 0.44, -0.52, 0.21, -0.37,
        0.58, -0.18, -0.46, 0.32, -0.29, 0.49, -0.15, 0.25, -0.39, 0.43,
    ]
    may = [
        -0.42, 0.38, -0.31, 0.27, -0.44, 0.36, -0.22, 2.18, 2.36, 0.28,
        -0.47, 0.39, -0.25, 0.51, -0.36, 0.17, -0.58, 0.22, -0.41, 0.49,
        -0.19, 0.31, -0.52, 0.24, -0.33, 0.46, -0.27, 0.35, -0.18, 0.42,
    ]
    april_items = [{"z": value} for value in april]
    may_items = [{"z": value} for value in may]
    april_items[7]["manual_note"] = NOTE_LJ_13S
    april_items[14]["manual_note"] = NOTE_LJ_ENV
    may_items[7]["manual_note"] = NOTE_LJ_SHIFT
    may_items[8]["manual_note"] = NOTE_LJ_SHIFT
    return {"2026-04": april_items, "2026-05": may_items}


def _building_zscore_maps(level_ids: list[str]) -> list[dict[str, float]]:
    base = _building_zscores()
    maps: list[dict[str, float]] = []
    for index, z_value in enumerate(base):
        row: dict[str, float] = {}
        for level_position, level_id in enumerate(level_ids):
            modifier = 0.18 * math.sin((index + 1) * (level_position + 2))
            row[level_id] = _clamp(z_value * (0.72 - level_position * 0.05) + modifier, -1.05, 1.05)
        maps.append(row)
    return maps


def _stable_zscore_months(level_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    single = _stable_single_level_months()
    result: dict[str, list[dict[str, Any]]] = {}
    for month, items in single.items():
        z_maps: list[dict[str, Any]] = []
        for index, item in enumerate(items):
            base_z = float(item["z"])
            z_maps.append(
                {
                    "z": {
                        level_id: _clamp(
                            base_z * (0.72 - level_index * 0.06)
                            + 0.22 * math.sin((index + 1) * (level_index + 1)),
                            -1.15,
                            1.15,
                        )
                        for level_index, level_id in enumerate(level_ids)
                    }
                }
            )
        result[month] = z_maps
    return result


def _zscore_abnormal_zmaps(level_count: int) -> dict[str, list[dict[str, Any]]]:
    level_ids = [f"Level {index}" for index in range(1, level_count + 1)]
    stable = _stable_zscore_months(level_ids)
    if level_count == 2:
        april = stable["2026-04"]
        may = stable["2026-05"]
        april[7] = {"z": {"Level 1": 3.18, "Level 2": -0.42}, "manual_note": NOTE_ZS_RANDOM}
        may[7] = {"z": {"Level 1": 2.16, "Level 2": 0.28}, "manual_note": NOTE_ZS_SYSTEM}
        may[8] = {"z": {"Level 1": 2.31, "Level 2": -0.22}, "manual_note": NOTE_ZS_SYSTEM}
        return {"2026-04": april, "2026-05": may}

    april = stable["2026-04"]
    may = stable["2026-05"]
    april[7] = {
        "z": {"Level 1": 2.28, "Level 2": 2.18, "Level 3": 0.26},
        "manual_note": NOTE_ZS_2OF3,
    }
    may[7] = {
        "z": {"Level 1": 1.24, "Level 2": 1.31, "Level 3": 1.18},
        "manual_note": NOTE_ZS_SYSTEM,
    }
    may[14] = {
        "z": {"Level 1": -2.24, "Level 2": 2.18, "Level 3": -0.18},
        "manual_note": NOTE_ZS_RANDOM,
    }
    return {"2026-04": april, "2026-05": may}


def _instant_values(*, abnormal: bool) -> list[float]:
    values = [
        28.35,
        28.42,
        28.31,
        28.48,
        28.39,
        28.44,
        28.29,
        28.52,
        28.36,
        28.46,
        28.33,
        28.41,
        28.49,
        28.37,
        28.45,
        28.32,
        28.50,
        28.38,
        28.43,
        28.34,
    ]
    if abnormal:
        values[11] = 31.2
    return values


def _zscore_base_targets(level_count: int, *, abnormal: bool) -> dict[str, dict[str, float]]:
    if level_count == 2:
        return {
            "Level 1": {"mean": 26.8 if abnormal else 98.4, "sd": 0.42 if abnormal else 1.05},
            "Level 2": {"mean": 31.2 if abnormal else 152.7, "sd": 0.56 if abnormal else 1.72},
        }
    return {
        "Level 1": {"mean": 18.4 if abnormal else 65.2, "sd": 0.32 if abnormal else 0.88},
        "Level 2": {"mean": 42.6 if abnormal else 121.6, "sd": 0.74 if abnormal else 1.42},
        "Level 3": {"mean": 88.9 if abnormal else 207.3, "sd": 1.15 if abnormal else 2.35},
    }


def _values_from_zscores(mean: float, sd: float, zscores: list[float]) -> list[float]:
    return [_round_value(mean + z_value * sd) for z_value in zscores]


def _target_mean(profile: dict[str, Any]) -> float:
    value = profile.get("final_target_mean") or profile.get("target_mean") or profile.get("provisional_mean")
    return _require_float(value, "Z-score 靶均值")


def _target_sd(profile: dict[str, Any]) -> float:
    value = profile.get("final_target_sd") or profile.get("target_sd") or profile.get("provisional_sd")
    sd = _require_float(value, "Z-score 靶 SD")
    _assert(not math.isclose(sd, 0.0, abs_tol=1e-12), "Z-score 靶 SD 不能为 0。")
    return sd


def _operator_for(index: int) -> str:
    return OPERATOR_POOL[(index - 1) % len(OPERATOR_POOL)]


def _format_datetime(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _round_value(value: float, digits: int = 4) -> float:
    return float(round(float(value), digits))


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, float(value)))


def _require_float(value: Any, label: str) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"{label} 缺失或不是数值。") from exc
    if not math.isfinite(numeric):
        raise AssertionError(f"{label} 不是有限数值。")
    return numeric


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
