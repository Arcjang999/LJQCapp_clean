"""PDF section rules stay below headings, with readable body and table spacing."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection

from services.report_pdf_layout import (
    CONTENT_BOTTOM,
    PAGE_LEFT,
    PAGE_RIGHT,
    _build_lj_summary_pages,
    _build_pdf_rc_params,
    _build_zscore_summary_pages,
    _draw_section_title,
    _draw_text_block,
    _new_canvas,
    _wrap_measured_text,
)
from services.report_service import (
    _resolve_pdf_font_name,
    build_lj_monthly_report_package,
    build_zscore_monthly_report_package,
)
from tests.lj_monthly_report_smoke_test import (
    TemporaryDatabaseContext,
    seed_lj_batch_with_formal_monthly_data,
)
from tests.zscore_v12_fixtures import seed_zscore_configuration
from tests.zscore_v12_integration_smoke_test import add_run


MIN_GAP_POINTS = 2.0
SECTION_TITLES = (
    "基本信息", "报告期质控概况", "本月质控概况", "报告期统计摘要",
    "月度统计摘要", "检测记录统计摘要", "报告期结论", "月度结论",
    "纸质签署", "异常/失控记录表", "异常/失控汇总表", "各水平统计摘要",
    "统计说明", "原因与纠正措施", "异常说明", "固定声明", "Ct提醒记录",
)


def _compact(text):
    return "".join(str(text).split())


def _assert_section_spacing(figure):
    figure.canvas.draw()
    renderer = figure.canvas.get_renderer()
    points_per_pixel = 72 / figure.dpi
    for axis in figure.axes:
        titles = [text for text in axis.texts if text.get_fontweight() == "bold"]
        rules = []
        for collection in axis.collections:
            if not isinstance(collection, LineCollection):
                continue
            for segment in collection.get_segments():
                if len(segment) == 2 and abs(segment[0][1] - segment[1][1]) < 1e-9:
                    rules.append(collection.get_transform().transform(segment)[0][1])
        body_boxes = [text.get_window_extent(renderer) for text in axis.texts if text not in titles]
        body_boxes.extend(table.get_window_extent(renderer) for table in axis.tables)
        for title in titles:
            title_box = title.get_window_extent(renderer)
            title_top = title.get_transform().transform(title.get_position())[1]
            candidates = [rule_y for rule_y in rules if rule_y < title_top]
            assert candidates, f"{title.get_text()}: missing section rule"
            rule_y = max(candidates)
            title_gap = (title_box.y0 - rule_y) * points_per_pixel
            assert title_gap >= MIN_GAP_POINTS, (
                f"{title.get_text()}: heading/rule gap {title_gap:.2f}pt"
            )
            following = [box for box in body_boxes if box.y1 < title_box.y1]
            if following:
                body_top = max(box.y1 for box in following)
                body_gap = (rule_y - body_top) * points_per_pixel
                assert body_gap >= MIN_GAP_POINTS, (
                    f"{title.get_text()}: rule/body gap {body_gap:.2f}pt"
                )


def _assert_summary_bounds(figure):
    renderer = figure.canvas.get_renderer()
    for axis in figure.axes:
        for text in axis.texts:
            box = text.get_window_extent(renderer)
            assert box.x0 >= figure.bbox.width * (PAGE_LEFT - .005), text.get_text()
            assert box.x1 <= figure.bbox.width * (PAGE_RIGHT + .005), text.get_text()
            assert box.y0 > figure.bbox.height * (CONTENT_BOTTOM - .015), text.get_text()
        for table in axis.tables:
            for cell in table.get_celld().values():
                text_box = cell.get_text().get_window_extent(renderer)
                cell_box = cell.get_window_extent(renderer)
                assert text_box.x0 >= cell_box.x0 - .5, cell.get_text().get_text()
                assert text_box.x1 <= cell_box.x1 + .5, cell.get_text().get_text()
                assert text_box.y0 >= cell_box.y0 - .5, cell.get_text().get_text()
                assert text_box.y1 <= cell_box.y1 + .5, cell.get_text().get_text()
                assert cell_box.y0 > figure.bbox.height * (CONTENT_BOTTOM - .015)


def test_common_section_headings_leave_visible_gaps():
    with plt.rc_context(_build_pdf_rc_params(_resolve_pdf_font_name())):
        for title in SECTION_TITLES:
            canvas = _new_canvas(report_title="质控报告", page_title="报告摘要", subtitle_lines=[])
            try:
                _draw_section_title(canvas, title)
                _draw_text_block(canvas, "核对本报告期间质控记录和处理结论。")
                _assert_section_spacing(canvas.figure)
            finally:
                plt.close(canvas.figure)


def test_summary_wrap_keeps_brackets_and_short_english_words_together():
    from matplotlib.backends.backend_agg import RendererAgg
    from matplotlib.font_manager import FontProperties

    samples = (
        "批次允许不精密度（CV）",
        "Roche Elecsys | HCV化学发光 Chemi-Lumi",
        "水平1：丙型肝炎病毒抗体（抗HCV）血清液体室内质控品；26F02P。",
        "材料（ABCDEFGHIJKLMNOPQRSTUV）血清液体室内质控品。",
    )
    renderer = RendererAgg(1, 1, 72)
    with plt.rc_context(_build_pdf_rc_params(_resolve_pdf_font_name())):
        for sample in samples:
            wrapped = _wrap_measured_text(sample, 100, 9)
            assert _compact(wrapped) == _compact(sample)
            for line in wrapped.splitlines():
                assert line[0] not in "，。；：！？、）】》,.!?:;%)]}"
                assert line[-1] not in "（【《([{"
                assert renderer.get_text_width_height_descent(line, FontProperties(size=9), False)[0] <= 100
            for token in ("CV", "Roche", "Elecsys", "HCV", "Chemi-Lumi", "26F02P"):
                if token in sample:
                    assert any(token in line for line in wrapped.splitlines()), (token, wrapped)


def test_lj_and_zscore_summaries_preserve_spacing_and_long_fields():
    reagent = "Roche Elecsys | HCV化学发光Chemi-Lumi检测试剂盒（电化学发光法）"
    material = "邦德盛丨丙型肝炎病毒抗体（抗HCV）血清液体室内质控品"
    instrument = "DOOM slayer's BigFuckingGun / Cobas e801"
    source = "基于本批次有效建立点计算。上海配置：水平1；丙型肝炎病毒抗体（抗HCV）血清液体室内质控品；26F02P。"
    conclusion = "报告期存在失控记录，需结合原因与纠正措施复核。"
    with TemporaryDatabaseContext():
        _, lj_batch = seed_lj_batch_with_formal_monthly_data()
        z = seed_zscore_configuration(level_count=3)
        for index in range(7):
            add_run(z, index, 3)
        cases = (
            (build_lj_monthly_report_package(lj_batch, "2026-04").report, _build_lj_summary_pages),
            (build_zscore_monthly_report_package(z["batch_id"], "2026-09").report, _build_zscore_summary_pages),
        )
        with plt.rc_context(_build_pdf_rc_params(_resolve_pdf_font_name())):
            for report, builder in cases:
                for long_text in (False, True):
                    evidence = source * (45 if long_text else 1)
                    report_case = replace(
                        report,
                        report_type=report.report_type.replace("monthly", "period"),
                        basic_info=replace(report.basic_info, reagent=reagent, qc_material=material,
                                           instrument=instrument, lot_no="26F02P",
                                           target_source_detail=evidence),
                        conclusion=conclusion * (12 if long_text else 1),
                    )
                    pages = builder(report_case)
                    try:
                        assert pages
                        for figure in pages:
                            _assert_section_spacing(figure)
                            _assert_summary_bounds(figure)
                        all_body = "".join(text.get_text() for figure in pages
                                           for axis in figure.axes for text in axis.texts)
                        table_text = "".join(cell.get_text().get_text() for figure in pages
                                             for axis in figure.axes for table in axis.tables
                                             for cell in table.get_celld().values())
                        for expected in (reagent, material, instrument):
                            assert _compact(expected) in _compact(table_text), expected
                        assert _compact(report_case.conclusion) in _compact(all_body)
                        label_column = 0 if builder is _build_lj_summary_pages else 2
                        evidence_parts = []
                        for figure in pages:
                            for axis in figure.axes:
                                for table in axis.tables:
                                    for (row, column), cell in table.get_celld().items():
                                        if column == label_column and cell.get_text().get_text() == "来源说明":
                                            evidence_parts.append(table[(row, column + 1)].get_text().get_text())
                        assert _compact("".join(evidence_parts)) == _compact(evidence)
                        signature_blocks = [text for figure in pages for axis in figure.axes
                                            for text in axis.texts if "检验员（签名）" in text.get_text()]
                        assert len(signature_blocks) == 1
                        signature = signature_blocks[0]
                        assert len(signature.get_text().splitlines()) == 2
                        assert "复核员（签名）" in signature.get_text().splitlines()[1]
                        signature_figure = signature.figure
                        signature_box = signature.get_window_extent(signature_figure.canvas.get_renderer())
                        height_points = signature_box.height * 72 / signature_figure.dpi
                        assert height_points >= signature.get_fontsize() * 1.8
                    finally:
                        for figure in pages:
                            plt.close(figure)


if __name__ == "__main__":
    test_common_section_headings_leave_visible_gaps()
    test_summary_wrap_keeps_brackets_and_short_english_words_together()
    test_lj_and_zscore_summaries_preserve_spacing_and_long_fields()
    print("report_section_spacing_smoke_test passed")
