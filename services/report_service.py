from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from io import BytesIO
import math
import textwrap
from typing import Any

import pandas as pd
from matplotlib import font_manager, pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages

from database import (
    create_report_export_snapshot,
    get_batch,
    get_results,
)
from plotting import plot_lj_chart
from qc_logic import LJ_FORMAL_PHASE_LABEL, persist_lj_batch_outlier_snapshot
from services.value_type_service import get_input_value_type_label, normalize_input_value_type
from zscore_logic import (
    PHASE_FORMAL_QC,
    build_zscore_plot_dataframe as build_zscore_plot_dataframe_logic,
    build_zscore_rule_templates,
    format_level_id_display,
    format_zscore_level_label_summary,
    get_phase_label,
    get_zscore_level_targets,
    get_zscore_runs,
    resolve_zscore_batch_context,
    should_enable_formal_rules,
)
from zscore_plotting import plot_zscore_overlay


REPORT_TYPE_LJ_MONTHLY = "lj_monthly_report"
LJ_METHOD_LABEL = "单水平（LJ法）"
LJ_REPORT_TITLE = "单水平（LJ法）月度质控报告"
DEFAULT_DECLARATION = (
    "本报告由软件根据所选月份内的 LJ 正式期质控数据自动生成，仅作为室内质控归档与复核参考。"
    "报告结果应结合原始记录、异常备注和实验室实际处理情况共同判读。"
)
PDF_FONT_CANDIDATES = [
    "Microsoft YaHei",
    "SimHei",
    "SimSun",
    "Noto Sans CJK SC",
]
ABNORMAL_TABLE_COLUMNS = ["检测时间", "检测序号", "结果值", "状态", "触发规则", "手动备注"]
ABNORMAL_TABLE_WIDTHS = [0.19, 0.10, 0.12, 0.10, 0.14, 0.35]
ABNORMAL_RECORDS_PER_PAGE = 12
REPORT_TYPE_ZSCORE_MONTHLY = "zscore_monthly_report"
ZSCORE_METHOD_LABEL = "多水平（Z-score法）"
ZSCORE_REPORT_TITLE = "多水平（Z-score法）月度质控报告"
ZSCORE_TEMPLATE_DISPLAY_NAMES = {
    "2_level_classic": "两水平经典多规则组合",
    "3_level_threes": "三水平多规则组合",
    "2-level classic": "两水平经典多规则组合",
    "3-level threes": "三水平多规则组合",
}
ZSCORE_RUN_STATUS_LABELS = {
    "accept": "在控",
    "warning": "警告",
    "reject": "失控",
    "pending": "待判读",
}
ZSCORE_ERROR_TYPE_LABELS = {
    "random": "随机误差风险",
    "systematic": "系统误差风险",
    "shift": "系统偏移风险",
    "trend": "趋势性漂移风险",
    "mixed": "混合误差风险",
    "not_applicable": "建靶阶段不适用",
    "unknown": "待进一步判断",
}
ZSCORE_RULE_DISPLAY_NAMES = {
    "10_x": "10x",
    "12_x": "12x",
}
ZSCORE_ABNORMAL_TABLE_COLUMNS = ["检测时间", "run 序号", "状态", "触发规则", "误差类型", "手动备注"]
ZSCORE_ABNORMAL_TABLE_WIDTHS = [0.18, 0.10, 0.10, 0.18, 0.16, 0.28]


@dataclass(frozen=True)
class LjMonthlyReportBasicInfo:
    project_name: str
    report_month_label: str
    method_label: str
    input_value_type_label: str
    lot_no: str
    instrument: str
    reagent: str
    qc_material: str
    concentration: str
    target_source_label: str
    target_source_detail: str


@dataclass(frozen=True)
class LjMonthlyReportStatistics:
    formal_count: int
    in_control_count: int
    warning_count: int
    out_of_control_count: int
    undetermined_count: int
    monthly_mean: float | None
    monthly_sd: float | None
    monthly_cv: float | None
    target_mean: float | None
    target_sd: float | None
    cv_limit: float | None


@dataclass(frozen=True)
class LjMonthlyAbnormalRecord:
    test_time: str
    sequence: int
    value: float
    status: str
    rule_hits: str
    manual_note: str


@dataclass(frozen=True)
class LjMonthlyReportData:
    report_type: str
    title: str
    method_label: str
    report_month: str
    report_month_label: str
    report_period_label: str
    generated_at: str
    file_name: str
    project_id: int
    batch_id: int
    input_value_type: str
    input_value_type_label: str
    basic_info: LjMonthlyReportBasicInfo
    statistics: LjMonthlyReportStatistics
    abnormal_records: list[LjMonthlyAbnormalRecord]
    corrective_actions: list[str]
    overview_text: str
    corrective_actions_empty_text: str
    abnormal_summary_text: str
    conclusion: str
    declaration: str
    chart_title: str
    chart_axis_label: str

    def to_snapshot_summary(self) -> dict[str, Any]:
        return {
            "report_type": self.report_type,
            "title": self.title,
            "method_label": self.method_label,
            "report_month": self.report_month,
            "report_month_label": self.report_month_label,
            "report_period_label": self.report_period_label,
            "generated_at": self.generated_at,
            "file_name": self.file_name,
            "project_id": self.project_id,
            "batch_id": self.batch_id,
            "input_value_type": self.input_value_type,
            "input_value_type_label": self.input_value_type_label,
            "basic_info": asdict(self.basic_info),
            "statistics": asdict(self.statistics),
            "abnormal_records": [asdict(record) for record in self.abnormal_records],
            "corrective_actions": list(self.corrective_actions),
            "overview_text": self.overview_text,
            "corrective_actions_empty_text": self.corrective_actions_empty_text,
            "abnormal_summary_text": self.abnormal_summary_text,
            "conclusion": self.conclusion,
            "declaration": self.declaration,
            "chart_title": self.chart_title,
            "chart_axis_label": self.chart_axis_label,
        }


@dataclass
class LjMonthlyReportPackage:
    report: LjMonthlyReportData
    formal_df: pd.DataFrame
    stats: dict[str, Any]


@dataclass(frozen=True)
class ZScoreMonthlyReportBasicInfo:
    project_name: str
    report_month_label: str
    method_label: str
    input_value_type_label: str
    level_count_label: str
    level_summary: str
    template_label: str
    lot_no: str
    instrument: str
    reagent: str
    qc_material: str
    concentration: str
    target_source_label: str
    target_source_detail: str


@dataclass(frozen=True)
class ZScoreMonthlyReportStatistics:
    formal_count: int
    in_control_count: int
    warning_count: int
    out_of_control_count: int
    template_label: str
    current_phase_label: str
    all_levels_ready: bool


@dataclass(frozen=True)
class ZScoreMonthlyLevelStatistic:
    level_id: str
    level_label: str
    monthly_count: int
    monthly_mean: float | None
    monthly_sd: float | None
    monthly_cv: float | None
    target_mean: float | None
    target_sd: float | None
    cv_limit: float | None


@dataclass(frozen=True)
class ZScoreMonthlyAbnormalRecord:
    test_time: str
    run_sequence: int
    status: str
    rule_hits: str
    error_type: str
    manual_note: str


