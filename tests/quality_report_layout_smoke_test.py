"""Long Chinese provenance must remain within the report page and footer."""
from pathlib import Path
import sys
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import matplotlib.pyplot as plt
from services.report_pdf_layout import _build_quality_pages, _build_pdf_rc_params
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


if __name__ == '__main__':
    test_long_provenance_wraps_and_paginates()
    print('quality_report_layout_smoke_test passed')
