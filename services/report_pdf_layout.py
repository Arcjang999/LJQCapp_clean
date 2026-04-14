from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
import math
import textwrap
from typing import Any

import pandas as pd
from matplotlib import pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.lines import Line2D

from plotting import plot_lj_chart
from zscore_logic import format_level_id_display
from zscore_plotting import plot_zscore_single_level


MM_PER_INCH = 25.4
A4_PAGE_SIZE = (210 / MM_PER_INCH, 297 / MM_PER_INCH)
PAGE_LEFT = 0.07
PAGE_RIGHT = 0.93
PAGE_WIDTH = PAGE_RIGHT - PAGE_LEFT
HEADER_COLOR = "#355c7d"
BORDER_COLOR = "#b8c4d0"
FOOTER_COLOR = "#5f6b76"
HEADER_TITLE_Y = 0.968
HEADER_PAGE_TITLE_Y = 0.938
HEADER_SUBTITLE_START_Y = 0.908
HEADER_SUBTITLE_LINE_HEIGHT = 0.020
HEADER_DIVIDER_Y = 0.822
CONTENT_TOP = 0.790
CONTENT_BOTTOM = 0.115
SECTION_TITLE_HEIGHT = 0.043
SECTION_GAP = 0.018
PARAGRAPH_LINE_HEIGHT = 0.022
TABLE_UNIT_HEIGHT = 0.034
TABLE_HEADER_UNITS = 1.25
TABLE_ROW_BASE_UNITS = 1.0
TABLE_EXTRA_LINE_UNITS = 0.42
TEXT_WIDTH = 60
NARRATIVE_TEXT_WIDTH = 54
DECLARATION_TEXT_WIDTH = 46
CHART_AXES_RECT = [0.10, 0.150, 0.80, 0.610]
FOOTER_DIVIDER_Y = 0.085
FOOTER_TEXT_Y = 0.055
PDF_AUTHOR = "邦德盛"
PDF_CREATOR = "邦德盛"

LJ_ABNORMAL_TABLE_COLUMNS = ["检测时间", "检测序号", "结果值", "状态", "触发规则", "手动备注"]
LJ_ABNORMAL_TABLE_WIDTHS = [0.19, 0.10, 0.12, 0.10, 0.14, 0.35]
ZSCORE_ABNORMAL_TABLE_COLUMNS = ["检测时间", "run 序号", "状态", "触发规则", "误差类型", "手动备注"]
ZSCORE_ABNORMAL_TABLE_WIDTHS = [0.18, 0.10, 0.10, 0.18, 0.16, 0.28]


@dataclass
class _PageCanvas:
    figure: Any
    axis: Any
    cursor_y: float
    initial_cursor_y: float
    content_bottom: float = CONTENT_BOTTOM


@dataclass(frozen=True)
class _TextSectionSpec:
    title: str
    paragraphs: list[str]
    width: int = TEXT_WIDTH


def render_lj_monthly_report_pdf(package: Any, font_name: str) -> bytes:
    report = package.report
    buffer = BytesIO()
    with plt.rc_context({"font.family": font_name, "axes.unicode_minus": False}):
        with PdfPages(buffer) as pdf:
            metadata = pdf.infodict()
            metadata["Title"] = report.title
            metadata["Author"] = PDF_AUTHOR
            metadata["Subject"] = report.report_type
            metadata["Keywords"] = "LJ, monthly report, single level"
            metadata["Creator"] = PDF_CREATOR
            metadata["CreationDate"] = datetime.now()

            pages: list[tuple[Any, str]] = [
                (_build_lj_summary_page(report), "摘要页"),
                (_build_lj_chart_page(package), "图表页"),
            ]
            if report.abnormal_records:
                for abnormal_index, figure in enumerate(_build_lj_abnormal_pages(report), start=1):
                    pages.append((figure, f"异常记录页 {abnormal_index}"))
            for action_index, figure in enumerate(_build_action_pages(report), start=1):
                pages.append((figure, f"说明页 {action_index}"))

            _write_pages(pdf, pages, report)
    return buffer.getvalue()