@dataclass(frozen=True)
class ZScoreMonthlyReportData:
    report_type: str
    title: str
    method_label: str
    report_month: str
    report_month_label: str
    report_period_label: str
    generated_at: str
    file_name: str
    project_id: int
    batch_id: int
    input_value_type: str
    input_value_type_label: str
    basic_info: ZScoreMonthlyReportBasicInfo
    statistics: ZScoreMonthlyReportStatistics
    level_statistics: list[ZScoreMonthlyLevelStatistic]
    abnormal_records: list[ZScoreMonthlyAbnormalRecord]
    corrective_actions: list[str]
    overview_text: str
    corrective_actions_empty_text: str
    abnormal_summary_text: str
    conclusion: str
    declaration: str
    chart_title: str
    chart_axis_label: str

    def to_snapshot_summary(self) -> dict[str, Any]:
        return {
            "report_type": self.report_type,
            "title": self.title,
            "method_label": self.method_label,
            "report_month": self.report_month,
            "report_month_label": self.report_month_label,
            "report_period_label": self.report_period_label,
            "generated_at": self.generated_at,
            "file_name": self.file_name,
            "project_id": self.project_id,
            "batch_id": self.batch_id,
            "input_value_type": self.input_value_type,
            "input_value_type_label": self.input_value_type_label,
            "basic_info": asdict(self.basic_info),
            "statistics": asdict(self.statistics),
            "level_statistics": [asdict(item) for item in self.level_statistics],
            "abnormal_records": [asdict(record) for record in self.abnormal_records],
            "corrective_actions": list(self.corrective_actions),
            "overview_text": self.overview_text,
            "corrective_actions_empty_text": self.corrective_actions_empty_text,
            "abnormal_summary_text": self.abnormal_summary_text,
            "conclusion": self.conclusion,
            "declaration": self.declaration,
            "chart_title": self.chart_title,
            "chart_axis_label": self.chart_axis_label,
        }


@dataclass
class ZScoreMonthlyReportPackage:
    report: ZScoreMonthlyReportData
    monthly_plot_df: pd.DataFrame
    active_levels: list[str]


def list_lj_report_month_options(batch_id: int) -> list[str]:
    results_df = get_results(batch_id, include_manual_note=True)
    if results_df.empty:
        return []
    months = (
        results_df["test_time"]
        .dropna()
        .dt.to_period("M")
        .astype(str)
        .drop_duplicates()
        .tolist()
    )
    return sorted(months, reverse=True)


def build_lj_monthly_report_package(batch_id: int, report_month: str) -> LjMonthlyReportPackage:
    normalized_month = _normalize_report_month(report_month)
    batch = get_batch(batch_id)
    qc_df, stats = persist_lj_batch_outlier_snapshot(batch_id)
    formal_df = _filter_monthly_formal_df(qc_df, normalized_month)
    if formal_df.empty:
        raise ValueError("所选月份无正式期数据，无法生成单水平（LJ法）月报。")

    input_value_type = normalize_input_value_type(batch["input_value_type"])
    input_value_type_label = get_input_value_type_label(input_value_type)
    statistics = _build_statistics(formal_df, stats, batch)
    abnormal_records = _build_abnormal_records(formal_df)
    corrective_actions = _build_corrective_actions(abnormal_records)
    report_month_label = _format_report_month_label(normalized_month)
    report_period_label = _format_report_period_label(normalized_month)
    target_source_label, target_source_detail = _resolve_target_source(batch)
    overview_text = _build_monthly_overview(statistics, batch)
    corrective_actions_empty_text = _build_corrective_actions_empty_text(abnormal_records)
    abnormal_summary_text = _build_abnormal_summary_text(
        abnormal_records=abnormal_records,
        corrective_actions_empty_text=corrective_actions_empty_text,
    )
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    file_name = _build_report_file_name(
        project_name=str(batch["project_name"]),
        lot_no=str(batch["lot_no"]),
        report_month=normalized_month,
    )
    report = LjMonthlyReportData(
        report_type=REPORT_TYPE_LJ_MONTHLY,
        title=LJ_REPORT_TITLE,
        method_label=LJ_METHOD_LABEL,
        report_month=normalized_month,
        report_month_label=report_month_label,
        report_period_label=report_period_label,
        generated_at=generated_at,
        file_name=file_name,
        project_id=int(batch["project_id"]),
        batch_id=int(batch["id"]),
        input_value_type=input_value_type,
        input_value_type_label=input_value_type_label,
        basic_info=LjMonthlyReportBasicInfo(
            project_name=str(batch["project_name"]),
            report_month_label=report_month_label,
            method_label=LJ_METHOD_LABEL,
            input_value_type_label=input_value_type_label,
            lot_no=str(batch["lot_no"] or "-"),
            instrument=str(batch["instrument"] or "-"),
            reagent=str(batch["reagent"] or "-"),
            qc_material=str(batch["qc_material"] or "-"),
            concentration=str(batch["concentration"] or "-"),
            target_source_label=target_source_label,
            target_source_detail=target_source_detail,
        ),
        statistics=statistics,
        abnormal_records=abnormal_records,
        corrective_actions=corrective_actions,
        overview_text=overview_text,
        corrective_actions_empty_text=corrective_actions_empty_text,
        abnormal_summary_text=abnormal_summary_text,
        conclusion=_build_conclusion(statistics),
        declaration=DEFAULT_DECLARATION,
        chart_title=(
            f"{LJ_METHOD_LABEL}月度质控图\n"
            f"项目：{batch['project_name']}｜质控批号：{batch['lot_no']}｜报告月份：{report_month_label}"
        ),
        chart_axis_label=input_value_type_label,
    )
    return LjMonthlyReportPackage(
        report=report,
        formal_df=formal_df.copy(),
        stats=dict(stats or {}),
    )


def build_lj_monthly_report_pdf(package: LjMonthlyReportPackage) -> bytes:
    buffer = BytesIO()
    font_name = _resolve_pdf_font_name()
    with plt.rc_context({"font.family": font_name, "axes.unicode_minus": False}):
        with PdfPages(buffer) as pdf:
            metadata = pdf.infodict()
            metadata["Title"] = package.report.title
            metadata["Author"] = "LJQCApp"
            metadata["Subject"] = REPORT_TYPE_LJ_MONTHLY
            metadata["Keywords"] = "LJ, monthly report, single level"
            metadata["Creator"] = "LJQCApp"
            metadata["CreationDate"] = datetime.now()

            summary_figure = _build_summary_page(package.report)
            pdf.savefig(summary_figure, bbox_inches="tight")
            plt.close(summary_figure)

            chart_figure = plot_lj_chart(
                qc_df=package.formal_df.copy(),
                stats=package.stats,
                title=package.report.chart_title,
                view_mode="正式质控图",
                y_axis_mode="标准视图",
                standard_sd_limit=4.0,
                y_axis_label=package.report.chart_axis_label,
            )
            chart_figure.set_size_inches(8.27, 11.69)
            pdf.savefig(chart_figure, bbox_inches="tight")
            plt.close(chart_figure)

            abnormal_chunks = _chunk_abnormal_records(package.report.abnormal_records)
            for chunk_index, chunk in enumerate(abnormal_chunks, start=1):
                abnormal_figure = _build_abnormal_page(
                    report=package.report,
                    abnormal_chunk=chunk,
                    chunk_index=chunk_index,
                    chunk_count=len(abnormal_chunks),
                )
                pdf.savefig(abnormal_figure, bbox_inches="tight")
                plt.close(abnormal_figure)

            action_figure = _build_action_page(package.report)
            pdf.savefig(action_figure, bbox_inches="tight")
            plt.close(action_figure)

    return buffer.getvalue()


def save_lj_monthly_report_snapshot(package: LjMonthlyReportPackage) -> int:
    report = package.report
    return create_report_export_snapshot(
        report_type=report.report_type,
        project_id=report.project_id,
        batch_id=report.batch_id,
        report_month=report.report_month,
        generated_at=report.generated_at,
        input_value_type=report.input_value_type,
        method_label=report.method_label,
        summary=report.to_snapshot_summary(),
        file_name=report.file_name,
    )


