"""Generated QC and handling PDFs embed fonts and keep Chinese text selectable."""
from io import BytesIO
from pathlib import Path
import sys
import unittest
import warnings

from fontTools.ttLib import TTFont
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.out_of_control_report_service import (
    build_event_report_package_from_snapshot, render_event_report_pdf,
)
from services.report_service import (
    _resolve_pdf_font_name, build_lj_monthly_report_package, build_lj_monthly_report_pdf,
    build_zscore_monthly_report_package, build_zscore_monthly_report_pdf,
)
from tests.lj_monthly_report_smoke_test import (
    TemporaryDatabaseContext, seed_lj_batch_with_formal_monthly_data,
)
from tests.zscore_monthly_report_smoke_test import seed_zscore_batch_with_formal_monthly_data


def assert_embedded_chinese_pdf(testcase, data, expected):
    reader = PdfReader(BytesIO(data))
    text = ''.join(page.extract_text() for page in reader.pages)
    testcase.assertIn(expected, text)
    embedded = []
    for page in reader.pages:
        for reference in page['/Resources'].get('/Font', {}).values():
            font = reference.get_object()
            testcase.assertNotEqual('/Type3', font.get('/Subtype'))
            if font.get('/Subtype') != '/Type0':
                continue
            testcase.assertIn('/ToUnicode', font)
            for descendant_reference in font['/DescendantFonts']:
                descendant = descendant_reference.get_object()
                descriptor = descendant['/FontDescriptor'].get_object()
                testcase.assertIn('/FontFile2', descriptor)
                embedded.append(descriptor['/FontFile2'].get_object().get_data())
    testcase.assertTrue(embedded, 'No embedded TrueType fonts found')
    testcase.assertTrue(all(embedded), 'An embedded font is empty')
    for program in embedded:
        with TTFont(BytesIO(program)) as font:
            testcase.assertIn('glyf', font, 'FontFile2 must contain TrueType outlines')
            testcase.assertIn('loca', font)
            testcase.assertNotIn('CFF ', font, 'CFF outlines cannot be embedded as FontFile2')
            testcase.assertNotIn('CFF2', font)


class ChinesePdfFontTests(unittest.TestCase):
    def setUp(self):
        self.assertNotEqual('DejaVu Sans', _resolve_pdf_font_name(), 'Install a Chinese font before PDF checks')

    def render_checked(self, renderer, package, expected):
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter('always')
            data = renderer(package)
        missing = [str(item.message) for item in captured if 'Glyph' in str(item.message) and 'missing' in str(item.message)]
        self.assertEqual([], missing)
        assert_embedded_chinese_pdf(self, data, expected)

    def test_lj_and_zscore_reports_embed_extractable_chinese_fonts(self):
        with TemporaryDatabaseContext():
            for seed, builder, renderer, expected in (
                (seed_lj_batch_with_formal_monthly_data, build_lj_monthly_report_package,
                 build_lj_monthly_report_pdf, '单水平（LJ法）月度质控报告'),
                (seed_zscore_batch_with_formal_monthly_data, build_zscore_monthly_report_package,
                 build_zscore_monthly_report_pdf, '多水平（Z-score法）月度质控报告'),
            ):
                with self.subTest(expected=expected):
                    _, batch_id = seed()
                    self.render_checked(renderer, builder(batch_id, '2026-04'), expected)

    def test_handling_report_uses_the_same_embedded_font_policy(self):
        event = dict(event_id=1, revision_no=1, status='pending', original_classification='reject',
                     origin_snapshot=dict(project_name='中文质控项目', qc_method='lj', input_value_type='raw'),
                     content=dict(cause_analysis='检查仪器和试剂记录'))
        package = build_event_report_package_from_snapshot(event, [], lab_info=dict(lab_name='中文实验室'))
        self.render_checked(render_event_report_pdf, package, '失控处理报告')


if __name__ == '__main__':
    unittest.main()