def render_zscore_monthly_report_pdf(package: Any, font_name: str) -> bytes:
    report = package.report
    buffer = BytesIO()
    with plt.rc_context({"font.family": font_name, "axes.unicode_minus": False}):
        with PdfPages(buffer) as pdf:
            metadata = pdf.infodict()
            metadata["Title"] = report.title
            metadata["Author"] = PDF_AUTHOR
            metadata["Subject"] = report.report_type
            metadata["Keywords"] = "Z-score, monthly report, multi level"
            metadata["Creator"] = PDF_CREATOR
            metadata["CreationDate"] = datetime.now()

            pages: list[tuple[Any, str]] = [
                (_build_zscore_summary_page(report), "摘要页"),
            ]
            for level_index, level_id in enumerate(package.active_levels, start=1):
                pages.append((_build_zscore_level_chart_page(package, level_id), f"单水平图页 {level_index}"))
            pages.append((_build_zscore_level_summary_page(report), "各 level 统计页"))
            if report.abnormal_records:
                for abnormal_index, figure in enumerate(_build_zscore_abnormal_pages(report), start=1):
                    pages.append((figure, f"异常记录页 {abnormal_index}"))
            for action_index, figure in enumerate(_build_action_pages(report), start=1):
                pages.append((figure, f"说明页 {action_index}"))

            _write_pages(pdf, pages, report)
    return buffer.getvalue()


def _write_pages(pdf, pages: list[tuple[Any, str]], report: Any) -> None:
    total_pages = len(pages)
    for page_index, (figure, footer_label) in enumerate(pages, start=1):
        _apply_page_footer(
            figure=figure,
            method_label=report.method_label,
            report_month_label=report.report_month_label,
            generated_at=report.generated_at,
            page_label=footer_label,
            page_index=page_index,
            total_pages=total_pages,
        )
        pdf.savefig(figure)
        plt.close(figure)


def _build_lj_summary_page(report: Any):
    canvas = _new_canvas(
        report_title=report.title,
        page_title="报告摘要",
        subtitle_lines=[
            f"实验室名称：{report.basic_info.lab_name}    科室名称：{report.basic_info.department_name}",
            f"项目名称：{report.basic_info.project_name}    报告月份：{report.report_month_label}",
            f"报告期间：{report.report_period_label}    生成时间：{report.generated_at}",
            (
                f"方法标识：{report.method_label}"
                f"    质控负责人：{report.basic_info.qc_owner_name}"
                f"    审核人：{report.basic_info.reviewer_name}"
            ),
        ],
    )

    basic_rows = [
        ["方法", report.basic_info.method_label, "输入值类型", report.basic_info.input_value_type_label],
        ["质控品批号", report.basic_info.lot_no, "仪器", report.basic_info.instrument],
        ["试剂", report.basic_info.reagent, "质控品", report.basic_info.qc_material],
        ["浓度", report.basic_info.concentration, "当前靶值来源", report.basic_info.target_source_label],
        ["来源说明", _wrap_text(report.basic_info.target_source_detail, 28), "", ""],
    ]
    _draw_table_section(
        canvas,
        title="基本信息",
        cell_text=basic_rows,
        col_widths=[0.18, 0.32, 0.18, 0.32],
        font_size=8.8,
    )
    _draw_text_section(canvas, "本月质控概况", report.overview_text, width=58)

    summary_rows = [
        ["月度正式期总记录数", str(report.statistics.formal_count), "在控记录数", str(report.statistics.in_control_count)],
        ["警告记录数", str(report.statistics.warning_count), "失控记录数", str(report.statistics.out_of_control_count)],
        ["月度均值", _format_float(report.statistics.monthly_mean), "月度 SD", _format_lj_metric(report.statistics, "sd")],
        ["月度 CV%", _format_lj_metric(report.statistics, "cv"), "当前目标均值", _format_float(report.statistics.target_mean)],
        ["当前目标 SD", _format_float(report.statistics.target_sd), "批次 CV 要求", _format_float(report.statistics.cv_limit, digits=2, suffix="%")],
    ]
    _draw_table_section(
        canvas,
        title="月度统计摘要",
        cell_text=summary_rows,
        col_widths=[0.23, 0.27, 0.23, 0.27],
        font_size=9.0,
    )
    _draw_text_section(canvas, "月度结论", report.conclusion, width=58)
    return canvas.figure