def build_lj_monthly_preview_summary(report: LjMonthlyReportData) -> list[tuple[str, str]]:
    statistics = report.statistics
    return [
        ("正式期总记录数", str(statistics.formal_count)),
        ("在控记录数", str(statistics.in_control_count)),
        ("警告记录数", str(statistics.warning_count)),
        ("失控记录数", str(statistics.out_of_control_count)),
        ("月度均值", _format_float(statistics.monthly_mean)),
        ("月度 SD", _format_monthly_stat_text(statistics, "sd")),
        ("月度 CV%", _format_monthly_stat_text(statistics, "cv")),
        ("当前目标均值", _format_float(statistics.target_mean)),
        ("当前目标 SD", _format_float(statistics.target_sd)),
        ("批次 CV 要求", _format_float(statistics.cv_limit, digits=2, suffix="%")),
    ]


def list_zscore_report_month_options(batch_id: int) -> list[str]:
    batch_context = resolve_zscore_batch_context(batch_id)
    history_runs = get_zscore_runs(batch_id, str(batch_context["template_id"]))
    if not history_runs:
        return []
    run_times = [
        pd.Timestamp(run["test_time"])
        for run in history_runs
        if run.get("test_time") is not None
    ]
    if not run_times:
        return []
    months = pd.Series(run_times).dt.to_period("M").astype(str).drop_duplicates().tolist()
    return sorted(months, reverse=True)


def build_zscore_monthly_report_package(
    batch_id: int,
    report_month: str,
) -> ZScoreMonthlyReportPackage:
    normalized_month = _normalize_report_month(report_month)
    batch_context = resolve_zscore_batch_context(batch_id)
    batch = batch_context["batch"]
    template_id = str(batch_context["template_id"])
    template = batch_context["template"]
    required_level_ids = list(batch_context["required_level_ids"])
    level_label_map = dict(batch_context["level_label_map"])
    history_runs = get_zscore_runs(batch_id, template_id)
    monthly_formal_runs = _filter_zscore_monthly_formal_runs(history_runs, normalized_month)
    if not monthly_formal_runs:
        raise ValueError("所选月份无正式期数据，无法生成多水平（Z-score法）月报。")

    input_value_type = normalize_input_value_type(batch["input_value_type"])
    input_value_type_label = get_input_value_type_label(input_value_type)
    template_label = _format_zscore_template_label(template)
    level_count = int(batch_context["level_count"])
    level_count_label = f"{level_count} 水平"
    level_summary = format_zscore_level_label_summary(batch, required_level_ids)
    level_target_profiles = _resolve_zscore_level_target_profiles(batch_id, template_id, template, required_level_ids)
    all_levels_ready = should_enable_formal_rules(level_target_profiles, required_level_ids)
    statistics = _build_zscore_monthly_statistics(
        monthly_formal_runs=monthly_formal_runs,
        template_label=template_label,
        current_phase_label=get_phase_label(PHASE_FORMAL_QC),
        all_levels_ready=all_levels_ready,
    )
    level_statistics = _build_zscore_level_statistics(
        monthly_formal_runs=monthly_formal_runs,
        required_level_ids=required_level_ids,
        level_label_map=level_label_map,
        level_target_profiles=level_target_profiles,
        batch=batch,
    )
    abnormal_records = _build_zscore_abnormal_records(monthly_formal_runs)
    corrective_actions = _build_zscore_corrective_actions(abnormal_records)
    report_month_label = _format_report_month_label(normalized_month)
    report_period_label = _format_report_period_label(normalized_month)
    target_source_label, target_source_detail = _resolve_zscore_target_source()
    overview_text = _build_zscore_monthly_overview(statistics, template_label)
    corrective_actions_empty_text = _build_zscore_corrective_actions_empty_text(abnormal_records)
    abnormal_summary_text = _build_zscore_abnormal_summary_text(
        abnormal_records=abnormal_records,
        corrective_actions_empty_text=corrective_actions_empty_text,
    )
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    file_name = _build_zscore_report_file_name(
        project_name=str(batch["project_name"]),
        lot_no=str(batch["lot_no"]),
        report_month=normalized_month,
    )
    report = ZScoreMonthlyReportData(
        report_type=REPORT_TYPE_ZSCORE_MONTHLY,
        title=ZSCORE_REPORT_TITLE,
        method_label=ZSCORE_METHOD_LABEL,
        report_month=normalized_month,
        report_month_label=report_month_label,
        report_period_label=report_period_label,
        generated_at=generated_at,
        file_name=file_name,
        project_id=int(batch["project_id"]),
        batch_id=int(batch["id"]),
        input_value_type=input_value_type,
        input_value_type_label=input_value_type_label,
        basic_info=ZScoreMonthlyReportBasicInfo(
            project_name=str(batch["project_name"]),
            report_month_label=report_month_label,
            method_label=ZSCORE_METHOD_LABEL,
            input_value_type_label=input_value_type_label,
            level_count_label=level_count_label,
            level_summary=level_summary,
            template_label=template_label,
            lot_no=str(batch["lot_no"] or "-"),
            instrument=str(batch["instrument"] or "-"),
            reagent=str(batch["reagent"] or "-"),
            qc_material=str(batch["qc_material"] or "-"),
            concentration=str(batch["concentration"] or "-"),
            target_source_label=target_source_label,
            target_source_detail=target_source_detail,
        ),
        statistics=statistics,
        level_statistics=level_statistics,
        abnormal_records=abnormal_records,
        corrective_actions=corrective_actions,
        overview_text=overview_text,
        corrective_actions_empty_text=corrective_actions_empty_text,
        abnormal_summary_text=abnormal_summary_text,
        conclusion=_build_zscore_monthly_conclusion(statistics),
        declaration=DEFAULT_DECLARATION,
        chart_title=(
            f"{ZSCORE_METHOD_LABEL}月度质控图\n"
            f"项目：{batch['project_name']}｜质控批号：{batch['lot_no']}｜"
            f"水平数：{level_count_label}｜报告月份：{report_month_label}"
        ),
        chart_axis_label=input_value_type_label,
    )
    full_plot_df = build_zscore_plot_dataframe_logic(history_runs, draft_run=None, display_phase=None)
    monthly_plot_df = _filter_zscore_monthly_plot_df(full_plot_df, normalized_month)
    return ZScoreMonthlyReportPackage(
        report=report,
        monthly_plot_df=monthly_plot_df,
        active_levels=required_level_ids,
    )


def build_zscore_monthly_report_pdf(package: ZScoreMonthlyReportPackage) -> bytes:
    buffer = BytesIO()
    font_name = _resolve_pdf_font_name()
    with plt.rc_context({"font.family": font_name, "axes.unicode_minus": False}):
        with PdfPages(buffer) as pdf:
            metadata = pdf.infodict()
            metadata["Title"] = package.report.title
            metadata["Author"] = "LJQCApp"
            metadata["Subject"] = REPORT_TYPE_ZSCORE_MONTHLY
            metadata["Keywords"] = "Z-score, monthly report, multi level"
            metadata["Creator"] = "LJQCApp"
            metadata["CreationDate"] = datetime.now()

            summary_figure = _build_zscore_summary_page(package.report)
            pdf.savefig(summary_figure, bbox_inches="tight")
            plt.close(summary_figure)

            chart_figure = plot_zscore_overlay(
                plot_df=package.monthly_plot_df.copy(),
                title=package.report.chart_title,
                active_levels=package.active_levels,
                phase_scope="formal",
                y_axis_mode="标准视图",
                standard_sd_limit=4.0,
                y_axis_label=package.report.chart_axis_label,
            )
            chart_figure.set_size_inches(8.27, 11.69)
            pdf.savefig(chart_figure, bbox_inches="tight")
            plt.close(chart_figure)

            level_figure = _build_zscore_level_summary_page(package.report)
            pdf.savefig(level_figure, bbox_inches="tight")
            plt.close(level_figure)

            abnormal_chunks = _chunk_zscore_abnormal_records(package.report.abnormal_records)
            for chunk_index, chunk in enumerate(abnormal_chunks, start=1):
                abnormal_figure = _build_zscore_abnormal_page(
                    report=package.report,
                    abnormal_chunk=chunk,
                    chunk_index=chunk_index,
                    chunk_count=len(abnormal_chunks),
                )
                pdf.savefig(abnormal_figure, bbox_inches="tight")
                plt.close(abnormal_figure)

            action_figure = _build_zscore_action_page(package.report)
            pdf.savefig(action_figure, bbox_inches="tight")
            plt.close(action_figure)

    return buffer.getvalue()


