"""Archive inventory pages retain their own complete, unobstructed page footers."""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pypdf import PdfReader

from services import report_pdf_layout as layout
from services.report_archive_service import _status_pdf


def _compact(value):
    return "".join(str(value).split())


def _report():
    return SimpleNamespace(
        title="单水平（L-J法）质控报告",
        method_label="单水平（L-J法）",
        report_month_label="2026-11-01 至 2026-11-30",
        report_period_label="2026-11-01 至 2026-11-30",
        generated_at="2026-11-09 12:00",
    )


def _evidence(event_count, unregistered_count):
    events = [dict(event_id=1000 + index, revision_no=index % 3 + 1,
                   test_time=f"2026-11-{index % 28 + 1:02d} 08:30:00",
                   status_label="处理完成", patient_impact_assessment=f"已核对影响范围{index}",
                   retest_count=index % 3, attachment_count=index % 2)
              for index in range(event_count)]
    unregistered = [dict(source_id=2000 + index,
                         test_time=f"2026-11-{index % 28 + 1:02d} 09:00:00")
                    for index in range(unregistered_count)]
    return events, unregistered


def _assert_footer_geometry(figure):
    figure.canvas.draw()
    renderer = figure.canvas.get_renderer()
    padding = 2 * figure.dpi / 72
    divider_y = figure.bbox.height * layout.FOOTER_DIVIDER_Y
    footer_texts = [text for text in figure.texts
                    if abs(text.get_position()[1] - layout.FOOTER_TEXT_Y) < 1e-9]
    assert len(footer_texts) == 2
    footer_boxes = [text.get_window_extent(renderer) for text in footer_texts]
    for box in footer_boxes:
        assert box.x0 >= figure.bbox.width * (layout.PAGE_LEFT - .005)
        assert box.x1 <= figure.bbox.width * (layout.PAGE_RIGHT + .005)
        assert box.y0 > 0
        assert box.y1 + padding < divider_y
    assert footer_boxes[0].x1 + padding < footer_boxes[1].x0
    for axis in figure.axes:
        for text in axis.texts:
            box = text.get_window_extent(renderer)
            assert box.y0 > divider_y + padding, text.get_text()
            assert all(not box.overlaps(footer_box) for footer_box in footer_boxes)


def _check_inventory(event_count, unregistered_count, *, expect_multiple_pages):
    report = _report()
    events, unregistered = _evidence(event_count, unregistered_count)
    original_apply = layout._apply_page_footer
    rendered = []

    def apply_and_check(**kwargs):
        original_apply(**kwargs)
        _assert_footer_geometry(kwargs["figure"])
        rendered.append((kwargs["page_index"], kwargs["total_pages"], kwargs["page_label"]))

    with patch.object(layout, "_apply_page_footer", side_effect=apply_and_check):
        pdf_bytes = _status_pdf(report, events, unregistered)
    reader = PdfReader(BytesIO(pdf_bytes))
    page_count = len(reader.pages)
    assert (page_count > 1) if expect_multiple_pages else (page_count == 1)
    assert rendered == [(index, page_count, "归档清单") for index in range(1, page_count + 1)]
    page_texts = [page.extract_text() or "" for page in reader.pages]
    for index, text in enumerate(page_texts, 1):
        compact = _compact(text)
        assert _compact(f"报告范围：{report.report_month_label}") in compact
        assert _compact(f"归档清单 | 第 {index}/{page_count} 页") in compact
        assert "失控处理归档清单" in compact
    all_text = _compact("".join(page_texts))
    for event in events:
        assert _compact(f"处理记录 {event['event_id']} / 版本 {event['revision_no']}") in all_text
        assert _compact(event["patient_impact_assessment"]) in all_text
    for source in unregistered:
        assert _compact(f"检测编号 {source['source_id']}；尚未登记失控处理。") in all_text
    assert "报告日期以外的复测单独列出" in all_text
    assert "患者结果是否可放行，由实验室结合调查确认。" in all_text


def test_single_page_inventory_has_its_own_footer():
    _check_inventory(1, 1, expect_multiple_pages=False)


def test_multiple_page_inventory_keeps_footers_and_all_evidence():
    _check_inventory(25, 15, expect_multiple_pages=True)


def test_empty_inventory_creates_no_extra_pages():
    assert _status_pdf(_report(), [], []) is None


if __name__ == "__main__":
    test_single_page_inventory_has_its_own_footer()
    test_multiple_page_inventory_keeps_footers_and_all_evidence()
    test_empty_inventory_creates_no_extra_pages()
    print("report_archive_footer_smoke_test passed")