def _build_lj_chart_page(package: Any):
    figure = plot_lj_chart(
        qc_df=package.formal_df.copy(),
        stats=package.stats,
        title="正式期月度质控图",
        view_mode="正式质控图",
        y_axis_mode="标准视图",
        standard_sd_limit=4.0,
        y_axis_label=package.report.chart_axis_label,
    )
    _decorate_chart_page(
        figure=figure,
        report_title=package.report.title,
        page_title="月度质控图",
        subtitle_lines=[
            f"项目名称：{package.report.basic_info.project_name}    报告月份：{package.report.report_month_label}",
            f"报告期间：{package.report.report_period_label}    质控品批号：{package.report.basic_info.lot_no}",
        ],
    )
    return figure


def _build_lj_abnormal_pages(report: Any) -> list[Any]:
    rows = [
        [
            record.test_time,
            str(record.sequence),
            f"{record.value:.4f}",
            record.status,
            _wrap_text(record.rule_hits, 12),
            _wrap_text(record.manual_note or "未填写", 18),
        ]
        for record in report.abnormal_records
    ]
    chunks = _chunk_rows_by_height(rows, max_height=_full_page_table_max_height(), include_header=True)
    pages: list[Any] = []
    for chunk in chunks:
        canvas = _new_canvas(
            report_title=report.title,
            page_title="异常/失控记录表",
            subtitle_lines=[
                f"项目名称：{report.basic_info.project_name}    报告月份：{report.report_month_label}",
                f"报告期间：{report.report_period_label}    生成时间：{report.generated_at}",
            ],
        )
        _draw_table_section(
            canvas,
            title="异常/失控记录表",
            cell_text=chunk,
            col_labels=LJ_ABNORMAL_TABLE_COLUMNS,
            col_widths=LJ_ABNORMAL_TABLE_WIDTHS,
            font_size=8.4,
        )
        pages.append(canvas.figure)
    return pages


def _build_zscore_summary_page(report: Any):
    canvas = _new_canvas(
        report_title=report.title,
        page_title="报告摘要",
        subtitle_lines=[
            f"实验室名称：{report.basic_info.lab_name}    科室名称：{report.basic_info.department_name}",
            f"项目名称：{report.basic_info.project_name}    报告月份：{report.report_month_label}",
            f"报告期间：{report.report_period_label}    生成时间：{report.generated_at}",
            (
                f"方法标识：{report.method_label}"
                f"    质控负责人：{report.basic_info.qc_owner_name}"
                f"    审核人：{report.basic_info.reviewer_name}"
            ),
        ],
    )

    basic_rows = [
        ["方法", report.basic_info.method_label, "输入值类型", report.basic_info.input_value_type_label],
        ["水平数", report.basic_info.level_count_label, "各 level 说明", _wrap_text(report.basic_info.level_summary, 20)],
        ["当前规则组合", report.basic_info.template_label, "质控品批号", report.basic_info.lot_no],
        ["仪器", report.basic_info.instrument, "试剂", report.basic_info.reagent],
        ["质控品", report.basic_info.qc_material, "浓度", report.basic_info.concentration],
        ["当前靶值来源", report.basic_info.target_source_label, "来源说明", _wrap_text(report.basic_info.target_source_detail, 20)],
    ]
    _draw_table_section(
        canvas,
        title="基本信息",
        cell_text=basic_rows,
        col_widths=[0.18, 0.32, 0.18, 0.32],
        font_size=8.6,
    )
    _draw_text_section(canvas, "本月质控概况", report.overview_text, width=58)

    run_summary_rows = [
        ["本月正式期总 run 数", str(report.statistics.formal_count), "在控 run 数", str(report.statistics.in_control_count)],
        ["警告 run 数", str(report.statistics.warning_count), "失控 run 数", str(report.statistics.out_of_control_count)],
        ["当前规则组合", report.statistics.template_label, "当前阶段", report.statistics.current_phase_label],
        ["全部 level 已完成建靶", "是" if report.statistics.all_levels_ready else "否", "", ""],
    ]
    _draw_table_section(
        canvas,
        title="run 级统计摘要",
        cell_text=run_summary_rows,
        col_widths=[0.24, 0.26, 0.24, 0.26],
        font_size=9.0,
    )
    _draw_text_section(canvas, "月度结论", report.conclusion, width=58)
    return canvas.figure