def save_zscore_monthly_report_snapshot(package: ZScoreMonthlyReportPackage) -> int:
    report = package.report
    return create_report_export_snapshot(
        report_type=report.report_type,
        project_id=report.project_id,
        batch_id=report.batch_id,
        report_month=report.report_month,
        generated_at=report.generated_at,
        input_value_type=report.input_value_type,
        method_label=report.method_label,
        summary=report.to_snapshot_summary(),
        file_name=report.file_name,
    )


def build_zscore_monthly_preview_summary(report: ZScoreMonthlyReportData) -> list[tuple[str, str]]:
    statistics = report.statistics
    return [
        ("正式期总 run 数", str(statistics.formal_count)),
        ("在控 run 数", str(statistics.in_control_count)),
        ("警告 run 数", str(statistics.warning_count)),
        ("失控 run 数", str(statistics.out_of_control_count)),
        ("当前规则组合", statistics.template_label),
        ("当前阶段", statistics.current_phase_label),
        ("全部 level 已完成建靶", "是" if statistics.all_levels_ready else "否"),
    ]


def _normalize_report_month(report_month: str) -> str:
    text = str(report_month or "").strip()
    if len(text) != 7:
        raise ValueError("报告月份格式不正确，应为 YYYY-MM。")
    try:
        return pd.Period(text, freq="M").strftime("%Y-%m")
    except ValueError as exc:
        raise ValueError("报告月份格式不正确，应为 YYYY-MM。") from exc


def _filter_monthly_formal_df(qc_df: pd.DataFrame, report_month: str) -> pd.DataFrame:
    if qc_df.empty:
        return qc_df.copy()
    dataframe = qc_df.copy()
    dataframe["test_time"] = pd.to_datetime(dataframe["test_time"], errors="coerce")
    month_start = pd.Period(report_month, freq="M").start_time
    month_end = pd.Period(report_month, freq="M").end_time
    return dataframe[
        (dataframe["phase"] == LJ_FORMAL_PHASE_LABEL)
        & dataframe["test_time"].between(month_start, month_end)
    ].sort_values(["test_time", "id"]).reset_index(drop=True)


def _build_statistics(
    formal_df: pd.DataFrame,
    stats: dict[str, Any],
    batch,
) -> LjMonthlyReportStatistics:
    formal_count = len(formal_df)
    monthly_mean = float(formal_df["value"].mean()) if formal_count else None
    monthly_sd = float(formal_df["value"].std(ddof=1)) if formal_count >= 2 else None
    monthly_cv = (
        None
        if monthly_mean in (None, 0) or monthly_sd is None or math.isclose(float(monthly_mean), 0.0, abs_tol=1e-12)
        else float(monthly_sd / monthly_mean * 100)
    )
    return LjMonthlyReportStatistics(
        formal_count=formal_count,
        in_control_count=int((formal_df["status"] == "符合质控").sum()),
        warning_count=int((formal_df["status"] == "警告").sum()),
        out_of_control_count=int((formal_df["status"] == "失控").sum()),
        undetermined_count=int((formal_df["status"] == "无法判定（SD=0）").sum()),
        monthly_mean=monthly_mean,
        monthly_sd=monthly_sd,
        monthly_cv=monthly_cv,
        target_mean=_safe_float(stats.get("mean")),
        target_sd=_safe_float(stats.get("sd")),
        cv_limit=_safe_float(batch["cv_limit"]) if batch["cv_limit"] not in (None, "") else None,
    )


def _build_abnormal_records(formal_df: pd.DataFrame) -> list[LjMonthlyAbnormalRecord]:
    abnormal_df = formal_df[formal_df["status"].isin(["警告", "失控"])].copy()
    if abnormal_df.empty:
        return []
    records: list[LjMonthlyAbnormalRecord] = []
    for _, row in abnormal_df.iterrows():
        test_time = pd.Timestamp(row["test_time"]).strftime("%Y-%m-%d %H:%M")
        sequence = int(row["sequence"]) if not pd.isna(row["sequence"]) else 0
        records.append(
            LjMonthlyAbnormalRecord(
                test_time=test_time,
                sequence=sequence,
                value=float(row["value"]),
                status=str(row["status"] or ""),
                rule_hits=str(row.get("rule_hits", "") or "-"),
                manual_note=str(row.get("manual_note", "") or "").strip(),
            )
        )
    return records


def _build_corrective_actions(records: list[LjMonthlyAbnormalRecord]) -> list[str]:
    if not records:
        return []
    values: list[str] = []
    has_empty_note = False
    for record in records:
        note = str(record.manual_note or "").strip()
        if not note:
            has_empty_note = True
            continue
        if note not in values:
            values.append(note)
    if has_empty_note or not values:
        values.append("未填写")
    return values


def _build_corrective_actions_empty_text(records: list[LjMonthlyAbnormalRecord]) -> str:
    if records:
        return ""
    return "本月无异常记录，无需原因与纠正措施。"


def _build_abnormal_summary_text(
    *,
    abnormal_records: list[LjMonthlyAbnormalRecord],
    corrective_actions_empty_text: str,
) -> str:
    if not abnormal_records:
        return corrective_actions_empty_text

    has_empty_note = any(not str(record.manual_note or "").strip() for record in abnormal_records)
    summary = (
        f"本月共记录 {len(abnormal_records)} 条警告/失控事件。"
        "以上内容按现有手动备注归并展示。"
    )
    if has_empty_note:
        summary += " 未填写备注的异常记录统一标记为“未填写”。"
    return summary


def _resolve_target_source(batch) -> tuple[str, str]:
    source_method = str(batch["source_method"] or "").strip().lower()
    if source_method == "instant":
        source_project = str(batch["source_instant_project_name"] or "").strip() or "即时法项目"
        source_batch = str(batch["source_instant_batch_lot_no"] or "").strip() or "未填写质控批号"
        transfer_time = str(batch["source_transfer_time"] or "").strip() or "-"
        return (
            "即时法转入后形成的 LJ 靶值",
            f"该批次由即时法转入形成；来源项目：{source_project}；来源批次：{source_batch}；转入时间：{transfer_time}",
        )
    return ("本批次建靶值", "基于本批次建靶期有效建靶点计算。")


def _build_monthly_overview(
    statistics: LjMonthlyReportStatistics,
    batch,
) -> str:
    summary = (
        f"本月该 LJ 批次正式期共纳入 {statistics.formal_count} 条记录，"
        f"其中在控 {statistics.in_control_count} 条、警告 {statistics.warning_count} 条、"
        f"失控 {statistics.out_of_control_count} 条。"
    )
    if statistics.out_of_control_count > 0:
        summary += " 本月存在失控记录，详见后续异常与纠正措施说明。"
    elif statistics.warning_count > 0:
        summary += " 本月存在警告记录，建议持续观察。"
    elif statistics.undetermined_count > 0:
        summary += " 本月存在无法判定记录，建议结合原始结果进一步复核。"
    else:
        summary += " 本月未发现警告或失控记录，整体运行稳定。"

    if str(batch["source_method"] or "").strip().lower() == "instant":
        summary += " 当前批次来源于即时法转入。"
    return summary


