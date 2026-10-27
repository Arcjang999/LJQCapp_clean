"""Release-boundary standards, exported method names and frozen report evidence."""
from datetime import datetime
from io import BytesIO
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pypdf import PdfReader
from streamlit.testing.v1 import AppTest

from services import time_service
from services.quality_catalog_service import browse_quality_catalog
from services.quality_target_service import item_context, decode
from services.quality_review_service import default_process_requirements
from services.quality_applicability_service import assess
from services.report_service import (
    LJ_METHOD_LABEL, build_lj_monthly_report_package, build_lj_monthly_report_pdf,
    save_lj_monthly_report_snapshot, get_report_history_record,
    read_report_history_pdf, filter_report_history_records,
)
from services.project_config_io_service import build_project_import_template_xlsx
from services.export_utils import xlsx_bytes_to_dataframes
from tests.quality_applicability_smoke_test import IsolatedDatabase, draft, context, adopt, rejected
from tests.lj_monthly_report_smoke_test import seed_lj_batch_with_formal_monthly_data


class TrialFeedbackAlignmentTest(unittest.TestCase):
    def test_standard_cutover_uses_laboratory_date_and_keeps_saved_sources(self):
        with patch.object(time_service, 'local_now', return_value=datetime(2026, 10, 31, 23, 59, 59)), IsolatedDatabase():
            _, item_id = draft()
            candidates = assess(item_context('project', item_id), context())
            old_process = default_process_requirements(candidates)
            self.assertEqual(old_process['source_ids'], ['wst641-2018-iqc'])
            adopt(item_id, process_requirements=old_process)
            frozen = item_context('project', item_id)['quality_review_json']
            with patch.object(time_service, 'local_now', return_value=datetime(2026, 11, 1)):
                rows = browse_quality_catalog('641', section='general')
                self.assertEqual([r['id'] for r in rows], ['wst641-2026-iqc'])
                self.assertEqual(item_context('project', item_id)['quality_review_json'], frozen)
                rejected(lambda: adopt(item_id, process_requirements=old_process), '重新选择')
                adopt(item_id)
                updated = decode(item_context('project', item_id)['quality_review_json'])
                self.assertEqual(updated['process_requirements']['source_ids'], ['wst641-2026-iqc'])

    def test_open_procedure_draft_keeps_local_text_at_standard_cutover(self):
        with patch.object(time_service, 'local_now', return_value=datetime(2026, 10, 31, 23, 59)), IsolatedDatabase():
            _, item_id = draft()
            item = item_context('project', item_id)
            app = AppTest.from_string(
                'import streamlit as st\n'
                'from services.quality_applicability_service import assess\n'
                'from ui.quality_applicability import render_process_requirements\n'
                'render_process_requirements(assess(st.session_state["item"], st.session_state["context"]), {}, "cutover")',
                default_timeout=30)
            app.session_state['item'], app.session_state['context'] = item, context()
            app.run()
            self.assertFalse(app.exception)
            app.text_area(key='cutover_process_text').set_value('按本实验室现行程序执行。').run()
            with patch.object(time_service, 'local_now', return_value=datetime(2026, 11, 1)):
                app.run()
                self.assertFalse(app.exception)
                self.assertEqual(app.multiselect(key='cutover_process_sources').value, ['wst641-2026-iqc'])
                self.assertEqual(app.text_area(key='cutover_process_text').value, '按本实验室现行程序执行。')

    def test_new_import_template_uses_the_display_name(self):
        sheets = xlsx_bytes_to_dataframes(build_project_import_template_xlsx())
        self.assertEqual(sheets['项目配置'].iloc[0]['质控方法*'], 'L-J')
        instructions = sheets['填写说明'].set_index('项目')['说明']
        self.assertIn('L-J', instructions['质控方法'])

    def test_new_report_names_and_historical_file_bytes(self):
        with IsolatedDatabase():
            _, batch_id = seed_lj_batch_with_formal_monthly_data()
            package = build_lj_monthly_report_package(batch_id, '2026-04')
            self.assertEqual(package.report.method_label, LJ_METHOD_LABEL)
            payload = build_lj_monthly_report_pdf(package)
            text = '\n'.join(page.extract_text() or '' for page in PdfReader(BytesIO(payload)).pages)
            self.assertIn('L-J法', text)
            self.assertNotIn('LJ法', text)
            export_id = save_lj_monthly_report_snapshot(package)
            original = read_report_history_pdf(export_id)
            # Stored metadata from an earlier spelling must remain selectable;
            # the archived file itself is always returned unchanged.
            from database import get_connection
            with get_connection() as connection:
                connection.execute('UPDATE report_exports SET method_label=? WHERE id=?', ('单水平（LJ法）', export_id))
            record = get_report_history_record(export_id)
            self.assertEqual(record.method_label, LJ_METHOD_LABEL)
            self.assertEqual(filter_report_history_records([record], method_label=LJ_METHOD_LABEL), [record])
            self.assertEqual(read_report_history_pdf(export_id), original)


if __name__ == '__main__':
    unittest.main(verbosity=2)