def _build_zscore_level_chart_page(package: Any, level_id: str):
    level_label = next(
        (item.level_label for item in package.report.level_statistics if item.level_id == level_id),
        format_level_id_display(level_id),
    )
    figure = plot_zscore_single_level(
        plot_df=package.monthly_plot_df.copy(),
        level_id=level_id,
        title=f"{level_label} 单水平月度图",
        phase_scope="formal",
        y_axis_mode="标准视图",
        standard_sd_limit=4.0,
        y_axis_label=package.report.chart_axis_label,
    )
    _decorate_chart_page(
        figure=figure,
        report_title=package.report.title,
        page_title=f"{level_label} 单水平月度图",
        subtitle_lines=[
            f"项目名称：{package.report.basic_info.project_name}    报告月份：{package.report.report_month_label}",
            f"报告期间：{package.report.report_period_label}    当前规则组合：{package.report.statistics.template_label}",
        ],
    )
    return figure


def _build_zscore_level_summary_page(report: Any):
    canvas = _new_canvas(
        report_title=report.title,
        page_title="各 level 统计摘要",
        subtitle_lines=[
            f"项目名称：{report.basic_info.project_name}    报告月份：{report.report_month_label}",
            f"报告期间：{report.report_period_label}    水平数：{report.basic_info.level_count_label}",
        ],
    )
    cell_text = [
        [
            item.level_label,
            str(item.monthly_count),
            _format_float(item.monthly_mean),
            _format_zscore_metric(item, "sd"),
            _format_zscore_metric(item, "cv"),
            _format_float(item.target_mean),
            _format_float(item.target_sd),
            _format_float(item.cv_limit, digits=2, suffix="%"),
        ]
        for item in report.level_statistics
    ]
    _draw_table_section(
        canvas,
        title="各 level 统计摘要",
        cell_text=cell_text,
        col_labels=["Level", "记录数", "月度均值", "月度 SD", "月度 CV%", "目标均值", "目标 SD", "CV 要求"],
        col_widths=[0.18, 0.09, 0.13, 0.13, 0.14, 0.12, 0.11, 0.10],
        font_size=8.4,
    )
    _draw_text_section(
        canvas,
        "统计口径说明",
        "各 level 月度均值、SD、CV% 基于所选月份内正式期数据计算；当前目标均值和目标 SD 取当前批次已生效建靶值。",
        width=60,
    )
    return canvas.figure


def _build_zscore_abnormal_pages(report: Any) -> list[Any]:
    rows = [
        [
            record.test_time,
            str(record.run_sequence),
            record.status,
            _wrap_text(record.rule_hits, 12),
            _wrap_text(record.error_type, 12),
            _wrap_text(record.manual_note or "未填写", 16),
        ]
        for record in report.abnormal_records
    ]
    chunks = _chunk_rows_by_height(rows, max_height=_full_page_table_max_height(), include_header=True)
    pages: list[Any] = []
    for chunk in chunks:
        canvas = _new_canvas(
            report_title=report.title,
            page_title="异常/失控 run 表",
            subtitle_lines=[
                f"项目名称：{report.basic_info.project_name}    报告月份：{report.report_month_label}",
                f"报告期间：{report.report_period_label}    当前规则组合：{report.statistics.template_label}",
            ],
        )
        _draw_table_section(
            canvas,
            title="异常/失控 run 表",
            cell_text=chunk,
            col_labels=ZSCORE_ABNORMAL_TABLE_COLUMNS,
            col_widths=ZSCORE_ABNORMAL_TABLE_WIDTHS,
            font_size=8.3,
        )
        pages.append(canvas.figure)
    return pages


def _build_action_pages(report: Any) -> list[Any]:
    sections = [
        _TextSectionSpec(
            title="原因与纠正措施",
            paragraphs=[f"{index}. {action}" for index, action in enumerate(report.corrective_actions, start=1)]
            or [report.corrective_actions_empty_text],
            width=NARRATIVE_TEXT_WIDTH,
        ),
        _TextSectionSpec(title="异常说明", paragraphs=[report.abnormal_summary_text], width=NARRATIVE_TEXT_WIDTH),
        _TextSectionSpec(title="固定声明", paragraphs=[report.declaration], width=DECLARATION_TEXT_WIDTH),
    ]
    return _build_text_pages(
        report_title=report.title,
        page_title="原因与声明",
        subtitle_lines=[
            f"项目名称：{report.basic_info.project_name}    报告月份：{report.report_month_label}",
            f"报告期间：{report.report_period_label}    生成时间：{report.generated_at}",
        ],
        sections=sections,
    )