def _build_conclusion(statistics: LjMonthlyReportStatistics) -> str:
    if statistics.out_of_control_count > 0:
        return "本月存在失控记录，需结合原因与纠正措施复核。"
    if statistics.warning_count > 0:
        return "本月存在警告记录，建议持续观察。"
    if statistics.undetermined_count > 0:
        return "本月存在无法判定记录，建议结合原始结果与人工复核进一步确认。"
    return "本月正式期数据整体在控。"


def _build_report_file_name(project_name: str, lot_no: str, report_month: str) -> str:
    project_fragment = _build_safe_name(project_name, "project")
    lot_fragment = _build_safe_name(lot_no, "lot")
    month_fragment = report_month.replace("-", "")
    return f"{project_fragment}_{lot_fragment}_lj_monthly_report_{month_fragment}.pdf"


def _build_safe_name(text: str, fallback: str) -> str:
    characters: list[str] = []
    for character in str(text or "").strip():
        if character.isalnum():
            characters.append(character)
        elif character in {"-", "_"}:
            characters.append(character)
        else:
            characters.append("_")
    safe_text = "".join(characters).strip("_")
    return safe_text or fallback


def _format_report_month_label(report_month: str) -> str:
    period = pd.Period(report_month, freq="M")
    return f"{period.year}年{period.month:02d}月"


def _format_report_period_label(report_month: str) -> str:
    period = pd.Period(report_month, freq="M")
    start_date = period.start_time.strftime("%Y-%m-%d")
    end_date = period.end_time.strftime("%Y-%m-%d")
    return f"{start_date} 至 {end_date}"


def _resolve_pdf_font_name() -> str:
    available_fonts = {font.name for font in font_manager.fontManager.ttflist}
    for font_name in PDF_FONT_CANDIDATES:
        if font_name in available_fonts:
            return font_name
    return "DejaVu Sans"


def _build_summary_page(report: LjMonthlyReportData):
    figure = plt.figure(figsize=(8.27, 11.69), dpi=150)
    axis = figure.add_axes([0.04, 0.04, 0.92, 0.92])
    axis.axis("off")

    axis.text(0.5, 0.975, report.title, ha="center", va="top", fontsize=20, fontweight="bold")
    axis.text(
        0.5,
        0.945,
        f"项目名称：{report.basic_info.project_name}    报告月份：{report.report_month_label}",
        ha="center",
        va="top",
        fontsize=10,
    )
    axis.text(
        0.5,
        0.922,
        f"报告期间：{report.report_period_label}    生成时间：{report.generated_at}",
        ha="center",
        va="top",
        fontsize=10,
    )
    axis.text(0.5, 0.899, f"方法标识：{report.method_label}", ha="center", va="top", fontsize=11)

    _draw_section_title(axis, 0.868, "基本信息")
    basic_rows = [
        ["方法", report.basic_info.method_label, "输入值类型", report.basic_info.input_value_type_label],
        ["质控品批号", report.basic_info.lot_no, "仪器", report.basic_info.instrument],
        ["试剂", report.basic_info.reagent, "质控品", report.basic_info.qc_material],
        ["浓度", report.basic_info.concentration, "报告期间", report.report_period_label],
        ["当前靶值来源", report.basic_info.target_source_label, "报告月份", report.report_month_label],
        ["来源说明", report.basic_info.target_source_detail, "", ""],
    ]
    basic_table = axis.table(
        cellText=basic_rows,
        cellLoc="left",
        colWidths=[0.15, 0.35, 0.15, 0.35],
        bbox=[0.0, 0.64, 1.0, 0.18],
    )
    _style_table(basic_table, header_rows=0, font_size=9)

    _draw_section_title(axis, 0.605, "本月质控概况")
    axis.text(
        0.0,
        0.572,
        textwrap.fill(report.overview_text, width=58),
        ha="left",
        va="top",
        fontsize=10.5,
    )

    _draw_section_title(axis, 0.495, "月度统计摘要")
    summary_rows = [
        ["月度正式期总记录数", str(report.statistics.formal_count), "在控记录数", str(report.statistics.in_control_count)],
        ["警告记录数", str(report.statistics.warning_count), "失控记录数", str(report.statistics.out_of_control_count)],
        ["月度均值", _format_float(report.statistics.monthly_mean), "月度 SD", _format_monthly_stat_text(report.statistics, "sd")],
        ["月度 CV%", _format_monthly_stat_text(report.statistics, "cv"), "当前目标均值", _format_float(report.statistics.target_mean)],
        ["当前目标 SD", _format_float(report.statistics.target_sd), "当前批次 CV 要求", _format_float(report.statistics.cv_limit, digits=2, suffix="%")],
    ]
    summary_table = axis.table(
        cellText=summary_rows,
        cellLoc="left",
        colWidths=[0.20, 0.30, 0.20, 0.30],
        bbox=[0.0, 0.27, 1.0, 0.18],
    )
    _style_table(summary_table, header_rows=0, font_size=9.5)

    _draw_section_title(axis, 0.235, "月度结论")
    conclusion_lines = textwrap.fill(report.conclusion, width=48)
    axis.text(0.0, 0.202, conclusion_lines, ha="left", va="top", fontsize=11)

    _draw_section_title(axis, 0.13, "声明区")
    declaration_lines = textwrap.fill(report.declaration, width=56)
    axis.text(0.0, 0.097, declaration_lines, ha="left", va="top", fontsize=10)
    return figure


def _build_abnormal_page(
    *,
    report: LjMonthlyReportData,
    abnormal_chunk: list[LjMonthlyAbnormalRecord],
    chunk_index: int,
    chunk_count: int,
):
    figure = plt.figure(figsize=(8.27, 11.69), dpi=150)
    axis = figure.add_axes([0.04, 0.04, 0.92, 0.92])
    axis.axis("off")

    axis.text(0.5, 0.975, report.title, ha="center", va="top", fontsize=18, fontweight="bold")
    axis.text(
        0.5,
        0.945,
        f"异常/失控记录表（第 {chunk_index}/{chunk_count} 页）｜项目：{report.basic_info.project_name}｜月份：{report.report_month_label}",
        ha="center",
        va="top",
        fontsize=10,
    )

    _draw_section_title(axis, 0.905, "异常/失控记录表")
    if not abnormal_chunk:
        axis.text(0.0, 0.865, "本月未发现警告或失控记录。", ha="left", va="top", fontsize=12)
        return figure

    cell_text = []
    for record in abnormal_chunk:
        cell_text.append(
            [
                record.test_time,
                str(record.sequence),
                f"{record.value:.4f}",
                record.status,
                _wrap_cell_text(record.rule_hits, width=12),
                _wrap_cell_text(record.manual_note or "未填写", width=18),
            ]
        )
    table = axis.table(
        cellText=cell_text,
        colLabels=ABNORMAL_TABLE_COLUMNS,
        colLoc="left",
        cellLoc="left",
        colWidths=ABNORMAL_TABLE_WIDTHS,
        bbox=[0.0, 0.10, 1.0, 0.76],
    )
    _style_table(table, header_rows=1, font_size=8.5)
    return figure


