from __future__ import annotations

from io import BytesIO
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
    create_zscore_batch,
    create_zscore_project,
    init_db,
    list_report_exports,
)
from services.report_service import (
    REPORT_TYPE_ZSCORE_MONTHLY,
    ZSCORE_METHOD_LABEL,
    ZSCORE_REPORT_TITLE,
    build_zscore_monthly_preview_summary,
    build_zscore_monthly_report_package,
    build_zscore_monthly_report_pdf,
    list_zscore_report_month_options,
    save_zscore_monthly_report_snapshot,
)
from zscore_logic import create_zscore_run, get_template_id_for_level_count


ZSCORE_PAGE_APPTEST_SCRIPT = """
import sys
from pathlib import Path

ROOT = Path(r'D:\\Github\\LJQCapp_clean')
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pages.zscore_page import render_zscore_page

render_zscore_page()
"""


class TemporaryDatabaseContext:
    def __enter__(self):
        self._tempdir = TemporaryDirectory()
        self._original_db_path = database.DB_PATH
        self._original_legacy_candidates = list(database.LEGACY_DB_CANDIDATES)
        database.DB_PATH = Path(self._tempdir.name) / "zscore_monthly_report_smoke_test.db"
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


def _make_level_results(level_1: float, level_2: float) -> list[dict[str, float]]:
    return [
        {"level_id": "Level 1", "raw_value": float(level_1)},
        {"level_id": "Level 2", "raw_value": float(level_2)},
    ]


def seed_zscore_batch_with_formal_monthly_data() -> tuple[int, int]:
    project_id = create_zscore_project("Z-score Monthly Project", level_count=2, input_value_type="raw")
    batch_id = create_zscore_batch(
        project_id=project_id,
        instrument="AU5800",
        reagent="Chemistry Reagent",
        qc_material="Control A",
        concentration="Normal",
        lot_no="ZS-202604",
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
        ("2026-04-01 08:00:00", (100.0, 150.0), "month starts in control"),
        ("2026-04-02 08:00:00", (100.4, 150.0), "recheck calibration status"),
        ("2026-04-03 08:00:00", (100.6, 150.0), ""),
    ]

    for test_time, values, note in building_runs + formal_runs:
        create_zscore_run(
            batch_id=batch_id,
            test_time=test_time,
            operator="tester",
            level_results=_make_level_results(*values),
            template_id=template_id,
            required_n=5,
            manual_note=note,
        )
    return project_id, batch_id


def seed_zscore_batch_with_building_only_data() -> tuple[int, int]:
    project_id = create_zscore_project("Z-score Building Only", level_count=2, input_value_type="ct")
    batch_id = create_zscore_batch(
        project_id=project_id,
        instrument="QS-7",
        reagent="PCR Reagent",
        qc_material="Ct Control",
        concentration="Normal",
        lot_no="ZS-BUILD",
        target_n=5,
        level_1_label="Low",
        level_2_label="High",
    )
    template_id = get_template_id_for_level_count(2)
    for index, values in enumerate([(24.5, 26.0), (24.6, 26.1), (24.4, 25.9)], start=1):
        create_zscore_run(
            batch_id=batch_id,
            test_time=f"2026-04-0{index} 09:00:00",
            operator="builder",
            level_results=_make_level_results(*values),
            template_id=template_id,
            required_n=5,
        )
    return project_id, batch_id


def test_zscore_monthly_report_builds_pdf_and_snapshot() -> None:
    with TemporaryDatabaseContext():
        _project_id, batch_id = seed_zscore_batch_with_formal_monthly_data()
        package = build_zscore_monthly_report_package(batch_id, "2026-04")
        preview_summary = dict(build_zscore_monthly_preview_summary(package.report))

        assert package.report.title == ZSCORE_REPORT_TITLE
        assert package.report.method_label == ZSCORE_METHOD_LABEL
        assert package.report.report_period_label == "2026-04-01 至 2026-04-30"
        assert package.report.input_value_type_label == "真实检测值"
        assert package.report.basic_info.level_count_label == "2 水平"
        assert package.report.statistics.formal_count == 3
        assert package.report.statistics.in_control_count == 1
        assert package.report.statistics.warning_count == 1
        assert package.report.statistics.out_of_control_count == 1
        assert preview_summary["当前阶段"] == "正式质控"
        assert preview_summary["全部 level 已完成建靶"] == "是"
        assert len(package.report.level_statistics) == 2
        assert all(item.monthly_count == 3 for item in package.report.level_statistics)
        assert len(package.report.abnormal_records) == 2
        assert package.report.corrective_actions == ["recheck calibration status", "未填写"]
        assert "失控 run" in package.report.overview_text
        assert not package.monthly_plot_df.empty

        pdf_bytes = build_zscore_monthly_report_pdf(package)
        assert pdf_bytes.startswith(b"%PDF")

        reader = pypdf.PdfReader(BytesIO(pdf_bytes))
        assert len(reader.pages) == 5
        assert str(reader.metadata.get("/Subject", "")) == REPORT_TYPE_ZSCORE_MONTHLY

        snapshot_id = save_zscore_monthly_report_snapshot(package)
        assert snapshot_id > 0

        report_exports_df = list_report_exports(
            report_type=REPORT_TYPE_ZSCORE_MONTHLY,
            batch_id=batch_id,
        )
        assert len(report_exports_df) == 1
        latest_export = report_exports_df.iloc[0]
        assert latest_export["report_month"] == "2026-04"
        assert latest_export["formal_count"] == 3
        assert latest_export["warning_count"] == 1
        assert latest_export["out_of_control_count"] == 1
        assert latest_export["summary_json"]["title"] == ZSCORE_REPORT_TITLE
        assert latest_export["summary_json"]["report_period_label"] == "2026-04-01 至 2026-04-30"


def test_zscore_monthly_report_requires_formal_data() -> None:
    with TemporaryDatabaseContext():
        _project_id, batch_id = seed_zscore_batch_with_building_only_data()
        month_options = list_zscore_report_month_options(batch_id)
        assert month_options == ["2026-04"]
        try:
            build_zscore_monthly_report_package(batch_id, "2026-04")
        except ValueError as exc:
            assert "所选月份无正式期数据" in str(exc)
        else:
            raise AssertionError("expected build_zscore_monthly_report_package to reject building-only months")


def test_zscore_monthly_report_page_exposes_generate_and_download_flow() -> None:
    with TemporaryDatabaseContext():
        project_id, batch_id = seed_zscore_batch_with_formal_monthly_data()
        report_scope = f"zscore_monthly_report_{batch_id}"
        at = AppTest.from_string(ZSCORE_PAGE_APPTEST_SCRIPT)
        at.session_state["zscore_selected_project_id"] = project_id
        at.session_state["zscore_selected_batch_id"] = batch_id
        at.run()

        assert not list(at.exception)
        assert any(selectbox.key == f"{report_scope}_month" for selectbox in at.selectbox)

        at.selectbox(key=f"{report_scope}_month").set_value("2026-04").run()
        at.button(key=f"{report_scope}_generate").click().run()

        assert not list(at.exception)
        download_elements = at.get("download_button")
        assert any(element.label == "下载 PDF" for element in download_elements)
        assert any("报告期间：2026-04-01 至 2026-04-30" in str(item.value) for item in at.caption)


if __name__ == "__main__":
    test_zscore_monthly_report_builds_pdf_and_snapshot()
    test_zscore_monthly_report_requires_formal_data()
    test_zscore_monthly_report_page_exposes_generate_and_download_flow()
    print("zscore_monthly_report_smoke_test passed")