def _new_canvas(*, report_title: str, page_title: str, subtitle_lines: list[str]) -> _PageCanvas:
    figure = plt.figure(figsize=A4_PAGE_SIZE, dpi=150)
    axis = figure.add_axes([0.0, 0.0, 1.0, 1.0])
    axis.axis("off")
    axis.set_xlim(0, 1)
    axis.set_ylim(0, 1)
    _draw_page_header(
        figure=figure,
        report_title=report_title,
        page_title=page_title,
        subtitle_lines=subtitle_lines,
    )
    return _PageCanvas(figure=figure, axis=axis, cursor_y=CONTENT_TOP, initial_cursor_y=CONTENT_TOP)


def _draw_page_header(
    *,
    figure,
    report_title: str,
    page_title: str,
    subtitle_lines: list[str],
) -> None:
    figure.text(0.5, HEADER_TITLE_Y, report_title, ha="center", va="top", fontsize=18.5, fontweight="bold")
    figure.text(0.5, HEADER_PAGE_TITLE_Y, page_title, ha="center", va="top", fontsize=12.2, fontweight="bold")

    subtitle_y = HEADER_SUBTITLE_START_Y
    minimum_subtitle_y = HEADER_DIVIDER_Y + 0.012
    for line in subtitle_lines:
        cleaned = str(line or "").strip()
        if not cleaned:
            continue
        if subtitle_y < minimum_subtitle_y:
            break
        figure.text(0.5, subtitle_y, cleaned, ha="center", va="top", fontsize=9.3, color="#3b4752")
        subtitle_y -= HEADER_SUBTITLE_LINE_HEIGHT

    figure.add_artist(
        Line2D(
            [PAGE_LEFT, PAGE_RIGHT],
            [HEADER_DIVIDER_Y, HEADER_DIVIDER_Y],
            transform=figure.transFigure,
            color=HEADER_COLOR,
            linewidth=1.25,
        )
    )


def _draw_text_section(canvas: _PageCanvas, title: str, text: str, *, width: int = TEXT_WIDTH) -> None:
    _draw_section_title(canvas, title)
    _draw_text_block(canvas, text, width=width)


def _draw_section_title(canvas: _PageCanvas, title: str) -> None:
    canvas.axis.text(
        PAGE_LEFT,
        canvas.cursor_y,
        title,
        ha="left",
        va="top",
        fontsize=12.3,
        fontweight="bold",
        color=HEADER_COLOR,
    )
    canvas.axis.hlines(canvas.cursor_y - 0.012, xmin=PAGE_LEFT, xmax=PAGE_RIGHT, colors=BORDER_COLOR, linewidth=0.9)
    canvas.cursor_y -= SECTION_TITLE_HEIGHT


def _draw_text_block(
    canvas: _PageCanvas,
    text: str,
    *,
    width: int = TEXT_WIDTH,
    fontsize: float = 10.5,
    line_height: float = PARAGRAPH_LINE_HEIGHT,
    spacing_after: float = SECTION_GAP,
) -> None:
    wrapped = _wrap_text(text, width)
    line_count = wrapped.count("\n") + 1
    canvas.axis.text(PAGE_LEFT, canvas.cursor_y, wrapped, ha="left", va="top", fontsize=fontsize, color="#20252b")
    canvas.cursor_y -= line_count * line_height + spacing_after


def _estimate_text_height(
    text: str,
    *,
    width: int = TEXT_WIDTH,
    line_height: float = PARAGRAPH_LINE_HEIGHT,
    spacing_after: float = SECTION_GAP,
) -> float:
    wrapped = _wrap_text(text, width)
    return (wrapped.count("\n") + 1) * line_height + spacing_after