def _build_action_page(report: LjMonthlyReportData):
    figure = plt.figure(figsize=(8.27, 11.69), dpi=150)
    axis = figure.add_axes([0.04, 0.04, 0.92, 0.92])
    axis.axis("off")

    axis.text(0.5, 0.975, report.title, ha="center", va="top", fontsize=18, fontweight="bold")
    axis.text(
        0.5,
        0.945,
        f"原因与纠正措施｜项目：{report.basic_info.project_name}｜月份：{report.report_month_label}",
        ha="center",
        va="top",
        fontsize=10,
    )

    _draw_section_title(axis, 0.905, "原因与纠正措施")
    y_position = 0.865
    if report.corrective_actions:
        for index, action in enumerate(report.corrective_actions, start=1):
            wrapped = textwrap.fill(f"{index}. {action}", width=54)
            axis.text(0.0, y_position, wrapped, ha="left", va="top", fontsize=11)
            y_position -= 0.09 + 0.02 * wrapped.count("\n")
    else:
        axis.text(
            0.0,
            y_position,
            textwrap.fill(report.corrective_actions_empty_text, width=54),
            ha="left",
            va="top",
            fontsize=11,
        )

    _draw_section_title(axis, 0.38, "异常说明")
    axis.text(
        0.0,
        0.345,
        textwrap.fill(report.abnormal_summary_text, width=54),
        ha="left",
        va="top",
        fontsize=11,
    )

    _draw_section_title(axis, 0.25, "固定声明")
    axis.text(0.0, 0.215, textwrap.fill(report.declaration, width=56), ha="left", va="top", fontsize=10)
    return figure


def _draw_section_title(axis, y: float, title: str) -> None:
    axis.text(0.0, y, title, ha="left", va="top", fontsize=13, fontweight="bold")
    axis.hlines(y - 0.01, xmin=0.0, xmax=1.0, colors="#4e79a7", linewidth=1.2)


def _style_table(table, *, header_rows: int, font_size: float) -> None:
    table.auto_set_font_size(False)
    table.set_fontsize(font_size)
    for (row_index, _column_index), cell in table.get_celld().items():
        cell.set_edgecolor("#b8c4d0")
        cell.set_linewidth(0.6)
        if row_index < header_rows:
            cell.set_facecolor("#eaf2fb")
            cell.set_text_props(weight="bold")
        else:
            cell.set_facecolor("#ffffff")
        cell.PAD = 0.06


def _chunk_abnormal_records(
    abnormal_records: list[LjMonthlyAbnormalRecord],
) -> list[list[LjMonthlyAbnormalRecord]]:
    if not abnormal_records:
        return [[]]
    return [
        abnormal_records[index : index + ABNORMAL_RECORDS_PER_PAGE]
        for index in range(0, len(abnormal_records), ABNORMAL_RECORDS_PER_PAGE)
    ]


def _wrap_cell_text(text: str, width: int) -> str:
    compact = str(text or "").strip() or "未填写"
    return textwrap.fill(compact, width=width)


def _format_monthly_stat_text(
    statistics: LjMonthlyReportStatistics,
    metric: str,
) -> str:
    if metric == "sd":
        if statistics.formal_count < 2:
            return "暂不计算（样本数不足）"
        return _format_float(statistics.monthly_sd)
    if metric == "cv":
        if statistics.formal_count < 2:
            return "暂不计算（样本数不足）"
        return _format_float(statistics.monthly_cv, digits=2, suffix="%")
    raise ValueError(f"unsupported monthly metric: {metric}")


def _format_float(value: float | None, *, digits: int = 4, suffix: str = "") -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value):.{digits}f}{suffix}"


def _safe_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric_value):
        return None
    return numeric_value


def _resolve_zscore_level_target_profiles(
    batch_id: int,
    template_id: str,
    template: dict[str, Any],
    required_level_ids: list[str],
) -> dict[str, dict[str, Any]]:
    required_n = int(template.get("required_n") or 5)
    profiles = get_zscore_level_targets(batch_id, template_id, required_n=required_n)
    return {level_id: dict(profiles.get(level_id, {})) for level_id in required_level_ids}


def _filter_zscore_monthly_formal_runs(
    history_runs: list[dict[str, Any]],
    report_month: str,
) -> list[dict[str, Any]]:
    month_start = pd.Period(report_month, freq="M").start_time
    month_end = pd.Period(report_month, freq="M").end_time
    filtered_runs = [
        run
        for run in history_runs
        if str(run.get("phase") or "") == PHASE_FORMAL_QC
        and run.get("test_time") is not None
        and month_start <= pd.Timestamp(run["test_time"]) <= month_end
    ]
    return sorted(
        filtered_runs,
        key=lambda run: (
            int(run.get("test_sequence") or run.get("run_id") or run.get("id") or 0),
            int(run.get("run_id") or run.get("id") or 0),
        ),
    )


def _build_zscore_monthly_statistics(
    *,
    monthly_formal_runs: list[dict[str, Any]],
    template_label: str,
    current_phase_label: str,
    all_levels_ready: bool,
) -> ZScoreMonthlyReportStatistics:
    return ZScoreMonthlyReportStatistics(
        formal_count=len(monthly_formal_runs),
        in_control_count=sum(1 for run in monthly_formal_runs if str(run.get("run_status") or "") == "accept"),
        warning_count=sum(1 for run in monthly_formal_runs if str(run.get("run_status") or "") == "warning"),
        out_of_control_count=sum(1 for run in monthly_formal_runs if str(run.get("run_status") or "") == "reject"),
        template_label=template_label,
        current_phase_label=current_phase_label,
        all_levels_ready=all_levels_ready,
    )


def _build_zscore_level_statistics(
    *,
    monthly_formal_runs: list[dict[str, Any]],
    required_level_ids: list[str],
    level_label_map: dict[str, str],
    level_target_profiles: dict[str, dict[str, Any]],
    batch,
) -> list[ZScoreMonthlyLevelStatistic]:
    cv_limit = _safe_float(batch["cv_limit"]) if batch["cv_limit"] not in (None, "") else None
    level_statistics: list[ZScoreMonthlyLevelStatistic] = []
    for level_id in required_level_ids:
        raw_values: list[float] = []
        for run in monthly_formal_runs:
            for level_result in run.get("level_results", []):
                if str(level_result.get("level_id")) != level_id:
                    continue
                raw_value = _safe_float(level_result.get("raw_value"))
                if raw_value is not None:
                    raw_values.append(raw_value)
        monthly_count = len(raw_values)
        monthly_mean = float(pd.Series(raw_values).mean()) if monthly_count else None
        monthly_sd = float(pd.Series(raw_values).std(ddof=1)) if monthly_count >= 2 else None
        monthly_cv = (
            None
            if monthly_mean in (None, 0) or monthly_sd is None or math.isclose(float(monthly_mean), 0.0, abs_tol=1e-12)
            else float(monthly_sd / monthly_mean * 100)
        )
        target_profile = level_target_profiles.get(level_id, {})
        target_mean = _safe_float(
            target_profile.get("final_target_mean")
            if target_profile.get("is_ready")
            else target_profile.get("provisional_mean")
        )
        target_sd = _safe_float(
            target_profile.get("final_target_sd")
            if target_profile.get("is_ready")
            else target_profile.get("provisional_sd")
        )
        level_statistics.append(
            ZScoreMonthlyLevelStatistic(
                level_id=level_id,
                level_label=_format_zscore_level_label(level_id, level_label_map),
                monthly_count=monthly_count,
                monthly_mean=monthly_mean,
                monthly_sd=monthly_sd,
                monthly_cv=monthly_cv,
                target_mean=target_mean,
                target_sd=target_sd,
                cv_limit=cv_limit,
            )
        )
    return level_statistics


def _build_zscore_abnormal_records(
    monthly_formal_runs: list[dict[str, Any]],
) -> list[ZScoreMonthlyAbnormalRecord]:
    records: list[ZScoreMonthlyAbnormalRecord] = []
    for run in monthly_formal_runs:
        run_status = str(run.get("run_status") or "")
        if run_status not in {"warning", "reject"}:
            continue
        records.append(
            ZScoreMonthlyAbnormalRecord(
                test_time=pd.Timestamp(run["test_time"]).strftime("%Y-%m-%d %H:%M"),
                run_sequence=int(run.get("test_sequence") or run.get("run_id") or run.get("id") or 0),
                status=_format_zscore_run_status(run_status),
                rule_hits=_format_zscore_rule_hits(run.get("rule_hits_run", [])),
                error_type=_format_zscore_error_type(run.get("error_type_hint")),
                manual_note=str(run.get("manual_note", "") or "").strip(),
            )
        )
    return records


