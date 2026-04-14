from __future__ import annotations

from io import BytesIO
import sqlite3
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import pypdf
from streamlit.testing.v1 import AppTest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import database
from database import (
    DB_PATH,
    add_result,
    create_batch,
    create_project,
    create_zscore_batch,
    create_zscore_project,
    init_db,
    list_report_exports,
)
from services.report_service import (
    LJ_METHOD_LABEL,
    REPORT_TYPE_LJ_MONTHLY,
    REPORT_TYPE_ZSCORE_MONTHLY,
    ZSCORE_METHOD_LABEL,
    build_lj_monthly_report_package,
    build_zscore_monthly_report_package,
    filter_report_history_records,
    get_report_history_record,
    list_report_history_records,
    regenerate_report_from_history,
    save_lj_monthly_report_snapshot,
    save_zscore_monthly_report_snapshot,
)
from zscore_logic import create_zscore_run, get_template_id_for_level_count


REPORT_HISTORY_PAGE_APPTEST_SCRIPT = """
import sys
from pathlib import Path

ROOT = Path(r'D:\\Github\\LJQCapp_clean')
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pages.report_history_page import render_report_history_page

render_report_history_page()
"""


class TemporaryDatabaseContext:
    def __enter__(self):
        self._tempdir = TemporaryDirectory()
        self._original_db_path = database.DB_PATH
        self._original_legacy_candidates = list(database.LEGACY_DB_CANDIDATES)
        database.DB_PATH = Path(self._tempdir.name) / "report_history_smoke_test.db"
        database.LEGACY_DB_CANDIDATES = []
        init_db()
        return self

    def __exit__(self, exc_type, exc, exc_tb):
        database.DB_PATH = self._original_db_path
        database.LEGACY_DB_CANDIDATES = self._original_legacy_candidates
        try:
            self._tempdir.cleanup()
        except PermissionError:
            pass


def _seed_lj_report_snapshot() -> tuple[int, int, int]:
    project_id = create_project("Hist LJ Project", input_value_type="raw")
    batch_id = create_batch(
        project_id=project_id,
        instrument="AU5800",
        reagent="CRP Reagent",
        qc_material="Control A",
        concentration="L1",
        lot_no="LJ-HIST-202604",
        target_n=5,
        cv_limit=5.0,
    )

    for index, value in enumerate([100.0, 100.2, 99.8, 100.1, 99.9], start=0):
        add_result(
            batch_id=batch_id,
            test_time=f"2026-03-28 08:{index:02d}:00",
            operator=f"lj-builder-{index}",
            value=float(value),
            log_value=None,
        )

    formal_rows = [
        ("2026-04-01 08:00:00", 100.0, "monthly start"),
        ("2026-04-02 08:00:00", 100.4, "check calibration"),
        ("2026-04-03 08:00:00", 100.6, ""),
    ]
    for index, (test_time, value, note) in enumerate(formal_rows, start=0):
        add_result(
            batch_id=batch_id,
            test_time=test_time,
            operator=f"lj-formal-{index}",
            value=float(value),
            log_value=None,
            manual_note=note,
        )

    package = build_lj_monthly_report_package(batch_id, "2026-04")
    export_id = save_lj_monthly_report_snapshot(package)
    return export_id, project_id, batch_id


def _seed_zscore_report_snapshot() -> tuple[int, int, int]:
    project_id = create_zscore_project("Hist Zscore Project", level_count=2, input_value_type="raw")
    batch_id = create_zscore_batch(
        project_id=project_id,
        instrument="AU5800",
        reagent="Chemistry Reagent",
        qc_material="Control B",
        concentration="Normal",
        lot_no="ZS-HIST-202604",
        target_n=5,
        level_1_label="Level 1",
        level_2_label="Level 2",
        cv_limit=5.0,
    )
    template_id = get_template_id_for_level_count(2)

    building_runs = [
        ("2026-03-27 08:00:00", (100.0, 150.0), ""),
        ("2026-03-28 08:00:00", (100.2, 150.2), ""),
        ("2026-03-29 08:00:00", (99.8, 149.8), ""),
        ("2026-03-30 08:00:00", (100.1, 150.1), ""),
        ("2026-03-31 08:00:00", (99.9, 149.9), ""),
    ]
    formal_runs = [
        ("2026-04-01 08:00:00", (100.0, 150.0), "start in control"),
        ("2026-04-02 08:00:00", (100.4, 150.0), "recheck calibration"),
        ("2026-04-03 08:00:00", (100.6, 150.0), ""),
    ]

    for test_time, values, note in building_runs + formal_runs:
        create_zscore_run(
            batch_id=batch_id,
            test_time=test_time,
            operator="zscore-tester",
            level_results=[
                {"level_id": "Level 1", "raw_value": float(values[0])},
                {"level_id": "Level 2", "raw_value": float(values[1])},
            ],
            template_id=template_id,
            required_n=5,
            manual_note=note,
        )

    package = build_zscore_monthly_report_package(batch_id, "2026-04")
    export_id = save_zscore_monthly_report_snapshot(package)
    return export_id, project_id, batch_id