def _draw_table_section(
    canvas: _PageCanvas,
    *,
    title: str,
    cell_text: list[list[str]],
    col_labels: list[str] | None = None,
    col_widths: list[float] | None = None,
    font_size: float = 9.0,
) -> None:
    _draw_section_title(canvas, title)
    column_count = len(col_labels) if col_labels else (len(cell_text[0]) if cell_text else 1)
    rows = cell_text or [["-"] + [""] * (column_count - 1)]
    row_units = _build_row_units(rows, include_header=col_labels is not None)
    table_height = _estimate_table_height(row_units)
    table_bottom = canvas.cursor_y - table_height
    table = canvas.axis.table(
        cellText=rows,
        colLabels=col_labels,
        cellLoc="left",
        colLoc="left",
        colWidths=col_widths,
        bbox=[PAGE_LEFT, table_bottom, PAGE_WIDTH, table_height],
    )
    _style_table(table, header_rows=1 if col_labels else 0, font_size=font_size)
    _apply_row_heights(table, row_units, column_count)
    canvas.cursor_y = table_bottom - SECTION_GAP


def _build_row_units(cell_text: list[list[str]], *, include_header: bool) -> list[float]:
    row_units: list[float] = [TABLE_HEADER_UNITS] if include_header else []
    for row in cell_text:
        max_lines = max((str(cell or "").count("\n") + 1) for cell in row) if row else 1
        row_units.append(TABLE_ROW_BASE_UNITS + max(0, max_lines - 1) * TABLE_EXTRA_LINE_UNITS)
    return row_units


def _estimate_table_height(row_units: list[float]) -> float:
    return sum(row_units) * TABLE_UNIT_HEIGHT


def _apply_row_heights(table, row_units: list[float], column_count: int) -> None:
    total_units = sum(row_units) or 1.0
    for row_index, row_unit in enumerate(row_units):
        row_height = row_unit / total_units
        for column_index in range(column_count):
            cell_key = (row_index, column_index)
            if cell_key not in table.get_celld():
                continue
            table.get_celld()[cell_key].set_height(row_height)


def _chunk_rows_by_height(
    rows: list[list[str]],
    *,
    max_height: float,
    include_header: bool,
) -> list[list[list[str]]]:
    if not rows:
        return [[]]

    chunks: list[list[list[str]]] = []
    current_chunk: list[list[str]] = []
    for row in rows:
        candidate_chunk = current_chunk + [row]
        candidate_units = _build_row_units(candidate_chunk, include_header=include_header)
        if current_chunk and _estimate_table_height(candidate_units) > max_height:
            chunks.append(current_chunk)
            current_chunk = [row]
        else:
            current_chunk = candidate_chunk
    if current_chunk:
        chunks.append(current_chunk)
    return chunks


def _build_text_pages(
    *,
    report_title: str,
    page_title: str,
    subtitle_lines: list[str],
    sections: list[_TextSectionSpec],
) -> list[Any]:
    figures: list[Any] = []
    page_count = 0

    def start_new_page() -> _PageCanvas:
        nonlocal page_count
        page_count += 1
        current_title = page_title if page_count == 1 else f"{page_title}（续）"
        return _new_canvas(report_title=report_title, page_title=current_title, subtitle_lines=subtitle_lines)

    canvas = start_new_page()
    for section in sections:
        section_title = section.title
        paragraphs = section.paragraphs
        section_width = section.width
        if not paragraphs:
            continue

        paragraph_index = 0
        section_continues = False
        while paragraph_index < len(paragraphs):
            title_text = section_title if not section_continues else f"{section_title}（续）"
            required_height = SECTION_TITLE_HEIGHT + _estimate_text_height(
                paragraphs[paragraph_index],
                width=section_width,
            )
            if not _has_space(canvas, required_height) and not _is_fresh_page(canvas):
                figures.append(canvas.figure)
                canvas = start_new_page()
                section_continues = True
                continue

            _draw_section_title(canvas, title_text)
            while paragraph_index < len(paragraphs):
                paragraph = paragraphs[paragraph_index]
                paragraph_height = _estimate_text_height(paragraph, width=section_width)
                if not _has_space(canvas, paragraph_height) and not _is_fresh_page(canvas):
                    figures.append(canvas.figure)
                    canvas = start_new_page()
                    section_continues = True
                    break
                _draw_text_block(canvas, paragraph, width=section_width)
                paragraph_index += 1

    figures.append(canvas.figure)
    return figures