def _build_zscore_corrective_actions(records: list[ZScoreMonthlyAbnormalRecord]) -> list[str]:
    if not records:
        return []
    values: list[str] = []
    has_empty_note = False
    for record in records:
        note = str(record.manual_note or "").strip()
        if not note:
            has_empty_note = True
            continue
        if note not in values:
            values.append(note)
    if has_empty_note or not values:
        values.append("未填写")
    return values


def _build_zscore_corrective_actions_empty_text(records: list[ZScoreMonthlyAbnormalRecord]) -> str:
    if records:
        return ""
    return "本月无异常记录，无需原因与纠正措施。"


def _build_zscore_abnormal_summary_text(
    *,
    abnormal_records: list[ZScoreMonthlyAbnormalRecord],
    corrective_actions_empty_text: str,
) -> str:
    if not abnormal_records:
        return corrective_actions_empty_text
    has_empty_note = any(not str(record.manual_note or "").strip() for record in abnormal_records)
    summary = (
        f"本月共记录 {len(abnormal_records)} 次警告/失控 run。"
        "以上内容按现有手动备注归并展示。"
    )
    if has_empty_note:
        summary += " 未填写备注的异常 run 统一标记为“未填写”。"
    return summary


def _resolve_zscore_target_source() -> tuple[str, str]:
    return ("本批次各水平建靶值", "基于本批次各 level 建靶期有效点计算。")


def _build_zscore_monthly_overview(
    statistics: ZScoreMonthlyReportStatistics,
    template_label: str,
) -> str:
    summary = (
        f"本月该 Z-score 批次正式期共纳入 {statistics.formal_count} 次 run，"
        f"其中在控 {statistics.in_control_count} 次、警告 {statistics.warning_count} 次、"
        f"失控 {statistics.out_of_control_count} 次，当前规则组合为{template_label}。"
    )
    if statistics.out_of_control_count > 0:
        summary += " 本月存在失控 run，详见后续异常与纠正措施说明。"
    elif statistics.warning_count > 0:
        summary += " 本月存在警告 run，建议持续观察。"
    else:
        summary += " 本月未发现异常 run，整体运行稳定。"
    return summary


def _build_zscore_monthly_conclusion(statistics: ZScoreMonthlyReportStatistics) -> str:
    if statistics.out_of_control_count > 0:
        return "本月存在失控 run，需结合原因与纠正措施复核。"
    if statistics.warning_count > 0:
        return "本月存在警告 run，建议持续观察。"
    return "本月正式期数据整体在控。"


def _build_zscore_report_file_name(project_name: str, lot_no: str, report_month: str) -> str:
    project_fragment = _build_safe_name(project_name, "project")
    lot_fragment = _build_safe_name(lot_no, "lot")
    month_fragment = report_month.replace("-", "")
    return f"{project_fragment}_{lot_fragment}_zscore_monthly_report_{month_fragment}.pdf"


def _format_zscore_template_label(template: dict[str, Any] | str) -> str:
    if isinstance(template, dict):
        template_key = str(template.get("template_id") or template.get("label") or "").strip()
        fallback = str(template.get("label") or template_key)
    else:
        template_key = str(template or "").strip()
        fallback = template_key
    return ZSCORE_TEMPLATE_DISPLAY_NAMES.get(template_key, ZSCORE_TEMPLATE_DISPLAY_NAMES.get(fallback, fallback or "规则组合"))


def _format_zscore_run_status(status: Any) -> str:
    normalized_status = str(status or "").strip()
    return ZSCORE_RUN_STATUS_LABELS.get(normalized_status, normalized_status or "状态未知")


def _format_zscore_error_type(error_type: Any) -> str:
    normalized_error_type = str(error_type or "").strip()
    return ZSCORE_ERROR_TYPE_LABELS.get(normalized_error_type, normalized_error_type or "待进一步判断")


def _format_zscore_rule_hits(rule_hits: Any) -> str:
    if not isinstance(rule_hits, list) or not rule_hits:
        return "-"
    display_names: list[str] = []
    for hit in rule_hits:
        rule_id = ""
        if isinstance(hit, dict):
            rule_id = str(hit.get("rule_id") or "").strip()
        else:
            rule_id = str(hit or "").strip()
        if not rule_id:
            continue
        display_name = ZSCORE_RULE_DISPLAY_NAMES.get(rule_id, rule_id)
        if display_name not in display_names:
            display_names.append(display_name)
    return "、".join(display_names) if display_names else "-"


def _format_zscore_level_label(level_id: str, level_label_map: dict[str, str]) -> str:
    default_label = format_level_id_display(level_id)
    custom_label = str(level_label_map.get(level_id, level_id) or level_id).strip() or level_id
    if custom_label == level_id:
        return default_label
    return f"{default_label}：{custom_label}"


def _filter_zscore_monthly_plot_df(plot_df: pd.DataFrame, report_month: str) -> pd.DataFrame:
    if plot_df.empty:
        return plot_df.copy()
    dataframe = plot_df.copy()
    dataframe["test_time"] = pd.to_datetime(dataframe["test_time"], errors="coerce")
    month_start = pd.Period(report_month, freq="M").start_time
    month_end = pd.Period(report_month, freq="M").end_time
    filtered_df = dataframe[
        dataframe["phase"].eq(PHASE_FORMAL_QC)
        & dataframe["test_time"].between(month_start, month_end)
    ].copy()
    if not filtered_df.empty:
        filtered_df["plot_phase"] = PHASE_FORMAL_QC
    return filtered_df


def _build_zscore_summary_page(report: ZScoreMonthlyReportData):
    figure = plt.figure(figsize=(8.27, 11.69), dpi=150)
    axis = figure.add_axes([0.04, 0.04, 0.92, 0.92])
    axis.axis("off")

    axis.text(0.5, 0.975, report.title, ha="center", va="top", fontsize=20, fontweight="bold")
    axis.text(
        0.5,
        0.945,
        f"项目名称：{report.basic_info.project_name}    报告月份：{report.report_month_label}",
        ha="center",
        va="top",
        fontsize=10,
    )
    axis.text(
        0.5,
        0.922,
        f"报告期间：{report.report_period_label}    生成时间：{report.generated_at}",
        ha="center",
        va="top",
        fontsize=10,
    )
    axis.text(0.5, 0.899, f"方法标识：{report.method_label}", ha="center", va="top", fontsize=11)

    _draw_section_title(axis, 0.868, "基本信息")
    basic_rows = [
        ["方法", report.basic_info.method_label, "输入值类型", report.basic_info.input_value_type_label],
        ["水平数", report.basic_info.level_count_label, "规则组合", report.basic_info.template_label],
        ["各 level 说明", report.basic_info.level_summary, "质控品批号", report.basic_info.lot_no],
        ["仪器", report.basic_info.instrument, "试剂", report.basic_info.reagent],
        ["质控品", report.basic_info.qc_material, "浓度", report.basic_info.concentration],
        ["当前靶值来源", report.basic_info.target_source_label, "报告期间", report.report_period_label],
        ["来源说明", report.basic_info.target_source_detail, "", ""],
    ]
    basic_table = axis.table(
        cellText=basic_rows,
        cellLoc="left",
        colWidths=[0.18, 0.32, 0.18, 0.32],
        bbox=[0.0, 0.60, 1.0, 0.22],
    )
    _style_table(basic_table, header_rows=0, font_size=8.8)

    _draw_section_title(axis, 0.565, "本月质控概况")
    axis.text(
        0.0,
        0.532,
        textwrap.fill(report.overview_text, width=58),
        ha="left",
        va="top",
        fontsize=10.5,
    )

    _draw_section_title(axis, 0.455, "月度统计摘要")
    summary_rows = [
        ["月度正式期总 run 数", str(report.statistics.formal_count), "在控 run 数", str(report.statistics.in_control_count)],
        ["警告 run 数", str(report.statistics.warning_count), "失控 run 数", str(report.statistics.out_of_control_count)],
        ["当前规则组合", report.statistics.template_label, "当前阶段", report.statistics.current_phase_label],
        ["全部 level 已完成建靶", "是" if report.statistics.all_levels_ready else "否", "", ""],
    ]
    summary_table = axis.table(
        cellText=summary_rows,
        cellLoc="left",
        colWidths=[0.22, 0.28, 0.22, 0.28],
        bbox=[0.0, 0.28, 1.0, 0.14],
    )
    _style_table(summary_table, header_rows=0, font_size=9.5)

    _draw_section_title(axis, 0.245, "月度结论")
    axis.text(0.0, 0.212, textwrap.fill(report.conclusion, width=50), ha="left", va="top", fontsize=11)

    _draw_section_title(axis, 0.14, "声明区")
    axis.text(0.0, 0.107, textwrap.fill(report.declaration, width=56), ha="left", va="top", fontsize=10)
    return figure


