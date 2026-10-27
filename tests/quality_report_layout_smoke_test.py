"""Long Chinese provenance must remain within the report page and footer."""
from pathlib import Path
import sys
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import matplotlib.pyplot as plt
from services.report_pdf_layout import (
    _build_lot_trace_pages, _build_quality_pages, _build_pdf_rc_params,
    HEADER_DIVIDER_Y, PAGE_LEFT, PAGE_RIGHT,
)
from services.report_service import _resolve_pdf_font_name


def test_long_provenance_wraps_and_paginates():
    report = SimpleNamespace(title='隔离验证月度报告', report_month_label='2026年09月',
        basic_info=SimpleNamespace(project_name='PCR'), quality_summary={'review': {
            'recorded': {'source_name': '来源核查记录', 'source_version': 'TEST-2026',
                         'requirement_text': '核对方法标本结果尺度并保留原始要求。'*110},
            'evidence': '实验室来源和条件已核对。'*100,
            'confirmed_by': '测试人', 'reviewed_at': '2026-09-22',
            'context': {'specimen': '模拟基质'*45}}})
    with plt.rc_context(_build_pdf_rc_params(_resolve_pdf_font_name())):
        figures = _build_quality_pages(report)
        assert len(figures) > 2
        try:
            for fig in figures:
                fig.canvas.draw()
                renderer = fig.canvas.get_renderer()
                for ax in fig.axes:
                    for text in ax.texts:
                        box = text.get_window_extent(renderer)
                        assert box.x1 <= fig.bbox.width * .95, text.get_text()[:50]
                        assert box.y0 >= fig.bbox.height * .10, text.get_text()[:50]
        finally:
            for fig in figures:
                plt.close(fig)


def test_trace_basis_wraps_inside_header_and_survives_pagination():
    basis = '按本次结果保存的批号及参数版本回顾；累计判定按各版本自己的窗口，缺失历史批号显示未记录。' * 2
    report = SimpleNamespace(title='单水平（L-J法）质控报告', report_month_label='2026年10月',
        basic_info=SimpleNamespace(project_name='V12 C反应蛋白'), lot_trace={
            'basis': basis,
            'actual_lots': [dict(reagent_lot_no=f'REAGENT-{index:03d}', test_time='2026-10-05 08:00:00',
                                 qc_lot_no='水平1: V12-LJ-LOT-001') for index in range(36)],
            'statistics_by_target_version': [], 'events': []})
    project_line = f'项目：{report.basic_info.project_name}  /  {report.report_month_label}'
    with plt.rc_context(_build_pdf_rc_params(_resolve_pdf_font_name())):
        figures = _build_lot_trace_pages(report)
        assert len(figures) > 1
        try:
            for figure in figures:
                figure.canvas.draw()
                renderer = figure.canvas.get_renderer()
                basis_texts = [text for text in figure.texts
                               if text.get_fontsize() == 9.3 and text.get_text() != project_line]
                assert len(basis_texts) > 1
                assert ''.join(text.get_text() for text in basis_texts) == basis
                for text in basis_texts:
                    box = text.get_window_extent(renderer)
                    assert box.x0 >= figure.bbox.width * PAGE_LEFT
                    assert box.x1 <= figure.bbox.width * PAGE_RIGHT
                    assert box.y0 > figure.bbox.height * HEADER_DIVIDER_Y
                header_bottom = min(text.get_window_extent(renderer).y0 for text in basis_texts)
                for axis in figure.axes:
                    for text in axis.texts:
                        box = text.get_window_extent(renderer)
                        assert box.y1 < header_bottom
                        assert box.y0 >= figure.bbox.height * .10
                contents = ''.join(text.get_text() for axis in figure.axes for text in axis.texts)
                assert contents
            all_body = ''.join(text.get_text() for figure in figures for axis in figure.axes for text in axis.texts)
            for index in range(36):
                assert f'REAGENT-{index:03d}' in all_body
        finally:
            for figure in figures:
                plt.close(figure)


if __name__ == '__main__':
    test_long_provenance_wraps_and_paginates()
    test_trace_basis_wraps_inside_header_and_survives_pagination()
    print('quality_report_layout_smoke_test passed')