def test_report_history_lists_mixed_records_and_filters() -> None:
    with TemporaryDatabaseContext():
        lj_export_id, _lj_project_id, _lj_batch_id = _seed_lj_report_snapshot()
        zscore_export_id, _z_project_id, _z_batch_id = _seed_zscore_report_snapshot()

        records = list_report_history_records()
        assert [record.export_id for record in records] == [zscore_export_id, lj_export_id]
        assert {record.project_name for record in records} == {"Hist LJ Project", "Hist Zscore Project"}
        assert {record.method_label for record in records} == {LJ_METHOD_LABEL, ZSCORE_METHOD_LABEL}
        assert all(record.summary_text for record in records)
        assert all(record.file_name for record in records)

        project_filtered = filter_report_history_records(records, project_query="Hist LJ")
        assert len(project_filtered) == 1
        assert project_filtered[0].project_name == "Hist LJ Project"

        method_filtered = filter_report_history_records(records, method_label=ZSCORE_METHOD_LABEL)
        assert len(method_filtered) == 1
        assert method_filtered[0].report_type == REPORT_TYPE_ZSCORE_MONTHLY

        batch_filtered = filter_report_history_records(records, batch_query="ZS-HIST-202604")
        assert len(batch_filtered) == 1
        assert batch_filtered[0].project_name == "Hist Zscore Project"

        month_filtered = filter_report_history_records(records, report_month="2026-04")
        assert len(month_filtered) == 2


def test_report_history_regenerates_lj_and_zscore_reports() -> None:
    with TemporaryDatabaseContext():
        lj_export_id, _lj_project_id, _lj_batch_id = _seed_lj_report_snapshot()
        zscore_export_id, _z_project_id, _z_batch_id = _seed_zscore_report_snapshot()

        lj_result = regenerate_report_from_history(lj_export_id)
        assert lj_result.report_type == REPORT_TYPE_LJ_MONTHLY
        assert lj_result.pdf_bytes.startswith(b"%PDF")

        zscore_result = regenerate_report_from_history(zscore_export_id)
        assert zscore_result.report_type == REPORT_TYPE_ZSCORE_MONTHLY
        assert zscore_result.pdf_bytes.startswith(b"%PDF")

        lj_reader = pypdf.PdfReader(BytesIO(lj_result.pdf_bytes))
        zscore_reader = pypdf.PdfReader(BytesIO(zscore_result.pdf_bytes))
        assert len(lj_reader.pages) >= 4
        assert len(zscore_reader.pages) >= 5

        exports_df = list_report_exports()
        assert len(exports_df) == 4
        assert int((exports_df["report_type"] == REPORT_TYPE_LJ_MONTHLY).sum()) == 2
        assert int((exports_df["report_type"] == REPORT_TYPE_ZSCORE_MONTHLY).sum()) == 2


def test_report_history_page_renders_from_global_entry() -> None:
    with TemporaryDatabaseContext():
        _seed_lj_report_snapshot()
        _seed_zscore_report_snapshot()

        app = AppTest.from_file(str(PROJECT_ROOT / "app.py"))
        app.run()

        assert not list(app.exception)
        assert any(
            "WATERMARK_TEXT: 本软件由邦德盛开发，该版本仅供演示或试用。" in str(item.value)
            for item in app.markdown
        )
        assert any(button.key == "open_report_history_page" for button in app.button)

        app.button(key="open_report_history_page").click().run()
        assert not list(app.exception)
        assert any(
            "WATERMARK_TEXT: 本软件由邦德盛开发，该版本仅供演示或试用。" in str(item.value)
            for item in app.markdown
        )
        assert any(text_input.key == "report_history_project_query" for text_input in app.text_input)
        assert any(selectbox.key == "report_history_method_filter" for selectbox in app.selectbox)
        assert any(selectbox.key == "report_history_month_filter" for selectbox in app.selectbox)

        page = AppTest.from_string(REPORT_HISTORY_PAGE_APPTEST_SCRIPT)
        page.run()
        assert not list(page.exception)
        assert any("Hist LJ Project" in str(item.value) for item in page.markdown)
        assert any("Hist Zscore Project" in str(item.value) for item in page.markdown)

        page.text_input(key="report_history_project_query").set_value("Hist LJ").run()
        assert not list(page.exception)
        assert any("Hist LJ Project" in str(item.value) for item in page.markdown)
        assert not any("Hist Zscore Project" in str(item.value) for item in page.markdown)


def test_report_history_handles_missing_batch_gracefully() -> None:
    with TemporaryDatabaseContext():
        export_id, _project_id, batch_id = _seed_lj_report_snapshot()

        with sqlite3.connect(database.DB_PATH) as connection:
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.execute("DELETE FROM batches WHERE id = ?", (batch_id,))
            connection.commit()

        record = get_report_history_record(export_id)
        assert record.project_name == "Hist LJ Project"
        try:
            regenerate_report_from_history(export_id)
        except ValueError as exc:
            assert "批次不存在" in str(exc)
        else:
            raise AssertionError("expected missing batch to surface a clear regeneration error")


if __name__ == "__main__":
    test_report_history_lists_mixed_records_and_filters()
    test_report_history_regenerates_lj_and_zscore_reports()
    test_report_history_page_renders_from_global_entry()
    test_report_history_handles_missing_batch_gracefully()
    print("report_history_smoke_test passed")