def _decorate_chart_page(
    *,
    figure,
    report_title: str,
    page_title: str,
    subtitle_lines: list[str],
) -> None:
    figure.set_size_inches(*A4_PAGE_SIZE)
    _draw_page_header(
        figure=figure,
        report_title=report_title,
        page_title=page_title,
        subtitle_lines=subtitle_lines,
    )
    if figure.axes:
        figure.axes[0].set_position(CHART_AXES_RECT)
def _apply_page_footer(
    *,
    figure,
    method_label: str,
    report_month_label: str,
    generated_at: str,
    page_label: str,
    page_index: int,
    total_pages: int,
) -> None:
    figure.add_artist(
        Line2D(
            [PAGE_LEFT, PAGE_RIGHT],
            [FOOTER_DIVIDER_Y, FOOTER_DIVIDER_Y],
            transform=figure.transFigure,
            color=BORDER_COLOR,
            linewidth=0.9,
        )
    )
    left_text = f"{method_label} | 报告月份：{report_month_label} | 生成时间：{generated_at}"
    right_text = f"{page_label} | 第 {page_index}/{total_pages} 页"
    figure.text(PAGE_LEFT, FOOTER_TEXT_Y, left_text, ha="left", va="center", fontsize=8.4, color=FOOTER_COLOR)
    figure.text(PAGE_RIGHT, FOOTER_TEXT_Y, right_text, ha="right", va="center", fontsize=8.4, color=FOOTER_COLOR)


def _is_fresh_page(canvas: _PageCanvas) -> bool:
    return math.isclose(canvas.cursor_y, canvas.initial_cursor_y, abs_tol=1e-9)


def _has_space(canvas: _PageCanvas, required_height: float) -> bool:
    return (canvas.cursor_y - canvas.content_bottom) >= required_height


def _full_page_table_max_height() -> float:
    return CONTENT_TOP - CONTENT_BOTTOM - SECTION_TITLE_HEIGHT - 0.01


def _wrap_text(text: str, width: int) -> str:
    compact = str(text or "").strip()
    if not compact:
        return "-"

    wrapped_lines: list[str] = []
    for paragraph in compact.splitlines():
        normalized = paragraph.strip()
        if not normalized:
            if wrapped_lines and wrapped_lines[-1] != "":
                wrapped_lines.append("")
            continue
        wrapped_lines.extend(
            textwrap.wrap(
                normalized,
                width=width,
                replace_whitespace=False,
                drop_whitespace=True,
                break_long_words=True,
                break_on_hyphens=True,
            )
            or ["-"]
        )
    return "\n".join(wrapped_lines or ["-"])


def _format_float(value: float | None, *, digits: int = 4, suffix: str = "") -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value):.{digits}f}{suffix}"


def _format_lj_metric(statistics: Any, metric: str) -> str:
    if metric == "sd":
        if int(statistics.formal_count) < 2:
            return "暂不计算（样本数不足）"
        return _format_float(statistics.monthly_sd)
    if metric == "cv":
        if int(statistics.formal_count) < 2:
            return "暂不计算（样本数不足）"
        return _format_float(statistics.monthly_cv, digits=2, suffix="%")
    raise ValueError(f"unsupported lj metric: {metric}")


def _format_zscore_metric(statistic: Any, metric: str) -> str:
    if metric == "sd":
        if int(statistic.monthly_count) < 2:
            return "暂不计算（样本数不足）"
        return _format_float(statistic.monthly_sd)
    if metric == "cv":
        if int(statistic.monthly_count) < 2:
            return "暂不计算（样本数不足）"
        return _format_float(statistic.monthly_cv, digits=2, suffix="%")
    raise ValueError(f"unsupported zscore metric: {metric}")


def _style_table(table, *, header_rows: int, font_size: float) -> None:
    table.auto_set_font_size(False)
    table.set_fontsize(font_size)
    for (row_index, _column_index), cell in table.get_celld().items():
        cell.set_edgecolor(BORDER_COLOR)
        cell.set_linewidth(0.6)
        if row_index < header_rows:
            cell.set_facecolor("#eaf2fb")
            cell.set_text_props(weight="bold")
        else:
            cell.set_facecolor("#ffffff")
        cell.PAD = 0.06
