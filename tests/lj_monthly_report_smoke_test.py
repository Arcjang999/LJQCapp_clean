from __future__ import annotations

from io import BytesIO
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd
import pypdf
from streamlit.testing.v1 import AppTest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import database
from database import (
    add_result,
    create_batch,
    create_project,
    init_db,
    list_report_exports,
)
from services.report_service import (
    LJ_METHOD_LABEL,
    LJ_REPORT_TITLE,
    REPORT_TYPE_LJ_MONTHLY,
    build_lj_monthly_preview_summary,
    build_lj_monthly_report_package,
    build_lj_monthly_report_pdf,
    list_lj_report_month_options,
    save_lj_monthly_report_snapshot,
)
from services.settings_service import REPORT_SETTINGS_FALLBACKS, save_report_settings_form


LJ_PAGE_APPTEST_SCRIPT = """
import sys
from pathlib import Path

ROOT = Path(r'D:\\Github\\LJQCapp_clean')
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pages.lj_page import render_lj_page

render_lj_page()
"""


class TemporaryDatabaseContext:
    def __enter__(self):
        self._tempdir = TemporaryDirectory()
        self._original_db_path = database.DB_PATH
        self._original_legacy_candidates = list(database.LEGACY_DB_CANDIDATES)
        database.DB_PATH = Path(self._tempdir.name) / "lj_monthly_report_smoke_test.db"
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


def seed_lj_batch_with_formal_monthly_data() -> tuple[int, int]:
    project_id = create_project("LJ 月报项目", input_value_type="raw")
    batch_id = create_batch(
        project_id=project_id,
        instrument="AU5800",
        reagent="CRP 试剂",
        qc_material="CRP 质控品",
        concentration="L1",
        lot_no="LOT-202604",
        target_n=5,
        cv_limit=5.0,
    )
    building_values = [100.0, 100.2, 99.8, 100.1, 99.9]
    for index, value in enumerate(building_values, start=0):
        add_result(
            batch_id=batch_id,
            test_time=f"2026-03-28 08:{index:02d}:00",
            operator=f"builder-{index}",
            value=float(value),
            log_value=None,
        )

    formal_rows = [
        ("2026-04-01 08:00:00", 100.0, "月初在控"),
        ("2026-04-02 08:00:00", 100.4, "复查校准状态"),
        ("2026-04-03 08:00:00", 100.6, ""),
    ]
    for index, (test_time, value, note) in enumerate(formal_rows, start=0):
        add_result(
            batch_id=batch_id,
            test_time=test_time,
            operator=f"formal-{index}",
            value=float(value),
            log_value=None,
            manual_note=note,
        )
    return project_id, batch_id


def seed_lj_batch_with_building_only_data() -> tuple[int, int]:
    project_id = create_project("LJ 建靶批次", input_value_type="ct")
    batch_id = create_batch(
        project_id=project_id,
        instrument="QS-7",
        reagent="PCR 试剂",
        qc_material="Ct 质控品",
        concentration="单水平",
        lot_no="CT-ONLY",
        target_n=5,
        cv_limit=None,
    )
    for index, value in enumerate([24.5, 24.6, 24.4, 24.7, 24.5], start=0):
        add_result(
            batch_id=batch_id,
            test_time=f"2026-04-02 09:{index:02d}:00",
            operator=f"builder-{index}",
            value=float(value),
            log_value=None,
        )
    return project_id, batch_id


def seed_lj_batch_with_single_formal_record() -> tuple[int, int]:
    project_id = create_project("LJ 单条正式期", input_value_type="raw")
    batch_id = create_batch(
        project_id=project_id,
        instrument="Cobas e801",
        reagent="AFP 试剂",
        qc_material="AFP 质控品",
        concentration="L1",
        lot_no="ONE-FORMAL",
        target_n=5,
        cv_limit=4.5,
    )
    for index, value in enumerate([80.0, 80.1, 79.9, 80.0, 80.2], start=0):
        add_result(
            batch_id=batch_id,
            test_time=f"2026-03-30 07:{index:02d}:00",
            operator=f"builder-{index}",
            value=float(value),
            log_value=None,
        )
    add_result(
        batch_id=batch_id,
        test_time="2026-04-10 07:30:00",
        operator="formal-0",
        value=80.0,
        log_value=None,
        manual_note="",
    )
    return project_id, batch_id


def test_lj_monthly_report_builds_pdf_and_snapshot() -> None:
    with TemporaryDatabaseContext():
        _project_id, batch_id = seed_lj_batch_with_formal_monthly_data()
        package = build_lj_monthly_report_package(batch_id, "2026-04")

        assert package.report.title == LJ_REPORT_TITLE
        assert package.report.method_label == LJ_METHOD_LABEL
        assert package.report.report_period_label == "2026-04-01 至 2026-04-30"
        assert package.report.input_value_type_label == "真实检测值"
        assert package.report.basic_info.target_source_label == "本批次建靶值"
        assert package.report.statistics.formal_count == 3
        assert package.report.statistics.in_control_count == 1
        assert package.report.statistics.warning_count == 1
        assert package.report.statistics.out_of_control_count == 1
        assert "存在失控记录" in package.report.overview_text
        assert package.report.corrective_actions == ["复查校准状态", "未填写"]
        assert "未填写备注的异常记录统一标记为“未填写”" in package.report.abnormal_summary_text

        pdf_bytes = build_lj_monthly_report_pdf(package)
        assert pdf_bytes.startswith(b"%PDF")

        reader = pypdf.PdfReader(BytesIO(pdf_bytes))
        assert len(reader.pages) == 4
        assert str(reader.metadata.get("/Subject", "")) == REPORT_TYPE_LJ_MONTHLY

        snapshot_id = save_lj_monthly_report_snapshot(package)
        assert snapshot_id > 0

        report_exports_df = list_report_exports(
            report_type=REPORT_TYPE_LJ_MONTHLY,
            batch_id=batch_id,
        )
        assert len(report_exports_df) == 1
        latest_export = report_exports_df.iloc[0]
        assert latest_export["report_month"] == "2026-04"
        assert latest_export["formal_count"] == 3
        assert latest_export["warning_count"] == 1
        assert latest_export["out_of_control_count"] == 1
        assert latest_export["summary_json"]["title"] == LJ_REPORT_TITLE
        assert latest_export["summary_json"]["report_period_label"] == "2026-04-01 至 2026-04-30"


