"""Monthly summary text stays inside cells and paginates before the footer."""
from pathlib import Path
from io import BytesIO
from pypdf import PdfReader
import sys
from dataclasses import replace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import matplotlib.pyplot as plt
from services.report_pdf_layout import _build_lj_summary_pages, _build_zscore_summary_pages, _build_pdf_rc_params
from services.report_service import build_lj_monthly_report_package, build_zscore_monthly_report_package, _resolve_pdf_font_name
from tests.lj_monthly_report_smoke_test import seed_lj_batch_with_formal_monthly_data
from tests.zscore_v12_fixtures import seed_zscore_configuration
from tests.zscore_v12_integration_smoke_test import add_run
from tests.lj_monthly_report_smoke_test import TemporaryDatabaseContext


def check_pages(pages):
    assert len(pages) >= 2
    for fig in pages:
        fig.canvas.draw()
        renderer = fig.canvas.get_renderer()
        for ax in fig.axes:
            for text in ax.texts:
                box = text.get_window_extent(renderer)
                assert box.x0 >= fig.bbox.width * .065, text.get_text()
                assert box.x1 <= fig.bbox.width * .94, text.get_text()
                assert box.y0 > fig.bbox.height * .10, text.get_text()
            for table in ax.tables:
                for cell in table.get_celld().values():
                    box = cell.get_text().get_window_extent(renderer)
                    boundary = cell.get_window_extent(renderer)
                    assert box.x0 >= boundary.x0 - .5, cell.get_text().get_text()
                    assert box.x1 <= boundary.x1 + .5, cell.get_text().get_text()
                    assert box.y0 >= boundary.y0 - .5, cell.get_text().get_text()
                    assert box.y1 <= boundary.y1 + .5, cell.get_text().get_text()
                    assert boundary.y0 > fig.bbox.height * .10


def test_long_summary_fields_fit_and_preserve_text():
    with TemporaryDatabaseContext():
        _, lj_batch = seed_lj_batch_with_formal_monthly_data()
        z = seed_zscore_configuration(level_count=3)
        for i in range(6):
            add_run(z, i, 3)
        reports = [(build_lj_monthly_report_package(lj_batch, '2026-04').report, _build_lj_summary_pages),
                   (build_zscore_monthly_report_package(z['batch_id'], '2026-09').report, _build_zscore_summary_pages)]
        reagent = '乙型肝炎病毒核酸定量检测试剂盒（PCR-荧光探针法）'
        material = '乙型肝炎病毒脱氧核糖核酸(HBV DNA)血清(液体)室内质控品'
        evidence = '按本批次制备水平建立控制参数并核对实际批号。' * 70
        conclusion = '需要核对全部检测记录及失控处理后确认月度结论。' * 15
        for report, builder in reports:
            report = replace(report, basic_info=replace(report.basic_info, reagent=reagent,
                             qc_material=material, target_source_detail=evidence), conclusion=conclusion)
            with plt.rc_context(_build_pdf_rc_params(_resolve_pdf_font_name())):
                pages = builder(report)
                try:
                    check_pages(pages)
                    all_text = ''.join(text.get_text() for fig in pages for ax in fig.axes for text in ax.texts)
                    assert ''.join(conclusion.split()) in ''.join(all_text.split())
                    table_text = ''.join(cell.get_text().get_text() for fig in pages for ax in fig.axes
                                         for table in ax.tables for cell in table.get_celld().values())
                    compact = ''.join(table_text.split())
                    assert ''.join(reagent.split()) in compact
                    assert ''.join(material.split()) in compact
                    evidence_parts = []
                    label_column = 0 if builder is _build_lj_summary_pages else 2
                    for fig in pages:
                        for ax in fig.axes:
                            for table in ax.tables:
                                for (row, col), cell in table.get_celld().items():
                                    if col == label_column and cell.get_text().get_text() == '来源说明':
                                        evidence_parts.append(table[(row, col + 1)].get_text().get_text())
                    assert ''.join(''.join(evidence_parts).split()) == ''.join(evidence.split())
                finally:
                    for fig in pages:
                        plt.close(fig)


def test_handling_monthly_keeps_summary_continuation():
    from services.out_of_control_report_service import render_monthly_with_handling
    with TemporaryDatabaseContext():
        _, lj_batch = seed_lj_batch_with_formal_monthly_data()
        z = seed_zscore_configuration(level_count=3)
        for i in range(6):
            add_run(z, i, 3)
        cases = [(build_lj_monthly_report_package(lj_batch, '2026-04'), 'lj'),
                 (build_zscore_monthly_report_package(z['batch_id'], '2026-09'), 'zscore')]
        summary = {'event_id': 1, 'revision_no': 1, 'test_time': '2026-04-01 08:00:00',
                   'original_classification': 'reject', 'status': 'completed',
                   'cause_analysis': '原因核对完成', 'corrective_action': '纠正完成',
                   'effect_description': '复测恢复在控', 'reports': []}
        for package, method in cases:
            report = replace(package.report, conclusion='需要保留完整月度结论与处理附页。' * 20,
                             handling_summaries=[summary])
            package = replace(package, report=report)
            reader = PdfReader(BytesIO(render_monthly_with_handling(package, _resolve_pdf_font_name(), method=method)))
            text = ''.join(''.join(page.extract_text().split()) for page in reader.pages)
            assert '报告摘要（续）' in text
            assert '月度结论' in text
            assert ''.join(report.conclusion.split()) in text
            assert ('月度统计摘要' if method == 'lj' else '检测记录统计摘要') in text
            assert '失控处理摘要' in text


if __name__ == '__main__':
    test_long_summary_fields_fit_and_preserve_text()
    test_handling_monthly_keeps_summary_continuation()
    print('monthly_summary_layout_smoke_test passed')