def _build_zscore_level_summary_page(report: ZScoreMonthlyReportData):
    figure = plt.figure(figsize=(8.27, 11.69), dpi=150)
    axis = figure.add_axes([0.04, 0.04, 0.92, 0.92])
    axis.axis("off")

    axis.text(0.5, 0.975, report.title, ha="center", va="top", fontsize=18, fontweight="bold")
    axis.text(
        0.5,
        0.945,
        f"各 level 统计摘要｜项目：{report.basic_info.project_name}｜月份：{report.report_month_label}",
        ha="center",
        va="top",
        fontsize=10,
    )

    _draw_section_title(axis, 0.905, "各 level 统计摘要")
    cell_text = [
        [
            item.level_label,
            _format_float(item.monthly_mean),
            _format_zscore_level_stat_text(item, "sd"),
            _format_zscore_level_stat_text(item, "cv"),
            _format_float(item.target_mean),
            _format_float(item.target_sd),
            _format_float(item.cv_limit, digits=2, suffix="%"),
        ]
        for item in report.level_statistics
    ]
    table = axis.table(
        cellText=cell_text,
        colLabels=["Level", "月度均值", "月度 SD", "月度 CV%", "当前目标均值", "当前目标 SD", "当前 CV 要求"],
        cellLoc="left",
        colLoc="left",
        colWidths=[0.23, 0.12, 0.15, 0.16, 0.14, 0.12, 0.08],
        bbox=[0.0, 0.56, 1.0, 0.28],
    )
    _style_table(table, header_rows=1, font_size=8.6)
    axis.text(
        0.0,
        0.50,
        "统计口径说明：以上各 level 月度均值、SD、CV% 基于所选月份内正式期数据计算；当前目标均值 / SD 取当前批次已生效建靶值。",
        ha="left",
        va="top",
        fontsize=10,
    )
    return figure


def _build_zscore_abnormal_page(
    *,
    report: ZScoreMonthlyReportData,
    abnormal_chunk: list[ZScoreMonthlyAbnormalRecord],
    chunk_index: int,
    chunk_count: int,
):
    figure = plt.figure(figsize=(8.27, 11.69), dpi=150)
    axis = figure.add_axes([0.04, 0.04, 0.92, 0.92])
    axis.axis("off")

    axis.text(0.5, 0.975, report.title, ha="center", va="top", fontsize=18, fontweight="bold")
    axis.text(
        0.5,
        0.945,
        f"异常/失控记录表（第 {chunk_index}/{chunk_count} 页）｜项目：{report.basic_info.project_name}｜月份：{report.report_month_label}",
        ha="center",
        va="top",
        fontsize=10,
    )

    _draw_section_title(axis, 0.905, "异常/失控记录表")
    if not abnormal_chunk:
        axis.text(0.0, 0.865, "本月未发现警告或失控 run。", ha="left", va="top", fontsize=12)
        return figure

    cell_text = []
    for record in abnormal_chunk:
        cell_text.append(
            [
                record.test_time,
                str(record.run_sequence),
                record.status,
                _wrap_cell_text(record.rule_hits, width=12),
                _wrap_cell_text(record.error_type, width=12),
                _wrap_cell_text(record.manual_note or "未填写", width=16),
            ]
        )
    table = axis.table(
        cellText=cell_text,
        colLabels=ZSCORE_ABNORMAL_TABLE_COLUMNS,
        cellLoc="left",
        colLoc="left",
        colWidths=ZSCORE_ABNORMAL_TABLE_WIDTHS,
        bbox=[0.0, 0.10, 1.0, 0.76],
    )
    _style_table(table, header_rows=1, font_size=8.5)
    return figure


def _build_zscore_action_page(report: ZScoreMonthlyReportData):
    figure = plt.figure(figsize=(8.27, 11.69), dpi=150)
    axis = figure.add_axes([0.04, 0.04, 0.92, 0.92])
    axis.axis("off")

    axis.text(0.5, 0.975, report.title, ha="center", va="top", fontsize=18, fontweight="bold")
    axis.text(
        0.5,
        0.945,
        f"原因与纠正措施｜项目：{report.basic_info.project_name}｜月份：{report.report_month_label}",
        ha="center",
        va="top",
        fontsize=10,
    )

    _draw_section_title(axis, 0.905, "原因与纠正措施")
    y_position = 0.865
    if report.corrective_actions:
        for index, action in enumerate(report.corrective_actions, start=1):
            wrapped = textwrap.fill(f"{index}. {action}", width=54)
            axis.text(0.0, y_position, wrapped, ha="left", va="top", fontsize=11)
            y_position -= 0.09 + 0.02 * wrapped.count("\n")
    else:
        axis.text(
            0.0,
            y_position,
            textwrap.fill(report.corrective_actions_empty_text, width=54),
            ha="left",
            va="top",
            fontsize=11,
        )

    _draw_section_title(axis, 0.38, "异常说明")
    axis.text(
        0.0,
        0.345,
        textwrap.fill(report.abnormal_summary_text, width=54),
        ha="left",
        va="top",
        fontsize=11,
    )

    _draw_section_title(axis, 0.25, "固定声明")
    axis.text(0.0, 0.215, textwrap.fill(report.declaration, width=56), ha="left", va="top", fontsize=10)
    return figure


def _chunk_zscore_abnormal_records(
    abnormal_records: list[ZScoreMonthlyAbnormalRecord],
) -> list[list[ZScoreMonthlyAbnormalRecord]]:
    if not abnormal_records:
        return [[]]
    return [
        abnormal_records[index : index + ABNORMAL_RECORDS_PER_PAGE]
        for index in range(0, len(abnormal_records), ABNORMAL_RECORDS_PER_PAGE)
    ]


def _format_zscore_level_stat_text(
    statistic: ZScoreMonthlyLevelStatistic,
    metric: str,
) -> str:
    if metric == "sd":
        if statistic.monthly_count < 2:
            return "暂不计算（样本数不足）"
        return _format_float(statistic.monthly_sd)
    if metric == "cv":
        if statistic.monthly_count < 2:
            return "暂不计算（样本数不足）"
        return _format_float(statistic.monthly_cv, digits=2, suffix="%")
    raise ValueError(f"unsupported zscore level metric: {metric}")