def test_lj_monthly_report_requires_formal_data() -> None:
    with TemporaryDatabaseContext():
        _project_id, batch_id = seed_lj_batch_with_building_only_data()
        month_options = list_lj_report_month_options(batch_id)
        assert month_options == ["2026-04"]
        try:
            build_lj_monthly_report_package(batch_id, "2026-04")
        except ValueError as exc:
            assert "所选月份无正式期数据" in str(exc)
        else:
            raise AssertionError("expected build_lj_monthly_report_package to reject building-only months")


def test_lj_monthly_report_uses_business_text_for_single_record_and_no_abnormal() -> None:
    with TemporaryDatabaseContext():
        _project_id, batch_id = seed_lj_batch_with_single_formal_record()
        package = build_lj_monthly_report_package(batch_id, "2026-04")
        preview_summary = dict(build_lj_monthly_preview_summary(package.report))

        assert package.report.report_period_label == "2026-04-01 至 2026-04-30"
        assert package.report.statistics.formal_count == 1
        assert preview_summary["月度 SD"] == "暂不计算（样本数不足）"
        assert preview_summary["月度 CV%"] == "暂不计算（样本数不足）"
        assert package.report.corrective_actions == []
        assert package.report.corrective_actions_empty_text == "本月无异常记录，无需原因与纠正措施。"
        assert package.report.abnormal_summary_text == "本月无异常记录，无需原因与纠正措施。"
        assert "整体运行稳定" in package.report.overview_text


def test_lj_monthly_report_page_exposes_generate_and_download_flow() -> None:
    with TemporaryDatabaseContext():
        project_id, batch_id = seed_lj_batch_with_formal_monthly_data()
        report_scope = f"lj_monthly_report_{batch_id}"
        at = AppTest.from_string(LJ_PAGE_APPTEST_SCRIPT)
        at.session_state["selected_project_id"] = project_id
        at.session_state["selected_batch_id"] = batch_id
        at.run()

        assert not list(at.exception)
        assert any(selectbox.key == f"{report_scope}_month" for selectbox in at.selectbox)

        at.selectbox(key=f"{report_scope}_month").set_value("2026-04").run()
        at.button(key=f"{report_scope}_generate").click().run()

        assert not list(at.exception)
        download_elements = at.get("download_button")
        assert any(element.label == "下载 PDF" for element in download_elements)
        assert any("报告期间：2026-04-01 至 2026-04-30" in str(item.value) for item in at.caption)


def test_lj_monthly_report_reads_saved_system_settings_and_falls_back_on_empty_values() -> None:
    with TemporaryDatabaseContext():
        save_report_settings_form(
            {
                "lab_name": "StarLab",
                "department_name": "Molecular Center",
                "qc_owner_name": "Alice QC",
                "reviewer_name": "Bob Review",
                "report_statement": "Monthly report for QC archive only.",
            }
        )
        _project_id, batch_id = seed_lj_batch_with_formal_monthly_data()
        package = build_lj_monthly_report_package(batch_id, "2026-04")

        assert package.report.basic_info.lab_name == "StarLab"
        assert package.report.basic_info.department_name == "Molecular Center"
        assert package.report.basic_info.qc_owner_name == "Alice QC"
        assert package.report.basic_info.reviewer_name == "Bob Review"
        assert package.report.declaration == "Monthly report for QC archive only."

        pdf_bytes = build_lj_monthly_report_pdf(package)
        assert pdf_bytes.startswith(b"%PDF")

        save_report_settings_form(
            {
                "lab_name": "",
                "department_name": "",
                "qc_owner_name": "",
                "reviewer_name": "",
                "report_statement": "",
            }
        )
        fallback_package = build_lj_monthly_report_package(batch_id, "2026-04")
        assert fallback_package.report.basic_info.lab_name == REPORT_SETTINGS_FALLBACKS["lab_name"]
        assert fallback_package.report.basic_info.department_name == REPORT_SETTINGS_FALLBACKS["department_name"]
        assert fallback_package.report.basic_info.qc_owner_name == REPORT_SETTINGS_FALLBACKS["qc_owner_name"]
        assert fallback_package.report.basic_info.reviewer_name == REPORT_SETTINGS_FALLBACKS["reviewer_name"]
        assert fallback_package.report.declaration == REPORT_SETTINGS_FALLBACKS["report_statement"]


if __name__ == "__main__":
    test_lj_monthly_report_builds_pdf_and_snapshot()
    test_lj_monthly_report_requires_formal_data()
    test_lj_monthly_report_uses_business_text_for_single_record_and_no_abnormal()
    test_lj_monthly_report_page_exposes_generate_and_download_flow()
    test_lj_monthly_report_reads_saved_system_settings_and_falls_back_on_empty_values()
    print("lj_monthly_report_smoke_test passed")
